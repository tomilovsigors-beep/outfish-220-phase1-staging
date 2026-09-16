import csv, io, json, os, threading, time
import requests
from flask import Flask, Response, send_file
from runtime import refresh,current_state,current_file,recover_current_snapshot
from precutover import generate_and_persist_package,load_current_package
from price_impact_audit import run_price_impact_audit
app=Flask(__name__)

_token_lock=threading.RLock(); _token_expires_at=0.0

def ensure_shopify_access_token():
    global _token_expires_at
    client_id=os.getenv('SHOPIFY_CLIENT_ID'); client_secret=os.getenv('SHOPIFY_CLIENT_SECRET')
    if not client_id or not client_secret:
        if os.getenv('SHOPIFY_ACCESS_TOKEN'): return
        raise RuntimeError('Shopify credentials missing: set SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET')
    with _token_lock:
        if os.getenv('SHOPIFY_ACCESS_TOKEN') and time.time() < _token_expires_at-60:return
        shop=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
        r=requests.post(f'https://{shop}/admin/oauth/access_token',headers={'Content-Type':'application/x-www-form-urlencoded'},data={'grant_type':'client_credentials','client_id':client_id,'client_secret':client_secret},timeout=30)
        if not r.ok: raise RuntimeError(f'Shopify client-credentials token request failed HTTP {r.status_code}')
        payload=r.json(); token=payload.get('access_token'); expires_in=int(payload.get('expires_in') or 0)
        if not token or expires_in<=0: raise RuntimeError('Shopify client-credentials response missing access_token/expires_in')
        os.environ['SHOPIFY_ACCESS_TOKEN']=token; _token_expires_at=time.time()+expires_in

def run_refresh():
    ensure_shopify_access_token(); return refresh()

def _dataset_rows(path):
    if not path:return {}
    with open(path,'r',encoding='utf-8-sig',newline='') as f:return {r.get('220_sku',''):r for r in csv.DictReader(f)}

def _row_changes(a,b):
    out=[]
    for sku in sorted(set(a)|set(b)):
        ra=a.get(sku); rb=b.get(sku)
        if ra==rb:continue
        if ra is None or rb is None:
            out.append({'220_sku':sku,'change':'added' if rb else 'removed'}); continue
        delta={k:{'before':ra.get(k,''),'after':rb.get(k,'')} for k in sorted(set(ra)|set(rb)) if ra.get(k,'')!=rb.get(k,'')}
        out.append({'220_sku':sku,'fields':delta})
    return out

def _soak_summary(a):
    ids=a.get('source_snapshot_ids') or {}
    return {'refresh_id':a.get('refresh_id'),'master_sha256':ids.get('master_sha256'),'sia_sha256':ids.get('sia_sha256'),'shopify_semantic_sha256':ids.get('shopify_semantic_sha256'),'shopify_raw_audit_sha256':ids.get('shopify_raw_audit_sha256'),'dataset_sha256':a.get('dataset_sha256'),'blocked':a.get('blocked'),'generated_xml_rows':a.get('generated_xml_rows'),'runtime_ok':a.get('runtime_ok'),'stock_diffs':a.get('stock_diffs'),'hours_diffs':a.get('hours_diffs'),'price_diffs':a.get('price_diffs')}

def bg():
    interval=max(300,int(os.getenv('REFRESH_SECONDS','900')))
    count=max(1,int(os.getenv('STARTUP_SOAK_REFRESHES','3'))); pause=max(0,int(os.getenv('STARTUP_SOAK_PAUSE_SECONDS','10')))
    try:
        recovered=recover_current_snapshot(); print('DURABLE_RECOVERY '+json.dumps(recovered,sort_keys=True),flush=True)
    except Exception as e: print('DURABLE_RECOVERY_NONE '+repr(e),flush=True)
    previous=None; previous_rows=None
    for i in range(count):
        try:
            a=run_refresh(); rows=_dataset_rows(current_file('runtime-dataset.csv')); summary=_soak_summary(a)
            print(f'SOAK_REFRESH_{i+1} '+json.dumps(summary,sort_keys=True),flush=True)
            if previous is not None:
                pids=previous.get('source_snapshot_ids') or {}; aids=a.get('source_snapshot_ids') or {}
                comparison={'from_refresh_id':previous.get('refresh_id'),'to_refresh_id':a.get('refresh_id'),'master_hash_equal':pids.get('master_sha256')==aids.get('master_sha256'),'sia_hash_equal':pids.get('sia_sha256')==aids.get('sia_sha256'),'shopify_semantic_hash_equal':pids.get('shopify_semantic_sha256')==aids.get('shopify_semantic_sha256'),'dataset_hash_equal':previous.get('dataset_sha256')==a.get('dataset_sha256'),'blockers_equal':previous.get('blocked')==a.get('blocked'),'xml_rows_equal':previous.get('generated_xml_rows')==a.get('generated_xml_rows')}
                comparison['row_level_changes']=[] if comparison['dataset_hash_equal'] else _row_changes(previous_rows or {},rows)
                print(f'SOAK_COMPARISON_{i} '+json.dumps(comparison,sort_keys=True),flush=True)
            previous=a; previous_rows=rows
        except Exception as e: print(f'SOAK_REFRESH_{i+1}_FAILED '+repr(e),flush=True)
        if i+1<count and pause: time.sleep(pause)
    try:
        pkg=generate_and_persist_package(); print('PRECUTOVER_PACKAGE '+json.dumps(pkg,sort_keys=True),flush=True)
    except Exception as e: print('PRECUTOVER_PACKAGE_FAILED '+repr(e),flush=True)
    while True:
        time.sleep(interval)
        try: print('PERIODIC_REFRESH '+json.dumps(_soak_summary(run_refresh()),sort_keys=True),flush=True)
        except Exception as e: print('refresh failed',repr(e),flush=True)
threading.Thread(target=bg,daemon=True).start()

def j(obj,code=200):return Response(json.dumps(obj,indent=2),status=code,mimetype='application/json')
@app.get('/health')
def health():
    s=current_state(); a=s.get('audit') or {}
    return j({'service_status':s['service_status'],'stale':s['stale'],'last_successful_refresh':s['last_successful_refresh'],'last_attempt':s['last_attempt'],'error':s['error'],'persistence_status':s.get('persistence_status'),'recovered_from_durable':s.get('recovered_from_durable'),'master_identities':a.get('master_identities',0),'shopify_mapped_expected':970,'shopify_fetched':a.get('shopify_fetched',0),'sia_external_expected':701,'sia_external_fetched':a.get('sia_external_fetched',0),'runtime_ok':a.get('runtime_ok',0),'blocked':a.get('blocked',0),'shopify_price_used':False,'old_production_feed_dependency':False,'dataset_sha256':a.get('dataset_sha256'),'shopify_semantic_sha256':(a.get('source_snapshot_ids') or {}).get('shopify_semantic_sha256')},200 if s['service_status']=='ok' else 503)
@app.get('/audit')
def audit():
    s=current_state(); return j({**(s.get('audit') or {}),'service_status':s['service_status'],'stale':s['stale'],'error':s['error'],'persistence_status':s.get('persistence_status'),'recovered_from_durable':s.get('recovered_from_durable')},200 if s.get('validated_dir') else 503)
@app.get('/source-snapshot-status')
def source_status():
    s=current_state(); a=s.get('audit') or {}; return j({'service_status':s['service_status'],'stale':s['stale'],'refresh_id':a.get('refresh_id'),'source_timestamps':a.get('source_timestamps'),'source_snapshot_ids':a.get('source_snapshot_ids'),'dataset_sha256':a.get('dataset_sha256'),'persistence_status':s.get('persistence_status'),'recovered_from_durable':s.get('recovered_from_durable'),'error':s['error']},200 if s.get('validated_dir') else 503)
@app.get('/snapshot-manifest')
def manifest():
    s=current_state(); return j(s.get('manifest') or {},200 if s.get('validated_dir') else 503)
@app.get('/precutover-summary')
def precutover_summary():
    try:
        p=load_current_package(); return j({'package_id':p['package_id'],'zip_sha256':p['zip_sha256'],**p['summary']},200) if p else j({'error':'no pre-cutover package yet'},404)
    except Exception as e:return j({'error':f'{type(e).__name__}: {e}'},503)
@app.get('/precutover-package.zip')
def precutover_zip():
    try:
        p=load_current_package()
        if not p:return j({'error':'no pre-cutover package yet'},404)
        return send_file(io.BytesIO(p['zip_content']),mimetype='application/zip',as_attachment=True,download_name='precutover-comparison-package.zip',conditional=False,max_age=0)
    except Exception as e:return j({'error':f'{type(e).__name__}: {e}'},503)
@app.get('/price-impact-audit')
def price_impact_audit():
    try:
        out=run_price_impact_audit(); return j(out,200 if out.get('gate')=='PASS' else 409)
    except Exception as e:return j({'gate':'ERROR','error':f'{type(e).__name__}: {e}'},503)
def serve(name,mime):
    p=current_file(name)
    if not p:return j({'error':'no validated durable snapshot available','service_status':current_state()['service_status']},503)
    return send_file(p,mimetype=mime,conditional=False,max_age=0)
@app.get('/220-stock-master-driven.xml')
def xml():return serve('220-stock-master-driven.xml','application/xml')
@app.get('/runtime-dataset.csv')
def dataset():return serve('runtime-dataset.csv','text/csv')
@app.get('/blockers.csv')
def blockers():return serve('blockers.csv','text/csv')
@app.get('/production-equivalent-comparison.csv')
def comp():return serve('production-equivalent-comparison.csv','text/csv')
@app.post('/refresh')
def manual_refresh():
    try:return j(run_refresh())
    except Exception:return health()
