from __future__ import annotations

import json, os, runpy, threading

_BASE=runpy.run_path('v10_gunicorn_conf.py')
_base_on_starting=_BASE.get('on_starting')
_base_when_ready=_BASE.get('when_ready')


def _register_v11_routes(server):
    try:
        from flask import Response
        from content_staging_app import app
        from current_product_attribute_audit_v11 import load_latest_artifact
        routes={
          '/v11/phh_attribute_contract_registry.csv':('phh_attribute_contract_registry.csv','text/csv'),
          '/v11/phh_attribute_allowed_values.csv':('phh_attribute_allowed_values.csv','text/csv'),
          '/v11/v11-phh-attribute-schema-drift.csv':('v11-phh-attribute-schema-drift.csv','text/csv'),
          '/v11/attribute_resolution_rules.csv':('attribute_resolution_rules.csv','text/csv'),
          '/v11/v11-product-required-attributes.csv':('v11-product-required-attributes.csv','text/csv'),
          '/v11/v11-required-attribute-coverage.csv':('v11-required-attribute-coverage.csv','text/csv'),
          '/v11/v11-product-attribute-readiness.csv':('v11-product-attribute-readiness.csv','text/csv'),
          '/v11/v11-attribute-exceptions.csv':('v11-attribute-exceptions.csv','text/csv'),
          '/v11/v11-attribute-summary.json':('v11-attribute-summary.json','application/json'),
          '/v11/v11-rule-audit.json':('v11-rule-audit.json','application/json'),
        }
        for idx,(path,(name,mime)) in enumerate(routes.items()):
            endpoint=f'v11_artifact_{idx}'
            def handler(_name=name,_mime=mime):
                b=load_latest_artifact(os.getenv('DATABASE_URL'),_name)
                if not b:
                    return Response(json.dumps({'error':'v11 artifact unavailable'}),status=503,mimetype='application/json')
                return Response(b,status=200,mimetype=_mime,headers={'Cache-Control':'no-store'})
            if endpoint not in app.view_functions:
                app.add_url_rule(path,endpoint,handler,methods=['GET'])
        server.log.info('V11_ARTIFACT_ROUTES_READY %s',json.dumps(sorted(routes),sort_keys=True))
    except Exception as exc:
        server.log.warning('V11_ARTIFACT_ROUTES_FAILED %s %s',type(exc).__name__,str(exc)[:3000])


def on_starting(server):
    if _base_on_starting:
        _base_on_starting(server)


def when_ready(server):
    _register_v11_routes(server)
    if os.getenv('RUN_V11_ATTRIBUTE_AUDIT','').strip()=='1':
        server.log.info('V11_ATTRIBUTE_AUDIT_HOOK_START')
        def _audit():
            try:
                from app import _master_rows, _shopify_products
                from current_product_attribute_audit_v11 import run_audit
                master=_master_rows(); shopify=_shopify_products(master)
                summary,_=run_audit(master,shopify,os.getenv('DATABASE_URL'))
                server.log.info('V11_ATTRIBUTE_AUDIT_COMPLETE %s',json.dumps(summary,sort_keys=True))
            except Exception as exc:
                server.log.warning('V11_ATTRIBUTE_AUDIT_FAILED %s %s',type(exc).__name__,str(exc)[:6000])
        threading.Thread(target=_audit,daemon=True,name='v11-attribute-audit').start()
    if _base_when_ready:
        _base_when_ready(server)
