from __future__ import annotations

import json, os, runpy, threading

_BASE=runpy.run_path('v11_gunicorn_conf.py')
_base_on_starting=_BASE.get('on_starting')
_base_when_ready=_BASE.get('when_ready')


def on_starting(server):
    if _base_on_starting:
        _base_on_starting(server)


def when_ready(server):
    if os.getenv('RUN_V11B_PMP_ATTRIBUTE_AUTHORITY','').strip()=='1':
        server.log.info('V11B_PMP_ATTRIBUTE_AUTHORITY_HOOK_START')
        def _probe():
            try:
                from pmp_attribute_authority_probe_v11b import run
                summary,_=run(os.getenv('DATABASE_URL'))
                server.log.info('V11B_PMP_ATTRIBUTE_AUTHORITY_COMPLETE %s',json.dumps(summary,sort_keys=True))
            except Exception as exc:
                server.log.warning('V11B_PMP_ATTRIBUTE_AUTHORITY_FAILED %s %s',type(exc).__name__,str(exc)[:6000])
        threading.Thread(target=_probe,daemon=True,name='v11b-pmp-attribute-authority').start()
    if _base_when_ready:
        _base_when_ready(server)
