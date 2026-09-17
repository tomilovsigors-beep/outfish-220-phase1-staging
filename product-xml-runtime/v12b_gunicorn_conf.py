from __future__ import annotations
import json, os, threading
import v12a_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V12B_CATEGORY_REVIEW_QUARANTINE','').strip()=='1':
        server.log.info('V12B_CATEGORY_REVIEW_QUARANTINE_START')
        def task():
            try:
                from phh_category_review_quarantine_v12b import run
                out=run()
                server.log.info('V12B_CATEGORY_REVIEW_QUARANTINE_COMPLETE %s',json.dumps(out,sort_keys=True))
            except Exception as exc:
                server.log.warning('V12B_CATEGORY_REVIEW_QUARANTINE_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v12b-category-review-quarantine').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
