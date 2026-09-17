from __future__ import annotations

import json, os, runpy, threading

_BASE=runpy.run_path('v9_gunicorn_conf.py')
_base_on_starting=_BASE.get('on_starting')
_base_when_ready=_BASE.get('when_ready')


def _register_v10_routes(server):
    try:
        from flask import Response
        from content_staging_app import app
        from current_product_category_audit_v10 import load_latest_artifact
        routes={
          '/v10/category_rule_library_v10.csv':('category_rule_library_v10.csv','text/csv'),
          '/v10/v10-family-adjudication.csv':('v10-family-adjudication.csv','text/csv'),
          '/v10/v10-product-category-mapping.csv':('v10-product-category-mapping.csv','text/csv'),
          '/v10/v10-status-transitions.csv':('v10-status-transitions.csv','text/csv'),
          '/v10/v10-category-coverage-summary.json':('v10-category-coverage-summary.json','application/json'),
          '/v10/v10-rule-audit.json':('v10-rule-audit.json','application/json'),
        }
        for idx,(path,(name,mime)) in enumerate(routes.items()):
            endpoint=f'v10_artifact_{idx}'
            def handler(_name=name,_mime=mime):
                b=load_latest_artifact(os.getenv('DATABASE_URL'),_name)
                if not b:
                    return Response(json.dumps({'error':'v10 artifact unavailable'}),status=503,mimetype='application/json')
                return Response(b,status=200,mimetype=_mime,headers={'Cache-Control':'no-store'})
            if endpoint not in app.view_functions:
                app.add_url_rule(path,endpoint,handler,methods=['GET'])
        server.log.info('V10_ARTIFACT_ROUTES_READY %s',json.dumps(sorted(routes),sort_keys=True))
    except Exception as exc:
        server.log.warning('V10_ARTIFACT_ROUTES_FAILED %s %s',type(exc).__name__,str(exc)[:3000])


def on_starting(server):
    if _base_on_starting:
        _base_on_starting(server)


def when_ready(server):
    _register_v10_routes(server)
    if os.getenv('RUN_V10_CATEGORY_AUDIT','').strip()=='1':
        server.log.info('V10_CATEGORY_AUDIT_HOOK_START')
        def _audit():
            try:
                from app import _master_rows, _shopify_products
                from current_product_category_audit_v10 import run_audit
                master=_master_rows(); shopify=_shopify_products(master)
                summary,_=run_audit(master,shopify,os.getenv('DATABASE_URL'))
                server.log.info('V10_CATEGORY_AUDIT_COMPLETE %s',json.dumps(summary,sort_keys=True))
            except Exception as exc:
                server.log.warning('V10_CATEGORY_AUDIT_FAILED %s %s',type(exc).__name__,str(exc)[:6000])
        threading.Thread(target=_audit,daemon=True,name='v10-category-audit').start()
    elif os.getenv('RUN_V10_LEAF_PROBE','').strip()=='1':
        server.log.info('V10_LEAF_PROBE_HOOK_START')
        def _probe():
            try:
                from current_product_leaf_adjudication_v10_probe import run
                summary,_=run(os.getenv('DATABASE_URL'))
                server.log.info('V10_LEAF_PROBE_COMPLETE %s',json.dumps(summary,sort_keys=True))
            except Exception as exc:
                server.log.warning('V10_LEAF_PROBE_FAILED %s %s',type(exc).__name__,str(exc)[:5000])
        threading.Thread(target=_probe,daemon=True,name='v10-leaf-probe').start()
    if _base_when_ready:
        _base_when_ready(server)
