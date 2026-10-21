from __future__ import annotations
import json,re
from pmp_api_probe import _api_login,_api_get,_docs_get,_embedded_spec

SELLER_ID='9990696'
CANDIDATES=[
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
        if not batch: break
        items.extend(batch)
        if len(batch)<limit: break
        offset+=len(batch)
    return items

def scan_categories(token):
    items=[]; offset=0; limit=100
    while offset<50000:
        r=_api_get(f'/v3/categories?limit={limit}&offset={offset}',token)
        r.raise_for_status(); body=r.json()
        batch=(body.get('category_list') if isinstance(body,dict) else None) or (body.get('items') if isinstance(body,dict) else None) or (body if isinstance(body,list) else [])
        if not batch: break
        items.extend(batch)
        if len(batch)<limit: break
        offset+=len(batch)
    return items

def deref(schema,schemas,depth=0):
    if depth>8 or not isinstance(schema,dict): return schema
    if '$ref' in schema:
        n=schema['$ref'].split('/')[-1]
        return {'ref':n,'schema':deref(schemas.get(n,{}),schemas,depth+1)}
    out={}
    for k,v in schema.items():
        if k in ('type','required','nullable','format','description','example','enum'):
            out[k]=v
        elif k=='properties':
            out[k]={kk:deref(vv,schemas,depth+1) for kk,vv in v.items()}
        elif k=='items':
            out[k]=deref(v,schemas,depth+1)
        elif k in ('allOf','oneOf','anyOf'):
            out[k]=[deref(x,schemas,depth+1) for x in v]
    return out

def run():
    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('PHH login failed')
    token=lr.json().get('token')
    offers=scan_offers(token)
    cats=scan_categories(token)

    absences=[]
    for c in CANDIDATES:
        br=_api_get(f"/v3/products/product-modifications/barcodes?ean={c['ean']}",token)
        try: bj=br.json()
        except Exception: bj={'raw':br.text[:1000]}
        bvals=set(scalars(bj))
        offer_matches=[]
        for o in offers:
            vals=set(scalars(o))
            if c['sku'] in vals or c['ean'] in vals:
                offer_matches.append({'id':o.get('id'),'status':o.get('status'),'modification':o.get('modification')})
        absences.append({
          **c,'ean13_valid':ean13_ok(c['ean']),
          'barcode_lookup_http':br.status_code,
          'barcode_lookup_body':bj,
          'barcode_contains_sku_or_ean':bool(c['sku'] in bvals or c['ean'] in bvals),
          'seller_offer_matches':offer_matches,
          'offers_scanned':len(offers),
          'absence_proven':not (c['sku'] in bvals or c['ean'] in bvals) and len(offer_matches)==0
        })

    def norm(s): return re.sub(r'[^a-z0-9]+',' ',str(s or '').casefold()).strip()
    comparable_category_ids=set()
    for cc in comparables:
        for item in (cc.get('body') if isinstance(cc.get('body'),list) else []):
            try:
                cid=((item.get('modification') or {}).get('category') or {}).get('id')
                if cid is not None: comparable_category_ids.add(str(cid))
            except Exception:
                pass
    category_matches=[c for c in cats if str(c.get('category_id') or c.get('id') or '') in comparable_category_ids]

    docs=_docs_get('/docs'); docs.raise_for_status()
    spec=_embedded_spec(docs.text)
    schemas=((spec.get('components') or {}).get('schemas') or {})
    post_schema=deref(schemas.get('ProductImportRequestPost') or {},schemas)

    history=_api_get(f'/v3/sellers/{SELLER_ID}/product/import/execution/{GEARAID_EXEC}/results?limit=100&offset=0',token)
    history_json=history.json() if history.ok else {'raw':history.text[:1500]}
    gear=[x for x in (history_json.get('items') or []) if isinstance(x,dict) and str(x.get('sku') or '')==GEARAID_SKU]

    comparables=[]
    for c in COMPARABLES:
        rr=_api_get(f"/v3/products/product-modifications/barcodes?ean={c['ean']}",token)
        try: body=rr.json()
        except Exception: body={'raw':rr.text[:1500]}
        comparables.append({**c,'http_status':rr.status_code,'body':body})
    out={
      'status':'PASS','writes':0,'seller_id':SELLER_ID,
      'comparable_existing_trekking_poles':comparables,
      'candidates':absences,
      'category_matches':category_matches,
      'product_import_request_post_schema':post_schema,
      'gearaid':{
        'product_id':GEARAID_PRODUCT_ID,'execution_id':GEARAID_EXEC,'sku':GEARAID_SKU,
        'execution_results_http':history.status_code,'execution_result_rows':gear,
        'known_original_payload_source':'phh_gearaid_68150_create_v46.py / phh_gearaid_68150_preflight_v42.py',
        'later_required_feature_source':'phh_gearaid_68150_feature_patch_v47.py'
      }
    }
    print('CREATE_PILOT_READONLY_PROBE '+json.dumps(out,ensure_ascii=False,sort_keys=True)[:240000],flush=True)
    return out

if __name__=='__main__': run()
