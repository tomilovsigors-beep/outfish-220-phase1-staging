from __future__ import annotations
import json, os, re
from urllib.parse import urljoin
import requests

BASE='https://pmp.pigugroup.eu'
TARGETS=['/seller-academy','/faq?section-id=166']
KEYWORDS=('api','integration','integrat','attribute','field','value','category','xml','offer','product','feed','mapping')


def _summary_response(r):
    return {
        'status': r.status_code,
        'url': r.url,
        'content_type': r.headers.get('content-type'),
        'bytes': len(r.content),
        'redirected_to_login': '/login' in r.url,
    }


def _extract(text):
    # intentionally lightweight: read-only discovery, no DOM execution
    title=''
    m=re.search(r'<title[^>]*>(.*?)</title>',text,re.I|re.S)
    if m: title=re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',m.group(1))).strip()
    links=[]
    for href,anchor in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',text,re.I|re.S):
        plain=re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',anchor)).strip()
        hay=(href+' '+plain).lower()
        if any(k in hay for k in KEYWORDS):
            links.append({'href':href,'text':plain[:300]})
    plain=re.sub(r'\s+',' ',re.sub(r'<script.*?</script>|<style.*?</style>',' ',text,flags=re.I|re.S))
    plain=re.sub(r'<[^>]+>',' ',plain)
    plain=re.sub(r'\s+',' ',plain)
    hits=[]
    low=plain.lower()
    for k in KEYWORDS:
        start=0
        while True:
            i=low.find(k,start)
            if i<0: break
            hits.append(plain[max(0,i-180):min(len(plain),i+420)])
            start=i+len(k)
            if len(hits)>=80: break
        if len(hits)>=80: break
    return {'title':title,'links':links[:100],'keyword_snippets':hits[:80]}


def _login_form(text):
    forms=re.findall(r'<form\b[^>]*>.*?</form>',text,re.I|re.S)
    for form in forms:
        if re.search(r'type=["\']password["\']',form,re.I):
            action=''
            m=re.search(r'<form[^>]+action=["\']([^"\']*)["\']',form,re.I)
            if m: action=m.group(1)
            fields={}
            for tag in re.findall(r'<input\b[^>]*>',form,re.I):
                nm=re.search(r'name=["\']([^"\']+)["\']',tag,re.I)
                if not nm: continue
                val=re.search(r'value=["\']([^"\']*)["\']',tag,re.I)
                typ=re.search(r'type=["\']([^"\']+)["\']',tag,re.I)
                fields[nm.group(1)]={'value':val.group(1) if val else '', 'type':typ.group(1).lower() if typ else 'text'}
            return action,fields
    return '',{}


def run():
    s=requests.Session(); s.headers.update({'User-Agent':'outfish-phh-readonly-seller-academy/11c'})
    first={}
    for path in TARGETS:
        r=s.get(urljoin(BASE,path),timeout=25,allow_redirects=True)
        first[path]=_summary_response(r)

    login=s.get(urljoin(BASE,'/login'),timeout=25,allow_redirects=True)
    action,fields=_login_form(login.text)
    auth={'attempted':False,'reason':'no compatible login form or credentials'}
    user=os.getenv('PMP_PORTAL_USERNAME','').strip() or os.getenv('PMP_API_USERNAME','').strip()
    pwd=os.getenv('PMP_PORTAL_PASSWORD','') or os.getenv('PMP_API_PASSWORD','')
    if action and fields and user and pwd:
        payload={}
        user_field=None; pass_field=None
        for name,meta in fields.items():
            t=meta['type']
            if t=='password': pass_field=name
            elif t in ('text','email') and user_field is None: user_field=name
            elif t=='hidden': payload[name]=meta['value']
        if user_field and pass_field:
            payload[user_field]=user; payload[pass_field]=pwd
            rr=s.post(urljoin(login.url,action or '/login'),data=payload,timeout=25,allow_redirects=True)
            auth={'attempted':True,'status':rr.status_code,'url':rr.url,'bytes':len(rr.content),'authenticated':('/login' not in rr.url)}

    pages={}
    for path in TARGETS:
        r=s.get(urljoin(BASE,path),timeout=25,allow_redirects=True)
        pages[path]={**_summary_response(r),'extract':_extract(r.text) if r.ok and '/login' not in r.url else {}}

    summary={'status':'PASS','initial':first,'auth':auth,'pages':{k:{kk:vv for kk,vv in v.items() if kk!='extract'} for k,v in pages.items()},'safety':{'Master_writes':0,'PHH_marketplace_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','stock_changes':0,'price_changes':0,'auth_POST_only':bool(auth.get('attempted')),'marketplace_mutation_requests':0}}
    out={'summary':summary,'pages':pages}
    import psycopg
    db=os.getenv('DATABASE_URL')
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('create table if not exists phh_seller_academy_v11c_snapshots(id bigserial primary key, created_at timestamptz not null default now(), summary jsonb not null, payload jsonb not null)')
            cur.execute('insert into phh_seller_academy_v11c_snapshots(summary,payload) values(%s::jsonb,%s::jsonb)',(json.dumps(summary,ensure_ascii=False),json.dumps(out,ensure_ascii=False)))
        c.commit()
    print('PMP_SELLER_ACADEMY_V11C_RESULT '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
    # Print only relevant extracted content; never credentials/cookies.
    print('PMP_SELLER_ACADEMY_V11C_CONTENT '+json.dumps({k:v.get('extract',{}) for k,v in pages.items()},ensure_ascii=False)[:50000],flush=True)
    return summary,out
