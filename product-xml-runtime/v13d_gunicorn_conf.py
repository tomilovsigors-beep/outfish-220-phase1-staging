from __future__ import annotations
import json, os, threading
import v13c_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V13D_SMOKE_CANDIDATE_DETAIL','').strip()=='1':
        server.log.info('V13D_SMOKE_CANDIDATE_DETAIL_START')
        def task():
            try:
                from phh_smoke_candidate_detail_v13d import run
                out=run()
                server.log.info('V13D_SMOKE_CANDIDATE_DETAIL_COMPLETE %s',json.dumps({'status':out.get('status'),'offer_id':out.get('offer_id')},sort_keys=True))
            except Exception as exc:
                server.log.warning('V13D_SMOKE_CANDIDATE_DETAIL_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v13d-smoke-candidate-detail').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
