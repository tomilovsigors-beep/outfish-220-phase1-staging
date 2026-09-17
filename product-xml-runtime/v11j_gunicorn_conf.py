from __future__ import annotations
import json,os,runpy,threading
_BASE=runpy.run_path('v11i_gunicorn_conf.py'); _s=_BASE.get('on_starting'); _r=_BASE.get('when_ready')
def on_starting(server):
    if _s:_s(server)
def when_ready(server):
    if os.getenv('RUN_V11J_PMP_ATTRIBUTE_CONTRACT_DIGEST','').strip()=='1':
        server.log.info('V11J_PMP_ATTRIBUTE_CONTRACT_DIGEST_HOOK_START')
        def f():
            try:
                from pmp_attribute_contract_digest_v11j import run
                o=run(); server.log.info('V11J_PMP_ATTRIBUTE_CONTRACT_DIGEST_COMPLETE %s',json.dumps({'status':o.get('status'),'validator_status':(o.get('validator') or {}).get('status'),'schema_names':sorted((o.get('selected_schemas') or {}).keys())},sort_keys=True))
            except Exception as e: server.log.warning('V11J_PMP_ATTRIBUTE_CONTRACT_DIGEST_FAILED %s %s',type(e).__name__,str(e)[:6000])
        threading.Thread(target=f,daemon=True).start()
    if _r:_r(server)
