from __future__ import annotations
import json, os, requests
from urllib.parse import urljoin
from pmp_api_probe import BASE,_api_login,_api_get

SELLER_ID=9990696
SKU='CNK2450DS012G'
EAN='6975641883678'
MODIFICATION_ID=297272175
APP='220.lv'
PRICE=27.0
AMOUNT=9

def headers(token):
    return {'User-Agent':'outfish-phh-gale-offer/16','Accept':'application/json','Content-Type':'application/json','Authorization':'Pigu-mp '+token}

def scalars(x):
    out=[]
    if isinstance(x,dict):
        for v in x.values(): out.extend(scalars(v))
    elif isinstance(x,list):
        for v in x: out.extend(scalars(v))
    elif isinstance(x,(str,int,float)) and not isinstance(x,bool):
        out.append(str(x))
    return out

def find_offer(token):
    offset=0; limit=100
    while offset<20000:
        r=_api_get(f'/v2/sellers/{SELLER_ID}/offers?app_name={APP}&limit={limit}&offset={offset}',token)
        r.raise_for_status(); d=r.json()
        items=(d.get('offers') if isinstance(d,dict) else None) or (d.get('items') if isinstance(d,dict) else None) or (d if isinstance(d,list) else [])
        if not items: return None
        for it in items:
            mod=(it.get('modification') or {}) if isinstance(it,dict) else {}
            vals=set(scalars(it))
            if str(mod.get('id') or '')==str(MODIFICATION_ID) or SKU in vals or EAN in vals:
                return it
        if len(items)<limit: return None
        offset+=len(items)
    return None

def channel_delivery_hours(me):
    channels=(me.get('channels') or {}) if isinstance(me,dict) else {}
    lv=channels.get('lv') or channels.get('LV') or {}
    dh=lv.get('delivery_hours') if isinstance(lv,dict) else None
    if dh is None:
        # Search only explicit delivery_hours values under the LV seller channel object.
        for k,v in (lv.items() if isinstance(lv,dict) else []):
            if str(k).lower()=='delivery_hours': dh=v
    return dh

def verify_offer(data):
    mod=(data.get('modification') or {}) if isinstance(data,dict) else {}
    vals=set(scalars(data))
    identity_ok=(str(mod.get('id') or '')==str(MODIFICATION_ID) or SKU in vals or EAN in vals)
    def f(v):
        try:return float(v)
        except:return None
    return {
      'identity_ok':identity_ok,
      'app_name':data.get('app_name'),
      'status':data.get('status'),
      'amount':data.get('amount'),
      'sell_price':data.get('sell_price'),
      'price_ok':f(data.get('sell_price'))==PRICE,
      'amount_ok':str(data.get('amount'))==str(AMOUNT),
      'status_ok':str(data.get('status'))=='active',
      'app_ok':str(data.get('app_name'))==APP,
      'offer_id':data.get('id'),
      'modification_id':mod.get('id'),
      'sku':mod.get('sku') or (SKU if SKU in vals else None),
      'ean_codes':mod.get('ean_codes') or mod.get('ean'),
    }

def run():
    if os.getenv('RUN_NATUREHIKE_GALE_OFFER','').strip()!='APPROVED_ONCE':
        return {'status':'SKIPPED'}
    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('PHH login failed')
    token=lr.json().get('token')
    me_r=_api_get('/v3/sellers/me',token); me_r.raise_for_status(); me=me_r.json()
    seller_id=int(me.get('id') or (me.get('seller') or {}).get('id') or 0)
    if seller_id!=SELLER_ID: raise RuntimeError(f'unexpected seller id {seller_id}')

    existing=find_offer(token)
    action=None
    if existing:
        oid=int(existing.get('id'))
        before=_api_get(f'/v3/offers/{oid}',token); before.raise_for_status(); bd=before.json()
        vals=set(scalars(bd))
        mod=bd.get('modification') or {}
        if str(bd.get('app_name'))!=APP or not (str(mod.get('id') or '')==str(MODIFICATION_ID) or SKU in vals or EAN in vals):
            raise RuntimeError('existing offer identity guard failed')
        body={'amount':AMOUNT,'sell_price':PRICE,'status':'active'}
        wr=requests.patch(urljoin(BASE,f'/v3/offers/{oid}'),headers=headers(token),json=body,timeout=30)
        action='PATCH_EXISTING'
    else:
        dh=channel_delivery_hours(me)
        if dh is None:
            raise RuntimeError('LV seller channel delivery_hours unavailable; refusing to guess')
        body={'seller_id':SELLER_ID,'app_name':APP,'ean':EAN,'sku':SKU,'delivery_hours':int(dh),'amount':AMOUNT,'sell_price':PRICE,'status':'active'}
        wr=requests.post(urljoin(BASE,'/v3/offers'),headers=headers(token),json=body,timeout=30)
        action='POST_CREATE'

    try: wj=wr.json()
    except Exception: wj={'raw':wr.text[:1500]}
    if not wr.ok:
        out={'status':'WRITE_ERROR','action':action,'http_status':wr.status_code,'response':wj,'sku':SKU,'ean':EAN,
             'safety':{'target_products':1,'other_product_writes':0,'Master_writes':0,'Shopify_writes':0}}
        print('PHH_NATUREHIKE_GALE_OFFER_V16 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
        return out

    oid=None
    if isinstance(wj,dict): oid=wj.get('id')
    elif isinstance(wj,list) and wj and isinstance(wj[0],dict): oid=wj[0].get('id')
    if not oid and existing: oid=existing.get('id')
    if not oid: raise RuntimeError(f'write succeeded but offer id missing: {str(wj)[:1000]}')

    rr=_api_get(f'/v3/offers/{oid}',token); rr.raise_for_status(); rd=rr.json()
    chk=verify_offer(rd)
    ok=all(chk[k] for k in ('identity_ok','price_ok','amount_ok','status_ok','app_ok'))
    out={'status':'PASS' if ok else 'VERIFY_FAILED','action':action,'http_status':wr.status_code,'offer_id':oid,
         'sku':SKU,'ean':EAN,'modification_id':MODIFICATION_ID,'requested_price':PRICE,'requested_amount':AMOUNT,
         'verification':chk,
         'safety':{'target_products':1,'other_product_writes':0,'Master_writes':0,'Shopify_writes':0}}
    print('PHH_NATUREHIKE_GALE_OFFER_V16 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
