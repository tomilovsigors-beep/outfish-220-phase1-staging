from __future__ import annotations
import csv, io, json, os, threading, time, traceback, hashlib
from flask import Flask, Response, request
from app import _master_rows, _shopify_products, _load_json
from generator import build
from content_runtime import build_snapshot, persist_snapshot
from master_bulk_write import run_controlled_master_write, EXPECTED_DATASET_HASH

app=Flask(__name__); LOCK=threading.RLock(); STATE={'status':'starting','error':None,'last_refresh':None,'summary':{},'artifacts':{},'product_xml_validation':{},'persistence_ok':False,'persistence_error':None,'recovered_from_postgres':False,'master_write':None}
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

def _persisted_mapping_artifact(name,mime):
    try:
        from current_product_category_audit_v4 import load_latest_artifact
        b=load_latest_artifact(os.getenv('DATABASE_URL'),name)
        if not b: return _json({'error':'product category audit artifact unavailable'},503)
        return Response(b,status=200,mimetype=mime,headers={'Cache-Control':'no-store'})
    except Exception as e:
        return _json({'error':'product category audit artifact unavailable','detail':f'{type(e).__name__}: {e}'},503)

def _persisted_v5_artifact(name,mime):
    try:
        from current_product_category_audit_v5 import load_latest_artifact
        b=load_latest_artifact(os.getenv('DATABASE_URL'),name)
        if not b: return _json({'error':'v5 family category audit artifact unavailable'},503)
        return Response(b,status=200,mimetype=mime,headers={'Cache-Control':'no-store'})
    except Exception as e:
        return _json({'error':'v5 family category audit artifact unavailable','detail':f'{type(e).__name__}: {e}'},503)

def _persisted_v6_artifact(name,mime):
    try:
        from current_product_category_audit_v6 import load_latest_artifact
        b=load_latest_artifact(os.getenv('DATABASE_URL'),name)
        if not b: return _json({'error':'v6 category rule audit artifact unavailable'},503)
        return Response(b,status=200,mimetype=mime,headers={'Cache-Control':'no-store'})
    except Exception as e:
        return _json({'error':'v6 category rule audit artifact unavailable','detail':f'{type(e).__name__}: {e}'},503)

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
@app.get('/master-bulk-write-proposal.json')
def proposal_json():
    try: offset=max(0,int(request.args.get('offset','0'))); limit=max(1,min(500,int(request.args.get('limit','500'))))
    except Exception: return _json({'error':'invalid pagination'},400)
    with LOCK: b=STATE['artifacts'].get('master-bulk-write-proposal.csv'); ds=STATE['summary'].get('dataset_hash')
    if not b: return _json({'error':'proposal unavailable'},503)
    rows=list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))
    return _json({'dataset_hash':ds,'proposal_sha256':hashlib.sha256(b).hexdigest(),'total':len(rows),'offset':offset,'limit':limit,'rows':rows[offset:offset+limit]})
@app.get('/master-bulk-write-report.json')
def write_report(): return _artifact('master-bulk-write-report.json','application/json')
@app.get('/master-bulk-write-dry-run.csv')
def write_dry(): return _artifact('master-bulk-write-dry-run.csv','text/csv')
@app.get('/master-bulk-write-rollback.csv')
def write_rollback(): return _artifact('master-bulk-write-rollback.csv','text/csv')
@app.get('/master-bulk-write-failures.csv')
def write_failures(): return _artifact('master-bulk-write-failures.csv','text/csv')
@app.get('/product-xml-validation.json')
def validation(): return _artifact('product-xml-validation.json','application/json')
@app.get('/product-xml-readiness.csv')
def readiness(): return _artifact('product-xml-readiness.csv','text/csv')
@app.get('/product-xml-blockers.csv')
def xml_blockers(): return _artifact('product-xml-blockers.csv','text/csv')
@app.get('/product-xml-dry-run.xml')
def xml(): return _artifact('product-xml-dry-run.xml','application/xml')
@app.get('/pmp-api-discovery.json')
def pmp_api_discovery():
    try:
        from pmp_api_probe import discover
        return _json(discover())
    except Exception as e:
        return _json({'status':'ERROR','error':f'{type(e).__name__}: {e}'},503)
@app.get('/current-product-category-summary.json')
def current_category_summary(): return _persisted_mapping_artifact('current-product-category-summary.json','application/json')
@app.get('/current-product-category-mapping.csv')
def current_category_mapping(): return _persisted_mapping_artifact('current-product-category-mapping.csv','text/csv')
@app.get('/current-product-category-exceptions.csv')
def current_category_exceptions(): return _persisted_mapping_artifact('current-product-category-exceptions.csv','text/csv')
@app.get('/current-product-attribute-gap.csv')
def current_attribute_gap(): return _persisted_mapping_artifact('current-product-attribute-gap.csv','text/csv')
@app.get('/family_category_registry.csv')
def family_category_registry(): return _persisted_v5_artifact('family_category_registry.csv','text/csv')
@app.get('/family_category_review_queue.csv')
def family_category_review_queue(): return _persisted_v5_artifact('family_category_review_queue.csv','text/csv')
@app.get('/v5-product-category-mapping.csv')
def v5_product_category_mapping(): return _persisted_v5_artifact('v5-product-category-mapping.csv','text/csv')
@app.get('/v5-category-coverage-summary.json')
def v5_category_coverage_summary(): return _persisted_v5_artifact('v5-category-coverage-summary.json','application/json')
@app.get('/category_rule_library_v1.csv')
def category_rule_library_v1(): return _persisted_v6_artifact('category_rule_library_v1.csv','text/csv')
@app.get('/v6-family-priority-queue.csv')
def v6_family_priority_queue(): return _persisted_v6_artifact('v6-family-priority-queue.csv','text/csv')
@app.get('/v6-family-rule-review.csv')
def v6_family_rule_review(): return _persisted_v6_artifact('v6-family-rule-review.csv','text/csv')
@app.get('/v6-product-category-mapping.csv')
def v6_product_category_mapping(): return _persisted_v6_artifact('v6-product-category-mapping.csv','text/csv')
@app.get('/v6-category-coverage-summary.json')
def v6_category_coverage_summary(): return _persisted_v6_artifact('v6-category-coverage-summary.json','application/json')
@app.get('/v6-rule-audit.json')
def v6_rule_audit(): return _persisted_v6_artifact('v6-rule-audit.json','application/json')


@app.get('/phh-naturehike-gale-probe.json')
def phh_naturehike_gale_probe():
    try:
        from phh_naturehike_gale_probe_v15 import run
        return _json(run())
    except Exception as e:
        return _json({'status':'ERROR','error':f'{type(e).__name__}: {e}'},503)

def _selftest_recovered_routes():
    paths=['/health','/content-summary.json','/content-dataset.csv','/image-audit.csv','/weight-audit.csv','/grouping-audit.csv','/title-qa.csv','/master-bulk-write-proposal.csv']
    with app.test_client() as c: statuses={p:c.get(p).status_code for p in paths}
    print('CONTENT_RECOVERY_ENDPOINT_SELFTEST',json.dumps({'statuses':statuses,'pass':all(v==200 for v in statuses.values()),'dataset_hash':STATE['summary'].get('dataset_hash')},sort_keys=True),flush=True)

def _maybe_run_master_write():
    flag=os.getenv('RUN_CONTROLLED_MASTER_WRITE','').strip()
    if flag!='APPROVED': return
    try:
        with LOCK: arts=dict(STATE['artifacts']); summ=dict(STATE['summary'])
        if str(summ.get('dataset_hash') or '')!=EXPECTED_DATASET_HASH: raise RuntimeError('approved write flag set but dataset hash mismatch')
        report,newarts=run_controlled_master_write(arts,summ)
        with LOCK: STATE['artifacts'].update(newarts); STATE['master_write']=report
        print('CONTROLLED_MASTER_WRITE_RESULT',json.dumps(report,sort_keys=True),flush=True)
    except Exception as e:
        with LOCK: STATE['master_write']={'status':'ERROR','error':f'{type(e).__name__}: {e}'}
        print('CONTROLLED_MASTER_WRITE_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_current_product_category_audit():
    if os.getenv('RUN_CURRENT_PRODUCT_CATEGORY_AUDIT','').strip()!='1': return
    try:
        from current_product_category_audit_v4 import run_audit
        master=_master_rows(); shopify=_shopify_products(master)
        summary,_=run_audit(master,shopify,os.getenv('DATABASE_URL'))
        print('CURRENT_PRODUCT_CATEGORY_AUDIT_RESULT',json.dumps(summary,sort_keys=True),flush=True)
    except Exception as e:
        print('CURRENT_PRODUCT_CATEGORY_AUDIT_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_family_category_audit_v5():
    if os.getenv('RUN_FAMILY_CATEGORY_AUDIT_V5','').strip()!='1': return
    try:
        from current_product_category_audit_v5 import run_audit
        master=_master_rows(); shopify=_shopify_products(master)
        summary,_=run_audit(master,shopify,os.getenv('DATABASE_URL'))
        print('FAMILY_CATEGORY_AUDIT_V5_RESULT',json.dumps(summary,sort_keys=True),flush=True)
    except Exception as e:
        print('FAMILY_CATEGORY_AUDIT_V5_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_category_rule_audit_v6():
    if os.getenv('RUN_CATEGORY_RULE_AUDIT_V6','').strip()!='1': return
    try:
        from current_product_category_audit_v6 import run_audit
        master=_master_rows(); shopify=_shopify_products(master)
        summary,_=run_audit(master,shopify,os.getenv('DATABASE_URL'))
        print('CATEGORY_RULE_AUDIT_V6_RESULT',json.dumps(summary,sort_keys=True),flush=True)
    except Exception as e:
        print('CATEGORY_RULE_AUDIT_V6_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_pmp_discovery():
    if os.getenv('RUN_PMP_API_DISCOVERY','').strip()!='1':
        return
    try:
        from pmp_api_probe import discover
        result=discover()
        safe={'status':result.get('status'),'docs':result.get('docs'),'selected_spec_url':result.get('selected_spec_url'),'openapi_summary':result.get('openapi_summary'),'spec_candidates':result.get('spec_candidates')}
        print('PMP_API_DISCOVERY_RESULT',json.dumps(safe,sort_keys=True),flush=True)
    except Exception as e:
        print('PMP_API_DISCOVERY_FAILED',type(e).__name__,str(e),flush=True)


def _maybe_run_naturehike_gale_probe():
    if os.getenv('RUN_NATUREHIKE_GALE_PROBE','').strip()!='1': return
    try:
        from phh_naturehike_gale_probe_v15 import run
        out=run()
        print('NATUREHIKE_GALE_PROBE_RESULT',json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    except Exception as e:
        print('NATUREHIKE_GALE_PROBE_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_naturehike_gale_create():
    if os.getenv('RUN_NATUREHIKE_GALE_CREATE','').strip()!='APPROVED_ONCE': return
    try:
        from phh_naturehike_gale_create_v15 import run
        run()
    except Exception as e:
        print('NATUREHIKE_GALE_CREATE_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_naturehike_gale_patch():
    if os.getenv('RUN_NATUREHIKE_GALE_PATCH','').strip()!='APPROVED_ONCE': return
    try:
        from phh_naturehike_gale_patch_v15 import run
        run()
    except Exception as e:
        print('NATUREHIKE_GALE_PATCH_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _boot():
    _maybe_run_naturehike_gale_patch()
    _maybe_run_naturehike_gale_create()
    _maybe_run_naturehike_gale_probe()
    _maybe_run_pmp_discovery()
    _maybe_run_current_product_category_audit()
    _maybe_run_family_category_audit_v5()
    _maybe_run_category_rule_audit_v6()
    print('CONTENT_STAGING_ENV',json.dumps({k:bool(os.getenv(k)) for k in ('GOOGLE_SERVICE_ACCOUNT_JSON','SHOPIFY_CLIENT_ID','SHOPIFY_CLIENT_SECRET','DATABASE_URL')},sort_keys=True),flush=True)
    restored=_restore_latest()
    if restored:
        _selftest_recovered_routes()
        if os.getenv('RUN_READINESS_REFRESH','').strip()=='1':
            try:
                refresh()
                with LOCK:
                    v=dict(STATE.get('product_xml_validation') or {}); s=dict(STATE.get('summary') or {})
                print('PRODUCT_XML_READINESS_REFRESH',json.dumps({'publish_gate':v.get('publish_gate'),'ready':v.get('ready_count',v.get('ready')),'blocked':v.get('blocked_count',v.get('blocked')),'dataset_hash':s.get('dataset_hash'),'projected_product_xml_ready':s.get('projected_product_xml_ready')},sort_keys=True),flush=True)
            except Exception as e:
                print('PRODUCT_XML_READINESS_REFRESH_FAILED',type(e).__name__,str(e),flush=True)
        _maybe_run_master_write(); return
    try:
        refresh(); _maybe_run_master_write()
    except Exception: pass
threading.Thread(target=_boot,daemon=True).start()