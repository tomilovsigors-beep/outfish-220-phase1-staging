from __future__ import annotations
import json, os, threading
import v12c_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V12D_FAMILY_PROPAGATION','').strip()=='1':
        server.log.info('V12D_FAMILY_PROPAGATION_START')
        def task():
            try:
                from phh_family_propagation_audit_v12d import run
                out=run()
                server.log.info('V12D_FAMILY_PROPAGATION_COMPLETE %s',json.dumps(out.get('summary',{}),sort_keys=True))
            except Exception as exc:
                server.log.warning('V12D_FAMILY_PROPAGATION_FAILED %s %s',type(exc).__name__,str(exc)[:4000])
        threading.Thread(target=task,daemon=True,name='v12d-family-propagation').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
