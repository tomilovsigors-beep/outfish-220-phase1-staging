from __future__ import annotations
import json, os
from pmp_api_probe import _docs_get,_embedded_spec

TARGET='/{version}/sellers/product/import/execution/{executionId}'

def deref(s,schemas,depth=0):
    if depth>6 or not isinstance(s,dict): return s
    if '$ref' in s:
        n=s['$ref'].split('/')[-1]
        return {'ref':n,'schema':deref(schemas.get(n,{}),schemas,depth+1)}
    out={}
    for k,v in s.items():
        if k in ('type','required','enum','nullable','format','description','example'):
            out[k]=v
        elif k=='properties':
            out[k]={kk:deref(vv,schemas,depth+1) for kk,vv in v.items()}
        elif k=='items':
            out[k]=deref(v,schemas,depth+1)
        elif k in ('allOf','oneOf','anyOf'):
            out[k]=[deref(x,schemas,depth+1) for x in v]
    return out

def run():
    r=_docs_get('/docs'); r.raise_for_status()
    spec=_embedded_spec(r.text)
    schemas=((spec.get('components') or {}).get('schemas') or {})
    op=((spec.get('paths') or {}).get(TARGET) or {}).get('patch') or {}
    content=((op.get('requestBody') or {}).get('content') or {})
    sch=(content.get('application/json') or {}).get('schema')
    out={'status':'PASS','request_schema':deref(sch,schemas)}
    print('PHH_PRODUCT_IMPORT_SCHEMA_V19 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
