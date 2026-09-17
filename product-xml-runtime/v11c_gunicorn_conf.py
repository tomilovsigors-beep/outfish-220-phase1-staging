from __future__ import annotations
import json, os, runpy, threading
_BASE=runpy.run_path('v11b_gunicorn_conf.py')
_base_on_starting=_BASE.get('on_starting')
_base_when_ready=_BASE.get('when_ready')

def on_starting(server):
    if _base_on_starting: _base_on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V11C_SELLER_ACADEMY_PROBE','').strip()=='1':
        server.log.info('V11C_SELLER_ACADEMY_PROBE_HOOK_START')
        def _run():
            try:
                from pmp_seller_academy_probe_v11c import run
                summary,_=run()
                server.log.info('V11C_SELLER_ACADEMY_PROBE_COMPLETE %s',json.dumps(summary,sort_keys=True))
            except Exception as exc:
                server.log.warning('V11C_SELLER_ACADEMY_PROBE_FAILED %s %s',type(exc).__name__,str(exc)[:6000])
        threading.Thread(target=_run,daemon=True,name='v11c-seller-academy-probe').start()
    if _base_when_ready: _base_when_ready(server)
