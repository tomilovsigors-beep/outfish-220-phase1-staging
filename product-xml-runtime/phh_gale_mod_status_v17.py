from __future__ import annotations
import json, os
from pmp_api_probe import _api_login,_api_get

SELLER_ID=9990696
SKU='CNK2450DS012G'
EAN='6975641883678'
PIGU_EXTERNAL_ID=297272175

def run():
    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('PHH login failed')
    token=lr.json().get('token')
    out={'status':'PASS','sku':SKU,'ean':EAN,'pigu_external_id':PIGU_EXTERNAL_ID,'reads':{},'safety':{'PHH_writes':0,'stock_changes':0,'price_changes':0}}
    paths={
      'barcode_lookup':f'/v3/products/product-modifications/barcodes?ean={EAN}',
      'auto_check_errors':f'/v3/product-modification/pigu-external-id/{PIGU_EXTERNAL_ID}/auto-check-errors',
      'offers_by_mod_v3':f'/v3/sellers/{SELLER_ID}/offers?app_name=220.lv&modifications%5B%5D={PIGU_EXTERNAL_ID}',
      'offers_by_pigu_external_v3':f'/v3/sellers/{SELLER_ID}/offers?app_name=220.lv&pigu_external_ids%5B%5D={PIGU_EXTERNAL_ID}',
    }
    for k,p in paths.items():
        r=_api_get(p,token)
        try: body=r.json()
        except Exception: body={'raw':r.text[:2000]}
        out['reads'][k]={'http_status':r.status_code,'body':body}
    print('PHH_GALE_MOD_STATUS_V17 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
