from __future__ import annotations
import json, os, threading
import v12f_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V13A_LIVE_SMOKE_PREP','').strip()=='1':
        server.log.info('V13A_LIVE_SMOKE_PREP_START')
        def task():
            try:
                from phh_live_smoke_prep_v13a import run
                out=run()
                server.log.info('V13A_LIVE_SMOKE_PREP_COMPLETE %s',json.dumps({'status':out.get('status'),'path_count':out.get('path_count'),'schema_count':out.get('schema_count')},sort_keys=True))
            except Exception as exc:
                server.log.warning('V13A_LIVE_SMOKE_PREP_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v13a-live-smoke-prep').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
