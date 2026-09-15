import json, os, threading, time
from flask import Flask, Response, send_file
import compact_loader
from runtime import refresh,current_state,current_file
app=Flask(__name__)

def bg():
    interval=max(300,int(os.getenv('REFRESH_SECONDS','900')))
    while True:
        try: refresh()
        except Exception as e: print('refresh failed',repr(e),flush=True)
        time.sleep(interval)
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
    try:return j(refresh())
    except Exception:return health()
