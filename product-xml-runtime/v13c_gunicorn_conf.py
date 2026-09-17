from __future__ import annotations
import json, os, threading
import v13b_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V13C_EXISTING_PRODUCT','').strip()=='1':
        server.log.info('V13C_EXISTING_PRODUCT_START')
        def task():
            try:
                from phh_existing_product_probe_v13c import run
                out=run()
                server.log.info('V13C_EXISTING_PRODUCT_COMPLETE %s',json.dumps({'status':out.get('status'),'match_count':out.get('match_count'),'offers_scanned':out.get('offers_scanned'),'readable_paths':list((out.get('readable_paths') or {}).keys())},sort_keys=True))
            except Exception as exc:
                server.log.warning('V13C_EXISTING_PRODUCT_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v13c-existing-product').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
