from __future__ import annotations
import json, os, threading, time, traceback
from flask import Flask, Response
from app import _master_rows, _shopify_products, _load_json
from generator import build
from content_runtime import build_snapshot, persist_snapshot

app=Flask(__name__); LOCK=threading.RLock(); STATE={'status':'starting','error':None,'last_refresh':None,'summary':{},'artifacts':{},'product_xml_validation':{},'persistence_ok':False,'persistence_error':None}
def _json(o,status=200): return Response(json.dumps(o,indent=2,sort_keys=True),status=status,mimetype='application/json')
def refresh():
    try:
        master=_master_rows(); shopify=_shopify_products(master); content_arts,summary=build_snapshot(master,shopify)
        xml_arts=build(master,shopify,_load_json('phh-category-mapping.json'),_load_json('phh-category-fields.json')); validation=json.loads(xml_arts['product-xml-validation.json'].decode())
        arts=dict(xml_arts); arts.update(content_arts); ok,perr=persist_snapshot(os.getenv('DATABASE_URL'),content_arts,summary)
        with LOCK: STATE.update(status='blocked' if validation.get('publish_gate')!='PASS' else 'ok',error=None,last_refresh=time.time(),summary=summary,artifacts=arts,product_xml_validation=validation,persistence_ok=ok,persistence_error=perr)
        print('CONTENT_SNAPSHOT_READY',json.dumps({'safe_mappings_fetched':summary.get('safe_mappings_fetched'),'dataset_hash':summary.get('dataset_hash'),'persistence_ok':ok,'persistence_error':perr},sort_keys=True),flush=True)
        return summary
    except Exception as e:
        with LOCK: STATE.update(status='degraded',error=f'{type(e).__name__}: {e}',last_refresh=time.time())
        print('CONTENT_SNAPSHOT_REFRESH_FAILED',type(e).__name__,str(e),flush=True)
        traceback.print_exc()
        raise
def _artifact(n,m):
    with LOCK: b=STATE['artifacts'].get(n); status=STATE['status']; err=STATE['error']
    if not b: return _json({'error':'snapshot unavailable','service_status':status,'detail':err},503)
    return Response(b,status=200,mimetype=m,headers={'Cache-Control':'no-store'})
@app.get('/health')
def health():
    with LOCK: x={k:v for k,v in STATE.items() if k!='artifacts'}
    x['env_status']={k:bool(os.getenv(k)) for k in ('GOOGLE_SERVICE_ACCOUNT_JSON','SHOPIFY_CLIENT_ID','SHOPIFY_CLIENT_SECRET','DATABASE_URL')}
    code=200 if x['status'] in {'ok','blocked'} and x['persistence_ok'] else 503
    return _json(x,code)
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

def _boot():
    print('CONTENT_STAGING_ENV',json.dumps({k:bool(os.getenv(k)) for k in ('GOOGLE_SERVICE_ACCOUNT_JSON','SHOPIFY_CLIENT_ID','SHOPIFY_CLIENT_SECRET','DATABASE_URL')},sort_keys=True),flush=True)
    try: refresh()
    except Exception: pass
threading.Thread(target=_boot,daemon=True).start()
