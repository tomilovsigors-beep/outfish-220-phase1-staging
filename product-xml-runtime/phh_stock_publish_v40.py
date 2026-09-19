from __future__ import annotations
import csv, io, json, os, time
from pathlib import Path
from urllib.parse import urljoin
import requests
from pmp_api_probe import BASE, _api_login
from phh_master_audit_v30 import scan_offers, _offer_skus, _offer_eans, _s

SELLER_ID='9990696'
APP='220.lv'
CSV_PATH=Path(__file__).with_name('phh_stock_change_set_v40.csv')
SHOPIFY_PREFLIGHT=Path(__file__).with_name('phh_stock_v40_shopify_preflight.json')

def _headers(token):
    return {'User-Agent':'outfish-phh-stock-v40/1.0','Accept':'application/json','Content-Type':'application/json','Authorization':'Pigu-mp '+token}

def _login():
    last=None
    for attempt in range(6):
        r=_api_login('v3',timeout=30)
        last=r
        if r is not None and r.status_code==200:
            return r.json()['token']
        if r is not None and r.status_code==429:
            time.sleep(2.0*(attempt+1)); continue
        if r is not None:
            r.raise_for_status()
    raise RuntimeError(f'PHH login failed after retries: {None if last is None else last.status_code}')

def _read_approved():
    text=CSV_PATH.read_text()
    rows=list(csv.DictReader(io.StringIO(text)))
    ready=[]
    for r in rows:
        if r.get('state')!='READY_STOCK_UPDATE':
            continue
        ready.append({
            'row':int(r['row']),
            'vendor':r['vendor'],
            'title':r['title'],
            'shopify_sku':r['shopify_sku'],
            'sku220':r['220_sku'],
            'ean220':r['220_ean'],
            'shopify_variant_id':r['shopify_variant_id'],
            'offer_id':str(r['phh_offer_id']),
            'approved_phh_amount':int(r['phh_amount']),
            'target_amount':int(r['proposed_amount']),
            'delta':int(r['delta'])
        })
    return ready

def _shopify_unchanged_set():
    d=json.loads(SHOPIFY_PREFLIGHT.read_text())
    return {int(x['row']) for x in d.get('unchanged',[])}, d

def _offer_index(offers):
    return {_s(o.get('id')):o for o in offers if _s(o.get('id'))}

def _phh_preflight(approved, offers):
    byid=_offer_index(offers)
    ok=[]; blocked=[]
    for x in approved:
        o=byid.get(x['offer_id'])
        if not o:
            blocked.append({**x,'reason':'OFFER_NOT_FOUND'}); continue
        status=_s(o.get('status')).lower()
        amount=o.get('amount')
        skus=_offer_skus(o); eans=_offer_eans(o)
        reasons=[]
        if status!='active': reasons.append('OFFER_NOT_ACTIVE')
        try: live_amt=int(amount)
        except Exception: live_amt=None
        if live_amt!=x['approved_phh_amount']: reasons.append('PHH_AMOUNT_CHANGED')
        if x['sku220'] not in skus and x['ean220'] not in eans: reasons.append('IDENTITY_CHANGED')
        if reasons:
            blocked.append({**x,'reason':'|'.join(reasons),'live_status':status,'live_amount':amount,'live_skus':sorted(skus),'live_eans':sorted(eans)})
        else:
            ok.append({**x,'live_status':status,'live_amount':live_amt})
    return ok,blocked

def _patch(token, rows):
    url=urljoin(BASE,'/v3/offers')
    payload=[{'id':int(x['offer_id']),'amount':int(x['target_amount'])} for x in rows]
    for attempt in range(5):
        r=requests.patch(url,headers=_headers(token),json=payload,timeout=60)
        if r.status_code==200:
            try: return r.json()
            except Exception: return {'raw':r.text}
        if r.status_code==429:
            time.sleep(2.0*(attempt+1)); continue
        raise RuntimeError(f'PATCH failed HTTP {r.status_code}: {r.text[:1000]}')
    raise RuntimeError('PATCH failed after 429 retries')

def run():
    approved=_read_approved()
    unchanged,sp=_shopify_unchanged_set()
    approved=[x for x in approved if x['row'] in unchanged]
    print('PHH_STOCK_V40_STAGE '+json.dumps({'stage':'approved_loaded','approved_rows':len(approved),'shopify_preflight_changed':sp.get('changed_rows'),'shopify_preflight_missing':sp.get('missing_rows')}),flush=True)
    if len(approved)!=302:
        raise RuntimeError(f'Expected 302 approved unchanged rows, got {len(approved)}')

    token=_login()
    before=scan_offers(token)
    ok,blocked=_phh_preflight(approved,before)
    print('PHH_STOCK_V40_PREFLIGHT '+json.dumps({'approved':len(approved),'write_ready':len(ok),'blocked':len(blocked),'blocked_rows':blocked[:50]},ensure_ascii=False),flush=True)
    if blocked:
        # User approved exact v40; skip conflicts rather than recalculating.
        print('PHH_STOCK_V40_NOTE exact-conflict rows skipped',flush=True)
    if not ok:
        raise RuntimeError('No rows passed PHH preflight')

    # Chunked exact writes; stop immediately on any failed chunk.
    chunks=[ok[i:i+50] for i in range(0,len(ok),50)]
    written=[]
    for i,ch in enumerate(chunks,1):
        resp=_patch(token,ch)
        written.extend(ch)
        print('PHH_STOCK_V40_WRITE_CHUNK '+json.dumps({'chunk':i,'chunks':len(chunks),'rows':len(ch),'written_total':len(written)}),flush=True)
        time.sleep(0.4)

    # Exact post-read verification.
    time.sleep(1.0)
    after=scan_offers(token)
    byid=_offer_index(after)
    verified=[]; failed=[]
    for x in written:
        o=byid.get(x['offer_id'])
        if not o:
            failed.append({**x,'reason':'OFFER_NOT_FOUND_POSTWRITE'}); continue
        try: amt=int(o.get('amount'))
        except Exception: amt=None
        status=_s(o.get('status')).lower()
        if amt==x['target_amount'] and status=='active':
            verified.append({**x,'post_amount':amt,'post_status':status})
        else:
            failed.append({**x,'reason':'POSTWRITE_MISMATCH','post_amount':amt,'post_status':status})
    summary={
        'status':'PASS' if not failed else 'PARTIAL',
        'approved_rows':302,
        'shopify_preflight_unchanged':len(approved),
        'phh_preflight_ready':len(ok),
        'phh_preflight_blocked':len(blocked),
        'phh_write_attempted':len(written),
        'phh_write_verified':len(verified),
        'phh_postwrite_failed':len(failed),
        'total_units_decrease':sum(-x['delta'] for x in verified if x['delta']<0),
        'total_units_increase':sum(x['delta'] for x in verified if x['delta']>0),
        'master_writes':0,'shopify_writes':0,
        'phh_writes':len(written)
    }
    print('PHH_STOCK_V40_SUMMARY '+json.dumps(summary,sort_keys=True),flush=True)
    if blocked:
        print('PHH_STOCK_V40_BLOCKED '+json.dumps(blocked,ensure_ascii=False,separators=(',',':')),flush=True)
    if failed:
        print('PHH_STOCK_V40_POSTWRITE_FAILED '+json.dumps(failed,ensure_ascii=False,separators=(',',':')),flush=True)
    return summary

if __name__=='__main__':
    run()
