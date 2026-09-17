from __future__ import annotations

import json, os, runpy, threading

_BASE=runpy.run_path('gunicorn.conf.py')
_base_on_starting=_BASE.get('on_starting')
_base_when_ready=_BASE.get('when_ready')


def _register_v8_routes(server):
    try:
        from flask import Response
        from content_staging_app import app
        from current_product_category_audit_v8 import load_latest_artifact
        routes={
            '/v8/category_rule_library_v1.csv':('category_rule_library_v1.csv','text/csv'),
            '/v8/v8-family-priority-queue.csv':('v8-family-priority-queue.csv','text/csv'),
            '/v8/v8-family-adjudication.csv':('v8-family-adjudication.csv','text/csv'),
            '/v8/v8-product-category-mapping.csv':('v8-product-category-mapping.csv','text/csv'),
            '/v8/v8-category-coverage-summary.json':('v8-category-coverage-summary.json','application/json'),
            '/v8/v8-identity-audit.json':('v8-identity-audit.json','application/json'),
            '/v8/v8-rule-audit.json':('v8-rule-audit.json','application/json'),
        }
        for idx,(path,(name,mime)) in enumerate(routes.items()):
            endpoint=f'v8_artifact_{idx}'
            def handler(_name=name,_mime=mime):
                b=load_latest_artifact(os.getenv('DATABASE_URL'),_name)
                if not b:
                    return Response(json.dumps({'error':'v8 artifact unavailable'}),status=503,mimetype='application/json')
                return Response(b,status=200,mimetype=_mime,headers={'Cache-Control':'no-store'})
            if endpoint not in app.view_functions:
                app.add_url_rule(path,endpoint,handler,methods=['GET'])
        server.log.info('V8_ARTIFACT_ROUTES_READY %s',json.dumps(sorted(routes),sort_keys=True))
    except Exception as exc:
        server.log.warning('V8_ARTIFACT_ROUTES_FAILED %s %s',type(exc).__name__,str(exc)[:2000])


def on_starting(server):
    if _base_on_starting:
        _base_on_starting(server)


def when_ready(server):
    _register_v8_routes(server)
    if _base_when_ready:
        _base_when_ready(server)
    if os.getenv('RUN_V8_TARGETED_LEAF_PROBE','').strip()!='1':
        return
    def _run():
        try:
            from app import _master_rows, _shopify_products, _shopify_token
            from current_product_category_audit_v8 import run_audit
            master=_master_rows(); shopify=_shopify_products(master)
            token=_shopify_token(); domain=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
            summary,_=run_audit(master,shopify,os.getenv('DATABASE_URL'),token=token,shop_domain=domain)
            server.log.info('V8_FINAL_AUDIT_COMPLETE %s',json.dumps(summary,sort_keys=True))
        except Exception as exc:
            server.log.warning('V8_FINAL_AUDIT_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
    threading.Thread(target=_run,daemon=True,name='v8-final-audit').start()
