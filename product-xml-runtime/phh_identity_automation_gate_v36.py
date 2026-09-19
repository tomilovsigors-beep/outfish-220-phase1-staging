from __future__ import annotations
import json, os
from collections import Counter
from phh_master_audit_v30 import _master_rows_full, _s

def _latest_v34_v35():
    db=os.getenv('DATABASE_URL')
    if not db: raise RuntimeError('DATABASE_URL missing')
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select max(audit_ts) from phh_residual_semantic_audit_v34')
            v34_ts=cur.fetchone()[0]
            cur.execute('select max(registry_ts) from phh_hard_conflict_registry_v35')
            v35_ts=cur.fetchone()[0]
            if not v34_ts or not v35_ts: raise RuntimeError('v34/v35 snapshot missing')
            cur.execute('''
              select row_no,severity,classification
              from phh_residual_semantic_audit_v34 where audit_ts=%s
            ''',(v34_ts,))
            v34={int(r[0]):{'severity':r[1],'classification':r[2]} for r in cur.fetchall()}
            cur.execute('''
              select row_no,decision,automation_gate,rationale
              from phh_hard_conflict_registry_v35 where registry_ts=%s
            ''',(v35_ts,))
            v35={int(r[0]):{'decision':r[1],'automation_gate':r[2],'rationale':r[3]} for r in cur.fetchall()}
    return v34_ts,v35_ts,v34,v35

def _persist(rows,summary,v34_ts,v35_ts):
    db=os.getenv('DATABASE_URL')
    if not db: return
    try:
        import psycopg
        with psycopg.connect(db) as c:
            with c.cursor() as cur:
                cur.execute('''create table if not exists phh_identity_automation_gate_v36 (
                    gate_ts timestamptz not null default now(),
                    source_v34_ts timestamptz not null,
                    source_v35_ts timestamptz not null,
                    row_no integer not null,
                    identity_gate text not null,
                    residual_severity text,
                    residual_classification text,
                    decision text,
                    automation_gate text,
                    master_220_status text,
                    shopify_status text,
                    vendor text,
                    title text,
                    shopify_sku text,
                    shopify_barcode text,
                    master_220_sku text,
                    master_220_ean text,
                    primary key(gate_ts,row_no)
                )''')
                cur.execute('''create table if not exists phh_identity_automation_gate_summary_v36 (
                    gate_ts timestamptz not null default now(),
                    source_v34_ts timestamptz not null,
                    source_v35_ts timestamptz not null,
                    summary jsonb not null
                )''')
                cur.execute('insert into phh_identity_automation_gate_summary_v36(source_v34_ts,source_v35_ts,summary) values(%s,%s,%s)',
                            (v34_ts,v35_ts,json.dumps(summary)))
                vals=[(v34_ts,v35_ts,x['row_no'],x['identity_gate'],x['residual_severity'],x['residual_classification'],
                       x['decision'],x['automation_gate'],x['master_220_status'],x['shopify_status'],x['vendor'],x['title'],
                       x['shopify_sku'],x['shopify_barcode'],x['master_220_sku'],x['master_220_ean']) for x in rows]
                cur.executemany('''insert into phh_identity_automation_gate_v36(
                    source_v34_ts,source_v35_ts,row_no,identity_gate,residual_severity,residual_classification,
                    decision,automation_gate,master_220_status,shopify_status,vendor,title,shopify_sku,shopify_barcode,
                    master_220_sku,master_220_ean
                ) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',vals)
            c.commit()
    except Exception as e:
        print('PHH_IDENTITY_AUTOMATION_GATE_V36_PERSIST_FAILED',type(e).__name__,str(e),flush=True)

def run():
    v34_ts,v35_ts,v34,v35=_latest_v34_v35()
    master=_master_rows_full()
    rows=[]; counts=Counter(); blocked_active=0
    for row_no,m in enumerate(master,start=2):
        sem=v34.get(row_no); reg=v35.get(row_no)
        if reg:
            gate='BLOCKED_HUMAN_DECISION'
        elif sem and sem.get('severity')=='EXPECTED':
            gate='EXPECTED_NO_ACTION'
        elif sem:
            gate='BLOCKED_HUMAN_DECISION'
        else:
            gate='STABLE'
        if gate=='BLOCKED_HUMAN_DECISION' and _s(m.get('220_status'))=='ACTIVE_220':
            blocked_active+=1
        x={
          'row_no':row_no,'identity_gate':gate,
          'residual_severity':(sem or {}).get('severity',''),
          'residual_classification':(sem or {}).get('classification',''),
          'decision':(reg or {}).get('decision',''),
          'automation_gate':(reg or {}).get('automation_gate',''),
          'master_220_status':_s(m.get('220_status')),
          'shopify_status':_s(m.get('shopify_status')),
          'vendor':_s(m.get('vendor')),
          'title':_s(m.get('shopify_title')),
          'shopify_sku':_s(m.get('shopify_sku')),
          'shopify_barcode':_s(m.get('shopify_barcode')),
          'master_220_sku':_s(m.get('220_sku')),
          'master_220_ean':_s(m.get('220_ean'))
        }
        rows.append(x); counts[gate]+=1
    summary={
      'status':'PASS','master_rows':len(rows),'gate_counts':dict(counts),
      'blocked_active_220_rows':blocked_active,
      'source_v34_ts':v34_ts.isoformat() if hasattr(v34_ts,'isoformat') else str(v34_ts),
      'source_v35_ts':v35_ts.isoformat() if hasattr(v35_ts,'isoformat') else str(v35_ts),
      'safety':{'master_writes':0,'phh_writes':0,'shopify_writes':0}
    }
    _persist(rows,summary,v34_ts,v35_ts)
    print('PHH_IDENTITY_AUTOMATION_GATE_V36_SUMMARY '+json.dumps(summary,sort_keys=True),flush=True)
    blocked=[x for x in rows if x['identity_gate']=='BLOCKED_HUMAN_DECISION']
    print('PHH_IDENTITY_AUTOMATION_GATE_V36_BLOCKED '+json.dumps(blocked,ensure_ascii=False,separators=(',',':')),flush=True)
    return summary
