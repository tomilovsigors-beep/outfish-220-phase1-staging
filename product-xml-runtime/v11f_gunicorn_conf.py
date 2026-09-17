from __future__ import annotations
import json, os, runpy, threading
_BASE=runpy.run_path('v11e_gunicorn_conf.py')
_base_on_starting=_BASE.get('on_starting')
_base_when_ready=_BASE.get('when_ready')

def on_starting(server):
    if _base_on_starting: _base_on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V11F_PMP_PORTAL_BUNDLE','').strip()=='1':
        server.log.info('V11F_PMP_PORTAL_BUNDLE_HOOK_START')
        def _run():
            try:
                from pmp_portal_bundle_probe_v11f import run
                summary=run()
                server.log.info('V11F_PMP_PORTAL_BUNDLE_COMPLETE %s',json.dumps({'status':summary.get('status'),'interesting_count':summary.get('interesting_count')},sort_keys=True))
            except Exception as exc:
                server.log.warning('V11F_PMP_PORTAL_BUNDLE_FAILED %s %s',type(exc).__name__,str(exc)[:6000])
        threading.Thread(target=_run,daemon=True,name='v11f-pmp-bundle').start()
    if _base_when_ready: _base_when_ready(server)
