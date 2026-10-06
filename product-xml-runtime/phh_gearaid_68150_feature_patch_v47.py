from __future__ import annotations
import json,os,time
from urllib.parse import urljoin
import requests
from pmp_api_probe import BASE,_api_login,_api_get

SKU='68150'
EAN='0021563681505'
PRODUCT_ID=270344850
SELLER_ID='9990696'
# Category 3377: Lithuanuan canonical feature titles, as in the authoritative taxonomy.
FEATURES=[
 {'name':'Medžiaga','value':'Mikrofibra'},
 {'name':'Spalva','value':'Mėlyna'},
 {'name':'Išmatavimai','value':'51 x 102 cm'},
 {'name':'Komplektacija','value':'1'},
 {'name':'Rankšluosčių tipas','value':'Sportinis'},
]
PAYLOAD={'product_features':FEATURES,
         'modifications':[{'sku':SKU,'manufacturer_code':SKU,'tare_deposit_quantity':0}]}

def _all_scalars(x):
    if isinstance(x,dict):
        for v in x.values(): yield from _all_scalars(v)
    elif isinstance(x,list):
        for v in x: yield from _all_scalars(v)
    elif isinstance(x,(str,int,float)) and not isinstance(x,bool):
        yield str(x)

def run():
    if os.getenv('RUN_GEARAID_68150_CREATE','').strip()=='APPROVED_ONCE':
        return {'status':'BLOCKED_CREATE_FLAG_STILL_ON','writes':0}
    if os.getenv('RUN_GEARAID_68150_FEATURE_PATCH','').strip()!='APPROVED_ONCE':
        return {'status':'SKIPPED_NOT_AUTHORIZED','writes':0}
    lr=_api_login('v3')
    if lr is None or not lr.ok:
        return {'status':'AUTH_FAILED','http_status':None if lr is None else lr.status_code,'writes':0}
    token=lr.json().get('token')
    me=_api_get('/v3/sellers/me',token); me.raise_for_status()
    md=me.json(); seller=str(md.get('id') or (md.get('seller') or {}).get('id') or '')
    if seller!=SELLER_ID:
        return {'status':'BLOCKED_SELLER_MISMATCH','seller_id':seller,'writes':0}
    # PHH's documented API has no GET /v3/products/{productId}. Use successful
    # immutable prior import execution for identity instead of unsupported 404 route.
    identity=_api_get(f'/v3/sellers/{seller}/product/import/execution/58291763/results?limit=20&offset=0',token)
    if identity.status_code!=200:
        return {'status':'BLOCKED_IDENTITY_HISTORY_UNREADABLE','http_status':identity.status_code,'writes':0}
    rows=(identity.json().get('items') or [])
    matching=[x for x in rows if x.get('status')=='success' and str(x.get('sku'))==SKU and EAN in str(x.get('message') or '')]
    if len(matching)!=1:
        return {'status':'BLOCKED_IDENTITY_NOT_PROVEN','writes':0,'matching_records':len(matching)}

    er=requests.post(urljoin(BASE,f'/v3/sellers/{seller}/product/import/execution'),
        headers={'Authorization':'Pigu-mp '+token,'Accept':'application/json','Content-Type':'application/json'},timeout=30)
    if er.status_code!=201:
        return {'status':'EXECUTION_FAILED','http_status':er.status_code,'writes':0}
    execution_id=str(er.json().get('id') or '')
    if not execution_id:
        return {'status':'EXECUTION_ID_MISSING','writes':0}
    rr=requests.patch(urljoin(BASE,f'/v3/sellers/product/import/execution/{execution_id}'),
        json=PAYLOAD,
        headers={'Authorization':'Pigu-mp '+token,'Accept':'application/json','Content-Type':'application/json'},
        timeout=40)
    try: response=rr.json()
    except Exception: response={'raw':rr.text[:800]}
    out={'status':'SUBMITTED' if rr.status_code==200 else 'VALIDATION_ERROR',
         'product_id':PRODUCT_ID,'sku':SKU,'ean':EAN,'execution_id':execution_id,
         'http_status':rr.status_code,'response':response,
         'features':FEATURES,'changes_only':'product_features',
         'safety':{'product_creates':0,'stock_writes':0,'price_writes':0,'Shopify_writes':0,'Master_writes':0}}
    if rr.status_code==200:
        for _ in range(12):
            time.sleep(3)
            q=_api_get(f'/v3/sellers/{seller}/product/import/execution/{execution_id}/results?limit=20&offset=0',token)
            try: items=(q.json().get('items') or [])
            except Exception: items=[]
            target=[it for it in items if str(it.get('sku'))==SKU]
            if target and target[0].get('status') in ('success','error'):
                out['import_result']=target[0]
                out['status']=target[0]['status'].upper()
                break
    print('GEARAID_68150_FEATURE_PATCH_RESULT '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out

def probe():
    lr=_api_login('v3')
    if lr is None or not lr.ok:
        return {'status':'AUTH_FAILED','http_status':None if lr is None else lr.status_code,'writes':0}
    token=lr.json().get('token')
    product=_api_get(f'/v3/products/{PRODUCT_ID}',token)
    category=_api_get('/v3/categories?id=3377&limit=10&offset=0',token)
    def body(r):
        try: return r.json()
        except Exception: return {'raw':r.text[:500]}
    p=body(product)
    cat=body(category)
    history=_api_get('/v3/sellers/9990696/product/import/execution/58291763/results?limit=20&offset=0',token)
    history_body=body(history)
    barcode=_api_get(f'/v3/products/product-modifications/barcodes?ean={EAN}',token)
    barcode_body=body(barcode)
    from pmp_api_probe import _docs_get,_embedded_spec
    docs=_docs_get('/docs')
    spec=_embedded_spec(docs.text) if docs.ok else None
    operations=[]
    for path,verbs in ((spec or {}).get('paths') or {}).items():
        if any(w in path.lower() for w in ('product','feature','attribute')):
            operations.append({'path':path,'verbs':[m.upper() for m in ('get','patch','post','put') if m in verbs]})

    # Limit response strictly to existing product identity/feature fields and 3377 category attribute metadata.
    obj=(p.get('product') or p) if isinstance(p,dict) else {}
    items=cat.get('category_list') or [] if isinstance(cat,dict) else []
    chosen=[v for v in items if str(v.get('category_id'))=='3377']
    attrs=(chosen[0].get('attributes') or []) if chosen else []
    return {
       'status':'PASS' if product.ok and category.ok else 'READ_ERROR',
       'product_http_status':product.status_code,
       'category_http_status':category.status_code,
       'product_id':PRODUCT_ID,
       'product_fields':{k:obj.get(k) for k in ('id','product_id','category_id','title','modifications','product_features','features','status') if k in obj},
       'product_top_level_keys':list(p.keys()) if isinstance(p,dict) else [],
       'category_3377_attributes':attrs,
       'prior_successful_execution_http_status':history.status_code,
       'prior_successful_execution_items':history_body.get('items',[]) if isinstance(history_body,dict) else [],
       'barcode_lookup_http_status':barcode.status_code,
       'barcode_lookup_body':barcode_body,
       'product_related_operations':operations,
       'writes':0,
    }
