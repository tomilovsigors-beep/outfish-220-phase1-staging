from __future__ import annotations
import json
from pmp_api_probe import _docs_get,_embedded_spec

def run():
    r=_docs_get('/docs'); r.raise_for_status()
    spec=_embedded_spec(r.text)
    out=[]
    for path,methods in (spec.get('paths') or {}).items():
        hay=(path+' '+json.dumps(methods,ensure_ascii=False)).lower()
        if not any(k in hay for k in ('cn8','cn code','origin country','country of origin','origin_country','customs')):
            continue
        item={'path':path,'methods':{}}
        for m in ('get','post','put','patch','delete'):
            op=(methods or {}).get(m)
            if isinstance(op,dict):
                item['methods'][m.upper()]={'summary':op.get('summary'),'description':op.get('description'),'parameters':op.get('parameters'),'requestBody':op.get('requestBody')}
        if item['methods']: out.append(item)
    schemas={}
    for name,schema in ((spec.get('components') or {}).get('schemas') or {}).items():
        hay=(name+' '+json.dumps(schema,ensure_ascii=False)).lower()
        if any(k in hay for k in ('cn8','cn code','origin country','country of origin','origin_country','customs')):
            schemas[name]=schema
    return {'status':'PASS','operations':out,'schemas':schemas,'writes':0}
