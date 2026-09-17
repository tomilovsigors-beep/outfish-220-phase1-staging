from __future__ import annotations
import json, os, threading
import v13f_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V13G_IMPORT_TRANSPORT_SMOKE','').strip()=='1':
        server.log.info('V13G_IMPORT_TRANSPORT_SMOKE_START')
        def task():
            try:
                from phh_product_import_transport_smoke_v13g import run
                out=run()
                server.log.info('V13G_IMPORT_TRANSPORT_SMOKE_COMPLETE %s',json.dumps(out,sort_keys=True))
            except Exception as exc:
                server.log.warning('V13G_IMPORT_TRANSPORT_SMOKE_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v13g-import-transport-smoke').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
