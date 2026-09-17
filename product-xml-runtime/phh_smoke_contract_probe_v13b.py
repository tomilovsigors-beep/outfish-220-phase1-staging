from __future__ import annotations
import json, os, requests
from pmp_api_probe import _docs_get, _embedded_spec, _api_login, _api_get
from app import _master_rows

def run():
    docs=_docs_get('/docs'); docs.raise_for_status()
    spec=_embedded_spec(docs.text)
    paths=spec.get('paths') or {}; schemas=(spec.get('components') or {}).get('schemas') or {}
    import_paths={}
    for p,ops in paths.items():
        lp=p.lower()
        if 'product' in lp and 'import' in lp:
            import_paths[p]={m:v for m,v in (ops or {}).items() if m.lower() in {'get','post','patch','put','delete'}}
    schema_names=[n for n in schemas if 'ProductImport' in n or 'ModificationImport' in n or 'ProductFeaturesRequest' in n]
    compact_schemas={n:schemas[n] for n in schema_names}

    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('PHH login failed')
    token=lr.json().get('token')
    me=_api_get('/v3/sellers/me',token); me.raise_for_status()
    me_data=me.json()
    seller_id=(me_data.get('id') if isinstance(me_data,dict) else None) or ((me_data.get('seller') or {}).get('id') if isinstance(me_data,dict) else None)
    if not seller_id: raise RuntimeError('seller id unavailable')

    # Read existing 220.lv offers only; never mutate stock/price.
    offers=_api_get(f'/v2/sellers/{seller_id}/offers?app_name=220.lv&limit=100&offset=0',token)
    offers_info={'status':offers.status_code,'candidates':[]}
    if offers.ok:
        od=offers.json()
        items=(od.get('items') if isinstance(od,dict) else od) or []
        master=_master_rows()
        by_sku={str(r.get('220_sku') or '').strip():r for r in master if str(r.get('220_sku') or '').strip()}
        by_ean={str(r.get('220_ean') or '').strip():r for r in master if str(r.get('220_ean') or '').strip()}
        cands=[]
        for it in items:
            raw=json.dumps(it,ensure_ascii=False)
            sku=str(it.get('sku') or it.get('supplier_code') or it.get('external_mod_id') or '').strip()
            ean=str(it.get('ean') or it.get('barcode') or '').strip()
            m=by_sku.get(sku) or by_ean.get(ean)
            if not m:
                # fallback exact substring only for canonical identifiers
                ms=None
                for k,r in by_sku.items():
                    if k and k in raw: ms=r; sku=k; break
                if ms is None:
                    for k,r in by_ean.items():
                        if k and k in raw: ms=r; ean=k; break
                m=ms
            if m:
                cands.append({
                  '220_sku':str(m.get('220_sku') or ''),'220_ean':str(m.get('220_ean') or ''),
                  '220_title':str(m.get('220_title') or ''),'shopify_title':str(m.get('shopify_title') or ''),
                  'offer_id':it.get('id'),'external_id':it.get('external_id'),'pigu_external_id':it.get('pigu_external_id'),
                  'app_name':it.get('app_name'),'status':it.get('status')
                })
            if len(cands)>=20: break
        offers_info['candidates']=cands
        offers_info['matched_count']=len(cands)
        offers_info['response_shape']=list(od.keys()) if isinstance(od,dict) else 'list'
    out={'status':'PASS','seller_id':seller_id,'import_paths':import_paths,'schemas':compact_schemas,'offers':offers_info,
         'safety':{'PHH_writes':0,'stock_changes':0,'price_changes':0,'Master_writes':0,'Shopify_writes':0,'Product_XML':'OFF'}}
    print('PHH_SMOKE_CONTRACT_V13B '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
