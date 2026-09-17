from __future__ import annotations

import json, os, runpy, threading

_BASE=runpy.run_path('gunicorn.conf.py')
_base_on_starting=_BASE.get('on_starting')
_base_when_ready=_BASE.get('when_ready')


def on_starting(server):
    if _base_on_starting:
        _base_on_starting(server)


def _register_v9_routes(server):
    try:
        from flask import Response
        from content_staging_app import app
        from current_product_identity_audit_v9 import load_latest_artifact
        routes={
            '/v9/v9-family-identity-priority.csv':('v9-family-identity-priority.csv','text/csv'),
            '/v9/v9-family-identity-audit.csv':('v9-family-identity-audit.csv','text/csv'),
            '/v9/v9-family-evidence-gaps.csv':('v9-family-evidence-gaps.csv','text/csv'),
            '/v9/identity_migration_evidence.csv':('identity_migration_evidence.csv','text/csv'),
            '/v9/v9-family-split-recommendations.csv':('v9-family-split-recommendations.csv','text/csv'),
            '/v9/v9-identity-summary.json':('v9-identity-summary.json','application/json'),
            '/v9/v9-leaf-ready-queue.csv':('v9-leaf-ready-queue.csv','text/csv'),
        }
        for idx,(path,(name,mime)) in enumerate(routes.items()):
            endpoint=f'v9_artifact_{idx}'
            def handler(_name=name,_mime=mime):
                b=load_latest_artifact(os.getenv('DATABASE_URL'),_name)
                if not b:
                    return Response(json.dumps({'error':'v9 artifact unavailable'}),status=503,mimetype='application/json')
                return Response(b,status=200,mimetype=_mime,headers={'Cache-Control':'no-store'})
            if endpoint not in app.view_functions:
                app.add_url_rule(path,endpoint,handler,methods=['GET'])
        server.log.info('V9_ARTIFACT_ROUTES_READY %s',json.dumps(sorted(routes),sort_keys=True))
    except Exception as exc:
        server.log.warning('V9_ARTIFACT_ROUTES_FAILED %s %s',type(exc).__name__,str(exc)[:2000])


def _install_batched_exact_lookup(mod, server):
    def _lookup_skus(token, domain, skus):
        out={}; ambiguous={}; total=len(skus)
        node_fields='''id sku barcode title selectedOptions{name value} product{id title vendor productType tags status category{name fullName}}'''
        for start in range(0,total,10):
            chunk=skus[start:start+10]
            vardefs=','.join(f'$q{i}:String!' for i in range(len(chunk)))
            body=' '.join(f'a{i}:productVariants(first:20,query:$q{i}){{nodes{{{node_fields}}}}}' for i in range(len(chunk)))
            query=f'query V9Batch({vardefs}){{{body}}}'
            variables={f'q{i}':f'sku:{sku}' for i,sku in enumerate(chunk)}
            data=mod._graphql(token,domain,query,variables)
            for i,sku in enumerate(chunk):
                nodes=(data.get(f'a{i}') or {}).get('nodes') or []
                exact=[n for n in nodes if mod.v3._norm(n.get('sku'))==sku]
                if len(exact)==1: out[sku]=exact[0]
                elif len(exact)>1: ambiguous[sku]=exact
            server.log.info('V9_SKU_LOOKUP_PROGRESS %s/%s exact=%s ambiguous=%s',min(start+len(chunk),total),total,len(out),len(ambiguous))
        return out,ambiguous
    mod._lookup_skus=_lookup_skus


def when_ready(server):
    _register_v9_routes(server)
    run_v9=os.getenv('RUN_V9_IDENTITY_AUDIT','').strip()=='1'
    if run_v9:
        server.log.info('V9_IDENTITY_AUDIT_HOOK_START')
        def _run():
            try:
                from app import _master_rows, _shopify_token
                import current_product_identity_audit_v9 as v9
                _install_batched_exact_lookup(v9,server)
                master=_master_rows(); token=_shopify_token()
                domain=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
                summary,_=v9.run_audit(master,os.getenv('DATABASE_URL'),token,domain,top_n=30)
                server.log.info('V9_IDENTITY_AUDIT_COMPLETE %s',json.dumps(summary,sort_keys=True))
            except Exception as exc:
                server.log.warning('V9_IDENTITY_AUDIT_FAILED %s %s',type(exc).__name__,str(exc)[:5000])
        threading.Thread(target=_run,daemon=True,name='v9-identity-audit').start()
    if _base_when_ready:
        _base_when_ready(server)
