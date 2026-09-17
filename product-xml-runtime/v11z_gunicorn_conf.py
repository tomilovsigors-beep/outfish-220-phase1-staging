from __future__ import annotations
import json, os, threading
import v11y_gunicorn_conf as base

def _register(server):
    try:
        from flask import Response
        from content_staging_app import app
        from phh_manual_input_preflight_v11z import run, to_csv
        def js():
            try:
                out=run(False)
                return Response(json.dumps(out,ensure_ascii=False,indent=2),status=200 if out['summary']['status']=='PASS' else 409,mimetype='application/json',headers={'Cache-Control':'no-store'})
            except Exception as exc:
                return Response(json.dumps({'status':'ERROR','error':f'{type(exc).__name__}: {exc}'}),status=503,mimetype='application/json')
        def cs():
            try:
                out=run(False)
                if out['summary']['status']!='PASS':
                    return Response(json.dumps(out['summary'],ensure_ascii=False),status=409,mimetype='application/json')
                return Response(to_csv(out),status=200,mimetype='text/csv',headers={'Cache-Control':'no-store'})
            except Exception as exc:
                return Response(json.dumps({'status':'ERROR','error':f'{type(exc).__name__}: {exc}'}),status=503,mimetype='application/json')
        if 'v11z_manual_preflight_json' not in app.view_functions: app.add_url_rule('/v11/manual-input-preflight.json','v11z_manual_preflight_json',js,methods=['GET'])
        if 'v11z_manual_preflight_csv' not in app.view_functions: app.add_url_rule('/v11/manual-input-preflight.csv','v11z_manual_preflight_csv',cs,methods=['GET'])
        server.log.info('V11Z_MANUAL_PREFLIGHT_ROUTES_READY')
    except Exception as exc:
        server.log.warning('V11Z_MANUAL_PREFLIGHT_ROUTES_FAILED %s %s',type(exc).__name__,str(exc)[:3000])

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    _register(server)
    if os.getenv('RUN_V11Z_MANUAL_INPUT_PREFLIGHT','').strip()=='1':
        server.log.info('V11Z_MANUAL_INPUT_PREFLIGHT_START')
        def task():
            try:
                from phh_manual_input_preflight_v11z import run
                out=run(True)
                server.log.info('V11Z_MANUAL_INPUT_PREFLIGHT_COMPLETE %s',json.dumps(out['summary'],sort_keys=True))
            except Exception as exc:
                server.log.warning('V11Z_MANUAL_INPUT_PREFLIGHT_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v11z-manual-input-preflight').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
