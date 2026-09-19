from __future__ import annotations
import json, os
from collections import Counter, defaultdict
from phh_master_audit_v30 import _master_rows_full, _s

def _aliases(v):
    return {x.strip() for x in _s(v).split(',') if x.strip()}

def _zero_norm(v):
    s=_s(v)
    if not s or not s.isdigit():
        return s
    z=s.lstrip('0')
    return z or '0'

def _latest_v30_rows():
    db=os.getenv('DATABASE_URL')
    if not db:
        raise RuntimeError('DATABASE_URL missing')
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select max(audit_ts) from phh_master_audit_v30')
            ts=cur.fetchone()[0]
            if not ts:
                raise RuntimeError('no phh_master_audit_v30 snapshot')
            cur.execute('''
                select row_no,shopify_sku,shopify_barcode,master_220_sku,master_220_ean,
                       master_220_status,master_match_status,live_state,offer_id,offer_status,
                       offer_amount,offer_price,live_ean,live_sku,match_basis,mismatch_code
                from phh_master_audit_v30
                where audit_ts=%s and coalesce(mismatch_code,'')<>''
                order by row_no
            ''',(ts,))
            cols=[d.name for d in cur.description]
            rows=[dict(zip(cols,r)) for r in cur.fetchall()]
    return ts, rows

def _persist(rows, summary, source_ts):
    db=os.getenv('DATABASE_URL')
    if not db:
        return
    try:
        import psycopg
        with psycopg.connect(db) as c:
            with c.cursor() as cur:
                cur.execute('''create table if not exists phh_residual_semantic_audit_v34 (
                    audit_ts timestamptz not null default now(),
                    source_v30_ts timestamptz not null,
                    row_no integer not null,
                    severity text not null,
                    classification text not null,
                    reason text,
                    mismatch_code text,
                    vendor text,
                    title text,
                    shopify_status text,
                    shopify_sku text,
                    shopify_barcode text,
                    master_220_sku text,
                    master_220_ean text,
                    master_220_status text,
                    live_state text,
                    offer_status text,
                    live_sku text,
                    live_ean text,
                    owner_rows jsonb,
                    primary key(audit_ts,row_no)
                )''')
                cur.execute('''create table if not exists phh_residual_semantic_summary_v34 (
                    audit_ts timestamptz not null default now(),
                    source_v30_ts timestamptz not null,
                    summary jsonb not null
                )''')
                cur.execute('insert into phh_residual_semantic_summary_v34(source_v30_ts,summary) values(%s,%s)',
                            (source_ts,json.dumps(summary)))
                vals=[]
                for x in rows:
                    vals.append((source_ts,x['row_no'],x['severity'],x['classification'],x['reason'],
                                 x['mismatch_code'],x['vendor'],x['title'],x['shopify_status'],
                                 x['shopify_sku'],x['shopify_barcode'],x['master_220_sku'],
                                 x['master_220_ean'],x['master_220_status'],x['live_state'],
                                 x['offer_status'],x['live_sku'],x['live_ean'],json.dumps(x['owner_rows'])))
                cur.executemany('''insert into phh_residual_semantic_audit_v34(
                    source_v30_ts,row_no,severity,classification,reason,mismatch_code,vendor,title,
                    shopify_status,shopify_sku,shopify_barcode,master_220_sku,master_220_ean,
                    master_220_status,live_state,offer_status,live_sku,live_ean,owner_rows
                ) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)''',vals)
            c.commit()
    except Exception as e:
        print('PHH_RESIDUAL_SEMANTIC_AUDIT_V34_PERSIST_FAILED',type(e).__name__,str(e),flush=True)

def run():
    source_ts, residual=_latest_v30_rows()
    master=_master_rows_full()
    by_row={i:r for i,r in enumerate(master,start=2)}
    sku_active=defaultdict(list); ean_active=defaultdict(list); ean_any=defaultdict(list)
    for row_no,r in by_row.items():
        s220=_s(r.get('220_sku')); e220=_s(r.get('220_ean')); mst=_s(r.get('220_status'))
        if e220:
            ean_any[e220].append(row_no)
        sb=_s(r.get('shopify_barcode'))
        if sb:
            ean_any[sb].append(row_no)
        if mst=='ACTIVE_220':
            for s in _aliases(s220):
                sku_active[s].append(row_no)
            if e220:
                ean_active[e220].append(row_no)

    out=[]; class_counts=Counter(); severity_counts=Counter()
    for x in residual:
        row_no=int(x['row_no']); m=by_row.get(row_no,{})
        vendor=_s(m.get('vendor')); title=_s(m.get('shopify_title'))
        shop_status=_s(m.get('shopify_status'))
        source_sku=_s(m.get('shopify_sku')); source_barcode=_s(m.get('shopify_barcode'))
        live_sku=_s(x.get('live_sku')); live_ean=_s(x.get('live_ean'))
        master_ean=_s(x.get('master_220_ean')); master_sku=_s(x.get('master_220_sku'))
        mismatch=set(filter(None,_s(x.get('mismatch_code')).split('|')))
        owner_rows=sorted(set(
            [r for r in sku_active.get(live_sku,[]) if r!=row_no] +
            [r for r in ean_active.get(live_ean,[]) if r!=row_no]
        ))
        owner_meta=[]
        for rno in owner_rows:
            rr=by_row.get(rno,{})
            owner_meta.append({
                'row_no':rno,
                'shopify_product_id':_s(rr.get('shopify_product_id')),
                'shopify_sku':_s(rr.get('shopify_sku')),
                '220_sku':_s(rr.get('220_sku')),
                '220_ean':_s(rr.get('220_ean')),
                '220_status':_s(rr.get('220_status'))
            })

        severity='MANUAL'; cls='MANUAL_REVIEW'; reason=[]
        if row_no==3829 and ('MASTER_FALSE_ACTIVE_220' in mismatch or 'EAN_IDENTITY_MISMATCH' in mismatch):
            severity='EXPECTED'; cls='EXPECTED_LEADING_ZERO_EXCEPTION'; reason.append('documented_row3829_exception')
        elif row_no==1123 and 'ACTIVE_220_WITHOUT_SELLER_OFFER' in mismatch:
            severity='MANUAL'; cls='MANUAL_LOWA_CARD_ONLY_SPECIAL'; reason.append('documented_row1123_exception')
        elif 'EAN_IDENTITY_MISMATCH' in mismatch:
            if master_ean and live_ean and _zero_norm(master_ean)==_zero_norm(live_ean):
                severity='EXPECTED'; cls='EXPECTED_LEADING_ZERO_ALIAS'; reason.append('ean_equal_after_leading_zero_normalization')
            elif _s(x.get('offer_status')).lower()=='reset':
                severity='EXPECTED'; cls='EXPECTED_RESET_LEGACY_EAN'; reason.append('reset_offer_not_current_primary_identity')
            else:
                other=[r for r in ean_any.get(live_ean,[]) if r!=row_no]
                if other:
                    severity='HARD_CONFLICT'; cls='HARD_CROSS_ROW_EAN_CONFLICT'; reason.append('live_ean_present_on_other_master_row')
                elif vendor.upper()=='SIGG' and live_ean.startswith('222'):
                    severity='MANUAL'; cls='SIGG_MULTI_EAN_PRIMARY_REVIEW'; reason.append('sigg_internal_222_primary_ean')
                elif vendor.upper()=='THERMOWAVE' and source_barcode==master_ean:
                    severity='EXPECTED'; cls='EXPECTED_MULTI_EAN_ALIAS_REVIEW'; reason.append('source_ean_matches_master_while_offer_primary_differs')
                else:
                    severity='ACTIONABLE'; cls='ACTIONABLE_PRIMARY_EAN_REVIEW'; reason.append('active_primary_ean_diff_without_known_alias_rule')
        elif 'LIVE_OFFER_IDENTITY_MISSING_IN_MASTER' in mismatch:
            if owner_meta:
                if any(o['shopify_product_id'] for o in owner_meta):
                    severity='HARD_CONFLICT'; cls='HARD_DUPLICATE_ACTIVE_OWNER'; reason.append('live_identity_owned_by_other_shopify_linked_row')
                elif live_sku and live_sku in _aliases(source_sku):
                    severity='ACTIONABLE'; cls='LEGACY_OWNER_MIGRATION_CANDIDATE'; reason.append('current_source_sku_contains_live_sku_alias')
                elif not live_sku and live_ean and source_barcode==live_ean:
                    severity='ACTIONABLE'; cls='LEGACY_OWNER_MIGRATION_CANDIDATE'; reason.append('ean_only_offer_matches_current_source_barcode')
                else:
                    severity='HARD_CONFLICT'; cls='CROSS_SKU_LEGACY_OWNER_CONFLICT'; reason.append('legacy_owner_exists_but_current_source_sku_differs')
            else:
                if shop_status.lower()=='draft':
                    severity='EXPECTED'; cls='EXPECTED_DRAFT_DUPLICATE'; reason.append('shopify_draft')
                elif _s(x.get('offer_status')).lower()!='active':
                    severity='EXPECTED'; cls='EXPECTED_INACTIVE_IDENTITY'; reason.append('offer_not_active')
                elif live_sku and live_sku in _aliases(source_sku) and live_ean and source_barcode==live_ean:
                    severity='ACTIONABLE'; cls='DIRECT_RESTORE_CANDIDATE'; reason.append('unique_exact_sku_and_ean')
                elif live_sku and live_sku in _aliases(source_sku) and not live_ean:
                    severity='MANUAL'; cls='SKU_ONLY_ACTIVE_REVIEW'; reason.append('active_offer_has_no_live_ean_or_card_identity')
                else:
                    severity='MANUAL'; cls='UNOWNED_LIVE_IDENTITY_REVIEW'; reason.append('no_owner_but_identity_not_exact')
        elif 'MASTER_FALSE_ACTIVE_220' in mismatch:
            severity='ACTIONABLE'; cls='ACTIONABLE_FALSE_ACTIVE'; reason.append('master_active_without_live_offer_or_card')
        elif 'ACTIVE_220_WITHOUT_SELLER_OFFER' in mismatch:
            severity='MANUAL'; cls='ACTIVE_CARD_ONLY_REVIEW'; reason.append('card_exists_without_seller_offer')
        elif 'MASTER_FALSE_NOT_IN_220' in mismatch:
            if _s(x.get('live_state'))=='CARD_ONLY':
                severity='EXPECTED'; cls='EXPECTED_CARD_ONLY'; reason.append('card_identity_preserved_while_status_not_in_220')
            elif _s(x.get('offer_status')).lower()!='active':
                severity='EXPECTED'; cls='EXPECTED_INACTIVE_OFFER'; reason.append('inactive_or_reset_offer')
            else:
                severity='MANUAL'; cls='ACTIVE_STATUS_REVIEW'; reason.append('active_marketplace_state_requires_context')

        rec={
            'row_no':row_no,'severity':severity,'classification':cls,'reason':'|'.join(reason),
            'mismatch_code':_s(x.get('mismatch_code')),'vendor':vendor,'title':title,
            'shopify_status':shop_status,'shopify_sku':source_sku,'shopify_barcode':source_barcode,
            'master_220_sku':master_sku,'master_220_ean':master_ean,
            'master_220_status':_s(x.get('master_220_status')),'live_state':_s(x.get('live_state')),
            'offer_status':_s(x.get('offer_status')),'live_sku':live_sku,'live_ean':live_ean,
            'owner_rows':owner_meta
        }
        out.append(rec); class_counts[cls]+=1; severity_counts[severity]+=1

    summary={
        'status':'PASS','source_v30_ts':source_ts.isoformat() if hasattr(source_ts,'isoformat') else str(source_ts),
        'residual_rows':len(out),'severity_counts':dict(severity_counts),
        'classification_counts':dict(class_counts),
        'safety':{'phh_writes':0,'master_writes':0,'shopify_writes':0}
    }
    _persist(out,summary,source_ts)
    print('PHH_RESIDUAL_SEMANTIC_AUDIT_V34_SUMMARY '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
    chunk=80; total=(len(out)+chunk-1)//chunk
    for i in range(total):
        print(f'PHH_RESIDUAL_SEMANTIC_AUDIT_V34 {i+1}/{total} '+json.dumps(out[i*chunk:(i+1)*chunk],ensure_ascii=False,separators=(',',':')),flush=True)
    return summary
