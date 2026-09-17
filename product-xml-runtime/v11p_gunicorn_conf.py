from __future__ import annotations
import json, os, threading
import v11_attribute_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'):
        base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V11P_MANUAL_INPUT_MAP','').strip()=='1':
        server.log.info('V11P_MANUAL_INPUT_MAP_HOOK_START')
        def task():
            try:
                from phh_manual_input_map_v11p import run
                out=run()
                server.log.info('V11P_MANUAL_INPUT_MAP_COMPLETE %s',json.dumps({'status':out.get('status'),'count':out.get('count')},sort_keys=True))
            except Exception as exc:
                server.log.warning('V11P_MANUAL_INPUT_MAP_FAILED %s %s',type(exc).__name__,str(exc)[:5000])
        threading.Thread(target=task,daemon=True,name='v11p-manual-input-map').start()
    if hasattr(base,'when_ready'):
        base.when_ready(server)
