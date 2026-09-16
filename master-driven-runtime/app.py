import csv, io, json, os, threading, time
import requests
from flask import Flask, Response, send_file
import compact_loader
import runtime as runtime_module
from runtime import refresh,current_state,current_file
app=Flask(__name__)

# SIA FHM currently has legacy header whitespace (for example "sku ").
# Normalize only the staging read layer; source data is not modified.
_original_fetch_sia=runtime_module.fetch_sia
def normalized_fetch_sia():
    raw, rows=_original_fetch_sia()
    return raw,[{str(k).strip():v for k,v in r.items()} for r in rows]
runtime_module.fetch_sia=normalized_fetch_sia

_token_lock=threading.RLock()
_token_expires_at=0.0

def ensure_shopify_access_token():
    """Populate SHOPIFY_ACCESS_TOKEN from staging client credentials without exposing secrets."""
    global _token_expires_at
    client_id=os.getenv('SHOPIFY_CLIENT_ID')
    client_secret=os.getenv('SHOPIFY_CLIENT_SECRET')
    if not client_id or not client_secret:
        if os.getenv('SHOPIFY_ACCESS_TOKEN'):
            return
        raise RuntimeError('Shopify credentials missing: set SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET')
    with _token_lock:
        if os.getenv('SHOPIFY_ACCESS_TOKEN') and time.time() < _token_expires_at-60:
            return
        shop=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
        if not shop:
            raise RuntimeError('SHOPIFY_SHOP_DOMAIN missing')
        r=requests.post(
            f'https://{shop}/admin/oauth/access_token',
            headers={'Content-Type':'application/x-www-form-urlencoded'},
            data={'grant_type':'client_credentials','client_id':client_id,'client_secret':client_secret},
            timeout=30,
        )
        if not r.ok:
            raise RuntimeError(f'Shopify client-credentials token request failed HTTP {r.status_code}')
        payload=r.json()
        token=payload.get('access_token')
        expires_in=int(payload.get('expires_in') or 0)
        if not token or expires_in<=0:
            raise RuntimeError('Shopify client-credentials response missing access_token/expires_in')
        os.environ['SHOPIFY_ACCESS_TOKEN']=token
        _token_expires_at=time.time()+expires_in

def run_refresh():
    ensure_shopify_access_token()
    return refresh()

def _dataset_rows(path):
    if not path:return {}
    with open(path,'r',encoding='utf-8-sig',newline='') as f:
        return {r.get('220_sku',''):r for r in csv.DictReader(f)}

def _row_changes(a,b):
    keys=sorted(set(a)|set(b)); out=[]
    for sku in keys:
        ra=a.get(sku); rb=b.get(sku)
        if ra==rb: continue
        if ra is None or rb is None:
            out.append({'220_sku':sku,'change':'added' if rb else 'removed'})
            continue
        fields=sorted(set(ra)|set(rb)); delta={k:{'before':ra.get(k,''),'after':rb.get(k,'')} for k in fields if ra.get(k,'')!=rb.get(k,'')}
        out.append({'220_sku':sku,'fields':delta})
    return out

def bg():
    interval=max(300,int(os.getenv('REFRESH_SECONDS','900')))
    try:
        a1=run_refresh(); p1=current_file('runtime-dataset.csv'); rows1=_dataset_rows(p1)
        print('VALIDATION_REFRESH_1 '+json.dumps(a1,sort_keys=True),flush=True)
        a2=run_refresh(); p2=current_file('runtime-dataset.csv'); rows2=_dataset_rows(p2)
        print('VALIDATION_REFRESH_2 '+json.dumps(a2,sort_keys=True),flush=True)
        comparison={'dataset_hash_equal':a1.get('dataset_sha256')==a2.get('dataset_sha256'),'dataset_sha256_1':a1.get('dataset_sha256'),'dataset_sha256_2':a2.get('dataset_sha256'),'source_snapshot_ids_1':a1.get('source_snapshot_ids'),'source_snapshot_ids_2':a2.get('source_snapshot_ids')}
        if not comparison['dataset_hash_equal']:
            comparison['row_level_changes']=_row_changes(rows1,rows2)
        else:
            comparison['row_level_changes']=[]
        print('VALIDATION_REFRESH_COMPARISON '+json.dumps(comparison,sort_keys=True),flush=True)
    except Exception as e:
        print('VALIDATION_REFRESH_FAILED '+repr(e),flush=True)
    while True:
        time.sleep(interval)
        try:
            a=run_refresh(); print('PERIODIC_REFRESH '+json.dumps(a,sort_keys=True),flush=True)
        except Exception as e: print('refresh failed',repr(e),flush=True)
threading.Thread(target=bg,daemon=True).start()

def j(obj,code=200):return Response(json.dumps(obj,indent=2),status=code,mimetype='application/json')
@app.get('/health')
def health():
    s=current_state(); a=s.get('audit') or {}
    return j({'service_status':s['service_status'],'stale':s['stale'],'last_successful_refresh':s['last_successful_refresh'],'last_attempt':s['last_attempt'],'error':s['error'],'master_identities':a.get('master_identities',0),'shopify_mapped_expected':970,'shopify_fetched':a.get('shopify_fetched',0),'sia_external_expected':701,'sia_external_fetched':a.get('sia_external_fetched',0),'runtime_ok':a.get('runtime_ok',0),'blocked':a.get('blocked',0),'shopify_price_used':False,'old_production_feed_dependency':False,'dataset_sha256':a.get('dataset_sha256')},200 if s['service_status']=='ok' else 503)
@app.get('/audit')
def audit():
    s=current_state(); return j({**(s.get('audit') or {}),'service_status':s['service_status'],'stale':s['stale'],'error':s['error']},200 if s.get('validated_dir') else 503)
@app.get('/source-snapshot-status')
def source_status():
    s=current_state(); a=s.get('audit') or {}; return j({'service_status':s['service_status'],'stale':s['stale'],'refresh_id':a.get('refresh_id'),'source_timestamps':a.get('source_timestamps'),'source_snapshot_ids':a.get('source_snapshot_ids'),'validated_dir':s.get('validated_dir'),'error':s['error']},200 if s.get('validated_dir') else 503)
def serve(name,mime):
    p=current_file(name)
    if not p:return j({'error':'no validated snapshot available','service_status':current_state()['service_status']},503)
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
