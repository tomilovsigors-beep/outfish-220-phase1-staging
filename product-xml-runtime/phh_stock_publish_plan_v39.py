from __future__ import annotations
import json
from collections import Counter
from phh_stock_diff_v38 import run as diff_run

def run():
    # Recompute against live PHH using the frozen Shopify snapshot.
    # Still read-only: no PHH/Master/Shopify writes.
    diff_summary = diff_run()
    # phh_stock_diff_v38 emits full rows to logs but does not return them,
    # so v39 intentionally recomputes locally from the same source logic.
    import phh_stock_diff_v38 as v38
    snap=json.loads(v38.SNAPSHOT.read_text())
    rows=snap.get('rows') or []
    from phh_master_audit_v30 import scan_offers, _offer_skus, _offer_eans, _s
    from pmp_api_probe import _api_login
    lr=_api_login('v3'); lr.raise_for_status(); token=lr.json()['token']
    offers=scan_offers(token)
    by_sku={}; by_ean={}
    for o in offers:
        for s in _offer_skus(o): by_sku.setdefault(s,[]).append(o)
        for e in _offer_eans(o): by_ean.setdefault(e,[]).append(o)

    out=[]; counts=Counter()
    for x in rows:
        sku=_s(x.get('sku220')); ean=_s(x.get('ean220'))
        cand=[]
        if sku: cand+=by_sku.get(sku,[])
        if ean: cand+=by_ean.get(ean,[])
        uniq={_s(o.get('id')):o for o in cand if _s(o.get('id'))}
        exact=list(uniq.values())
        phh=None
        if len(exact)==1:
            phh=exact[0]
        elif len(exact)>1:
            active=[o for o in exact if _s(o.get('status')).lower()=='active']
            if len(active)==1: phh=active[0]

        inv=x.get('shopify_inventory_quantity')
        state='BLOCKED_NO_UNIQUE_PHH_OFFER'
        target=None; current=None; delta=None; offer_id=None; status=None
        if phh is not None:
            offer_id=_s(phh.get('id')); status=_s(phh.get('status')); current=phh.get('amount')
            try: delta=int(inv)-int(current)
            except Exception: delta=None
            if status.lower()!='active':
                state='BLOCKED_PHH_OFFER_NOT_ACTIVE'
            elif not isinstance(inv,int):
                state='BLOCKED_SHOPIFY_INVENTORY_MISSING'
            elif inv<0:
                state='BLOCKED_NEGATIVE_SHOPIFY_INVENTORY'
            elif delta==0:
                state='NO_CHANGE'
                target=inv
            else:
                state='READY_STOCK_UPDATE'
                target=inv

        counts[state]+=1
        out.append({**x,'phh_offer_id':offer_id,'phh_status':status,'phh_amount':current,
                    'shopify_inventory_quantity':inv,'proposed_target_amount':target,'delta':delta,
                    'publish_plan_state':state})

    ready=[x for x in out if x['publish_plan_state']=='READY_STOCK_UPDATE']
    down=[x for x in ready if x['delta']<0]
    up=[x for x in ready if x['delta']>0]
    summary={
      'status':'PASS','rows':len(out),'state_counts':dict(counts),
      'ready_updates':len(ready),'downward_updates':len(down),'upward_updates':len(up),
      'total_units_decrease':sum(-x['delta'] for x in down),
      'total_units_increase':sum(x['delta'] for x in up),
      'max_downward_delta':min([x['delta'] for x in down],default=0),
      'max_upward_delta':max([x['delta'] for x in up],default=0),
      'authorization_required_for_publish':True,
      'publish_status':'DRY_RUN_ONLY',
      'safety':{'phh_writes':0,'master_writes':0,'shopify_writes':0}
    }
    print('PHH_STOCK_PUBLISH_PLAN_V39_SUMMARY '+json.dumps(summary,sort_keys=True),flush=True)
    print('PHH_STOCK_PUBLISH_PLAN_V39_BLOCKED '+json.dumps(
        [x for x in out if x['publish_plan_state'].startswith('BLOCKED_')],
        ensure_ascii=False,separators=(',',':')),flush=True)
    # Print only top risk deltas, not all 302 candidates.
    down_sorted=sorted(down,key=lambda x:x['delta'])
    up_sorted=sorted(up,key=lambda x:x['delta'],reverse=True)
    print('PHH_STOCK_PUBLISH_PLAN_V39_TOP_DOWN '+json.dumps(down_sorted[:30],ensure_ascii=False,separators=(',',':')),flush=True)
    print('PHH_STOCK_PUBLISH_PLAN_V39_TOP_UP '+json.dumps(up_sorted[:30],ensure_ascii=False,separators=(',',':')),flush=True)
    return summary

if __name__=='__main__':
    run()
