from __future__ import annotations
import json
from pmp_api_probe import _api_login, _api_get

OFFER_ID=1041162678

DROP={'amount','sell_price','sell_price_after_discount','buybox_price','relevant_market_price','insult_price','price','stock'}

def redact(x):
    if isinstance(x,dict):
        return {k:redact(v) for k,v in x.items() if str(k).lower() not in DROP}
    if isinstance(x,list): return [redact(v) for v in x]
    return x

def run():
    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('login failed')
    token=lr.json().get('token')
    r=_api_get(f'/v3/offers/{OFFER_ID}',token); r.raise_for_status()
    data=redact(r.json())
    out={'status':'PASS','offer_id':OFFER_ID,'offer':data,
         'safety':{'PHH_writes':0,'stock_changes':0,'price_changes':0,'Master_writes':0,'Shopify_writes':0,'Product_XML':'OFF'}}
    print('PHH_SMOKE_CANDIDATE_DETAIL_V13D '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
