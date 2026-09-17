from __future__ import annotations
import json, os, threading
import v14b_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V14C_ECONOMICS_AGGREGATE','').strip()=='1':
        server.log.info('V14C_ECONOMICS_AGGREGATE_START')
        def task():
            try:
                from phh_economics_aggregate_v14c import run
                out=run()
                server.log.info('V14C_ECONOMICS_AGGREGATE_COMPLETE %s',json.dumps({
                  'status':out.get('status'),'orders_products_scanned':out.get('orders_products_scanned'),
                  'commission_rate_counts':out.get('commission_rate_counts'),
                  'commission_rate_ex_vat_counts':out.get('commission_rate_ex_vat_counts'),
                  'seller_invoices_count':out.get('seller_invoices_count'),
                  'returns_count':(out.get('returns') or {}).get('count')
                },sort_keys=True))
            except Exception as exc:
                server.log.warning('V14C_ECONOMICS_AGGREGATE_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v14c-economics-aggregate').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
