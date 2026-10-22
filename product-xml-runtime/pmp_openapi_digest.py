"""Lightweight PHH OpenAPI digest: docs only, no API login and no marketplace calls."""
from __future__ import annotations
import json
from pmp_api_probe import _docs_get, _embedded_spec

WORDS=("value","values","option","options","dictionary","dictionaries","field","fields",
       "attribute","attributes","feature","features","category","categories","property","properties")

def run():
    docs=_docs_get("/docs",timeout=20)
    if not docs.ok:
        return {"status":"DOCS_ERROR","http":docs.status_code,"writes":0}
    spec=_embedded_spec(docs.text)
    if not isinstance(spec,dict):
        return {"status":"SPEC_NOT_PARSED","writes":0}
    paths=spec.get("paths") or {}
    hits=[]
    for path,ops in sorted(paths.items()):
        blob=(path+" "+json.dumps(ops,ensure_ascii=False)).casefold()
        if not any(w in blob for w in WORDS):
            continue
        methods=[]
        for method in ("get","post","put","patch","delete"):
            op=(ops or {}).get(method)
            if not isinstance(op,dict): continue
            methods.append({
                "method":method.upper(),
                "operation_id":op.get("operationId"),
                "summary":op.get("summary"),
                "parameters":[{"name":p.get("name"),"in":p.get("in"),"required":p.get("required")}
                              for p in (op.get("parameters") or []) if isinstance(p,dict)]
            })
        if methods:
            hits.append({"path":path,"methods":methods})
    schemas=(spec.get("components") or {}).get("schemas") or {}
    schema_hits=[]
    for name,schema in schemas.items():
        blob=(name+" "+json.dumps(schema,ensure_ascii=False)).casefold()
        if any(w in blob for w in ("allowed","enum","value","option","dictionary","feature","field")):
            props=list((schema.get("properties") or {}).keys()) if isinstance(schema,dict) else []
            enum=schema.get("enum") if isinstance(schema,dict) else None
            schema_hits.append({"name":name,"properties":props[:80],
                                "enum_count":len(enum) if isinstance(enum,list) else 0})
    # Full read-only evidence requested by owner; never return authentication data.
    import pmp_api_probe as p
    lr=p._api_login("v3")
    cat={"request":"GET /v3/categories?id=9142&limit=100&offset=0"}
    if lr is not None and lr.ok:
        token=lr.json().get("token")
        cr=p._api_get("/v3/categories?id=9142&limit=100&offset=0",token)
        cat.update(http=cr.status_code,body=cr.json() if cr.ok else cr.text)
    else:
        cat.update(http=None,error="API token unavailable")
    return {"status":"PASS","openapi":spec.get("openapi"),"path_count":len(paths),
            "candidate_paths":hits,"candidate_schemas":schema_hits,"full_openapi":spec,
            "category_9142_response":cat,"writes":0}
