from __future__ import annotations
import json, os, requests
from urllib.parse import urljoin
from pmp_api_probe import BASE, _api_login, _api_get

OFFER_ID=1041162678
EXPECTED_SKU='11541-013'
EXPECTED_EAN='0021563115413'
EXPECTED_APP='220.lv'

def run():
    if os.getenv('RUN_V13E_LIVE_WRITE','').strip()!='1':
        return {'status':'SKIPPED'}
    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('login failed')
    token=lr.json().get('token')
    before=_api_get(f'/v3/offers/{OFFER_ID}',token); before.raise_for_status(); b=before.json()
    mod=b.get('modification') or {}
    eans=[str(x) for x in (mod.get('ean_codes') or [])]
    if str(mod.get('sku') or '')!=EXPECTED_SKU: raise RuntimeError('SKU guard failed')
    if EXPECTED_EAN not in eans: raise RuntimeError('EAN guard failed')
    if str(b.get('app_name') or '')!=EXPECTED_APP: raise RuntimeError('app_name guard failed')
    if str(b.get('status') or '')!='active': raise RuntimeError('status guard failed')

    # Intentional no-op write against one already-existing 220.lv offer.
    # Only id + same current status are sent. No amount, prices, delivery, EAN, SKU or content fields are changed.
    body=[{'id':OFFER_ID,'status':'active'}]
    wr=requests.patch(urljoin(BASE,'/v3/offers'),json=body,timeout=30,
        headers={'User-Agent':'outfish-phh-live-smoke/13e','Accept':'application/json','Content-Type':'application/json','Authorization':'Pigu-mp '+token})
    result_sample=None
    try:
        result_sample=wr.json()
    except Exception:
        result_sample=wr.text[:500]
    if not wr.ok:
        raise RuntimeError(f'PATCH failed HTTP {wr.status_code}: {str(result_sample)[:1000]}')

    after=_api_get(f'/v3/offers/{OFFER_ID}',token); after.raise_for_status(); a=after.json()
    amod=a.get('modification') or {}
    aeans=[str(x) for x in (amod.get('ean_codes') or [])]
    verified=(
      str(amod.get('sku') or '')==EXPECTED_SKU and EXPECTED_EAN in aeans and
      str(a.get('app_name') or '')==EXPECTED_APP and str(a.get('status') or '')=='active'
    )
    out={
      'status':'PASS' if verified else 'VERIFY_FAILED',
      'http_status':wr.status_code,
      'offer_id':OFFER_ID,'sku':EXPECTED_SKU,'ean':EXPECTED_EAN,'app_name':EXPECTED_APP,
      'before_status':b.get('status'),'after_status':a.get('status'),
      'before_updated_at':b.get('updated_at'),'after_updated_at':a.get('updated_at'),
      'write_scope':'NO_OP_STATUS_REASSERT_ONLY',
      'response_type':type(result_sample).__name__,
      'safety':{'PHH_write_requests':1,'stock_changes_requested':0,'price_changes_requested':0,'delivery_changes_requested':0,
                'content_changes_requested':0,'Master_writes':0,'Shopify_writes':0,'EAN_changes_requested':0,'Product_XML':'OFF'}
    }
    print('PHH_LIVE_SMOKE_WRITE_V13E '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
