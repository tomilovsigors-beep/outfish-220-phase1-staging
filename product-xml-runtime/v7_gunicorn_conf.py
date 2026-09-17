from __future__ import annotations

import json, os, runpy, threading

_BASE=runpy.run_path('gunicorn.conf.py')
_base_on_starting=_BASE.get('on_starting')
_base_when_ready=_BASE.get('when_ready')


def on_starting(server):
    if _base_on_starting:
        _base_on_starting(server)


def when_ready(server):
    if _base_when_ready:
        _base_when_ready(server)
    if os.getenv('RUN_V7_TARGETED_LEAF_PROBE','').strip()!='1':
        return
    def _run():
        try:
            from app import _master_rows, _shopify_products
            from current_product_category_audit_v7 import run_audit
            master=_master_rows(); shopify=_shopify_products(master)
            summary,_=run_audit(master,shopify,os.getenv('DATABASE_URL'))
            server.log.info('V7_FINAL_AUDIT_COMPLETE %s',json.dumps(summary,sort_keys=True))
        except Exception as exc:
            server.log.warning('V7_FINAL_AUDIT_FAILED %s %s',type(exc).__name__,str(exc)[:2000])
    threading.Thread(target=_run,daemon=True,name='v7-final-audit').start()
