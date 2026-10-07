from __future__ import annotations
import csv, io, json, os, threading, time, traceback, hashlib, requests
from flask import Flask, Response, request
from app import _master_rows, _shopify_products, _load_json
from generator import build
from content_runtime import build_snapshot, persist_snapshot
from master_bulk_write import run_controlled_master_write, EXPECTED_DATASET_HASH
from full_catalog_audit import run as run_full_catalog_audit

app=Flask(__name__); LOCK=threading.RLock(); FULL_CATALOG_LOCK=threading.RLock(); EXISTING_AUTOCHECK_LOCK=threading.RLock(); EXISTING_AUTOCHECK={'status':'not_run','error':None,'summary':{},'rows':[],'csv':b''}; FULL_CATALOG={'status':'not_run','error':None,'summary':{},'artifacts':{}}; STATE={'status':'starting','error':None,'last_refresh':None,'summary':{},'artifacts':{},'product_xml_validation':{},'persistence_ok':False,'persistence_error':None,'recovered_from_postgres':False,'master_write':None}

# Early read-only taxonomy probe for the single-product pilot. Runs before the heavy catalog audit.
try:
    import current_product_category_audit as _pilot_cat
    _ts,_cats,_attrs=_pilot_cat._latest_taxonomy(os.getenv('DATABASE_URL',''))
    _req={}
    for _a in _attrs:
        if str(_a.get('required')).casefold() in {'true','1','yes'}:
            _req.setdefault(str(_a.get('category_id')),[]).append({k:_a.get(k,'') for k in ('field_id','title_en','title_lt','title_lv','title_ee','title_fi','title_ru','required')})
    _terms=('towel','microfiber towel','microfibre towel','travel towel','camping towel','rankšluost','dviel','rätik','pyyhe','полотенц')
    _hits=[]
    for _x in _cats:
        if str(_x.get('allow_add_products')).casefold() not in {'true','1','yes'}: continue
        _txt=' '.join(str(_x.get(k,'') or '') for k in ('title_en','title_lv','title_lt','title_ee','title_fi','title_ru')).casefold()
        _matched=[t for t in _terms if t in _txt]
        if not _matched: continue
        _cid=str(_x.get('category_id'))
        _hits.append({'category_id':_cid,'parent_id':str(_x.get('parent_id') or ''),'title_en':_x.get('title_en',''),'title_lv':_x.get('title_lv',''),'title_lt':_x.get('title_lt',''),'title_ru':_x.get('title_ru',''),'matched_terms':_matched,'required_fields':_req.get(_cid,[])})
    print('PILOT_68150_EARLY_CATEGORY_PROBE '+json.dumps({'sku':'68150','ean':'0021563681505','candidate_count':len(_hits),'candidates':_hits[:50]},ensure_ascii=False,sort_keys=True),flush=True)
except Exception as _pilot_e:
    print('PILOT_68150_EARLY_CATEGORY_PROBE_FAILED',type(_pilot_e).__name__,str(_pilot_e),flush=True)
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

def _persisted_v10_artifact(name,mime):
    try:
        from current_product_category_audit_v10 import load_latest_artifact
        b=load_latest_artifact(os.getenv('DATABASE_URL'),name)
        if not b: return _json({'error':'v10 category audit artifact unavailable'},503)
        return Response(b,status=200,mimetype=mime,headers={'Cache-Control':'no-store'})
    except Exception as e:
        return _json({'error':'v10 category audit artifact unavailable','detail':f'{type(e).__name__}: {e}'},503)

def _persisted_v11_artifact(name,mime):
    try:
        from current_product_attribute_audit_v11 import load_latest_artifact
        b=load_latest_artifact(os.getenv('DATABASE_URL'),name)
        if not b: return _json({'error':'v11 attribute audit artifact unavailable'},503)
        return Response(b,status=200,mimetype=mime,headers={'Cache-Control':'no-store'})
    except Exception as e:
        return _json({'error':'v11 attribute audit artifact unavailable','detail':f'{type(e).__name__}: {e}'},503)

def _persisted_v6_artifact(name,mime):
    try:
        from current_product_category_audit_v6 import load_latest_artifact
        b=load_latest_artifact(os.getenv('DATABASE_URL'),name)
        if not b: return _json({'error':'v6 category rule audit artifact unavailable'},503)
        return Response(b,status=200,mimetype=mime,headers={'Cache-Control':'no-store'})
    except Exception as e:
        return _json({'error':'v6 category rule audit artifact unavailable','detail':f'{type(e).__name__}: {e}'},503)

@app.get('/phh-product-import-schema-v45.json')
def phh_product_import_schema_v45():
    try:
        from phh_product_import_schema_probe_v45 import run
        return _json(run())
    except Exception as e:
        return _json({'status':'ERROR','error':f'{type(e).__name__}: {e}','writes':0},500)

@app.get('/phh-existing-shape-v44.json')
def phh_existing_shape_v44():
    try:
        from phh_existing_product_shape_probe_v44 import run
        return _json(run())
    except Exception as e:
        return _json({'status':'ERROR','error':f'{type(e).__name__}: {e}','writes':0},500)

@app.get('/phh-compliance-ops-v43.json')
def phh_compliance_ops_v43():
    try:
        from phh_compliance_ops_probe_v43 import run
        return _json(run())
    except Exception as e:
        return _json({'status':'ERROR','error':f'{type(e).__name__}: {e}','writes':0},500)

@app.get('/phh-gearaid-68150-preflight.json')
def phh_gearaid_68150_preflight():
    try:
        from phh_gearaid_68150_preflight_v42 import preflight
        return _json(preflight())
    except Exception as e:
        return _json({'status':'ERROR','error':f'{type(e).__name__}: {e}','marketplace_writes':0},500)

@app.get('/phh-gearaid-36134-preflight.json')
def phh_gearaid_36134_preflight():
    try:
        from phh_gearaid_36134_create_v41 import preflight
        return _json(preflight())
    except Exception as e:
        return _json({'status':'ERROR','error':f'{type(e).__name__}: {e}','marketplace_writes':0},500)

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
@app.get('/phh/autocheck-batch.json')
def phh_autocheck_batch():
    raw=request.args.get('ids','').strip()
    ids=[x.strip() for x in raw.split(',') if x.strip().isdigit()][:100]
    from pmp_autocheck_batch import run
    return _json(run(ids))

@app.get('/phh/category-contract.json')
def phh_category_contract():
    raw=request.args.get('ids','').strip()
    ids=[x.strip() for x in raw.split(',') if x.strip().isdigit()][:50]
    if not ids: return _json({'status':'ERROR','error':'ids query param required'},400)
    import current_product_category_audit as _cat
    summary,cats,attrs=_cat._latest_taxonomy(os.getenv('DATABASE_URL',''))
    by={str(c.get('category_id')):c for c in cats}
    out=[]
    for cid in ids:
        c=by.get(cid) or {}
        req=[{k:a.get(k,'') for k in ('field_id','required','title_en','title_lt','title_lv','title_ee','title_fi','title_ru')}
             for a in attrs
             if str(a.get('category_id'))==cid and str(a.get('required')).casefold() in {'true','1','yes'}]
        out.append({'category_id':cid,
                    'exists':bool(c),
                    'allow_add_products':c.get('allow_add_products'),
                    'title_en':c.get('title_en'),'title_lt':c.get('title_lt'),'title_lv':c.get('title_lv'),
                    'required_fields':req,'required_count':len(req)})
    return _json({'status':'PASS','taxonomy_summary':summary,'categories':out,'writes':0})

@app.get('/phh/attribute-contract-digest.json')
def phh_attribute_contract_digest():
    from pmp_attribute_contract_digest_v11j import run
    return _json(run())

@app.get('/pmp-openapi-digest.json')
def pmp_openapi_digest():
    from pmp_openapi_digest import run
    return _json(run())

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


@app.get('/v10/v10-product-category-mapping.csv')
def v10_product_category_mapping(): return _persisted_v10_artifact('v10-product-category-mapping.csv','text/csv')
@app.get('/v11/v11-product-attribute-readiness.csv')
def v11_product_attribute_readiness(): return _persisted_v11_artifact('v11-product-attribute-readiness.csv','text/csv')
@app.get('/v11/v11-product-required-attributes.csv')
def v11_product_required_attributes(): return _persisted_v11_artifact('v11-product-required-attributes.csv','text/csv')


def _existing_autocheck_csv(rows):
    fields=['shopify_variant_id','sku','ean','vendor','phh_category_id','offer_id','offer_status',
            'offer_amount','offer_price','modification_id','pigu_external_id','shopify_stock_live',
            'shopify_price_live','phh_autocheck_state','phh_autocheck_errors','phh_autocheck_locales','writes']
    b=io.StringIO(); w=csv.DictWriter(b,fieldnames=fields,extrasaction='ignore',lineterminator='\n')
    w.writeheader(); w.writerows(rows)
    return b.getvalue().encode('utf-8')


def _persist_existing_autocheck(summary,rows):
    db=os.getenv('DATABASE_URL','').strip()
    if not db: return False,'DATABASE_URL_MISSING'
    try:
        import psycopg
        with psycopg.connect(db,connect_timeout=10) as con:
            with con.cursor() as cur:
                cur.execute("""create table if not exists outfish_existing_autocheck_snapshots(
                    id bigint generated always as identity primary key,
                    created_at timestamptz not null default now(),
                    summary jsonb not null, rows jsonb not null)""")
                cur.execute("insert into outfish_existing_autocheck_snapshots(summary,rows) values(%s::jsonb,%s::jsonb)",
                            (json.dumps(summary),json.dumps(rows)))
                cur.execute("""delete from outfish_existing_autocheck_snapshots where id not in
                    (select id from outfish_existing_autocheck_snapshots order by id desc limit 5)""")
            con.commit()
        return True,None
    except Exception as e:
        return False,f'{type(e).__name__}: {str(e)[:180]}'


def _restore_existing_autocheck():
    db=os.getenv('DATABASE_URL','').strip()
    if not db: return False
    try:
        import psycopg
        with psycopg.connect(db,connect_timeout=10) as con:
            with con.cursor() as cur:
                cur.execute("select to_regclass('outfish_existing_autocheck_snapshots')")
                if not cur.fetchone()[0]: return False
                cur.execute("select summary,rows,created_at from outfish_existing_autocheck_snapshots order by id desc limit 1")
                rec=cur.fetchone()
        if not rec: return False
        summary,rows,created=rec
        if isinstance(summary,str): summary=json.loads(summary)
        if isinstance(rows,str): rows=json.loads(rows)
        with EXISTING_AUTOCHECK_LOCK:
            EXISTING_AUTOCHECK.update(status='cached',error=None,summary=summary,rows=rows,
                                      csv=_existing_autocheck_csv(rows),last_refresh=str(created))
        return True
    except Exception as e:
        print('EXISTING_AUTOCHECK_RESTORE_FAILED',type(e).__name__,str(e)[:180],flush=True)
        return False


def _run_existing_catalog_autocheck():
    from collections import Counter
    with EXISTING_AUTOCHECK_LOCK:
        if EXISTING_AUTOCHECK.get('status')=='running': return
        EXISTING_AUTOCHECK.update(status='running',error=None)
    try:
        with FULL_CATALOG_LOCK:
            source=FULL_CATALOG.get('artifacts',{}).get('all-existing-offers.csv')
        if not source:
            raise RuntimeError('all-existing-offers.csv unavailable; refresh full catalog first')
        offers=list(csv.DictReader(io.StringIO(source.decode('utf-8-sig'))))
        ids=[str(r.get('pigu_external_id') or '').strip() for r in offers
             if str(r.get('pigu_external_id') or '').strip().isdigit()]
        from pmp_autocheck_batch import run as run_batch
        result=run_batch(ids)
        byid={str(x.get('pigu_external_id') or ''):x for x in result.get('items') or []}
        rows=[]; state_counts=Counter(); code_counts=Counter(); locale_counts=Counter()
        for src in offers:
            pid=str(src.get('pigu_external_id') or '').strip()
            item=byid.get(pid)
            codes=[]; locales=[]
            def walk(obj,loc=''):
                if isinstance(obj,dict):
                    code=str(obj.get('code') or '').strip()
                    if code:
                        codes.append(code)
                        if loc: locales.append(loc)
                    for k,v in obj.items():
                        if k in ('lt','lv','ee','fi','ru'): walk(v,k)
                        elif k not in ('code','updated_at','validator_id','words'): walk(v,loc)
                elif isinstance(obj,list):
                    for v in obj: walk(v,loc)
            if item and item.get('http')==200:
                walk(item.get('errors') or [])
                state='VERIFIED_EXISTING' if not codes else 'EXISTING_WITH_ERRORS'
            else:
                state='AUTOCHECK_UNAVAILABLE'
            state_counts[state]+=1
            for c in set(codes): code_counts[c]+=1
            for loc in set(locales): locale_counts[loc]+=1
            rows.append({**src,'phh_autocheck_state':state,
                         'phh_autocheck_errors':'|'.join(sorted(set(codes))),
                         'phh_autocheck_locales':'|'.join(sorted(set(locales))),
                         'writes':'0'})
        summary={'status':result.get('status'),'checked':len(rows),'status_counts':dict(state_counts),
                 'error_code_card_counts':dict(code_counts),'error_locale_card_counts':dict(locale_counts),
                 'http_counts':result.get('http_counts') or {},'phh_writes':0}
        saved,err=_persist_existing_autocheck(summary,rows)
        summary['persisted']=saved; summary['persistence_error']=err
        with EXISTING_AUTOCHECK_LOCK:
            EXISTING_AUTOCHECK.update(status='ok',error=None,summary=summary,rows=rows,
                                      csv=_existing_autocheck_csv(rows),last_refresh=time.time())
        print('EXISTING_CATALOG_AUTOCHECK_RESULT',json.dumps(summary,sort_keys=True),flush=True)
    except Exception as e:
        with EXISTING_AUTOCHECK_LOCK:
            EXISTING_AUTOCHECK.update(status='error',error=f'{type(e).__name__}: {str(e)[:250]}')
        print('EXISTING_CATALOG_AUTOCHECK_FAILED',type(e).__name__,str(e)[:250],flush=True)


@app.get('/phh/existing-catalog-autocheck')
def existing_catalog_autocheck_status():
    with EXISTING_AUTOCHECK_LOCK:
        return _json({'status':EXISTING_AUTOCHECK.get('status'),
                      'error':EXISTING_AUTOCHECK.get('error'),
                      'summary':EXISTING_AUTOCHECK.get('summary') or {},
                      'last_refresh':EXISTING_AUTOCHECK.get('last_refresh')})

@app.get('/phh/manufacturer-read-paths.json')
def phh_manufacturer_read_paths():
    try:
        from pmp_api_probe import _docs_get,_embedded_spec
        docs=_docs_get('/docs',timeout=20); docs.raise_for_status()
        spec=_embedded_spec(docs.text)
        schemas=((spec.get('components') or {}).get('schemas') or {})
        def schema_has_compliance(obj,seen=None):
            seen=set() if seen is None else seen
            if isinstance(obj,dict):
                ref=obj.get('$ref')
                if isinstance(ref,str) and ref.startswith('#/components/schemas/'):
                    name=ref.rsplit('/',1)[-1]
                    if name in seen: return False
                    seen.add(name)
                    return schema_has_compliance(schemas.get(name) or {},seen)
                props=obj.get('properties') or {}
                if any(k in props for k in ('manufacturer_name','manufacturer_address','manufacturer_email','representative_name','representative_address','representative_email')):
                    return True
                return any(schema_has_compliance(v,seen.copy()) for v in obj.values())
            if isinstance(obj,list):
                return any(schema_has_compliance(v,seen.copy()) for v in obj)
            return False
        hits=[]
        for path,ops in sorted((spec.get('paths') or {}).items()):
            op=(ops or {}).get('get')
            if not isinstance(op,dict): continue
            responses=op.get('responses') or {}
            matched=[]
            for code,resp in responses.items():
                content=(resp or {}).get('content') or {}
                for mime,body in content.items():
                    sch=(body or {}).get('schema')
                    if sch and schema_has_compliance(sch):
                        matched.append({'status':code,'mime':mime,'schema':sch})
            if matched:
                hits.append({'path':path,'operation_id':op.get('operationId'),'summary':op.get('summary'),
                             'parameters':op.get('parameters') or [],'responses':matched})
        return _json({'status':'PASS','read_paths':hits,'count':len(hits),'writes':0})
    except Exception as e:
        return _json({'status':'ERROR','error':f'{type(e).__name__}: {str(e)[:300]}','writes':0},500)

@app.get('/phh/manufacturer-openapi.json')
def phh_manufacturer_openapi():
    try:
        from pmp_api_probe import _docs_get,_embedded_spec
        docs=_docs_get('/docs',timeout=20); docs.raise_for_status()
        spec=_embedded_spec(docs.text)
        paths=[]
        for path,ops in sorted((spec.get('paths') or {}).items()):
            blob=(path+' '+json.dumps(ops,ensure_ascii=False)).casefold()
            if not any(x in blob for x in ('manufacturer','representative')):
                continue
            item={'path':path,'methods':{}}
            for method in ('get','post','put','patch','delete'):
                op=(ops or {}).get(method)
                if isinstance(op,dict):
                    item['methods'][method.upper()]={'operation_id':op.get('operationId'),'summary':op.get('summary'),'parameters':op.get('parameters'),'responses':op.get('responses'),'requestBody':op.get('requestBody')}
            paths.append(item)
        schemas={}
        for name,schema in ((spec.get('components') or {}).get('schemas') or {}).items():
            blob=(name+' '+json.dumps(schema,ensure_ascii=False)).casefold()
            if any(x in blob for x in ('manufacturer','representative')):
                schemas[name]=schema
        return _json({'status':'PASS','paths':paths,'schemas':schemas,'writes':0})
    except Exception as e:
        return _json({'status':'ERROR','error':f'{type(e).__name__}: {str(e)[:300]}','writes':0},500)

@app.get('/phh/existing-catalog-autocheck/scope-summary.json')
def existing_catalog_autocheck_scope_summary():
    from collections import Counter
    with EXISTING_AUTOCHECK_LOCK:
        status=EXISTING_AUTOCHECK.get('status')
        error=EXISTING_AUTOCHECK.get('error')
        rows=[dict(x) for x in (EXISTING_AUTOCHECK.get('rows') or [])]
        last_refresh=EXISTING_AUTOCHECK.get('last_refresh')
    if not rows:
        return _json({'status':status,'error':error,'count':0,'last_refresh':last_refresh,'phh_writes':0})
    try:
        from app import _shopify_token
        token=_shopify_token()
        shop=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
        ids=sorted({str(r.get('shopify_variant_id') or '').strip() for r in rows if str(r.get('shopify_variant_id') or '').strip()})
        query='''query ScopeVariants($ids:[ID!]!){nodes(ids:$ids){... on ProductVariant{id sku product{id title handle vendor productType status tags}}}}'''
        live={}
        for i in range(0,len(ids),50):
            batch=ids[i:i+50]
            resp=requests.post(f'https://{shop}/admin/api/2026-07/graphql.json',
                headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},
                json={'query':query,'variables':{'ids':batch}},timeout=90)
            resp.raise_for_status()
            payload=resp.json()
            if payload.get('errors'):
                raise RuntimeError('Shopify GraphQL errors: '+json.dumps(payload['errors'])[:1200])
            for node in ((payload.get('data') or {}).get('nodes') or []):
                if node and node.get('id'):
                    live[str(node['id'])]=node
        excluded=[]
        included=[]
        excluded_reasons=Counter()
        included_error_counts=Counter()
        excluded_error_counts=Counter()
        for row in rows:
            node=live.get(str(row.get('shopify_variant_id') or '').strip()) or {}
            p=node.get('product') or {}
            title=str(p.get('title') or row.get('shopify_title') or '').strip()
            handle=str(p.get('handle') or '').strip()
            ptype=str(p.get('productType') or row.get('product_type') or '').strip()
            tags=[str(x) for x in (p.get('tags') or [])]
            hay=' '.join([title,handle,ptype]+tags).casefold()
            reason=None
            if any(x in hay for x in ('gift card','giftcard','dāvanu karte','davanu karte','gift certificate','voucher')):
                reason='GIFT_CARD'
            else:
                rental_terms=(' rental','rental ',' rent ',' noma','nomā',' īre','īre ','hire ')
                if any(x in (' '+hay+' ') for x in rental_terms):
                    reason='RENTAL'
            if not reason:
                ptype_cf=ptype.casefold().strip()
                tag_cf={x.casefold().strip() for x in tags}
                title_cf=title.casefold()
                accessory_ptype=('accessor' in ptype_cf or ptype_cf in {'seat','paddle','paddles'})
                kayak_collection=('collection:kayaks' in tag_cf or 'collection:kayak' in tag_cf)
                direct_kayak=(ptype_cf in {'kayak','kayaks','kajaks','kajaki','kajak'} or (kayak_collection and not accessory_ptype))
                title_kayak=('kayak' in title_cf or 'kajak' in title_cf)
                accessory_terms=('holder','mount','rack','bracket','paddle','bag','crate','seat','cover','trolley','cart','anchor','accessory','accessories','rail','carrier','roof','storage','motor mount','rod holder')
                title_accessory=any(x in title_cf for x in accessory_terms)
                if direct_kayak or (title_kayak and not title_accessory):
                    reason='KAYAK'
            codes=[x for x in str(row.get('phh_autocheck_errors') or '').split('|') if x]
            item={'shopify_variant_id':row.get('shopify_variant_id'),'pigu_external_id':row.get('pigu_external_id'),
                  'title':title,'handle':handle,'vendor':p.get('vendor') or row.get('vendor'),
                  'product_type':ptype,'status':p.get('status'),'reason':reason,'errors':codes}
            if reason:
                excluded.append(item); excluded_reasons[reason]+=1
                for c in set(codes): excluded_error_counts[c]+=1
            else:
                included.append(item)
                for c in set(codes): included_error_counts[c]+=1
        return _json({'status':status,'error':error,'snapshot_count':len(rows),
            'live_resolved':len(live),'included_count':len(included),'excluded_count':len(excluded),
            'excluded_reasons':dict(excluded_reasons),
            'included_error_code_card_counts':dict(included_error_counts),
            'excluded_error_code_card_counts':dict(excluded_error_counts),
            'excluded_rows':excluded,'last_refresh':last_refresh,'phh_writes':0,'shopify_writes':0})
    except Exception as e:
        return _json({'status':'LIVE_SCOPE_JOIN_FAILED','error':f'{type(e).__name__}: {str(e)[:300]}','phh_writes':0,'shopify_writes':0},502)

@app.get('/phh/manufacturer-readiness-summary.json')
def phh_manufacturer_readiness_summary():
    try:
        registry=_load_json('manufacturer_compliance_registry.json') or {}
        recs={str(r.get('vendor_group') or '').casefold():r for r in (registry.get('records') or [])}
        overrides={str(x.get('shopify_sku') or '').strip():x for x in (registry.get('product_overrides') or []) if str(x.get('shopify_sku') or '').strip()}
        exclusions=list(registry.get('exclusions') or [])
        with EXISTING_AUTOCHECK_LOCK:
            source_rows=[dict(x) for x in (EXISTING_AUTOCHECK.get('rows') or [])]
            last_refresh=EXISTING_AUTOCHECK.get('last_refresh')
            status=EXISTING_AUTOCHECK.get('status')
        relevant=[]
        for row in source_rows:
            codes={x for x in str(row.get('phh_autocheck_errors') or '').split('|') if x}
            if 'manufacturer_representative_info_missing' in codes:
                relevant.append(row)

        live_by_variant={}
        try:
            from app import _shopify_token
            token=_shopify_token()
            shop=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
            ids=sorted({str(r.get('shopify_variant_id') or '').strip() for r in relevant if str(r.get('shopify_variant_id') or '').strip()})
            query='''query ComplianceReadinessVariants($ids:[ID!]!){nodes(ids:$ids){... on ProductVariant{id sku price product{title handle vendor status tags}}}}'''
            for i in range(0,len(ids),50):
                batch=ids[i:i+50]
                resp=requests.post(f'https://{shop}/admin/api/2026-07/graphql.json',
                    headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},
                    json={'query':query,'variables':{'ids':batch}},timeout=90)
                resp.raise_for_status()
                payload=resp.json()
                if payload.get('errors'):
                    raise RuntimeError('Shopify GraphQL errors: '+json.dumps(payload['errors'])[:1200])
                for node in ((payload.get('data') or {}).get('nodes') or []):
                    if node and node.get('id'):
                        live_by_variant[str(node['id'])]=node
        except Exception:
            live_by_variant={}

        buckets={
            'EXCLUDED_PROJECT_RULE':0,
            'READY_LEGAL_EU_TECH_PENDING':0,
            'READY_MANUFACTURER_AND_EU_OPERATOR_TECH_PENDING':0,
            'MANUFACTURER_VERIFIED_EU_OPERATOR_ROLE_PENDING':0,
            'MANUFACTURER_VERIFIED_EU_RESPONSIBLE_PENDING':0,
            'MANUFACTURER_UNVERIFIED':0,
            'OTHER_PARTIAL':0
        }
        group_counts={}
        group_bucket_counts={}
        samples={}
        for row in relevant:
            vendor=str(row.get('vendor') or '').strip() or '(blank)'
            node=live_by_variant.get(str(row.get('shopify_variant_id') or '').strip()) or {}
            product=node.get('product') or {}
            sku=str(node.get('sku') or row.get('shopify_sku') or '').strip()
            title=str(product.get('title') or row.get('shopify_title') or '').strip()
            handle=str(product.get('handle') or '').strip()
            override=overrides.get(sku)
            rec=override or recs.get(vendor.casefold()) or {}
            m=(rec.get('manufacturer') or {})
            phh=str(rec.get('phh_status') or '')

            excluded=False
            for rule in exclusions:
                if rule.get('shopify_product_handle') and handle==str(rule.get('shopify_product_handle')):
                    excluded=True
                    break
            if excluded:
                bucket='EXCLUDED_PROJECT_RULE'
            elif override:
                if phh=='MANUFACTURER_READY_REPRESENTATIVE_TECHNICAL_SEMANTICS_PENDING':
                    bucket='READY_LEGAL_EU_TECH_PENDING'
                elif phh=='MANUFACTURER_AND_EU_RESPONSIBLE_PERSON_VERIFIED':
                    bucket='READY_MANUFACTURER_AND_EU_OPERATOR_TECH_PENDING'
                else:
                    bucket='MANUFACTURER_VERIFIED_EU_OPERATOR_ROLE_PENDING'
            elif vendor.casefold()=='outfish':
                verified_titles=set(str(x).casefold() for x in ((m.get('verified_product_titles') or [])))
                if 'outfish' in title.casefold() or title.casefold() in verified_titles:
                    bucket='READY_LEGAL_EU_TECH_PENDING'
                else:
                    bucket='MANUFACTURER_UNVERIFIED'
            elif phh=='MANUFACTURER_AND_EU_RESPONSIBLE_PERSON_VERIFIED':
                bucket='READY_MANUFACTURER_AND_EU_OPERATOR_TECH_PENDING'
            elif phh=='MANUFACTURER_READY_REPRESENTATIVE_TECHNICAL_SEMANTICS_PENDING':
                bucket='READY_LEGAL_EU_TECH_PENDING'
            elif phh=='MANUFACTURER_VERIFIED_EU_OPERATOR_ROLE_PENDING':
                bucket='MANUFACTURER_VERIFIED_EU_OPERATOR_ROLE_PENDING'
            elif phh in {'BLOCKED_EU_RESPONSIBLE_PERSON','BLOCKED_EU_RESPONSIBLE_PERSON_ADDRESS'}:
                bucket='MANUFACTURER_VERIFIED_EU_RESPONSIBLE_PENDING'
            elif m.get('status') and str(m.get('status')).startswith('VERIFIED'):
                bucket='OTHER_PARTIAL'
            else:
                bucket='MANUFACTURER_UNVERIFIED'

            buckets[bucket]+=1
            group_counts[vendor]=group_counts.get(vendor,0)+1
            gbc=group_bucket_counts.setdefault(vendor,{})
            gbc[bucket]=gbc.get(bucket,0)+1
            if len(samples.setdefault(vendor,[]))<5:
                samples[vendor].append({'sku':sku,'title':title,'bucket':bucket})

        items=[]
        for vendor,count in sorted(group_counts.items(),key=lambda kv:(-kv[1],kv[0].casefold())):
            items.append({'vendor':vendor,'count':count,'bucket_counts':group_bucket_counts.get(vendor) or {},
                          'samples':samples.get(vendor) or []})
        return _json({'status':status,'last_refresh':last_refresh,'total_raw':len(relevant),
                      'total_in_scope':len(relevant)-buckets['EXCLUDED_PROJECT_RULE'],
                      'group_count':len(group_counts),'live_resolved':len(live_by_variant),
                      'buckets':buckets,'groups':items,
                      'registry_updated_at':registry.get('updated_at'),'phh_writes':0,'shopify_writes':0})
    except Exception as e:
        return _json({'status':'ERROR','error':f'{type(e).__name__}: {str(e)[:300]}','phh_writes':0},500)

@app.get('/phh/existing-catalog-autocheck/manufacturer-groups.json')
def existing_catalog_autocheck_manufacturer_groups():
    from collections import Counter
    code='manufacturer_representative_info_missing'
    vendor_filter=str(request.args.get('vendor') or '').strip().casefold()
    with EXISTING_AUTOCHECK_LOCK:
        status=EXISTING_AUTOCHECK.get('status')
        error=EXISTING_AUTOCHECK.get('error')
        source_rows=list(EXISTING_AUTOCHECK.get('rows') or [])
        last_refresh=EXISTING_AUTOCHECK.get('last_refresh')
    master_by_variant={}
    try:
        for m in _master_rows():
            vid=str(m.get('shopify_variant_id') or '').strip()
            if vid:
                master_by_variant[vid]=m
    except Exception:
        master_by_variant={}
    rows=[]
    groups={}
    for src in source_rows:
        codes=set(x for x in str(src.get('phh_autocheck_errors') or '').split('|') if x)
        if code not in codes:
            continue
        vendor=str(src.get('vendor') or '').strip() or '(blank)'
        if vendor_filter and vendor.casefold()!=vendor_filter:
            continue
        row={k:src.get(k) for k in (
            'shopify_variant_id','shopify_sku','shopify_barcode','shopify_title','variant_title',
            'vendor','pigu_external_id','offer_id','modification_id','phh_autocheck_state',
            'phh_autocheck_errors','phh_autocheck_locales') if k in src}
        m=master_by_variant.get(str(src.get('shopify_variant_id') or '').strip()) or {}
        for k in ('shopify_sku','shopify_barcode','shopify_title','variant_title','product_type','220_title','220_ean','220_manufacturer_name','220_manufacturer_email','220_manufacturer_address','220_manufacturer_source_url'):
            if not row.get(k) and m.get(k) not in (None,''):
                row[k]=m.get(k)
        rows.append(row)
        bucket=groups.setdefault(vendor,{'vendor':vendor,'count':0,'sample_skus':[],'pigu_external_ids':[]})
        bucket['count']+=1
        sku=str(src.get('shopify_sku') or '').strip()
        if sku and sku not in bucket['sample_skus'] and len(bucket['sample_skus'])<8:
            bucket['sample_skus'].append(sku)
        pid=str(src.get('pigu_external_id') or '').strip()
        if pid and pid not in bucket['pigu_external_ids'] and len(bucket['pigu_external_ids'])<8:
            bucket['pigu_external_ids'].append(pid)
    live_join=str(request.args.get('live') or '').strip()=='1'
    live_by_variant={}
    if live_join and rows:
        try:
            from app import _shopify_token
            token=_shopify_token()
            shop=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
            ids=sorted({str(r.get('shopify_variant_id') or '').strip() for r in rows if str(r.get('shopify_variant_id') or '').strip()})
            query='''query ComplianceVariants($ids:[ID!]!){nodes(ids:$ids){... on ProductVariant{id sku barcode title product{id title vendor productType status tags}}}}'''
            for i in range(0,len(ids),50):
                batch=ids[i:i+50]
                resp=requests.post(f'https://{shop}/admin/api/2026-07/graphql.json',headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},json={'query':query,'variables':{'ids':batch}},timeout=90)
                resp.raise_for_status()
                payload=resp.json()
                if payload.get('errors'):
                    raise RuntimeError('Shopify GraphQL errors: '+json.dumps(payload['errors'])[:1200])
                for node in ((payload.get('data') or {}).get('nodes') or []):
                    if node and node.get('id'):
                        live_by_variant[str(node['id'])]=node
            for row in rows:
                node=live_by_variant.get(str(row.get('shopify_variant_id') or '').strip()) or {}
                product=node.get('product') or {}
                row['live_shopify_sku']=node.get('sku')
                row['live_shopify_barcode']=node.get('barcode')
                row['live_variant_title']=node.get('title')
                row['live_product_title']=product.get('title')
                row['live_product_vendor']=product.get('vendor')
                row['live_product_type']=product.get('productType')
                row['live_product_status']=product.get('status')
                row['live_product_tags']=product.get('tags') or []
        except Exception as e:
            return _json({'status':'LIVE_SHOPIFY_JOIN_FAILED','error':f'{type(e).__name__}: {str(e)[:250]}','phh_writes':0},502)
    grouped=sorted(groups.values(),key=lambda x:(-x['count'],x['vendor'].lower()))
    title_prefix_counts=Counter()
    live_title_counts=Counter()
    live_status_counts=Counter()
    fhm_title_count=0
    outfish_named_count=0
    compact_rows=[]
    for row in rows:
        title=str(row.get('live_product_title') or row.get('shopify_title') or row.get('220_title') or '').strip()
        prefix=(title.split()[0] if title else '(blank)')
        title_prefix_counts[prefix]+=1
        if title:
            live_title_counts[title]+=1
        live_status_counts[str(row.get('live_product_status') or '(unknown)')]+=1
        if 'OUTFISH' in title.upper().split() or 'OUTFISH' in title.upper():
            outfish_named_count+=1
        if 'FHM' in title.upper().split() or any(str(t).upper()=='FHM' for t in (row.get('live_product_tags') or [])):
            fhm_title_count+=1
        compact_rows.append({k:row.get(k) for k in ('shopify_variant_id','shopify_sku','shopify_barcode','shopify_title','variant_title','product_type','pigu_external_id','offer_id','modification_id','live_shopify_sku','live_shopify_barcode','live_variant_title','live_product_title','live_product_vendor','live_product_type','live_product_status','live_product_tags')})
    compact=str(request.args.get('compact') or '').strip()=='1'
    return _json({'status':status,'error':error,'code':code,'vendor_filter':vendor_filter or None,'count':len(rows),
                  'group_count':len(grouped),'groups':grouped,
                  'title_prefix_counts':dict(title_prefix_counts),'live_title_counts':dict(live_title_counts),'distinct_live_titles':len(live_title_counts),'live_status_counts':dict(live_status_counts),'outfish_named_count':outfish_named_count,'fhm_title_count':fhm_title_count,'live_shopify_join':live_join,
                  'rows':compact_rows if compact else rows,
                  'last_refresh':last_refresh,'phh_writes':0})

@app.get('/phh/existing-catalog-autocheck/start')
def existing_catalog_autocheck_start_get():
    with EXISTING_AUTOCHECK_LOCK:
        if EXISTING_AUTOCHECK.get('status')=='running':
            return _json({'status':'ALREADY_RUNNING','phh_writes':0},202)
    threading.Thread(target=_run_existing_catalog_autocheck,daemon=True,name='phh-existing-autocheck').start()
    return _json({'status':'STARTED_READ_ONLY','phh_writes':0},202)

@app.post('/phh/existing-catalog-autocheck')
def existing_catalog_autocheck_start():
    with EXISTING_AUTOCHECK_LOCK:
        if EXISTING_AUTOCHECK.get('status')=='running':
            return _json({'status':'ALREADY_RUNNING','phh_writes':0},202)
    threading.Thread(target=_run_existing_catalog_autocheck,daemon=True,name='phh-existing-autocheck').start()
    return _json({'status':'STARTED_READ_ONLY','phh_writes':0},202)

@app.get('/full-catalog/refresh')
def full_catalog_refresh_status():
    with FULL_CATALOG_LOCK:
        return _json({'status':FULL_CATALOG['status'],
                      'summary':FULL_CATALOG.get('summary') or {},
                      'error':FULL_CATALOG.get('error'),
                      'persistence_ok':FULL_CATALOG.get('persistence_ok')})

@app.post('/full-catalog/refresh')
def full_catalog_refresh():
    # Only a read-only audit; all legacy PHH/Master/Shopify write handlers stay off.
    with FULL_CATALOG_LOCK:
        if FULL_CATALOG.get('status')=='running':
            return _json({'status':'ALREADY_RUNNING'},202)
    threading.Thread(target=_run_and_persist_catalog,daemon=True).start()
    return _json({'status':'STARTED_READ_ONLY','phh_writes':0},202)


def _persist_full_catalog(arts, summary):
    db=os.getenv('DATABASE_URL','').strip()
    if not db: return False,'DATABASE_URL_MISSING'
    try:
        import base64, gzip, psycopg
        payload={name:base64.b64encode(gzip.compress(data,compresslevel=6)).decode('ascii')
                 for name,data in arts.items()}
        with psycopg.connect(db,connect_timeout=10) as con:
            with con.cursor() as cur:
                cur.execute("""create table if not exists outfish_full_catalog_snapshots(
                    id bigint generated always as identity primary key,
                    created_at timestamptz not null default now(),
                    dataset_hash text not null,
                    summary jsonb not null, artifacts jsonb not null)""")
                h=(summary.get('publication_pipeline') or {}).get('dataset_hash','')
                cur.execute("""insert into outfish_full_catalog_snapshots(dataset_hash,summary,artifacts)
                               values(%s,%s::jsonb,%s::jsonb)""",
                            (h,json.dumps(summary),json.dumps(payload)))
                cur.execute("""delete from outfish_full_catalog_snapshots where id not in
                    (select id from outfish_full_catalog_snapshots order by id desc limit 5)""")
            con.commit()
        return True,None
    except Exception as e:
        return False,f'{type(e).__name__}: {str(e)[:180]}'


def _restore_full_catalog():
    db=os.getenv('DATABASE_URL','').strip()
    if not db: return False
    try:
        import base64, gzip, psycopg
        with psycopg.connect(db,connect_timeout=10) as con:
            with con.cursor() as cur:
                cur.execute("select to_regclass('outfish_full_catalog_snapshots')")
                if not cur.fetchone()[0]: return False
                cur.execute("""select summary,artifacts,created_at
                               from outfish_full_catalog_snapshots order by id desc limit 1""")
                record=cur.fetchone()
        if not record: return False
        summary,packed,created=record
        artifacts={k:gzip.decompress(base64.b64decode(v)) for k,v in packed.items()}
        with FULL_CATALOG_LOCK:
            FULL_CATALOG.update(status='cached',error=None,summary=summary,artifacts=artifacts,
                                last_refresh=str(created),persistence_ok=True)
        print('FULL_CATALOG_RESTORED',json.dumps({'checked':(summary.get('publication_pipeline') or {}).get('checked'),
              'created_at':str(created),'artifact_count':len(artifacts)},sort_keys=True),flush=True)
        return True
    except Exception as e:
        print('FULL_CATALOG_RESTORE_FAILED',type(e).__name__,str(e)[:180],flush=True)
        return False


def _run_and_persist_catalog():
    with FULL_CATALOG_LOCK:
        if FULL_CATALOG.get('status')=='running': return
        FULL_CATALOG['status']='running'
    try:
        arts,summary=run_full_catalog_audit()
        saved,err=_persist_full_catalog(arts,summary)
        with FULL_CATALOG_LOCK:
            FULL_CATALOG.update(status='ok',error=None,summary=summary,artifacts=arts,
                                last_refresh=time.time(),persistence_ok=saved,persistence_error=err)
        # Compact logs: no customer data, private URLs, product titles, tokens or giant JSON.
        print('FULL_CATALOG_PIPELINE_COUNTS',json.dumps({
              'checked':(summary.get('publication_pipeline') or {}).get('checked'),
              'counts':(summary.get('publication_pipeline') or {}).get('counts'),
              'phh_access_error':bool(summary.get('phh_access_error')),
              'persisted':saved,'persistence_error':err,
              'dataset_hash':(summary.get('publication_pipeline') or {}).get('dataset_hash')
              },sort_keys=True),flush=True)
    except Exception as e:
        with FULL_CATALOG_LOCK:
            FULL_CATALOG.update(status='error',error=f'{type(e).__name__}: {str(e)[:200]}')
        print('FULL_CATALOG_AUDIT_FAILED',type(e).__name__,str(e)[:200],flush=True)


def _full_catalog_artifact(name,mime):
    with FULL_CATALOG_LOCK: b=FULL_CATALOG['artifacts'].get(name); status=FULL_CATALOG['status']; err=FULL_CATALOG['error']
    if not b: return _json({'error':'full catalog audit artifact unavailable','status':status,'detail':err},503)
    return Response(b,status=200,mimetype=mime,headers={'Cache-Control':'no-store'})

@app.get('/full-catalog/all-existing-offers.csv')
def full_catalog_all_existing_offers(): return _full_catalog_artifact('all-existing-offers.csv','text/csv')

@app.get('/phh/existing-catalog-autocheck.csv')
def existing_catalog_autocheck_csv():
    with EXISTING_AUTOCHECK_LOCK:
        b=EXISTING_AUTOCHECK.get('csv'); status=EXISTING_AUTOCHECK.get('status'); err=EXISTING_AUTOCHECK.get('error')
    if not b: return _json({'error':'existing autocheck artifact unavailable','status':status,'detail':err},503)
    return Response(b,status=200,mimetype='text/csv',headers={'Cache-Control':'no-store'})

@app.get('/full-catalog/existing-autocheck-remediation.csv')
def full_catalog_existing_autocheck_remediation(): return _full_catalog_artifact('existing-autocheck-remediation.csv','text/csv')

@app.get('/full-catalog/stock-sync-review.csv')
def full_catalog_stock_sync_review(): return _full_catalog_artifact('stock-sync-review.csv','text/csv')

@app.get('/full-catalog/near-ready-cohort.csv')
def full_catalog_near_ready_cohort(): return _full_catalog_artifact('near-ready-cohort.csv','text/csv')
@app.get('/full-catalog/translation-queue.csv')
def full_catalog_translation_queue(): return _full_catalog_artifact('catalog-translation-queue.csv','text/csv')
@app.get('/full-catalog/category-rules.csv')
def full_catalog_category_rules(): return _full_catalog_artifact('catalog-category-rules.csv','text/csv')
@app.get('/full-catalog/category-backlog.csv')
def full_catalog_category_backlog(): return _full_catalog_artifact('catalog-category-backlog.csv','text/csv')
@app.get('/full-catalog/pipeline-state.json')
def full_catalog_pipeline_state(): return _full_catalog_artifact('catalog-pipeline-state.json','application/json')
@app.get('/full-catalog/summary.json')
def full_catalog_summary(): return _full_catalog_artifact('full-catalog-summary.json','application/json')
@app.get('/full-catalog/reconciliation.csv')
def full_catalog_reconciliation(): return _full_catalog_artifact('full-catalog-reconciliation.csv','text/csv')
@app.get('/full-catalog/ready-candidates.csv')
def full_catalog_ready_candidates(): return _full_catalog_artifact('full-catalog-ready-candidates.csv','text/csv')
@app.get('/full-catalog/exceptions.csv')
def full_catalog_exceptions(): return _full_catalog_artifact('full-catalog-exceptions.csv','text/csv')

@app.get('/phh-gearaid-10592-readonly-v52.json')
def phh_gearaid_10592_readonly_v52():
    try:
        from phh_gearaid_10592_readonly_v52 import run
        return _json(run())
    except Exception as e:
        return _json({'status':'BLOCKED_READONLY_ERROR','error_type':type(e).__name__,'writes':0},503)


@app.get('/phh-bushmen-bushbed-readonly-v51.json')
def phh_bushmen_bushbed_readonly_v51():
    try:
        from phh_bushmen_bushbed_camouflage_readonly_v51 import run
        return _json(run())
    except Exception as e:
        return _json({'status':'BLOCKED_READONLY_ERROR','error_type':type(e).__name__,'writes':0},503)


@app.get('/phh-gearaid-68150-feature-probe-v47.json')
def phh_gearaid_68150_feature_probe_v47():
    try:
        from phh_gearaid_68150_feature_patch_v47 import probe
        return _json(probe())
    except Exception as e:
        return _json({'status':'ERROR','error':f'{type(e).__name__}: {e}','writes':0},503)

@app.get('/phh-feature-value-ops-v23.json')
def phh_feature_value_ops_v23():
    try:
        from phh_feature_value_ops_v23 import run
        return _json(run())
    except Exception as e:
        return _json({'status':'ERROR','error':f'{type(e).__name__}: {e}'},503)

@app.get('/phh-offer-identity-export-v31.json')
def phh_offer_identity_export_v31():
    try:
        from phh_orphan_offer_export_v31 import run
        return _json(run())
    except Exception as e:
        return _json({'status':'ERROR','error':f'{type(e).__name__}: {e}'},503)

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


def _maybe_run_gearaid_68150_feature_patch():
    if os.getenv('RUN_GEARAID_68150_FEATURE_PATCH','').strip()!='APPROVED_ONCE': return
    try:
        from phh_gearaid_68150_feature_patch_v47 import run
        result=run()
        print('GEARAID_68150_FEATURE_PATCH_BOOT '+json.dumps(result,ensure_ascii=False,sort_keys=True),flush=True)
    except Exception as e:
        print('GEARAID_68150_FEATURE_PATCH_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_gearaid_68150_create():
    if os.getenv('RUN_GEARAID_68150_CREATE','').strip()!='APPROVED_ONCE': return
    try:
        from phh_gearaid_68150_create_v46 import run
        run()
    except Exception as e:
        print('PHH_GEARAID_68150_CREATE_V46_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


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


def _maybe_run_phh_offer_contract_v16():
    if os.getenv('RUN_PHH_OFFER_CONTRACT_V16','').strip()!='1': return
    try:
        from phh_offer_contract_v16 import run
        run()
    except Exception as e:
        print('PHH_OFFER_CONTRACT_V16_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_naturehike_gale_offer_v16():
    if os.getenv('RUN_NATUREHIKE_GALE_OFFER','').strip()!='APPROVED_ONCE': return
    try:
        from phh_naturehike_gale_offer_v16 import run
        run()
    except Exception as e:
        print('PHH_NATUREHIKE_GALE_OFFER_V16_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_gale_mod_status_v17():
    if os.getenv('RUN_PHH_GALE_MOD_STATUS_V17','').strip()!='1': return
    try:
        from phh_gale_mod_status_v17 import run
        run()
    except Exception as e:
        print('PHH_GALE_MOD_STATUS_V17_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_gale_autocheck_v18():
    if os.getenv('RUN_PHH_GALE_AUTOCHECK_V18','').strip()!='1': return
    try:
        from phh_gale_autocheck_v18 import run
        run()
    except Exception as e:
        print('PHH_GALE_AUTOCHECK_V18_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_product_import_schema_v19():
    if os.getenv('RUN_PHH_PRODUCT_IMPORT_SCHEMA_V19','').strip()!='1': return
    try:
        from phh_product_import_schema_v19 import run
        run()
    except Exception as e:
        print('PHH_PRODUCT_IMPORT_SCHEMA_V19_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_naturehike_gale_full_patch_v20():
    if os.getenv('RUN_NATUREHIKE_GALE_FULL_PATCH','').strip()!='APPROVED_ONCE': return
    try:
        from phh_naturehike_gale_full_patch_v20 import run
        run()
    except Exception as e:
        print('PHH_NATUREHIKE_GALE_FULL_PATCH_V20_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_field_capabilities_v21():
    if os.getenv('RUN_PHH_FIELD_CAPABILITIES_V21','').strip()!='1': return
    try:
        from phh_field_capabilities_v21 import run
        run()
    except Exception as e:
        print('PHH_FIELD_CAPABILITIES_V21_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_gale_autocheck_now_v22():
    if os.getenv('RUN_PHH_GALE_AUTOCHECK_NOW_V22','').strip()!='1': return
    try:
        from phh_gale_autocheck_now_v22 import run
        run()
    except Exception as e:
        print('PHH_GALE_AUTOCHECK_NOW_V22_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_feature_value_ops_v23():
    try:
        from phh_feature_value_ops_v23 import run
        result=run()
        print('PHH_FEATURE_VALUE_OPS_V23',json.dumps(result,sort_keys=True),flush=True)
    except Exception as e:
        print('PHH_FEATURE_VALUE_OPS_V23_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_pmp_import_history_mining_v11k():
    try:
        from pmp_import_history_mining_v11k import run
        result=run()
        print('PMP_IMPORT_HISTORY_MINING_V11K_RESULT',json.dumps(result,ensure_ascii=False,sort_keys=True)[:180000],flush=True)
    except Exception as e:
        print('PMP_IMPORT_HISTORY_MINING_V11K_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_9050_contract_bundle():
    try:
        from pmp_category_required_fields_probe_v11n import run as required_fields_run
        from pmp_attribute_contract_digest_v11j import run as attribute_contract_run
        from phh_product_import_schema_v19 import run as import_schema_run
        required_fields=required_fields_run()
        attribute_contract=attribute_contract_run()
        import_schema=import_schema_run()
        print('PHH_9050_CONTRACT_BUNDLE',json.dumps({
            'status':'PASS',
            'required_fields_9050':required_fields.get('9050',[]),
            'attribute_contract':attribute_contract,
            'product_import_schema':import_schema,
            'safety':{'marketplace_mutations':0,'writes':0}
        },ensure_ascii=False,sort_keys=True)[:180000],flush=True)
    except Exception as e:
        print('PHH_9050_CONTRACT_BUNDLE_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_image_paths_v24():
    if os.getenv('RUN_PHH_IMAGE_PATHS_V24','').strip()!='1': return
    try:
        from phh_image_paths_v24 import run
        run()
    except Exception as e:
        print('PHH_IMAGE_PATHS_V24_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_existence_check_v26():
    if os.getenv('RUN_PHH_EXISTENCE_CHECK_V26','').strip()!='1': return
    try:
        from phh_existence_check_v26 import run
        run()
    except Exception as e:
        print('PHH_EXISTENCE_CHECK_V26_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_audit_ops_v27():
    if os.getenv('RUN_PHH_AUDIT_OPS_V27','').strip()!='1': return
    try:
        from phh_audit_ops_v27 import run
        run()
    except Exception as e:
        print('PHH_AUDIT_OPS_V27_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_barcode_check_schema_v28():
    if os.getenv('RUN_PHH_BARCODE_CHECK_SCHEMA_V28','').strip()!='1': return
    try:
        from phh_barcode_check_schema_v28 import run
        run()
    except Exception as e:
        print('PHH_BARCODE_CHECK_SCHEMA_V28_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_barcode_check_test_v29():
    if os.getenv('RUN_PHH_BARCODE_CHECK_TEST_V29','').strip()!='1': return
    try:
        from phh_barcode_check_test_v29 import run
        run()
    except Exception as e:
        print('PHH_BARCODE_CHECK_TEST_V29_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_master_audit_v30():
    if os.getenv('RUN_PHH_MASTER_AUDIT_V30','').strip()!='1': return
    try:
        from phh_master_audit_v30 import run
        run()
    except Exception as e:
        print('PHH_MASTER_AUDIT_V30_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_offer_identity_export_v31():
    if os.getenv('RUN_PHH_OFFER_IDENTITY_EXPORT_V31','').strip()!='1': return
    try:
        from phh_orphan_offer_export_v31 import run
        out=run()
        offers=out.get('offers') or []
        print('PHH_OFFER_IDENTITY_EXPORT_V31_SUMMARY',json.dumps({k:out.get(k) for k in ('status','seller_id','app_name','offer_count','unique_eans','writes')},sort_keys=True),flush=True)
        chunk=100
        total=(len(offers)+chunk-1)//chunk
        for i in range(total):
            print(f'PHH_OFFER_IDENTITY_EXPORT_V31 {i+1}/{total} '+json.dumps(offers[i*chunk:(i+1)*chunk],ensure_ascii=False,separators=(',',':')),flush=True)
    except Exception as e:
        print('PHH_OFFER_IDENTITY_EXPORT_V31_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_identity_migration_audit_v32():
    if os.getenv('RUN_PHH_IDENTITY_MIGRATION_AUDIT_V32','').strip()!='1': return
    try:
        from phh_identity_migration_audit_v32 import run
        run()
    except Exception as e:
        print('PHH_IDENTITY_MIGRATION_AUDIT_V32_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()


def _maybe_run_phh_stock_publish_v40():
    if os.getenv('RUN_PHH_STOCK_PUBLISH_V40','').strip()!='1': return
    try:
        from phh_stock_publish_v40 import run
        run()
    except Exception as e:
        print('PHH_STOCK_PUBLISH_V40_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_phh_offer_import_status_probe():
    if os.getenv('RUN_PHH_OFFER_IMPORT_STATUS_PROBE','').strip()!='1': return
    try:
        from phh_offer_import_status_probe import run
        run()
    except Exception as e:
        print('PHH_OFFER_IMPORT_STATUS_PROBE_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_phh_offer_import_endpoint_probe():
    if os.getenv('RUN_PHH_OFFER_IMPORT_ENDPOINT_PROBE','').strip()!='1': return
    try:
        from phh_offer_import_endpoint_probe import run
        run()
    except Exception as e:
        print('PHH_OFFER_IMPORT_ENDPOINT_PROBE_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_phh_import_schema_probe():
    if os.getenv('RUN_PHH_IMPORT_SCHEMA_PROBE','').strip()!='1': return
    try:
        from phh_import_schema_probe import run
        run()
    except Exception as e:
        print('PHH_IMPORT_SCHEMA_PROBE_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_phh_import_contract_probe():
    if os.getenv('RUN_PHH_IMPORT_CONTRACT_PROBE','').strip()!='1': return
    try:
        from phh_import_contract_probe import run
        run()
    except Exception as e:
        print('PHH_IMPORT_CONTRACT_PROBE_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_phh_offer_write_contract_probe():
    if os.getenv('RUN_PHH_OFFER_WRITE_CONTRACT_PROBE','').strip()!='1': return
    try:
        from phh_offer_write_contract_probe import run
        run()
    except Exception as e:
        print('PHH_OFFER_WRITE_CONTRACT_PROBE_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_phh_stock_publish_plan_v39():
    if os.getenv('RUN_PHH_STOCK_PUBLISH_PLAN_V39','').strip()!='1': return
    try:
        from phh_stock_publish_plan_v39 import run
        run()
    except Exception as e:
        print('PHH_STOCK_PUBLISH_PLAN_V39_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_phh_stock_diff_v38():
    if os.getenv('RUN_PHH_STOCK_DIFF_V38','').strip()!='1': return
    try:
        from phh_stock_diff_v38 import run
        run()
    except Exception as e:
        print('PHH_STOCK_DIFF_V38_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_phh_stock_feed_readiness_v37():
    if os.getenv('RUN_PHH_STOCK_FEED_READINESS_V37','').strip()!='1': return
    try:
        from phh_stock_feed_readiness_v37 import run
        run()
    except Exception as e:
        print('PHH_STOCK_FEED_READINESS_V37_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_phh_identity_automation_gate_v36():
    if os.getenv('RUN_PHH_IDENTITY_AUTOMATION_GATE_V36','').strip()!='1': return
    try:
        from phh_identity_automation_gate_v36 import run
        run()
    except Exception as e:
        print('PHH_IDENTITY_AUTOMATION_GATE_V36_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_phh_hard_conflict_registry_v35():
    if os.getenv('RUN_PHH_HARD_CONFLICT_REGISTRY_V35','').strip()!='1': return
    try:
        from phh_hard_conflict_registry_v35 import run
        run()
    except Exception as e:
        print('PHH_HARD_CONFLICT_REGISTRY_V35_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_phh_residual_semantic_audit_v34():
    if os.getenv('RUN_PHH_RESIDUAL_SEMANTIC_AUDIT_V34','').strip()!='1': return
    try:
        from phh_residual_semantic_audit_v34 import run
        run()
    except Exception as e:
        print('PHH_RESIDUAL_SEMANTIC_AUDIT_V34_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _maybe_run_phh_card_identity_probe_v33():
    if os.getenv('RUN_PHH_CARD_IDENTITY_PROBE_V33','').strip()!='1': return
    try:
        from phh_card_identity_probe_v33 import run
        run()
    except Exception as e:
        print('PHH_CARD_IDENTITY_PROBE_V33_FAILED',type(e).__name__,str(e),flush=True); traceback.print_exc()

def _boot():
    # Restore snapshots before expensive network calls. Keep the web server responsive.
    print('OUTFISH_SAFE_BOOT_LEGACY_JOBS_DISABLED',flush=True)
    # Verify invariant gates once per worker before any full-catalog processing.
    try:
        import unittest
        suite=unittest.defaultTestLoader.loadTestsFromName('test_catalog_pipeline_gate')
        test_result=unittest.TestResult()
        suite.run(test_result)
        tests_ok=test_result.wasSuccessful() and test_result.testsRun>=9
        print('OUTFISH_CATALOG_GATE_SELFTEST',json.dumps({
              'passed':tests_ok,'tests':test_result.testsRun,
              'failures':len(test_result.failures),'errors':len(test_result.errors)
              },sort_keys=True),flush=True)
        if not tests_ok:
            with FULL_CATALOG_LOCK:
                FULL_CATALOG.update(status='error',error='CATALOG_GATE_SELFTEST_FAILED')
            return
    except Exception as e:
        print('OUTFISH_CATALOG_GATE_SELFTEST_ERROR',type(e).__name__,flush=True)
        return
    restored=_restore_full_catalog()
    _restore_existing_autocheck()
    _restore_latest()
    print('CONTENT_STAGING_ENV',json.dumps({k:bool(os.getenv(k)) for k in
          ('GOOGLE_SERVICE_ACCOUNT_JSON','SHOPIFY_CLIENT_ID','SHOPIFY_CLIENT_SECRET','DATABASE_URL')},
          sort_keys=True),flush=True)
    if os.getenv('RUN_FULL_CATALOG_AUDIT','1').strip()=='1':
        threading.Thread(target=_run_and_persist_catalog,daemon=True).start()
    print('OUTFISH_STAGING_READONLY_READY',json.dumps({'catalog_cache_restored':restored}),flush=True)
# Gunicorn forks workers after importing this module. A thread started at import
# time runs in the parent and its in-memory results are invisible to workers.
# Initialize once per worker on the first request, instead.
_WORKER_BOOT_PID=None
_WORKER_BOOT_LOCK=threading.Lock()

@app.before_request
def _worker_boot_once():
    global _WORKER_BOOT_PID
    pid=os.getpid()
    if _WORKER_BOOT_PID==pid: return
    with _WORKER_BOOT_LOCK:
        if _WORKER_BOOT_PID==pid: return
        _WORKER_BOOT_PID=pid
        threading.Thread(target=_boot,daemon=True,name='outfish-readonly-worker-boot').start()

