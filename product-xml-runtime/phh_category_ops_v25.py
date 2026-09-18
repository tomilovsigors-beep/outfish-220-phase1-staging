from __future__ import annotations
import json
from pmp_api_probe import _docs_get,_embedded_spec
def run():
    r=_docs_get('/docs'); r.raise_for_status()
    spec=_embedded_spec(r.text)
    out=[]
    for path,methods in (spec.get('paths') or {}).items():
        hay=(path+' '+json.dumps(methods,ensure_ascii=False)).lower()
        if 'categor' not in hay: continue
        item={'path':path,'methods':{}}
        for m in ('get','post','put','patch','delete'):
            op=(methods or {}).get(m)
            if isinstance(op,dict):
                item['methods'][m.upper()]={'summary':op.get('summary'),'description':op.get('description'),'parameters':op.get('parameters')}
        if item['methods']: out.append(item)
    print('PHH_CATEGORY_OPS_V25 '+json.dumps({'status':'PASS','operations':out},ensure_ascii=False,sort_keys=True),flush=True)
    return {'status':'PASS','operations':out}
