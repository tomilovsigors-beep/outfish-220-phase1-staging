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
    if os.getenv('RUN_V8_TARGETED_LEAF_PROBE','').strip()!='1':
        return
    def _run():
        try:
            from app import _master_rows, _shopify_products, _shopify_token
            from current_product_category_audit_v8 import run_audit
            master=_master_rows(); shopify=_shopify_products(master)
            token=_shopify_token(); domain=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
            summary,_=run_audit(master,shopify,os.getenv('DATABASE_URL'),token=token,shop_domain=domain)
            server.log.info('V8_FINAL_AUDIT_COMPLETE %s',json.dumps(summary,sort_keys=True))
        except Exception as exc:
            server.log.warning('V8_FINAL_AUDIT_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
    threading.Thread(target=_run,daemon=True,name='v8-final-audit').start()
