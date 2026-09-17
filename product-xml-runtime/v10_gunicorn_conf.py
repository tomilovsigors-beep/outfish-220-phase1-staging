from __future__ import annotations

import json, os, runpy, threading

_BASE=runpy.run_path('v9_gunicorn_conf.py')
_base_on_starting=_BASE.get('on_starting')
_base_when_ready=_BASE.get('when_ready')


def on_starting(server):
    if _base_on_starting:
        _base_on_starting(server)


def when_ready(server):
    if os.getenv('RUN_V10_LEAF_PROBE','').strip()=='1':
        server.log.info('V10_LEAF_PROBE_HOOK_START')
        def _run():
            try:
                from current_product_leaf_adjudication_v10_probe import run
                summary,_=run(os.getenv('DATABASE_URL'))
                server.log.info('V10_LEAF_PROBE_COMPLETE %s',json.dumps(summary,sort_keys=True))
            except Exception as exc:
                server.log.warning('V10_LEAF_PROBE_FAILED %s %s',type(exc).__name__,str(exc)[:5000])
        threading.Thread(target=_run,daemon=True,name='v10-leaf-probe').start()
    if _base_when_ready:
        _base_when_ready(server)
