from __future__ import annotations
import json, os
from pmp_api_probe import _docs_get, _embedded_spec

def run():
    docs=_docs_get('/docs')
    docs.raise_for_status()
    spec=_embedded_spec(docs.text)
    if not spec: raise RuntimeError('OpenAPI not parsed')
    paths=spec.get('paths') or {}
    schemas=(spec.get('components') or {}).get('schemas') or {}
    selected_paths={}
    for p,ops in paths.items():
        h=(p+' '+json.dumps(ops,ensure_ascii=False)).lower()
        if 'product' in h and ('import' in h or 'seller' in h or 'execution' in h):
            selected_paths[p]=ops
    selected_schemas={}
    for name,sch in schemas.items():
        n=name.lower()
        if ('product' in n and ('import' in n or 'request' in n or 'response' in n)) or 'modificationimport' in n:
            selected_schemas[name]=sch
    out={'status':'PASS','paths':selected_paths,'schemas':selected_schemas,'path_count':len(selected_paths),'schema_count':len(selected_schemas),
         'safety':{'PHH_writes':0,'Master_writes':0,'Shopify_writes':0,'Product_XML':'OFF'}}
    print('PHH_LIVE_SMOKE_PREP_V13A '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
