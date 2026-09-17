from __future__ import annotations

import os, re
from urllib.parse import urljoin, urlparse
import requests

BASE = "https://pmpapi.pigugroup.eu"
KEYWORDS = ("categor", "attribute", "field", "parameter", "property", "dictionary", "value", "product", "xml")
CANDIDATE_SPECS = ["/openapi.json","/swagger.json","/docs/openapi.json","/docs/swagger.json","/api-docs","/v3/api-docs"]


def _auth():
    u=os.getenv("PMP_DOCS_USERNAME","").strip(); p=os.getenv("PMP_DOCS_PASSWORD","")
    if not u or not p: raise RuntimeError("PMP docs credentials are not configured")
    return (u,p)


def _get(path_or_url, timeout=25):
    url=path_or_url if str(path_or_url).startswith('http') else urljoin(BASE,str(path_or_url))
    return requests.get(url,auth=_auth(),timeout=timeout,allow_redirects=True,headers={"User-Agent":"outfish-phh-readonly-discovery/4.0","Accept":"application/json,text/html;q=0.9,*/*;q=0.5"})


def _candidate_paths(text):
    values=set(); decoded=text.replace('&quot;','"').replace('&#39;',"'").replace('&amp;','&')
    for m in re.finditer(r"[\"']((?:https?://[^\"']+|/[A-Za-z0-9_{}?=&.\-/]+))[\"']",decoded):
        s=m.group(1)
        if any(k in s.lower() for k in KEYWORDS): values.add(s)
    for m in re.finditer(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_{}?=&.\-/]+)",decoded,re.I):
        route=f"{m.group(1).upper()} {m.group(2)}"
        if any(k in route.lower() for k in KEYWORDS): values.add(route)
    return sorted(values)[:500]


def _route_context(text, needle='/v3/categories'):
    i=text.find(needle)
    if i<0: return None
    s=text[max(0,i-2500):min(len(text),i+6000)]
    s=re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',s))
    return s[:8000]


def _shape(v, depth=0):
    if depth>4: return type(v).__name__
    if isinstance(v,dict): return {str(k):_shape(val,depth+1) for k,val in list(v.items())[:30]}
    if isinstance(v,list): return {"type":"list","length":len(v),"sample":_shape(v[0],depth+1) if v else None}
    if v is None: return None
    if isinstance(v,(str,int,float,bool)):
        if isinstance(v,str): return {"type":"str","sample":v[:160]}
        return {"type":type(v).__name__,"sample":v}
    return type(v).__name__


def _extract_links(html):
    out=[]; seen=set()
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',html,re.I|re.S):
        href=urljoin(BASE+'/docs',m.group(1).strip()); label=re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',m.group(2))).strip()
        if any(k in (href+' '+label).lower() for k in KEYWORDS) and (href,label) not in seen:
            seen.add((href,label)); out.append({'href':href,'label':label[:200]})
    return out[:100]


def discover():
    result={"base":BASE,"docs_auth_configured":bool(os.getenv("PMP_DOCS_USERNAME") and os.getenv("PMP_DOCS_PASSWORD")),"docs":{},"spec_candidates":[]}
    docs=_get('/docs'); html=docs.text if docs.ok else ''
    paths=_candidate_paths(html)
    result['docs']={"status":docs.status_code,"content_type":docs.headers.get('content-type'),"final_url":docs.url,"bytes":len(docs.content)}
    result['candidate_paths']=paths; result['route_context_v3_categories']=_route_context(html); result['doc_links']=_extract_links(html)

    # Explicitly probe only the documented GET category endpoint. No write methods are ever issued here.
    cat={}
    try:
        r=_get('/v3/categories')
        cat={"status":r.status_code,"content_type":r.headers.get('content-type'),"bytes":len(r.content),"final_url":r.url}
        if r.ok:
            try:
                data=r.json(); cat['json_shape']=_shape(data)
                if isinstance(data,list): cat['top_level_count']=len(data)
                elif isinstance(data,dict): cat['top_level_keys']=list(data.keys())[:50]
            except Exception:
                cat['text_sample']=r.text[:1000]
        else: cat['text_sample']=r.text[:1000]
    except Exception as e: cat={"error":f"{type(e).__name__}: {e}"}
    result['categories_get_probe']=cat

    for p in CANDIDATE_SPECS:
        u=urljoin(BASE,p)
        try:
            r=_get(u); result['spec_candidates'].append({'url':u,'status':r.status_code,'content_type':r.headers.get('content-type')})
        except Exception as e: result['spec_candidates'].append({'url':u,'error':f'{type(e).__name__}: {e}'})

    result['openapi_summary']={"source":"authenticated_docs_html","candidate_paths":paths[:150],"route_context_v3_categories":result.get('route_context_v3_categories'),"doc_links":result['doc_links'][:50],"categories_get_probe":cat}
    result['status']='PASS' if docs.ok else 'FAIL'
    return result
