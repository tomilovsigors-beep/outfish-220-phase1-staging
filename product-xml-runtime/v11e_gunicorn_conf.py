from __future__ import annotations
import json, os, runpy, threading
_BASE=runpy.run_path('v11d_gunicorn_conf.py')
_base_on_starting=_BASE.get('on_starting')
_base_when_ready=_BASE.get('when_ready')

def on_starting(server):
    if _base_on_starting: _base_on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V11E_PMP_PORTAL_LOGIN_STRUCTURE','').strip()=='1':
        server.log.info('V11E_PMP_PORTAL_LOGIN_STRUCTURE_HOOK_START')
        def _run():
            try:
                from pmp_portal_login_structure_probe_v11e import run
                summary=run()
                server.log.info('V11E_PMP_PORTAL_LOGIN_STRUCTURE_COMPLETE %s',json.dumps(summary,sort_keys=True))
            except Exception as exc:
                server.log.warning('V11E_PMP_PORTAL_LOGIN_STRUCTURE_FAILED %s %s',type(exc).__name__,str(exc)[:6000])
        threading.Thread(target=_run,daemon=True,name='v11e-pmp-login-structure').start()
    if _base_when_ready: _base_when_ready(server)
