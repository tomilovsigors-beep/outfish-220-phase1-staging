from __future__ import annotations
import json, os, threading
import v13g_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V14_FEE_LOGISTICS_DISCOVERY','').strip()=='1':
        server.log.info('V14_FEE_LOGISTICS_DISCOVERY_START')
        def task():
            try:
                from phh_fee_logistics_discovery_v14 import run
                out=run()
                server.log.info('V14_FEE_LOGISTICS_DISCOVERY_COMPLETE %s',json.dumps({'status':out.get('status'),'matched_paths':list((out.get('matched_paths') or {}).keys()),'read_probes':out.get('read_probes')},sort_keys=True))
            except Exception as exc:
                server.log.warning('V14_FEE_LOGISTICS_DISCOVERY_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v14-fee-logistics-discovery').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
