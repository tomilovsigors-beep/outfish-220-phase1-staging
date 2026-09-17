from __future__ import annotations
import json, os, re
from collections import Counter, defaultdict
from pmp_api_probe import _docs_get,_embedded_spec,_api_login,_api_get

KEYWORDS=('commission','fee','delivery','shipping','return','refund','seller_price','seller amount','seller_amount','payout','payment','invoice','service','logistic','courier','price')

def prune(obj,depth=0):
    if depth>5: return None
    if isinstance(obj,dict):
        out={}
        for k,v in obj.items():
            hay=str(k).lower()+' '+(str(v).lower() if not isinstance(v,(dict,list)) else '')
            if any(w in hay for w in KEYWORDS) or isinstance(v,(dict,list)):
                pv=prune(v,depth+1)
                if pv not in ({},[],None): out[k]=pv
        return out
    if isinstance(obj,list):
        vals=[prune(v,depth+1) for v in obj[:20]]
        return [v for v in vals if v not in ({},[],None)]
    return obj

def safe_order_summary(order):
    # Keep only economic/logistics fields; exclude names, addresses, phones, email.
    out={}
    def walk(x,path=''):
        if isinstance(x,dict):
            for k,v in x.items():
                p=f'{path}.{k}' if path else k
                kl=k.lower()
                if any(s in kl for s in ('name','phone','email','address','comment')): continue
                if isinstance(v,(dict,list)): walk(v,p)
                elif any(w in p.lower() for w in KEYWORDS) or kl in ('id','external_id','app_name','order_status','payment_status','total_price','price','amount','quantity','created_at'):
                    out[p]=v
        elif isinstance(x,list):
            for i,v in enumerate(x[:20]): walk(v,f'{path}[{i}]')
    walk(order)
    return out

def run():
    d=_docs_get('/docs'); d.raise_for_status(); spec=_embedded_spec(d.text)
    schemas=(spec.get('components') or {}).get('schemas') or {}
    interesting={}
    for n,s in schemas.items():
        blob=(n+' '+json.dumps(s,ensure_ascii=False)).lower()
        if any(w in blob for w in KEYWORDS):
            p=prune(s)
            if p not in ({},None): interesting[n]=p

    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('login failed')
    token=lr.json().get('token')
    me=_api_get('/v3/sellers/me',token); me.raise_for_status(); md=me.json()
    seller_id=md.get('id') or (md.get('seller') or {}).get('id')

    order_probes={}
    for version in ('v3','v4'):
        path=f'/{version}/sellers/{seller_id}/orders?limit=20&offset=0'
        rr=_api_get(path,token)
        info={'status':rr.status_code}
        if rr.ok:
            data=rr.json()
            items=(data.get('orders') if isinstance(data,dict) else None) or (data.get('items') if isinstance(data,dict) else None) or (data if isinstance(data,list) else [])
            info['count']=len(items)
            info['orders']=[safe_order_summary(x) for x in items[:10]]
            info['top_keys']=list(data.keys()) if isinstance(data,dict) else ['list']
        else: info['sample']=rr.text[:500]
        order_probes[path]=info

    # Returns-list endpoint, read only.
    return_probes={}
    for version in ('v3','v4'):
        for path in (f'/{version}/returns/{seller_id}/returns-list?limit=20&offset=0',):
            rr=_api_get(path,token)
            info={'status':rr.status_code}
            if rr.ok:
                data=rr.json(); info['shape']=list(data.keys()) if isinstance(data,dict) else 'list'
                info['sample']=prune(data)
            else: info['sample']=rr.text[:500]
            return_probes[path]=info

    out={'status':'PASS','seller_id':seller_id,'schemas':interesting,'order_probes':order_probes,'return_probes':return_probes,
         'safety':{'PHH_writes':0,'stock_changes':0,'price_changes':0,'Master_writes':0,'Shopify_writes':0}}
    print('PHH_UNIT_ECONOMICS_PROBE_V14B '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
