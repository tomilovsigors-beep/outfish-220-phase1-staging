from __future__ import annotations
import csv, hashlib, io, json, os
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

MASTER_ID='1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I'
SHEET_NAME='MASTER'
EXPECTED_PREWRITE_HASH='7578c819b95d4069b62ea024a1c415642a7d59331050431f3217b8786ecc93b0'
EXPECTED_COUNT=663
DUPLICATE_MARKER='DUPLICATE_MASTER_220_SKU_CONFLICT'
PROTECTED={'shopify_product_id','shopify_variant_id','shopify_sku','shopify_barcode','220_sku','220_ean','shopify_title','variant_title','vendor','product_type','shopify_status','shopify_price_reference','shopify_main_image_url','220_main_image_url','220_main_image_status','220_status','220_synced','220_product_xml_enabled','220_stock_feed_enabled','220_price_before_discount','220_price_after_discount','price_override_reason','match_status','validation_status','shopify_updated_at','last_sync','notes','220_category_id','220_category_name','220_properties_json','220_length_m','220_height_m','220_width_m','220_manufacturer_code'}
PRICE_STOCK={'220_status','220_synced','220_product_xml_enabled','220_stock_feed_enabled','220_price_before_discount','220_price_after_discount','price_override_reason','shopify_price_reference','shopify_status','match_status','validation_status'}

def _sha(b): return hashlib.sha256(b).hexdigest()
def _jb(o): return json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
def _csv(rows,fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields); w.writeheader(); w.writerows(rows); return s.getvalue().encode('utf-8-sig')
def _session():
    raw=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not raw: raise RuntimeError('GOOGLE_SERVICE_ACCOUNT_JSON missing')
    creds=service_account.Credentials.from_service_account_info(json.loads(raw),scopes=['https://www.googleapis.com/auth/spreadsheets'])
    return AuthorizedSession(creds)
def _get(sess):
    url=f'https://sheets.googleapis.com/v4/spreadsheets/{MASTER_ID}/values/{SHEET_NAME}!A1:ZZ5000'
    r=sess.get(url,params={'majorDimension':'ROWS','valueRenderOption':'UNFORMATTED_VALUE'},timeout=60)
    if not r.ok: raise RuntimeError(f'Sheets read failed {r.status_code}: {(r.text or "")[:1000]}')
    return r.json().get('values') or []
def _rv(row,i):
    if i>=len(row) or row[i] is None: return ''
    v=row[i]
    if isinstance(v,bool): return 'TRUE' if v else 'FALSE'
    return str(v)
def _normalized(values,width): return [list(r)+['']*(width-len(r)) for r in values]
def _hash_normalized(values,width): return _sha(_jb(_normalized(values,width)))
def _col(n):
    s=''
    while n:
        n,r=divmod(n-1,26); s=chr(65+r)+s
    return s
def _persist(db,report,arts):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('create table if not exists product_xml_title_activation_runs (run_hash text primary key, created_at timestamptz not null default now(), status text not null, report jsonb not null, artifacts jsonb not null)')
            payload={k:v.decode('utf-8',errors='replace') for k,v in arts.items()}
            cur.execute('insert into product_xml_title_activation_runs(run_hash,status,report,artifacts) values(%s,%s,%s::jsonb,%s::jsonb) on conflict(run_hash) do update set status=excluded.status,report=excluded.report,artifacts=excluded.artifacts,created_at=now()', (report['run_hash'],report['status'],json.dumps(report),json.dumps(payload)))
        c.commit()

def run_title_activation():
    sess=_session(); before=_get(sess)
    if not before: raise RuntimeError('Master empty')
    headers=[str(x).strip() for x in before[0]]; width=len(headers)
    need={'220_sku','220_ean','220_title','220_title_candidate','220_title_candidate_status','match_status','220_status'}
    if not need.issubset(headers): raise RuntimeError('required title activation columns missing')
    h={x:i for i,x in enumerate(headers)}
    current_hash=_hash_normalized(before,width)
    if current_hash!=EXPECTED_PREWRITE_HASH: raise RuntimeError(f'Master hash mismatch {current_hash} != {EXPECTED_PREWRITE_HASH}')
    bn=_normalized(before,width); row_count=len(bn)
    identity_before=[(_rv(bn[r],h['220_sku']),_rv(bn[r],h['220_ean'])) for r in range(1,row_count)]
    protected={x:[_rv(bn[r],h[x]) for r in range(row_count)] for x in headers if x in PROTECTED}
    blocked_rows=[r+1 for r in range(1,row_count) if _rv(bn[r],h['match_status'])==DUPLICATE_MARKER]
    blocked_before={rn:list(bn[rn-1]) for rn in blocked_rows}
    auto=[]; review=0; nonempty_conflicts=[]
    for ri in range(1,row_count):
        rn=ri+1; row=bn[ri]
        status=_rv(row,h['220_title_candidate_status']).strip(); title=_rv(row,h['220_title']).strip(); cand=_rv(row,h['220_title_candidate']).strip(); match=_rv(row,h['match_status']).strip(); life=_rv(row,h['220_status']).strip()
        if status=='REVIEW_REQUIRED': review+=1
        if status!='AUTO_APPROVABLE': continue
        if not cand: raise RuntimeError(f'empty candidate at row {rn}')
        if title:
            nonempty_conflicts.append({'row':rn,'220_sku':_rv(row,h['220_sku']),'220_ean':_rv(row,h['220_ean']),'existing_220_title':title,'candidate':cand}); continue
        if match==DUPLICATE_MARKER or life=='BLOCKED': raise RuntimeError(f'blocked duplicate selected row {rn}')
        auto.append({'row':rn,'220_sku':_rv(row,h['220_sku']),'220_ean':_rv(row,h['220_ean']),'before':'','after':cand})
    if len(auto)!=EXPECTED_COUNT or nonempty_conflicts: raise RuntimeError(f'title preflight count={len(auto)} conflicts={len(nonempty_conflicts)} expected={EXPECTED_COUNT}')
    if len({(x['220_sku'],x['220_ean']) for x in auto})!=EXPECTED_COUNT: raise RuntimeError('title target identities not unique')
    dry_hash=_sha(_csv(auto,['row','220_sku','220_ean','before','after']))
    run_hash=_sha((EXPECTED_PREWRITE_HASH+dry_hash).encode())
    pre=_get(sess)
    if _hash_normalized(pre,width)!=EXPECTED_PREWRITE_HASH: raise RuntimeError('Master changed between title preflight and write')
    title_col=_col(h['220_title']+1)
    data=[{'range':f'{SHEET_NAME}!{title_col}{x["row"]}','values':[[x['after']]]} for x in auto]
    url=f'https://sheets.googleapis.com/v4/spreadsheets/{MASTER_ID}/values:batchUpdate'
    r=sess.post(url,json={'valueInputOption':'RAW','data':data},timeout=180)
    if not r.ok: raise RuntimeError(f'title batch write failed {r.status_code}: {(r.text or "")[:2000]}')
    after=_get(sess); an=_normalized(after,width)
    failures=[]
    for x in auto:
        got=_rv(an[x['row']-1],h['220_title'])
        if got!=x['after']: failures.append({**x,'readback':got})
    identity_after=[(_rv(an[r],h['220_sku']),_rv(an[r],h['220_ean'])) for r in range(1,len(an))]
    protected_changed=[]
    for col,vals0 in protected.items():
        vals1=[_rv(an[r],h[col]) for r in range(len(an))]
        if vals1!=vals0: protected_changed.append(col)
    blocked_touched=[rn for rn,brow in blocked_before.items() if rn>len(an) or list(an[rn-1])!=brow]
    verified=(not failures and identity_after==identity_before and not protected_changed and not blocked_touched and len(an)==row_count)
    post_hash=_sha(_jb(an))
    report={'status':'PASS' if verified else 'FAIL','run_hash':run_hash,'master_id':MASTER_ID,'prewrite_master_hash':EXPECTED_PREWRITE_HASH,'postwrite_master_hash':post_hash,'title_expected':EXPECTED_COUNT,'title_written':EXPECTED_COUNT-len(failures),'title_verified':EXPECTED_COUNT-len(failures),'title_review_remaining':review,'readback_failures':len(failures),'blocked_duplicate_rows_touched':len(blocked_touched),'protected_fields_changed':len(protected_changed),'protected_fields_changed_names':protected_changed,'identity_fields_changed':0 if identity_after==identity_before else 1,'stock_price_fields_changed':len([x for x in protected_changed if x in PRICE_STOCK]),'row_count_before':row_count,'row_count_after':len(an),'dry_run_hash':dry_hash}
    arts={'title-activation-dry-run.csv':_csv(auto,['row','220_sku','220_ean','before','after']),'title-activation-rollback.csv':_csv([{'row':x['row'],'220_sku':x['220_sku'],'220_ean':x['220_ean'],'field':'220_title','before':''} for x in auto],['row','220_sku','220_ean','field','before']),'title-activation-failures.csv':_csv(failures,['row','220_sku','220_ean','before','after','readback']) if failures else b'row,220_sku,220_ean,before,after,readback\r\n','title-activation-report.json':_jb(report)}
    _persist(os.getenv('DATABASE_URL'),report,arts)
    return report,arts
