from __future__ import annotations
import json,re
from pmp_api_probe import _api_login,_api_get,_docs_get,_embedded_spec

SELLER_ID='9990696'
CANDIDATES=[
 {'sku':'ss8388-35-38','ean':'5908221354191'},
 {'sku':'ss8388-39-42','ean':'5908221354207'},
 {'sku':'ss10314-35-38','ean':'5908221359509'},
 {'sku':'ss10314-39-42','ean':'5908221359516'},
 {'sku':'ss8388-43-46','ean':'5908221354214'},
 {'sku':'ss10314-43-46','ean':'5908221359523'},
]
GEARAID_EXEC='58291763'
GEARAID_SKU='68150'
GEARAID_PRODUCT_ID='270344850'
COMPARABLES=[{'sku':'ss10309-35-38','ean':'5908221354283'}]

def scalars(x):
    out=[]
    if isinstance(x,dict):
        for v in x.values(): out.extend(scalars(v))
    elif isinstance(x,list):
        for v in x: out.extend(scalars(v))
    elif isinstance(x,(str,int,float)) and not isinstance(x,bool):
        out.append(str(x))
    return out

def ean13_ok(s):
    if not re.fullmatch(r'\d{13}',s): return False
    d=[int(x) for x in s]
    chk=(10-((sum(d[:12:2])+3*sum(d[1:12:2]))%10))%10
    return chk==d[12]

def scan_offers(token):
    items=[]; offset=0; limit=100
    while offset<30000:
        r=_api_get(f'/v2/sellers/{SELLER_ID}/offers?app_name=220.lv&limit={limit}&offset={offset}',token)
        r.raise_for_status(); body=r.json()
        batch=(body.get('offers') if isinstance(body,dict) else None) or (body.get('items') if isinstance(body,dict) else None) or (body if isinstance(body,list) else [])
        if not batch: return items
        items.extend(batch)
        if len(batch)<limit: return items
        offset+=len(batch)
    raise RuntimeError('Offer scan exceeded safety bound before completion')

def run():
    from datetime import datetime, timezone
    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('PHH login failed')
    token=lr.json().get('token')
    offers=scan_offers(token)
    results=[]
    for c in CANDIDATES:
        br=_api_get(f"/v3/products/product-modifications/barcodes?ean={c['ean']}",token)
        try: body=br.json()
        except Exception: body={'raw':br.text[:1000]}
        matches=[{'id':o.get('id')} for o in offers if c['sku'] in set(scalars(o)) or c['ean'] in set(scalars(o))]
        results.append({**c,'ean13_valid':ean13_ok(c['ean']),'barcode_lookup_http':br.status_code,'barcode_lookup_body':body,'seller_offer_matches':matches,'offers_scanned':len(offers),'absence_proven':br.status_code==404 and not matches and ean13_ok(c['ean'])})
    execution=_api_get('/v3/sellers/9990696/product/import/execution/58717994/results?limit=100&offset=0',token)
    print('OWNER_PILOT_FINAL_RESULTS '+json.dumps({'http':execution.status_code,'body':execution.json()},ensure_ascii=False),flush=True)
    out={'status':'PASS' if all(c['absence_proven'] for c in results) else 'BLOCKED','checked_at':datetime.now(timezone.utc).isoformat(),'writes':0,'shopify_writes':0,'sia_fhm_writes':0,'seller_id':SELLER_ID,'candidates':results,'scope':'Post-rejection control GET for all six EANs; execution 58717994; no mutation or retry'}
    print('CREATE_PILOT_READONLY_PROBE '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out

if __name__=='__main__': run()
