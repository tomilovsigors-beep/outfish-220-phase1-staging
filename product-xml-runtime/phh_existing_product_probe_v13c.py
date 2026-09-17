from __future__ import annotations
import json, os
from pmp_api_probe import _docs_get, _embedded_spec, _api_login, _api_get
from app import _master_rows

def scalars(x, path=''):
    out=[]
    if isinstance(x,dict):
        for k,v in x.items():
            out.extend(scalars(v, path+'.'+str(k) if path else str(k)))
    elif isinstance(x,list):
        for i,v in enumerate(x):
            out.extend(scalars(v, path+f'[{i}]'))
    elif isinstance(x,(str,int,float)) and not isinstance(x,bool):
        out.append((path,str(x)))
    return out

def run():
    docs=_docs_get('/docs'); docs.raise_for_status(); spec=_embedded_spec(docs.text)
    paths=spec.get('paths') or {}
    readable={}
    for p,ops in paths.items():
        g=(ops or {}).get('get')
        if not isinstance(g,dict): continue
        h=(p+' '+str(g.get('summary') or '')+' '+str(g.get('description') or '')).lower()
        if 'product' in h or 'offer' in h or 'modification' in h:
            readable[p]={'summary':g.get('summary'),'operationId':g.get('operationId'),'parameters':g.get('parameters'),'responses':g.get('responses')}

    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('login failed')
    token=lr.json().get('token')
    me=_api_get('/v3/sellers/me',token); me.raise_for_status(); md=me.json()
    seller_id=md.get('id') or (md.get('seller') or {}).get('id')
    master=_master_rows()
    by_sku={str(r.get('220_sku') or '').strip():r for r in master if str(r.get('220_sku') or '').strip()}
    by_ean={str(r.get('220_ean') or '').strip():r for r in master if str(r.get('220_ean') or '').strip()}
    matches=[]; scanned=0
    for offset in range(0,1000,100):
        rr=_api_get(f'/v2/sellers/{seller_id}/offers?app_name=220.lv&limit=100&offset={offset}',token)
        rr.raise_for_status(); d=rr.json()
        items=(d.get('offers') if isinstance(d,dict) else None) or (d.get('items') if isinstance(d,dict) else None) or (d if isinstance(d,list) else [])
        if not items: break
        scanned += len(items)
        for it in items:
            vals=scalars(it)
            sku_hits=[(p,v) for p,v in vals if v in by_sku]
            ean_hits=[(p,v) for p,v in vals if v in by_ean]
            if not sku_hits and not ean_hits: continue
            m=by_sku.get(sku_hits[0][1]) if sku_hits else by_ean.get(ean_hits[0][1])
            # record identifiers only; never log amount/price
            ids={k:it.get(k) for k in ('id','external_id','pigu_external_id','app_name','status') if isinstance(it,dict) and k in it}
            matches.append({
              '220_sku':str(m.get('220_sku') or ''),'220_ean':str(m.get('220_ean') or ''),
              '220_title':str(m.get('220_title') or ''),'shopify_title':str(m.get('shopify_title') or ''),
              'identifier_paths':[p for p,v in sku_hits+ean_hits][:8],
              'offer_identifiers':ids
            })
            if len(matches)>=25: break
        if len(matches)>=25 or len(items)<100: break
    out={'status':'PASS','seller_id':seller_id,'offers_scanned':scanned,'matches':matches,'match_count':len(matches),'readable_paths':readable,
         'safety':{'PHH_writes':0,'stock_changes':0,'price_changes':0,'Master_writes':0,'Shopify_writes':0,'Product_XML':'OFF'}}
    print('PHH_EXISTING_PRODUCT_V13C '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
