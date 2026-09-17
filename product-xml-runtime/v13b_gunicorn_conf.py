from __future__ import annotations
import json, os, threading
import v13a_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V13B_SMOKE_CONTRACT','').strip()=='1':
        server.log.info('V13B_SMOKE_CONTRACT_START')
        def task():
            try:
                from phh_smoke_contract_probe_v13b import run
                out=run()
                server.log.info('V13B_SMOKE_CONTRACT_COMPLETE %s',json.dumps({'status':out.get('status'),'candidate_count':len((out.get('offers') or {}).get('candidates') or []),'import_paths':list((out.get('import_paths') or {}).keys())},sort_keys=True))
            except Exception as exc:
                server.log.warning('V13B_SMOKE_CONTRACT_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v13b-smoke-contract').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
