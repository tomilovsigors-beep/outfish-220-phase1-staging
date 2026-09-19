from __future__ import annotations
import json, os
from collections import Counter
from pathlib import Path
from phh_master_audit_v30 import scan_offers, _offer_skus, _offer_eans, _s
from pmp_api_probe import _api_login

SNAPSHOT=Path(__file__).with_name('shopify_inventory_snapshot_v38.json')

def run():
    snap=json.loads(SNAPSHOT.read_text())
    rows=snap.get('rows') or []
    lr=_api_login('v3'); lr.raise_for_status(); token=lr.json()['token']
    offers=scan_offers(token)
    by_sku={}
    by_ean={}
    for o in offers:
        for s in _offer_skus(o):
            by_sku.setdefault(s,[]).append(o)
        for e in _offer_eans(o):
            by_ean.setdefault(e,[]).append(o)
    out=[]; c=Counter()
    for x in rows:
        sku=_s(x.get('sku220')); ean=_s(x.get('ean220'))
        cand=[]
        if sku: cand += by_sku.get(sku,[])
        if ean: cand += by_ean.get(ean,[])
        uniq={_s(o.get('id')):o for o in cand if _s(o.get('id'))}
        offers_match=list(uniq.values())
        phh=None
        exact=[]
        for o in offers_match:
            oskus=_offer_skus(o); oeans=_offer_eans(o)
            if (sku and sku in oskus) or (ean and ean in oeans):
                exact.append(o)
        if len(exact)==1:
            phh=exact[0]
        elif len(exact)>1:
            active=[o for o in exact if _s(o.get('status')).lower()=='active']
            if len(active)==1: phh=active[0]
        inv=x.get('shopify_inventory_quantity')
        if phh is None:
            state='NO_UNIQUE_PHH_OFFER'
            c[state]+=1
            out.append({**x,'phh_offer_id':None,'phh_status':None,'phh_amount':None,'delta':None,'dry_run_state':state})
            continue
        amt=phh.get('amount')
        status=_s(phh.get('status'))
        try: delta=int(inv)-int(amt)
        except Exception: delta=None
        if status.lower()!='active':
            state='PHH_OFFER_NOT_ACTIVE'
        elif delta==0:
            state='IN_SYNC'
        elif delta is None:
            state='UNCOMPARABLE'
        elif delta>0:
            state='SHOPIFY_HIGHER'
        else:
            state='SHOPIFY_LOWER'
        c[state]+=1
        out.append({**x,'phh_offer_id':_s(phh.get('id')),'phh_status':status,'phh_amount':amt,'delta':delta,'dry_run_state':state})
    summary={
      'status':'PASS','rows':len(out),'state_counts':dict(c),
      'changed_rows':sum(1 for x in out if isinstance(x.get('delta'),int) and x['delta']!=0),
      'total_abs_delta':sum(abs(x['delta']) for x in out if isinstance(x.get('delta'),int)),
      'negative_shopify_rows':sum(1 for x in out if isinstance(x.get('shopify_inventory_quantity'),int) and x['shopify_inventory_quantity']<0),
      'zero_shopify_rows':sum(1 for x in out if x.get('shopify_inventory_quantity')==0),
      'safety':{'phh_writes':0,'master_writes':0,'shopify_writes':0},
      'publish_status':'DRY_RUN_ONLY'
    }
    print('PHH_STOCK_DIFF_V38_SUMMARY '+json.dumps(summary,sort_keys=True),flush=True)
    chunk=100; total=(len(out)+chunk-1)//chunk
    for i in range(total):
        print(f'PHH_STOCK_DIFF_V38 {i+1}/{total} '+json.dumps(out[i*chunk:(i+1)*chunk],ensure_ascii=False,separators=(',',':')),flush=True)
    return summary

if __name__=='__main__':
    run()
