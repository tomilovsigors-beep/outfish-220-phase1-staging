from __future__ import annotations

import html as html_lib
import json
import os
import re
from urllib.parse import urljoin
import requests

BASE="https://pmpapi.pigugroup.eu"

def _auth():
    u=os.getenv('PMP_DOCS_USERNAME','').strip(); p=os.getenv('PMP_DOCS_PASSWORD','')
    if not u or not p: raise RuntimeError('PMP docs credentials are not configured')
    return u,p

def _get(path, *, api_token=None, timeout=25):
    url=path if str(path).startswith('http') else urljoin(BASE,str(path))
    headers={'User-Agent':'outfish-phh-readonly-discovery/5.0','Accept':'application/json,text/html;q=0.9,*/*;q=0.5'}
    auth=_auth()
    if api_token:
        headers['Authorization']='Bearer '+api_token; auth=None
    return requests.get(url,auth=auth,headers=headers,timeout=timeout,allow_redirects=True)

def _shape(v,depth=0):
    if depth>5:return type(v).__name__
    if isinstance(v,dict):return {str(k):_shape(x,depth+1) for k,x in list(v.items())[:40]}
    if isinstance(v,list):return {'type':'list','length':len(v),'sample':_shape(v[0],depth+1) if v else None}
    if isinstance(v,str):return {'type':'str','sample':v[:120]}
    if v is None:return None
    return {'type':type(v).__name__,'sample':v}

def _try_json(s):
    try:
        v=json.loads(s)
        if isinstance(v,str):
            try:return json.loads(v)
            except Exception:return v
        return v
    except Exception:return None

def _embedded_spec(text):
    # The docs page embeds the OpenAPI document as an escaped JSON blob. Try script bodies,
    # HTML-unescaped text, and decoded quoted strings. No credentials are included in page content.
    variants=[text,html_lib.unescape(text)]
    for s in list(variants):
        for m in re.finditer(r'<script[^>]*>(.*?)</script>',s,re.I|re.S):
            variants.append(m.group(1))
    decoder=json.JSONDecoder()
    for src in variants:
        probes=[]
        for needle in ('{"openapi"','{"swagger"','{"paths"','{\\"openapi\\"','{\\"paths\\"'):
            start=0
            while True:
                i=src.find(needle,start)
                if i<0:break
                probes.append((i,src[i:i+2_500_000])); start=i+1
        for _,blob in probes:
            for candidate in (blob, blob.replace('\\"','"').replace('\\/','/')):
                try:
                    obj,end=decoder.raw_decode(candidate)
                    if isinstance(obj,dict) and isinstance(obj.get('paths'),dict) and len(obj['paths'])>5:return obj
                except Exception:pass
        # Look for a JSON quoted string that contains an OpenAPI document.
        for m in re.finditer(r'"((?:\\.|[^"\\]){10000,})"',src,re.S):
            q='"'+m.group(1)+'"'; decoded=_try_json(q)
            if isinstance(decoded,str) and ('"paths"' in decoded or '\\"paths\\"' in decoded):
                obj=_try_json(decoded)
                if isinstance(obj,dict) and isinstance(obj.get('paths'),dict):return obj
    return None

def _op_summary(path,op):
    if not isinstance(op,dict):return None
    return {'path':path,'operationId':op.get('operationId'),'summary':op.get('summary'),'description':(op.get('description') or '')[:1200],
            'parameters':op.get('parameters') or [],'requestBody':op.get('requestBody'),'responses':op.get('responses'),'security':op.get('security')}

def discover():
    docs=_get('/docs'); text=docs.text if docs.ok else ''
    result={'docs':{'status':docs.status_code,'content_type':docs.headers.get('content-type'),'bytes':len(docs.content),'final_url':docs.url}}
    spec=_embedded_spec(text)
    if not spec:
        result['status']='EMBEDDED_SPEC_NOT_PARSED'; result['openapi_summary']={'source':'authenticated_docs_html','embedded_spec_parsed':False}; return result
    paths=spec.get('paths') or {}; comps=spec.get('components') or {}
    category_ops=[]; auth_ops=[]; import_ops=[]
    for p,ops in paths.items():
        if not isinstance(ops,dict):continue
        for method,op in ops.items():
            if method.lower() not in {'get','post','put','patch','delete'}:continue
            hay=(p+' '+str((op or {}).get('summary') or '')+' '+str((op or {}).get('operationId') or '')+' '+str((op or {}).get('tags') or '')).lower()
            row={'method':method.upper(),**(_op_summary(p,op) or {})}
            if 'categor' in hay:category_ops.append(row)
            if any(k in hay for k in ('login','token','auth','jwt','authorization')):auth_ops.append(row)
            if 'product import' in hay or '/product/import/' in p:import_ops.append(row)
    security=comps.get('securitySchemes') or {}
    schemas=comps.get('schemas') or {}
    category_schema_names=[k for k in schemas if any(x in k.lower() for x in ('categor','feature','attribute','property','field','value'))]
    product_schema_names=[k for k in schemas if 'productimport' in k.lower()]
    result['openapi_summary']={
        'source':'embedded_openapi','embedded_spec_parsed':True,'openapi':spec.get('openapi'),'title':(spec.get('info') or {}).get('title'),
        'version':(spec.get('info') or {}).get('version'),'path_count':len(paths),'security_schemes':security,
        'category_operations':category_ops[:30],'auth_operations':auth_ops[:30],'product_import_operations':import_ops[:20],
        'category_schema_names':category_schema_names[:80],'product_import_schema_names':product_schema_names[:50]
    }
    result['category_schemas']={k:schemas[k] for k in category_schema_names[:50]}
    result['product_import_schemas']={k:schemas[k] for k in product_schema_names[:20]}
    result['status']='PASS'
    return result
