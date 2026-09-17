from __future__ import annotations
import json, os, requests
from urllib.parse import urljoin
from pmp_api_probe import BASE,_api_login,_api_get

SKU='11541-013'
EAN='0021563115413'

def headers(token):
    return {'User-Agent':'outfish-phh-product-import-smoke/13g','Accept':'application/json','Content-Type':'application/json','Authorization':'Pigu-mp '+token}

def run():
    if os.getenv('RUN_V13G_IMPORT_TRANSPORT_SMOKE','').strip()!='1':
        return {'status':'SKIPPED'}
    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('login failed')
    token=lr.json().get('token')
    me=_api_get('/v3/sellers/me',token); me.raise_for_status(); md=me.json()
    seller_id=md.get('id') or (md.get('seller') or {}).get('id')
    if not seller_id: raise RuntimeError('seller id unavailable')

    # 1) Create a dedicated Product Import execution. This creates only an import container, not a product.
    er=requests.post(urljoin(BASE,f'/v3/sellers/{seller_id}/product/import/execution'),headers=headers(token),timeout=30)
    ej=er.json() if er.content else {}
    if er.status_code!=201 or not isinstance(ej,dict) or not ej.get('id'):
        raise RuntimeError(f'execution create failed HTTP {er.status_code}: {str(ej)[:1000]}')
    execution_id=str(ej['id'])

    # 2) Exercise the actual product-import POST route with an intentionally incomplete payload.
    # It carries the real SKU/EAN but omits mandatory product/category/package fields, so PHH must reject it at validation.
    # This must not create or update a marketplace product.
    invalid_payload={'modifications':[{'sku':SKU,'eans':[EAN]}]}
    pr=requests.post(urljoin(BASE,f'/v3/sellers/product/import/execution/{execution_id}'),headers=headers(token),json=invalid_payload,timeout=30)
    try: pj=pr.json()
    except Exception: pj={'raw':pr.text[:500]}
    if pr.status_code not in (400,422):
        raise RuntimeError(f'expected validation rejection, got HTTP {pr.status_code}: {str(pj)[:1200]}')

    errors=[]
    if isinstance(pj,dict):
        for e in pj.get('errors') or []:
            if isinstance(e,dict):
                errors.append({'property_path':e.get('property_path'),'message':e.get('message')})
    out={
      'status':'PASS','seller_id':seller_id,'execution_id':execution_id,
      'execution_http_status':er.status_code,'product_import_http_status':pr.status_code,
      'sku':SKU,'ean':EAN,'validation_errors':errors[:30],
      'transport_verified':True,'product_mutation_expected':False,
      'safety':{'PHH_execution_container_writes':1,'PHH_product_writes':0,'stock_changes':0,'price_changes':0,
                'Master_writes':0,'Shopify_writes':0,'EAN_changes':0,'Product_XML':'OFF'}
    }
    print('PHH_PRODUCT_IMPORT_TRANSPORT_SMOKE_V13G '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
