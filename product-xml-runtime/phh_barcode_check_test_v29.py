from __future__ import annotations
import json, requests
from urllib.parse import urljoin
from pmp_api_probe import BASE,_api_login,_api_get
SELLER_ID='9990696'
EAN='806074351195'
def h(token): return {'User-Agent':'outfish-phh-audit/29','Accept':'application/json','Content-Type':'application/json','Authorization':'Pigu-mp '+token}
def run():
    lr=_api_login('v3'); lr.raise_for_status(); token=lr.json()['token']
    e=requests.post(urljoin(BASE,f'/v3/sellers/{SELLER_ID}/barcodes/check/execution'),headers=h(token),timeout=30)
    try: ej=e.json()
    except Exception: ej={'raw':e.text}
    out={'create_http':e.status_code,'create':ej}
    if e.status_code not in (200,201) or not isinstance(ej,dict) or not ej.get('id'):
        print('PHH_BARCODE_CHECK_TEST_V29 '+json.dumps(out,ensure_ascii=False),flush=True); return out
    eid=ej['id']
    p=requests.post(urljoin(BASE,f'/v3/sellers/{SELLER_ID}/barcodes/check/execution/{eid}'),headers=h(token),json=[{'ean':EAN}],timeout=30)
    try: pj=p.json()
    except Exception: pj={'raw':p.text}
    out.update(post_http=p.status_code,post=pj)
    if p.status_code==200:
        import time; time.sleep(2)
        rr=_api_get(f'/v3/sellers/{SELLER_ID}/barcodes/check/execution/{eid}/results?limit=100&offset=0',token)
        try:rj=rr.json()
        except Exception:rj={'raw':rr.text}
        out.update(results_http=rr.status_code,results=rj)
    print('PHH_BARCODE_CHECK_TEST_V29 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
