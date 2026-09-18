from __future__ import annotations
import json
from pmp_api_probe import _docs_get,_embedded_spec

def _deref(schema, schemas, depth=0):
    if depth>4 or not isinstance(schema,dict): return schema
    if '$ref' in schema:
        name=schema['$ref'].split('/')[-1]
        return {'$ref':schema['$ref'],'resolved':_deref(schemas.get(name,{}),schemas,depth+1)}
    out={}
    for k,v in schema.items():
        if k in ('example','description','type','required','enum','nullable','format'):
            out[k]=v
        elif k in ('properties','items','allOf','oneOf','anyOf'):
            if isinstance(v,dict):
                out[k]={kk:_deref(vv,schemas,depth+1) for kk,vv in v.items()}
            elif isinstance(v,list):
                out[k]=[_deref(x,schemas,depth+1) for x in v]
            else: out[k]=v
    return out

def run():
    r=_docs_get('/docs'); r.raise_for_status()
    spec=_embedded_spec(r.text)
    if not spec: raise RuntimeError('openapi parse failed')
    schemas=(spec.get('components') or {}).get('schemas') or {}
    ops=[]
    for path,p in (spec.get('paths') or {}).items():
        hay=(path+' '+json.dumps(p,ensure_ascii=False)).lower()
        if 'offer' not in hay:
            continue
        for method in ('get','post','put','patch','delete'):
            op=(p or {}).get(method)
            if not isinstance(op,dict): continue
            body=((op.get('requestBody') or {}).get('content') or {}).get('application/json') or {}
            sch=body.get('schema')
            ops.append({
              'path':path,'method':method.upper(),'summary':op.get('summary'),'description':op.get('description'),
              'parameters':op.get('parameters'),
              'request_schema':_deref(sch,schemas) if sch else None,
              'responses':{k:{'description':(v or {}).get('description'),'schema':_deref((((v or {}).get('content') or {}).get('application/json') or {}).get('schema'),schemas) if (((v or {}).get('content') or {}).get('application/json') or {}).get('schema') else None} for k,v in (op.get('responses') or {}).items()}
            })
    out={'status':'PASS','offer_operations':ops}
    print('PHH_OFFER_CONTRACT_V16 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
