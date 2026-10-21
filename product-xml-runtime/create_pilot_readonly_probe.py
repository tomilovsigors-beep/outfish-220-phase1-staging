"""Read-only follow-up for successful execution 58718648. No mutations."""
import json,re,requests
from pmp_api_probe import _api_login,_api_get
def run():
    lr=_api_login('v3');lr.raise_for_status();token=lr.json()['token']
    reads=['/v3/sellers/9990696/product/import/execution/58718648/results?limit=100&offset=0','/v3/product-modification/auto-check-error-validators']
    reads += ['/v3/products/product-modifications/barcodes?ean='+e for e in ('5908221354191','5908221354207','5908221354214','5908221359509','5908221359516','5908221359523')]
    for path in reads:
        rr=_api_get(path,token)
        try:b=rr.json()
        except Exception:b={'raw':rr.text[:1500]}
        print('OWNER_RETRY1_VERIFY '+json.dumps({'path':path,'http':rr.status_code,'body':b},ensure_ascii=False),flush=True)
    rr=requests.get('https://pmp.pigugroup.eu/build/w5VTGQ-C.js',timeout=40)
    strings=re.findall(r'["\x27]([^"\x27]{1,240})["\x27]',rr.text)
    relevant=sorted(set(s for s in strings if ('product' in s.lower() or 'modification' in s.lower()) and (s.startswith('/') or 'http' in s)))
    print('OWNER_RETRY1_PORTAL_ROUTES '+json.dumps({'http':rr.status_code,'paths':relevant},ensure_ascii=False),flush=True)
    return {'writes':0}
