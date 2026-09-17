from __future__ import annotations
import json, os, runpy, threading
_BASE=runpy.run_path('v11g_gunicorn_conf.py')
_base_on_starting=_BASE.get('on_starting')
_base_when_ready=_BASE.get('when_ready')

def on_starting(server):
    if _base_on_starting: _base_on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V11H_PMP_FIELD_SCHEMA','').strip()=='1':
        server.log.info('V11H_PMP_FIELD_SCHEMA_HOOK_START')
        def _run():
            try:
                from pmp_field_schema_probe_v11h import run
                out=run()
                server.log.info('V11H_PMP_FIELD_SCHEMA_COMPLETE %s',json.dumps({'status':out.get('status'),'schemas':sorted((out.get('schemas') or {}).keys())},sort_keys=True))
            except Exception as exc:
                server.log.warning('V11H_PMP_FIELD_SCHEMA_FAILED %s %s',type(exc).__name__,str(exc)[:6000])
        threading.Thread(target=_run,daemon=True,name='v11h-pmp-field-schema').start()
    if _base_when_ready: _base_when_ready(server)
