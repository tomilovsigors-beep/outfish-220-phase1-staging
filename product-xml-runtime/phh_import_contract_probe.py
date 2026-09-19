from __future__ import annotations
import json
from pmp_api_probe import _docs_get,_embedded_spec

def run():
    r=_docs_get('/docs'); r.raise_for_status()
    spec=_embedded_spec(r.text)
    if not spec: raise RuntimeError('OpenAPI spec not parsed')
    out=[]
    for path,ops in sorted((spec.get('paths') or {}).items()):
        hay=(path+' '+json.dumps(ops,ensure_ascii=False)).lower()
        if 'import' not in hay and 'feed' not in hay and 'stock' not in hay:
            continue
        methods=[]
        for method in ('get','post','put','patch','delete'):
            op=(ops or {}).get(method)
            if not isinstance(op,dict): continue
            methods.append({
                'method':method.upper(),'summary':op.get('summary'),'operationId':op.get('operationId'),
                'parameters':op.get('parameters'),'requestBody':op.get('requestBody'),'responses':op.get('responses')
            })
        if methods: out.append({'path':path,'operations':methods})
    print('PHH_IMPORT_CONTRACT_PROBE '+json.dumps(out,ensure_ascii=False,separators=(',',':')),flush=True)
    return {'status':'PASS','paths':len(out)}

if __name__=='__main__': run()
