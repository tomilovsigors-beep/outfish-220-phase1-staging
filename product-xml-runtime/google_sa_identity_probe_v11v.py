from __future__ import annotations
import json, os

def run():
    raw=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON') or ''
    if not raw: raise RuntimeError('GOOGLE_SERVICE_ACCOUNT_JSON missing')
    data=json.loads(raw)
    out={'status':'PASS','client_email':data.get('client_email') or '','project_id':data.get('project_id') or '','secret_values_printed':0}
    print('GOOGLE_SA_IDENTITY_V11V '+json.dumps(out,sort_keys=True),flush=True)
    return out
