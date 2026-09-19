from __future__ import annotations
import json, os
from collections import Counter

def _latest_v34():
    db=os.getenv('DATABASE_URL')
    if not db: raise RuntimeError('DATABASE_URL missing')
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select max(audit_ts) from phh_residual_semantic_audit_v34')
            ts=cur.fetchone()[0]
            if not ts: raise RuntimeError('no v34 snapshot')
            cur.execute('''
              select row_no,severity,classification,reason,mismatch_code,vendor,title,shopify_status,
                     shopify_sku,shopify_barcode,master_220_sku,master_220_ean,master_220_status,
                     live_state,offer_status,live_sku,live_ean,owner_rows
              from phh_residual_semantic_audit_v34
              where audit_ts=%s and severity in ('HARD_CONFLICT','MANUAL')
              order by row_no
            ''',(ts,))
            cols=[d.name for d in cur.description]
            rows=[dict(zip(cols,r)) for r in cur.fetchall()]
    return ts,rows

def _decision(x):
    row=int(x['row_no']); cls=x['classification']
    if row==47:
        return ('KEEP_EXISTING_OWNER','BLOCK_SOURCE_BARCODE_COLLISION',
                'live SKU belongs to a different current FHM product; do not attach identity to Gale jacket row')
    if row==60:
        return ('KEEP_EXISTING_OWNER','BLOCK_SOURCE_BARCODE_COLLISION',
                'live SKU belongs to Cap Flexfit owner; FHM rain-pants row shares barcode/EAN only')
    if row==108:
        return ('KEEP_DISTINCT_ROWS','BLOCK_EAN_TRANSFER',
                'live EAN is already owned by row 109; no automatic EAN reassignment')
    if row in (758,1601):
        return ('MANUAL_CANONICAL_OWNER_SELECTION','BLOCK_SHARED_IDENTITY',
                'two active Shopify products share the same live LOWA identity; select canonical owner outside automation')
    if row==1489:
        return ('KEEP_EXISTING_OWNER','BLOCK_VARIANT_MAPPING_CONFLICT',
                'live SKU belongs to sibling variant row 1487 while row 1489 carries the live EAN as source barcode')
    if row==1123:
        return ('KEEP_MANUAL_EXCEPTION','BLOCK_AUTO_NORMALIZATION',
                'documented LOWA card-only/leading-zero special case')
    if row in (553,554,555):
        return ('POLICY_REQUIRED','BLOCK_SKU_ONLY_OFFER',
                'active PHH offer is SKU-only with no confirmed EAN/card; do not create a new ACTIVE-without-EAN pattern automatically')
    if row==1679:
        return ('MANUAL_PRODUCT_LINEAGE','BLOCK_CROSS_PRODUCT_IDENTITY',
                'SIGG source SKU/title differ from live SKU; barcode match alone is insufficient')
    if row in (3949,4016,4018,4046):
        return ('MANUAL_MULTI_EAN_LINEAGE','BLOCK_PRIMARY_EAN_REWRITE',
                'legacy identity-only row has different live primary EAN; require card/lineage evidence before changing F')
    if cls=='CROSS_SKU_LEGACY_OWNER_CONFLICT':
        return ('MANUAL_PRODUCT_LINEAGE','BLOCK_CROSS_SKU_TRANSFER',
                'current source SKU differs from live SKU and a legacy owner already exists')
    if cls=='HARD_DUPLICATE_ACTIVE_OWNER':
        return ('KEEP_EXISTING_OWNER','BLOCK_DUPLICATE_OWNER',
                'live identity already has another active owner')
    if cls=='HARD_CROSS_ROW_EAN_CONFLICT':
        return ('KEEP_DISTINCT_ROWS','BLOCK_EAN_TRANSFER',
                'live EAN is present on another Master row')
    if cls=='UNOWNED_LIVE_IDENTITY_REVIEW':
        return ('MANUAL_PRODUCT_LINEAGE','BLOCK_UNOWNED_NONEXACT',
                'live identity has no owner but does not match current source identity exactly')
    return ('MANUAL_REVIEW','BLOCK_AUTOMATION','no deterministic safe decision rule')

def _persist(rows,summary,source_ts):
    db=os.getenv('DATABASE_URL')
    if not db: return
    try:
        import psycopg
        with psycopg.connect(db) as c:
            with c.cursor() as cur:
                cur.execute('''create table if not exists phh_hard_conflict_registry_v35 (
                    registry_ts timestamptz not null default now(),
                    source_v34_ts timestamptz not null,
                    row_no integer not null,
                    severity text not null,
                    classification text not null,
                    decision text not null,
                    automation_gate text not null,
                    rationale text,
                    vendor text,title text,shopify_status text,shopify_sku text,shopify_barcode text,
                    master_220_sku text,master_220_ean text,master_220_status text,
                    live_state text,offer_status text,live_sku text,live_ean text,owner_rows jsonb,
                    primary key(registry_ts,row_no)
                )''')
                cur.execute('''create table if not exists phh_hard_conflict_registry_summary_v35 (
                    registry_ts timestamptz not null default now(),
                    source_v34_ts timestamptz not null,
                    summary jsonb not null
                )''')
                cur.execute('insert into phh_hard_conflict_registry_summary_v35(source_v34_ts,summary) values(%s,%s)',
                            (source_ts,json.dumps(summary)))
                vals=[]
                for x in rows:
                    vals.append((source_ts,x['row_no'],x['severity'],x['classification'],x['decision'],x['automation_gate'],
                                 x['rationale'],x['vendor'],x['title'],x['shopify_status'],x['shopify_sku'],x['shopify_barcode'],
                                 x['master_220_sku'],x['master_220_ean'],x['master_220_status'],x['live_state'],x['offer_status'],
                                 x['live_sku'],x['live_ean'],json.dumps(x.get('owner_rows') or [])))
                cur.executemany('''insert into phh_hard_conflict_registry_v35(
                    source_v34_ts,row_no,severity,classification,decision,automation_gate,rationale,vendor,title,shopify_status,
                    shopify_sku,shopify_barcode,master_220_sku,master_220_ean,master_220_status,live_state,offer_status,
                    live_sku,live_ean,owner_rows
                ) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)''',vals)
            c.commit()
    except Exception as e:
        print('PHH_HARD_CONFLICT_REGISTRY_V35_PERSIST_FAILED',type(e).__name__,str(e),flush=True)

def run():
    source_ts,rows=_latest_v34()
    out=[]; decisions=Counter(); gates=Counter(); severities=Counter()
    for x in rows:
        decision,gate,rationale=_decision(x)
        y=dict(x); y['decision']=decision; y['automation_gate']=gate; y['rationale']=rationale
        out.append(y); decisions[decision]+=1; gates[gate]+=1; severities[x['severity']]+=1
    summary={
        'status':'PASS',
        'source_v34_ts':source_ts.isoformat() if hasattr(source_ts,'isoformat') else str(source_ts),
        'registry_rows':len(out),
        'severity_counts':dict(severities),
        'decision_counts':dict(decisions),
        'automation_gate_counts':dict(gates),
        'auto_write_candidates':0,
        'safety':{'phh_writes':0,'master_writes':0,'shopify_writes':0}
    }
    _persist(out,summary,source_ts)
    print('PHH_HARD_CONFLICT_REGISTRY_V35_SUMMARY '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
    chunk=50; total=(len(out)+chunk-1)//chunk
    for i in range(total):
        print(f'PHH_HARD_CONFLICT_REGISTRY_V35 {i+1}/{total} '+json.dumps(out[i*chunk:(i+1)*chunk],ensure_ascii=False,separators=(',',':')),flush=True)
    return summary
