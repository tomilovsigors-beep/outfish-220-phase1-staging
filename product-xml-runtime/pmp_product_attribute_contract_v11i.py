from __future__ import annotations
import json, re
import requests
import pmp_api_probe as p

KEYS=('field','attribute','value','unit','option','enum','category','product-modification','import')

def _walk_hits(obj,path='$',out=None):
    if out is None: out=[]
    if isinstance(obj,dict):
        for k,v in obj.items():
            np=f'{path}.{k}'
            lk=str(k).lower()
            if any(x in lk for x in KEYS):
                out.append({'path':np,'value':v if not isinstance(v,(dict,list)) else None})
            _walk_hits(v,np,out)
    elif isinstance(obj,list):
        for i,v in enumerate(obj): _walk_hits(v,f'{path}[{i}]',out)
    return out

def run():
    docs=p._docs_get('/docs')
    spec=p._embedded_spec(docs.text) if docs.ok else None
    if not isinstance(spec,dict): raise RuntimeError('OpenAPI spec unavailable')
    paths=spec.get('paths') or {}; schemas=(spec.get('components') or {}).get('schemas') or {}
    ops=[]
    refs=set()
    for path,item in paths.items():
        if not isinstance(item,dict): continue
        hay=path.lower()+' '+json.dumps(item,ensure_ascii=False).lower()
        if not any(k in hay for k in ('product','attribute','field','category','import','modification')): continue
        for method,op in item.items():
            if method.lower() not in {'get','post','put','patch','delete'} or not isinstance(op,dict): continue
            if not any(k in (path+' '+str(op.get('summary'))+' '+str(op.get('operationId'))).lower() for k in ('product','attribute','field','category','import','modification')): continue
            rb=op.get('requestBody'); responses=op.get('responses')
            txt=json.dumps({'requestBody':rb,'responses':responses},ensure_ascii=False)
            refs.update(re.findall(r'#/components/schemas/([A-Za-z0-9_]+)',txt))
            ops.append({'path':path,'method':method.upper(),'summary':op.get('summary'),'operationId':op.get('operationId'),'requestBody':rb})
    # recursively include referenced schemas
    expanded={}; todo=list(refs); seen=set()
    while todo:
        name=todo.pop()
        if name in seen or name not in schemas: continue
        seen.add(name); sc=schemas[name]; expanded[name]=sc
        txt=json.dumps(sc,ensure_ascii=False)
        for r in re.findall(r'#/components/schemas/([A-Za-z0-9_]+)',txt):
            if r not in seen: todo.append(r)
    # include any schema whose name/body clearly concerns product fields/attributes/values
    for name,sc in schemas.items():
        hay=(name+' '+json.dumps(sc,ensure_ascii=False)).lower()
        if any(k in hay for k in ('field_id','fieldid','attributes','attribute','value_id','valueid','unit')):
            expanded.setdefault(name,sc)
    # safe live GET: validator definitions only
    login=p._api_login('v3'); token=p._extract_token(login.json()) if login.ok else None
    validator={'status':None,'json':None}
    if token:
        r=p._api_get('/v3/product-modification/auto-check-error-validators',token)
        validator={'status':r.status_code,'json':r.json() if r.ok else None}
    hits=_walk_hits({'operations':ops,'schemas':expanded,'validator':validator})
    summary={'status':'PASS','relevant_operations':len(ops),'referenced_and_semantic_schemas':len(expanded),'validator_http_status':validator['status'],'field_value_unit_hits':len(hits),'marketplace_mutations':0,'writes':0}
    out={'summary':summary,'operations':ops,'schemas':expanded,'validator':validator,'semantic_hits':hits[:5000]}
    print('PMP_PRODUCT_ATTRIBUTE_CONTRACT_V11I '+json.dumps(out,ensure_ascii=False,sort_keys=True)[:180000],flush=True)
    return out
