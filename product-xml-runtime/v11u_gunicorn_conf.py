from __future__ import annotations
import json, os, threading
import v11_attribute_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V11U_CATEGORY_SHEETS','').strip()=='1':
        server.log.info('V11U_CATEGORY_SHEETS_HOOK_START')
        def task():
            try:
                from phh_category_sheet_batches_v11u import run
                out=run()
                server.log.info('V11U_CATEGORY_SHEETS_COMPLETE %s',json.dumps(out,sort_keys=True))
            except Exception as exc:
                server.log.warning('V11U_CATEGORY_SHEETS_FAILED %s %s',type(exc).__name__,str(exc)[:5000])
        threading.Thread(target=task,daemon=True,name='v11u-category-sheets').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
