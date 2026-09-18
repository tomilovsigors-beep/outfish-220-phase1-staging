from __future__ import annotations
import json
from pmp_api_probe import _api_login,_api_get
from pmp_category_exporter import export_all

SKU='CNK2450DS012G'
EAN='6975641883678'

def _scalars(x,path=''):
    out=[]
    if isinstance(x,dict):
        for k,v in x.items():
            out.extend(_scalars(v, path+'.'+str(k) if path else str(k)))
    elif isinstance(x,list):
        for i,v in enumerate(x):
            out.extend(_scalars(v, path+f'[{i}]'))
    elif isinstance(x,(str,int,float)) and not isinstance(x,bool):
        out.append((path,str(x)))
    return out

def run():
    lr=_api_login('v3')
    if lr is None or not lr.ok:
        raise RuntimeError('PHH login failed')
    token=lr.json().get('token')
    if not token:
        raise RuntimeError('PHH token missing')
    me=_api_get('/v3/sellers/me',token); me.raise_for_status(); md=me.json()
    seller_id=md.get('id') or (md.get('seller') or {}).get('id')

    # Exact existing-identity read-only scan across seller offers.
    found=[]
    scanned=0
    offset=0
    limit=100
    while offset < 20000:
        r=_api_get(f'/v2/sellers/{seller_id}/offers?app_name=220.lv&limit={limit}&offset={offset}',token)
        r.raise_for_status()
        d=r.json()
        items=(d.get('offers') if isinstance(d,dict) else None) or (d.get('items') if isinstance(d,dict) else None) or (d if isinstance(d,list) else [])
        if not items:
            break
        scanned += len(items)
        for it in items:
            vals=_scalars(it)
            hits=[(p,v) for p,v in vals if v in (SKU,EAN)]
            if hits:
                found.append({'identifier_hits':hits[:8],'offer_identifiers':{k:it.get(k) for k in ('id','external_id','pigu_external_id','app_name','status') if isinstance(it,dict) and k in it}})
        if len(items)<limit:
            break
        offset += len(items)

    # Live authoritative taxonomy export; filter only addable categories by trekking/walking/pole/stick semantics.
    summary,arts=export_all('v3',100,persist=False,token=token)
    payload=json.loads(arts['pmp-categories-full.json'].decode('utf-8'))
    cats=payload.get('category_list') or []
    by_id={c.get('category_id'):c for c in cats}
    def path(cid):
        parts=[]; seen=set()
        while cid is not None and cid not in seen and cid in by_id:
            seen.add(cid); c=by_id[cid]
            title=c.get('title_en') or c.get('title_lv') or c.get('title_lt') or c.get('title_ru') or str(cid)
            parts.append(str(title)); cid=c.get('parent_id')
        return ' > '.join(reversed(parts))
    needles=('trek','hiking','walking','pole','stick','nūj','lazd','палк','трек')
    candidates=[]
    for c in cats:
        if c.get('allow_add_products') is not True:
            continue
        titles={k:str(c.get(k) or '') for k in ('title_en','title_lv','title_lt','title_ee','title_fi','title_ru','title_pl')}
        hay=(' '.join(titles.values())+' '+path(c.get('category_id'))).casefold()
        if not any(n in hay for n in needles):
            continue
        req=[]
        for a in c.get('attributes') or []:
            if isinstance(a,dict) and a.get('required'):
                req.append({k:a.get(k) for k in ('field_id','required','title_lt','title_lv','title_ee','title_fi','title_ru')})
        candidates.append({
            'category_id':c.get('category_id'),
            'parent_id':c.get('parent_id'),
            'path':path(c.get('category_id')),
            'titles':titles,
            'required_attributes':req,
            'attribute_count':len(c.get('attributes') or []),
        })

    out={
      'status':'PASS',
      'seller_id':seller_id,
      'sku':SKU,'ean':EAN,
      'offers_scanned':scanned,
      'identity_found':bool(found),
      'identity_matches':found,
      'taxonomy_summary':summary,
      'category_candidates':candidates,
      'safety':{'PHH_writes':0,'stock_changes':0,'price_changes':0,'Master_writes':0,'Shopify_writes':0}
    }
    print('PHH_NATUREHIKE_GALE_PROBE_V15 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
