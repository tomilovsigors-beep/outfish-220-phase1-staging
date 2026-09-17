import json, os
try:
    raw=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON') or ''
    email=(json.loads(raw).get('client_email') if raw else None)
    if email:
        print('GOOGLE_SERVICE_ACCOUNT_PRINCIPAL', email, flush=True)
except Exception:
    pass

if os.getenv('RUN_PMP_API_DISCOVERY','').strip()=='1':
    try:
        from pmp_api_probe import discover
        r=discover()
        print('PMP_API_DEEP_DISCOVERY '+json.dumps({
            'status':r.get('status'),
            'candidate_paths':r.get('candidate_paths',[])[:300],
            'keyword_contexts':r.get('keyword_contexts',[])[:40],
            'docs_assets':[
                {k:v for k,v in x.items() if k in ('url','status','content_type','bytes','candidate_paths','keyword_contexts')}
                for x in r.get('docs_assets',[])[:20]
            ]
        },ensure_ascii=False,sort_keys=True),flush=True)
    except Exception as e:
        print('PMP_API_DEEP_DISCOVERY_ERROR '+type(e).__name__+': '+str(e),flush=True)
