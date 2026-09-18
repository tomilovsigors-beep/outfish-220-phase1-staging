from __future__ import annotations
import json, os
from pmp_api_probe import _api_login,_api_get

SELLER_ID=9990696
TARGETS=[
 {'sku':'10415-015','ean':'0021563104158'},
 {'sku':'806386020236','ean':'806074351195'},
 {'sku':'10116-016','ean':'0021563101164'},
 {'sku':'STAL/05/35-38','ean':'5903282603424'},
]

def scalar_values(x):
    out=[]
    if isinstance(x,dict):
        for v in x.values(): out.extend(scalar_values(v))
    elif isinstance(x,list):
        for v in x: out.extend(scalar_values(v))
    elif isinstance(x,(str,int,float)) and not isinstance(x,bool):
        out.append(str(x))
    return out

def scan_offers(token):
    found={t['sku']:[] for t in TARGETS}
    offset=0; limit=100
    while offset<20000:
        r=_api_get(f'/v2/sellers/{SELLER_ID}/offers?app_name=220.lv&limit={limit}&offset={offset}',token)
        r.raise_for_status(); d=r.json()
        items=(d.get('offers') if isinstance(d,dict) else None) or (d.get('items') if isinstance(d,dict) else None) or (d if isinstance(d,list) else [])
        if not items: break
        for it in items:
            vals=set(scalar_values(it))
            for t in TARGETS:
                if t['sku'] in vals or t['ean'] in vals:
                    found[t['sku']].append(it)
        if len(items)<limit: break
        offset+=len(items)
    return found

def run():
    lr=_api_login('v3'); lr.raise_for_status(); token=lr.json().get('token')
    offers=scan_offers(token)
    out=[]
    for t in TARGETS:
        br=_api_get(f"/v3/products/product-modifications/barcodes?ean={t['ean']}",token)
        try: bb=br.json()
        except Exception: bb={'raw':br.text[:2000]}
        out.append({
          'sku':t['sku'],'ean':t['ean'],
          'seller_offers':offers.get(t['sku']) or [],
          'barcode_lookup_http':br.status_code,
          'barcode_lookup':bb
        })
    res={'status':'PASS','targets':out,'safety':{'writes':0}}
    print('PHH_EXISTENCE_CHECK_V26 '+json.dumps(res,ensure_ascii=False,sort_keys=True),flush=True)
    return res
