from __future__ import annotations
import json, os, threading
import v11z_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V12A_CATEGORY_VERIFY','').strip()=='1':
        server.log.info('V12A_CATEGORY_VERIFY_START')
        def task():
            try:
                from phh_category_assignment_verify_v12a import run
                out=run()
                server.log.info('V12A_CATEGORY_VERIFY_COMPLETE %s',json.dumps(out['summary'],sort_keys=True))
            except Exception as exc:
                server.log.warning('V12A_CATEGORY_VERIFY_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v12a-category-verify').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
