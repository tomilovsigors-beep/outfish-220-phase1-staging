from __future__ import annotations
import json, os, threading
import v13e_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V13F_PRODUCT_READ_PATHS','').strip()=='1':
        server.log.info('V13F_PRODUCT_READ_PATHS_START')
        def task():
            try:
                from phh_product_read_paths_v13f import run
                out=run()
                server.log.info('V13F_PRODUCT_READ_PATHS_COMPLETE %s',json.dumps({'path_count':len(out)},sort_keys=True))
            except Exception as exc:
                server.log.warning('V13F_PRODUCT_READ_PATHS_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v13f-product-read-paths').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
