from __future__ import annotations
import json
import pmp_api_probe as p

def run():
    docs=p._docs_get('/docs'); spec=p._embedded_spec(docs.text) if docs.ok else None
    if not isinstance(spec,dict): raise RuntimeError('OpenAPI unavailable')
    schemas=(spec.get('components') or {}).get('schemas') or {}
    names=['ProductFeaturesRequest','ProductImportRequestPost','ProductImportRequestPatch','ModificationImportRequest','ModificationImportRequestPatch','Field','AutoCheckValidator','AutoCheckValidatorResponse','AutoCheckFailureItem']
    selected={n:schemas.get(n) for n in names if n in schemas}
    login=p._api_login('v3'); token=None
    if login is not None and login.ok:
        b=login.json(); token=b.get('token') if isinstance(b,dict) else None
    validator={'status':None,'body':None}
    if token:
        r=p._api_get('/v3/product-modification/auto-check-error-validators',token)
        validator={'status':r.status_code,'body':r.json() if r.ok else r.text[:1000]}
    out={'status':'PASS','selected_schemas':selected,'validator':validator,'safety':{'writes':0,'marketplace_mutations':0}}
    print('PMP_ATTRIBUTE_CONTRACT_DIGEST_V11J '+json.dumps(out,ensure_ascii=False,sort_keys=True)[:100000],flush=True)
    return out
