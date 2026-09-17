from __future__ import annotations
import json, os, threading
import v13d_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V13E_LIVE_WRITE','').strip()=='1':
        server.log.info('V13E_LIVE_WRITE_START')
        def task():
            try:
                from phh_live_smoke_write_v13e import run
                out=run()
                server.log.info('V13E_LIVE_WRITE_COMPLETE %s',json.dumps(out,sort_keys=True))
            except Exception as exc:
                server.log.warning('V13E_LIVE_WRITE_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v13e-live-write').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
