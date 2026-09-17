from __future__ import annotations
import json, os, runpy, threading
_BASE=runpy.run_path('v11f_gunicorn_conf.py')
_base_on_starting=_BASE.get('on_starting')
_base_when_ready=_BASE.get('when_ready')

def on_starting(server):
    if _base_on_starting: _base_on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V11G_PMP_SEMANTIC_GET_INVENTORY','').strip()=='1':
        server.log.info('V11G_PMP_SEMANTIC_GET_INVENTORY_HOOK_START')
        def _run():
            try:
                from pmp_semantic_get_inventory_v11g import run
                out=run()
                server.log.info('V11G_PMP_SEMANTIC_GET_INVENTORY_COMPLETE %s',json.dumps({'status':out.get('status'),'get_count':out.get('get_count')},sort_keys=True))
            except Exception as exc:
                server.log.warning('V11G_PMP_SEMANTIC_GET_INVENTORY_FAILED %s %s',type(exc).__name__,str(exc)[:6000])
        threading.Thread(target=_run,daemon=True,name='v11g-pmp-semantic-get-inventory').start()
    if _base_when_ready: _base_when_ready(server)
