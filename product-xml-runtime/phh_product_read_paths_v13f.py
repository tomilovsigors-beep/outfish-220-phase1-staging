from __future__ import annotations
import json
from pmp_api_probe import _docs_get,_embedded_spec

def run():
    d=_docs_get('/docs'); d.raise_for_status(); spec=_embedded_spec(d.text)
    out={}
    for p,ops in (spec.get('paths') or {}).items():
        h=p.lower()
        if any(k in h for k in ('product-modification','products/product','barcodes')):
            out[p]=ops
    print('PHH_PRODUCT_READ_PATHS_V13F '+json.dumps({'status':'PASS','paths':out},ensure_ascii=False,sort_keys=True),flush=True)
    return out
