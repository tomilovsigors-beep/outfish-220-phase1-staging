from __future__ import annotations
import json, os, threading
import v12b_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V12C_SAFE_AUTOFILL_DISCOVERY','').strip()=='1':
        server.log.info('V12C_SAFE_AUTOFILL_DISCOVERY_START')
        def task():
            try:
                from phh_safe_autofill_discovery_v12c import run
                out=run()
                server.log.info('V12C_SAFE_AUTOFILL_DISCOVERY_COMPLETE %s',json.dumps(out.get('summary',{}),sort_keys=True))
            except Exception as exc:
                server.log.warning('V12C_SAFE_AUTOFILL_DISCOVERY_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v12c-safe-autofill-discovery').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
