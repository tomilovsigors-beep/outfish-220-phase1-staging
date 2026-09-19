from __future__ import annotations
import json, os, time
import requests
from urllib.parse import urljoin
from pmp_api_probe import BASE, _api_login
IDS=[1138081,347376908,141554241,155481013,1039160127]
def run():
    r=_api_login('v3',timeout=30); r.raise_for_status(); token=r.json()['token']
    headers={'User-Agent':'outfish-phh-import-status/1.0','Accept':'application/json','Authorization':'Pigu-mp '+token}
    out=[]
    for oid in IDS:
        rr=requests.get(urljoin(BASE,f'/v3/offers/{oid}'),headers=headers,timeout=30)
        item={'id':oid,'http':rr.status_code}
        try:item['body']=rr.json()
        except Exception:item['body']=rr.text[:1000]
        out.append(item); time.sleep(.3)
    print('PHH_OFFER_IMPORT_STATUS_PROBE '+json.dumps(out,ensure_ascii=False,separators=(',',':')),flush=True)
if __name__=='__main__': run()
