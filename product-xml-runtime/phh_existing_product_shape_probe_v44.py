from __future__ import annotations
from pmp_api_probe import _api_login,_api_get

EAN='0021563115413'
SKU='11541-013'

def run():
    lr=_api_login('v3')
    if lr is None or not lr.ok:
        return {'status':'LOGIN_FAILED','writes':0}
    token=lr.json().get('token')
    r=_api_get(f'/v3/products/product-modifications/barcodes?ean={EAN}',token)
    try: body=r.json()
    except Exception: body={'raw':r.text[:3000]}
    return {'status':'PASS' if r.ok else 'HTTP_ERROR','http_status':r.status_code,'ean':EAN,'sku':SKU,'body':body,'writes':0}
