from __future__ import annotations
import json, os, threading
import v11w_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V11X_PMP_PACKAGE_CONTRACT','').strip()=='1':
        server.log.info('V11X_PMP_PACKAGE_CONTRACT_START')
        def task():
            try:
                from pmp_package_contract_probe_v11x import run
                out=run()
                server.log.info('V11X_PMP_PACKAGE_CONTRACT_COMPLETE %s',json.dumps({'status':out.get('status'),'schemas':len(out.get('schemas') or {})},sort_keys=True))
            except Exception as exc:
                server.log.warning('V11X_PMP_PACKAGE_CONTRACT_FAILED %s %s',type(exc).__name__,str(exc)[:3000])
        threading.Thread(target=task,daemon=True,name='v11x-pmp-package-contract').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
