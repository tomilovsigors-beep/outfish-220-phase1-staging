from __future__ import annotations
import json, os, threading
import v11_attribute_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V11R_MANUAL_INPUT_MATERIALIZE','').strip()=='1':
        server.log.info('V11R_MANUAL_INPUT_MATERIALIZE_HOOK_START')
        def task():
            try:
                from phh_manual_input_materialize_v11r import run
                out=run()
                server.log.info('V11R_MANUAL_INPUT_MATERIALIZE_COMPLETE %s',json.dumps(out,sort_keys=True))
            except Exception as exc:
                server.log.warning('V11R_MANUAL_INPUT_MATERIALIZE_FAILED %s %s',type(exc).__name__,str(exc)[:5000])
        threading.Thread(target=task,daemon=True,name='v11r-manual-input-materialize').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
