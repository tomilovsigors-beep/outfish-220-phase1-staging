from __future__ import annotations
import json, os, threading
import v11v_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V11W_PHH_MANUAL_INPUT_WRITER','').strip()=='1':
        server.log.info('V11W_PHH_MANUAL_INPUT_WRITER_START')
        def task():
            try:
                from phh_manual_input_sheet_writer_v11w import run
                out=run()
                server.log.info('V11W_PHH_MANUAL_INPUT_WRITER_COMPLETE %s',json.dumps({'status':'PASS','categories':len(out)},sort_keys=True))
            except Exception as exc:
                server.log.warning('V11W_PHH_MANUAL_INPUT_WRITER_FAILED %s %s',type(exc).__name__,str(exc)[:3000])
        threading.Thread(target=task,daemon=True,name='v11w-phh-manual-input-writer').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
