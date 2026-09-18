from __future__ import annotations
import json, os
from pmp_api_probe import _docs_get,_embedded_spec,_api_login,_api_get

PRODUCT_ID=269889165
MOD_ID=297272175
EAN='6975641883678'
SELLER_ID=9990696
PIGU_EXTERNAL_ID=8817510854

def _op_summary(spec):
    out=[]
    for path,ops in (spec.get('paths') or {}).items():
        hay=(path+' '+json.dumps(ops,ensure_ascii=False)).lower()
        if not any(k in hay for k in ('auto-check','autocheck','check error','modification','barcode')):
            continue
        row={'path':path,'ops':{}}
        for m in ('get','post','put','patch','delete'):
            op=(ops or {}).get(m)
            if isinstance(op,dict):
                row['ops'][m.upper()]={'summary':op.get('summary'),'description':op.get('description'),'parameters':op.get('parameters')}
        if row['ops']: out.append(row)
    return out

def run():
    dr=_docs_get('/docs'); dr.raise_for_status()
    spec=_embedded_spec(dr.text)
    lr=_api_login('v3'); lr.raise_for_status()
    token=lr.json().get('token')
    candidates=[
      f'/v3/product-modification/{MOD_ID}/auto-check-errors',
      f'/v3/product-modifications/{MOD_ID}/auto-check-errors',
      f'/v3/product-modification/pigu-external-id/{PIGU_EXTERNAL_ID}/auto-check-errors',
      f'/v3/products/{PRODUCT_ID}',
      f'/v3/products/{PRODUCT_ID}/modifications',
      f'/v3/products/product-modifications/barcodes?ean={EAN}',
      f'/v3/sellers/{SELLER_ID}/offers?app_name=220.lv&modifications%5B%5D={MOD_ID}',
    ]
    reads={}
    for p in candidates:
        r=_api_get(p,token)
        try:b=r.json()
        except Exception:b={'raw':r.text[:3000]}
        reads[p]={'http_status':r.status_code,'body':b}
    out={'status':'PASS','openapi_ops':_op_summary(spec),'reads':reads}
    print('PHH_GALE_AUTOCHECK_V18 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
