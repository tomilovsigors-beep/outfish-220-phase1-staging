from __future__ import annotations
import json, os, re, requests
from urllib.parse import urljoin
from pmp_api_probe import BASE,_docs_get,_embedded_spec,_api_login,_api_get

WORDS=('commission','fee','invoice','settlement','payment','payout','delivery','shipment','shipping','return','refund','logistic','courier','fulfillment','fulfilment','warehouse','price list','tariff','rate')

def compact_op(op):
    if not isinstance(op,dict): return {}
    return {
      'summary':op.get('summary'),'description':op.get('description'),'operationId':op.get('operationId'),
      'parameters':op.get('parameters'),'requestBody':op.get('requestBody'),'responses':op.get('responses')
    }

def run():
    d=_docs_get('/docs'); d.raise_for_status(); spec=_embedded_spec(d.text)
    paths=spec.get('paths') or {}; schemas=(spec.get('components') or {}).get('schemas') or {}
    matched_paths={}
    for p,ops in paths.items():
        blob=(p+' '+json.dumps(ops,ensure_ascii=False)).lower()
        if any(w in blob for w in WORDS):
            matched_paths[p]={m:compact_op(v) for m,v in (ops or {}).items() if m in ('get','post','patch','put','delete')}
    matched_schemas={}
    for n,s in schemas.items():
        blob=(n+' '+json.dumps(s,ensure_ascii=False)).lower()
        if any(w in blob for w in WORDS):
            matched_schemas[n]=s

    # Probe seller-scoped read endpoints that look financial/logistical, GET only.
    lr=_api_login('v3')
    token=None; seller_id=None; probes={}
    if lr is not None and lr.ok:
        token=lr.json().get('token')
        me=_api_get('/v3/sellers/me',token)
        if me.ok:
            md=me.json(); seller_id=md.get('id') or (md.get('seller') or {}).get('id')
    if token and seller_id:
        candidates=[
          f'/v3/sellers/{seller_id}/invoices',
          f'/v3/sellers/{seller_id}/payments',
          f'/v3/sellers/{seller_id}/settlements',
          f'/v3/sellers/{seller_id}/returns?limit=20&offset=0',
          f'/v3/sellers/{seller_id}/shipments?limit=20&offset=0',
        ]
        for path in candidates:
            try:
                rr=_api_get(path,token)
                probes[path]={'status':rr.status_code,'content_type':rr.headers.get('content-type'),'sample':rr.text[:1200] if rr.status_code!=404 else ''}
            except Exception as e:
                probes[path]={'error':f'{type(e).__name__}: {e}'}

    # Seller Academy / FAQ public-ish URLs, no credentials beyond portal session unknown; record status only.
    portal={}
    for u in ('https://pmp.pigugroup.eu/seller-academy','https://pmp.pigugroup.eu/faq?section-id=166'):
        try:
            rr=requests.get(u,timeout=20,allow_redirects=True,headers={'User-Agent':'outfish-phh-fee-discovery/14'})
            portal[u]={'status':rr.status_code,'final_url':rr.url,'bytes':len(rr.content),'sample':re.sub(r'\s+',' ',rr.text[:700])}
        except Exception as e:
            portal[u]={'error':f'{type(e).__name__}: {e}'}

    out={'status':'PASS','seller_id':seller_id,'matched_paths':matched_paths,'matched_schemas':matched_schemas,'read_probes':probes,'portal':portal,
         'safety':{'PHH_writes':0,'stock_changes':0,'price_changes':0,'Master_writes':0,'Shopify_writes':0}}
    print('PHH_FEE_LOGISTICS_DISCOVERY_V14 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
