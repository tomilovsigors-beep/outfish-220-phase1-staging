from __future__ import annotations
import csv, io, json, os, threading, time, traceback
from flask import Flask, Response
from app import _master_rows, _shopify_products, _load_json
from generator import build
from content_runtime import build_snapshot, persist_snapshot

app=Flask(__name__); LOCK=threading.RLock(); STATE={'status':'starting','error':None,'last_refresh':None,'summary':{},'artifacts':{},'product_xml_validation':{},'persistence_ok':False,'persistence_error':None,'recovered_from_postgres':False}
def _json(o,status=200): return Response(json.dumps(o,indent=2,sort_keys=True),status=status,mimetype='application/json')
def refresh():
    try:
        master=_master_rows(); shopify=_shopify_products(master); content_arts,summary=build_snapshot(master,shopify)
        xml_arts=build(master,shopify,_load_json('phh-category-mapping.json'),_load_json('phh-category-fields.json')); validation=json.loads(xml_arts['product-xml-validation.json'].decode())
        arts=dict(xml_arts); arts.update(content_arts); ok,perr=persist_snapshot(os.getenv('DATABASE_URL'),content_arts,summary)
        with LOCK: STATE.update(status='blocked' if validation.get('publish_gate')!='PASS' else 'ok',error=None,last_refresh=time.time(),summary=summary,artifacts=arts,product_xml_validation=validation,persistence_ok=ok,persistence_error=perr,recovered_from_postgres=False)
        print('CONTENT_SNAPSHOT_READY',json.dumps({'safe_mappings_fetched':summary.get('safe_mappings_fetched'),'dataset_hash':summary.get('dataset_hash'),'persistence_ok':ok,'persistence_error':perr},sort_keys=True),flush=True); return summary
    except Exception as e:
        with LOCK: STATE.update(status='degraded',error=f'{type(e).__name__}: {e}',last_refresh=time.time())
        print('CONTENT_SNAPSHOT_REFRESH_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc(); raise

def _persist_normalized_proposal(db,dataset_hash,rows):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('create table if not exists product_xml_master_proposal_rows (dataset_hash text not null, row_no integer not null, sku text not null, ean text not null, field_name text not null, field_value text, source text, write_class text, primary key(dataset_hash,row_no))')
            cur.execute('delete from product_xml_master_proposal_rows where dataset_hash=%s',(dataset_hash,))
            cur.executemany('insert into product_xml_master_proposal_rows(dataset_hash,row_no,sku,ean,field_name,field_value,source,write_class) values(%s,%s,%s,%s,%s,%s,%s,%s)',[(dataset_hash,i+1,r.get('220_sku',''),r.get('220_ean',''),r.get('field',''),r.get('value',''),r.get('source',''),r.get('write_class','')) for i,r in enumerate(rows)])
        c.commit()

def _restore_latest():
    db=os.getenv('DATABASE_URL')
    if not db: return False
    try:
        import psycopg
        with psycopg.connect(db) as c:
            with c.cursor() as cur:
                cur.execute("select to_regclass('public.product_xml_content_snapshots')")
                if cur.fetchone()[0] is None: return False
                cur.execute('select dataset_hash,summary,artifacts,created_at from product_xml_content_snapshots order by created_at desc limit 1')
                row=cur.fetchone()
                if not row: return False
        dataset_hash,summary,payload,created_at=row
        if not isinstance(summary,dict): summary=json.loads(summary)
        if not isinstance(payload,dict): payload=json.loads(payload)
        arts={k:(v.encode('utf-8') if isinstance(v,str) else bytes(v)) for k,v in payload.items()}
        if summary.get('dataset_hash')!=dataset_hash: raise RuntimeError('persisted dataset hash mismatch')
        image_rows=list(csv.DictReader(io.StringIO(arts['image-audit.csv'].decode('utf-8-sig')))); proposal_rows=list(csv.DictReader(io.StringIO(arts['master-bulk-write-proposal.csv'].decode('utf-8-sig'))))
        _persist_normalized_proposal(db,dataset_hash,proposal_rows)
        diag=dict(summary); diag.update({'images_https_pass':sum(r.get('https_pass')=='YES' for r in image_rows),'images_direct_pass':sum(r.get('direct_pass')=='YES' for r in image_rows),'images_lt1000':sum((float(r.get('width') or 0)<1000 or float(r.get('height') or 0)<1000) for r in image_rows),'bulk_write_rows':len(proposal_rows),'bulk_write_cells':len(proposal_rows)})
        with LOCK: STATE.update(status='blocked',error=None,last_refresh=created_at.timestamp() if hasattr(created_at,'timestamp') else time.time(),summary=diag,artifacts=arts,product_xml_validation={},persistence_ok=True,persistence_error=None,recovered_from_postgres=True)
        print('CONTENT_SNAPSHOT_RECOVERED',json.dumps(diag,sort_keys=True),flush=True); print('MASTER_PROPOSAL_ROWS_PERSISTED',json.dumps({'dataset_hash':dataset_hash,'rows':len(proposal_rows)},sort_keys=True),flush=True); return True
    except Exception as e:
        print('CONTENT_SNAPSHOT_RECOVERY_FAILED',type(e).__name__,str(e),flush=True); return False

def _artifact(n,m):
    with LOCK: b=STATE['artifacts'].get(n); status=STATE['status']; err=STATE['error']
    if not b: return _json({'error':'snapshot unavailable','service_status':status,'detail':err},503)
    return Response(b,status=200,mimetype=m,headers={'Cache-Control':'no-store'})
@app.get('/health')
def health():
    with LOCK: x={k:v for k,v in STATE.items() if k!='artifacts'}
    x['env_status']={k:bool(os.getenv(k)) for k in ('GOOGLE_SERVICE_ACCOUNT_JSON','SHOPIFY_CLIENT_ID','SHOPIFY_CLIENT_SECRET','DATABASE_URL')}
    return _json(x,200 if x['status'] in {'ok','blocked'} and x['persistence_ok'] else 503)
@app.post('/refresh')
def refresh_ep():
    try: return _json(refresh())
    except Exception: return health()
@app.get('/content-summary.json')
def summary(): return _artifact('content-summary.json','application/json')
@app.get('/content-dataset.csv')
def dataset(): return _artifact('content-dataset.csv','text/csv')
@app.get('/image-audit.csv')
def image_audit(): return _artifact('image-audit.csv','text/csv')
@app.get('/weight-audit.csv')
def weight_audit(): return _artifact('weight-audit.csv','text/csv')
@app.get('/grouping-audit.csv')
def grouping_audit(): return _artifact('grouping-audit.csv','text/csv')
@app.get('/title-qa.csv')
def title_qa(): return _artifact('title-qa.csv','text/csv')
@app.get('/content-blockers.csv')
def blockers(): return _artifact('content-blockers.csv','text/csv')
@app.get('/master-bulk-write-proposal.csv')
def proposal(): return _artifact('master-bulk-write-proposal.csv','text/csv')
@app.get('/product-xml-validation.json')
def validation(): return _artifact('product-xml-validation.json','application/json')
@app.get('/product-xml-readiness.csv')
def readiness(): return _artifact('product-xml-readiness.csv','text/csv')
@app.get('/product-xml-blockers.csv')
def xml_blockers(): return _artifact('product-xml-blockers.csv','text/csv')
@app.get('/product-xml-dry-run.xml')
def xml(): return _artifact('product-xml-dry-run.xml','application/xml')

def _selftest_recovered_routes():
    paths=['/health','/content-summary.json','/content-dataset.csv','/image-audit.csv','/weight-audit.csv','/grouping-audit.csv','/title-qa.csv','/master-bulk-write-proposal.csv']
    with app.test_client() as c: statuses={p:c.get(p).status_code for p in paths}
    print('CONTENT_RECOVERY_ENDPOINT_SELFTEST',json.dumps({'statuses':statuses,'pass':all(v==200 for v in statuses.values()),'dataset_hash':STATE['summary'].get('dataset_hash')},sort_keys=True),flush=True)

def _boot():
    print('CONTENT_STAGING_ENV',json.dumps({k:bool(os.getenv(k)) for k in ('GOOGLE_SERVICE_ACCOUNT_JSON','SHOPIFY_CLIENT_ID','SHOPIFY_CLIENT_SECRET','DATABASE_URL')},sort_keys=True),flush=True)
    if _restore_latest(): _selftest_recovered_routes(); return
    try: refresh()
    except Exception: pass
threading.Thread(target=_boot,daemon=True).start()
