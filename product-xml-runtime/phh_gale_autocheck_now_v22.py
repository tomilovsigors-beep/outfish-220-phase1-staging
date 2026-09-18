from __future__ import annotations
import json, os
from pmp_api_probe import _api_login,_api_get
PID=8817510854
def run():
    lr=_api_login('v3'); lr.raise_for_status(); token=lr.json().get('token')
    r=_api_get(f'/v3/product-modification/pigu-external-id/{PID}/auto-check-errors',token)
    try:b=r.json()
    except Exception:b={'raw':r.text[:4000]}
    out={'status':'PASS','http_status':r.status_code,'body':b}
    print('PHH_GALE_AUTOCHECK_NOW_V22 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
