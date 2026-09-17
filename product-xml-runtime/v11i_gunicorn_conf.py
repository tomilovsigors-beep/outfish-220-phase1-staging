from __future__ import annotations
import json,os,runpy,threading
_BASE=runpy.run_path('v11h_gunicorn_conf.py'); _s=_BASE.get('on_starting'); _r=_BASE.get('when_ready')
def on_starting(server):
    if _s:_s(server)
def when_ready(server):
    if os.getenv('RUN_V11I_PMP_PRODUCT_ATTRIBUTE_CONTRACT','').strip()=='1':
        server.log.info('V11I_PMP_PRODUCT_ATTRIBUTE_CONTRACT_HOOK_START')
        def f():
            try:
                from pmp_product_attribute_contract_v11i import run
                o=run(); server.log.info('V11I_PMP_PRODUCT_ATTRIBUTE_CONTRACT_COMPLETE %s',json.dumps(o.get('summary'),sort_keys=True))
            except Exception as e: server.log.warning('V11I_PMP_PRODUCT_ATTRIBUTE_CONTRACT_FAILED %s %s',type(e).__name__,str(e)[:6000])
        threading.Thread(target=f,daemon=True).start()
    if _r:_r(server)
