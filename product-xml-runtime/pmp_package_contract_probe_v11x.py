from __future__ import annotations
import json
from pmp_api_probe import _docs_get, _embedded_spec

def run():
    r=_docs_get('/docs'); r.raise_for_status()
    spec=_embedded_spec(r.text)
    if not spec: raise RuntimeError('OpenAPI not parsed')
    schemas=((spec.get('components') or {}).get('schemas') or {})
    hits={}
    for name,schema in schemas.items():
        raw=json.dumps(schema,ensure_ascii=False).lower()
        if any(k in raw for k in ('package_weight','package_length','package_width','package_height')):
            props=(schema or {}).get('properties') or {}
            selected={}
            for k,v in props.items():
                if k in {'package_weight','package_length','package_width','package_height'}:
                    selected[k]={x:v.get(x) for x in ('type','format','title','description','example','minimum','maximum','default') if v.get(x) is not None}
            hits[name]={'required':(schema or {}).get('required') or [],'package_properties':selected}
    out={'status':'PASS','schemas':hits,'safety':{'PHH_writes':0,'Master_writes':0,'Shopify_writes':0}}
    print('PMP_PACKAGE_CONTRACT_V11X '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
