from __future__ import annotations
import json, os, threading
import v11_attribute_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V11V_GOOGLE_SA_IDENTITY','').strip()=='1':
        server.log.info('V11V_GOOGLE_SA_IDENTITY_HOOK_START')
        def task():
            try:
                from google_sa_identity_probe_v11v import run
                out=run()
                server.log.info('V11V_GOOGLE_SA_IDENTITY_COMPLETE %s',json.dumps({'status':out.get('status'),'client_email':out.get('client_email')},sort_keys=True))
            except Exception as exc:
                server.log.warning('V11V_GOOGLE_SA_IDENTITY_FAILED %s %s',type(exc).__name__,str(exc)[:3000])
        threading.Thread(target=task,daemon=True,name='v11v-google-sa-identity').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
