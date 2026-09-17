from __future__ import annotations
import json, os, threading
import v12e_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V12F_FAMILY_DEFAULTS','').strip()=='1':
        server.log.info('V12F_FAMILY_DEFAULTS_START')
        def task():
            try:
                from phh_family_manual_defaults_v12f import run
                out=run()
                server.log.info('V12F_FAMILY_DEFAULTS_COMPLETE %s',json.dumps(out,sort_keys=True))
            except Exception as exc:
                server.log.warning('V12F_FAMILY_DEFAULTS_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v12f-family-defaults').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
