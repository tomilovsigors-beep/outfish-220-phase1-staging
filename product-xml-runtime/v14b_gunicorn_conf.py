from __future__ import annotations
import json, os, threading
import v14_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V14B_UNIT_ECONOMICS','').strip()=='1':
        server.log.info('V14B_UNIT_ECONOMICS_START')
        def task():
            try:
                from phh_unit_economics_probe_v14b import run
                out=run()
                server.log.info('V14B_UNIT_ECONOMICS_COMPLETE %s',json.dumps({'status':out.get('status'),'order_probes':{k:{'status':v.get('status'),'count':v.get('count')} for k,v in (out.get('order_probes') or {}).items()},'return_probes':{k:v.get('status') for k,v in (out.get('return_probes') or {}).items()}},sort_keys=True))
            except Exception as exc:
                server.log.warning('V14B_UNIT_ECONOMICS_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v14b-unit-economics').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
