from __future__ import annotations

import html as html_lib
import json
import os
import re
from urllib.parse import urljoin
import requests

BASE="https://pmpapi.pigugroup.eu"

def _creds():
    u=os.getenv('PMP_DOCS_USERNAME','').strip(); p=os.getenv('PMP_DOCS_PASSWORD','')
    if not u or not p: raise RuntimeError('PMP credentials are not configured')
    return u,p

def _docs_get(path,timeout=25):
    return requests.get(urljoin(BASE,path),auth=_creds(),timeout=timeout,headers={'User-Agent':'outfish-phh-readonly-discovery/6.0'})

def _api_get(path,token,timeout=25):
    return requests.get(urljoin(BASE,path),timeout=timeout,headers={'User-Agent':'outfish-phh-readonly-discovery/6.0','Accept':'application/json','Authorization':'Pigu-mp '+token})

def _api_login(version='v3',timeout=25):
    u,p=_creds()
    return requests.post(urljoin(BASE,f'/{version}/login'),json={'username':u,'password':p},timeout=timeout,headers={'User-Agent':'outfish-phh-readonly-discovery/6.0','Accept':'application/json'})

def _embedded_spec(text):
    variants=[text,html_lib.unescape(text)]
    for s in list(variants):
        variants += [m.group(1) for m in re.finditer(r'<script[^>]*>(.*?)</script>',s,re.I|re.S)]
    decoder=json.JSONDecoder()
    for src in variants:
        for needle in ('{"openapi"','{"swagger"','{"paths"','{\\"openapi\\"','{\\"paths\\"'):
            start=0
            while True:
                i=src.find(needle,start)
                if i<0:break
                blob=src[i:i+3_000_000]
                for candidate in (blob,blob.replace('\\"','"').replace('\\/','/')):
                    try:
                        obj,_=decoder.raw_decode(candidate)
                        if isinstance(obj,dict) and isinstance(obj.get('paths'),dict) and len(obj['paths'])>5:return obj
                    except Exception:pass
                start=i+1
        for m in re.finditer(r'"((?:\\.|[^"\\]){10000,})"',src,re.S):
            try:
                decoded=json.loads('"'+m.group(1)+'"')
                if isinstance(decoded,str) and '"paths"' in decoded:
                    obj=json.loads(decoded)
                    if isinstance(obj,dict) and isinstance(obj.get('paths'),dict):return obj
            except Exception:pass
    return None

def _shape(v,depth=0):
    if depth>5:return type(v).__name__
    if isinstance(v,dict):return {str(k):_shape(x,depth+1) for k,x in list(v.items())[:50]}
    if isinstance(v,list):return {'type':'list','length':len(v),'sample':_shape(v[0],depth+1) if v else None}
    if isinstance(v,str):return {'type':'str','sample':v[:140]}
    if v is None:return None
    return {'type':type(v).__name__,'sample':v}

def discover():
    docs=_docs_get('/docs'); text=docs.text if docs.ok else ''
    result={'docs':{'status':docs.status_code,'bytes':len(docs.content),'content_type':docs.headers.get('content-type')}}
    spec=_embedded_spec(text)
    if not spec:
        result['status']='EMBEDDED_SPEC_NOT_PARSED'; result['openapi_summary']={'embedded_spec_parsed':False}; return result
    paths=spec.get('paths') or {}; schemas=(spec.get('components') or {}).get('schemas') or {}
    category_op=(paths.get('/{version}/categories') or {}).get('get') or {}
    login_op=(paths.get('/{version}/login') or {}).get('post') or {}
    selected_schema_names=['CategoryListResponse','CategoryListItem','Category','Category2','CategoryResponse','Field','ProductFeaturesRequest','ProductImportRequestPost','ProductImportRequestPatch']
    selected_schemas={k:schemas.get(k) for k in selected_schema_names if k in schemas}

    # Authentication call only obtains an API token. It does not mutate marketplace data.
    login_info={}
    token=None
    try:
        lr=_api_login('v3'); login_info={'status':lr.status_code,'content_type':lr.headers.get('content-type'),'bytes':len(lr.content)}
        if lr.ok:
            try:
                body=lr.json(); token=body.get('token') if isinstance(body,dict) else None
                login_info['token_received']=bool(token)
            except Exception: login_info['token_received']=False
        else: login_info['response_sample']=lr.text[:400]
    except Exception as e: login_info={'error':f'{type(e).__name__}: {e}'}

    cat_info={'attempted':False}
    if token:
        try:
            cr=_api_get('/v3/categories?limit=100&offset=0',token); cat_info={'attempted':True,'status':cr.status_code,'content_type':cr.headers.get('content-type'),'bytes':len(cr.content)}
            if cr.ok:
                data=cr.json(); cat_info['json_shape']=_shape(data)
                if isinstance(data,dict):cat_info['top_level_keys']=list(data.keys())[:30]
            else: cat_info['response_sample']=cr.text[:400]
        except Exception as e: cat_info={'attempted':True,'error':f'{type(e).__name__}: {e}'}

    result['openapi_summary']={
        'source':'embedded_openapi','embedded_spec_parsed':True,'openapi':spec.get('openapi'),'title':(spec.get('info') or {}).get('title'),'version':(spec.get('info') or {}).get('version'),'path_count':len(paths),
        'login_operation':{'summary':login_op.get('summary'),'description':login_op.get('description'),'requestBody':login_op.get('requestBody'),'responses':login_op.get('responses')},
        'category_operation':{'summary':category_op.get('summary'),'parameters':category_op.get('parameters'),'responses':category_op.get('responses')},
        'selected_schemas':selected_schemas,'login_probe':login_info,'categories_probe':cat_info,
        'security_schemes':(spec.get('components') or {}).get('securitySchemes') or {}
    }
    result['status']='PASS'
    return result
