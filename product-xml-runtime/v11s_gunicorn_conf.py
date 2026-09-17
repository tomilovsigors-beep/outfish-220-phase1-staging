from __future__ import annotations
import json, os, threading
import v11_attribute_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V11S_MANUAL_INPUT_BATCHES','').strip()=='1':
        server.log.info('V11S_MANUAL_INPUT_BATCHES_HOOK_START')
        def task():
            try:
                from phh_manual_input_batches_v11s import run
                out=run()
                server.log.info('V11S_MANUAL_INPUT_BATCHES_COMPLETE %s',json.dumps(out,sort_keys=True))
            except Exception as exc:
                server.log.warning('V11S_MANUAL_INPUT_BATCHES_FAILED %s %s',type(exc).__name__,str(exc)[:5000])
        threading.Thread(target=task,daemon=True,name='v11s-manual-input-batches').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
