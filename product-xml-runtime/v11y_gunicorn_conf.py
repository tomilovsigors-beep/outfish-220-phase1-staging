from __future__ import annotations
import json, os, threading
import v11x_gunicorn_conf as base

def on_starting(server):
    if hasattr(base,'on_starting'): base.on_starting(server)

def when_ready(server):
    if os.getenv('RUN_V11Y_PHH_PACKAGING_DASHBOARD','').strip()=='1':
        server.log.info('V11Y_PHH_PACKAGING_DASHBOARD_START')
        def task():
            try:
                from phh_packaging_dashboard_writer_v11y import run
                out=run()
                server.log.info('V11Y_PHH_PACKAGING_DASHBOARD_COMPLETE %s',json.dumps({'status':out.get('status'),'products':out.get('products')},sort_keys=True))
            except Exception as exc:
                server.log.warning('V11Y_PHH_PACKAGING_DASHBOARD_FAILED %s %s',type(exc).__name__,str(exc)[:3000])
        threading.Thread(target=task,daemon=True,name='v11y-phh-packaging-dashboard').start()
    if hasattr(base,'when_ready'): base.when_ready(server)
