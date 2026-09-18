from __future__ import annotations
import json, os
from pmp_api_probe import _docs_get,_embedded_spec,_api_login,_api_get

PRODUCT_ID=269889165
MOD_ID=297272175
EAN='6975641883678'

def run():
    dr=_docs_get('/docs'); dr.raise_for_status()
    spec=_embedded_spec(dr.text)
    ops=[]
    for path,methods in (spec.get('paths') or {}).items():
        hay=(path+' '+json.dumps(methods,ensure_ascii=False)).lower()
        if not any(k in hay for k in ('image','manufacturer','feature','product-modification','warranty')):
            continue
        item={'path':path,'methods':{}}
        for m in ('get','post','put','patch','delete'):
            op=(methods or {}).get(m)
            if isinstance(op,dict):
                item['methods'][m.upper()]={'summary':op.get('summary'),'description':op.get('description'),'parameters':op.get('parameters')}
        if item['methods']: ops.append(item)
    lr=_api_login('v3'); lr.raise_for_status(); token=lr.json().get('token')
    reads={}
    for p in [f'/v3/products/{PRODUCT_ID}', f'/v3/products/product-modifications/barcodes?ean={EAN}']:
        r=_api_get(p,token)
        try:b=r.json()
        except Exception:b={'raw':r.text[:4000]}
        reads[p]={'http_status':r.status_code,'body':b}
    out={'status':'PASS','operations':ops,'reads':reads}
    print('PHH_FIELD_CAPABILITIES_V21 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
