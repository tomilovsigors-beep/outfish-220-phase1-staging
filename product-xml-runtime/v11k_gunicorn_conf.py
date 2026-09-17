from __future__ import annotations
import json,os,runpy,threading
_BASE=runpy.run_path('v11j_gunicorn_conf.py'); _s=_BASE.get('on_starting'); _r=_BASE.get('when_ready')
def on_starting(server):
    if _s:_s(server)
def when_ready(server):
    if os.getenv('RUN_V11K_PMP_IMPORT_HISTORY_MINING','').strip()=='1':
        server.log.info('V11K_PMP_IMPORT_HISTORY_MINING_HOOK_START')
        def f():
            try:
                from pmp_import_history_mining_v11k import run
                o=run(); server.log.info('V11K_PMP_IMPORT_HISTORY_MINING_COMPLETE %s',json.dumps({'status':o.get('status'),'seller_id_found':o.get('seller_id_found'),'executions_status':o.get('executions_status'),'result_calls':len(o.get('results') or [])},sort_keys=True))
            except Exception as e: server.log.warning('V11K_PMP_IMPORT_HISTORY_MINING_FAILED %s %s',type(e).__name__,str(e)[:6000])
        threading.Thread(target=f,daemon=True).start()
    if _r:_r(server)
