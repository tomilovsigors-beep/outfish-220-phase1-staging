"""Read-only PHH product-modification auto-check batch probe.

Uses official GET /v3/product-modification/pigu-external-id/{id}/auto-check-errors.
No offer/product writes are implemented here.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from pmp_api_probe import _api_login, _api_get

def run(ids):
    clean=[]
    seen=set()
    for raw in ids:
        x=str(raw or "").strip()
        if x.isdigit() and x not in seen:
            seen.add(x); clean.append(x)
    clean=clean[:100]
    if not clean:
        return {"status":"ERROR","error":"NO_IDS","writes":0,"items":[]}
    lr=_api_login("v3"); lr.raise_for_status()
    token=lr.json()["token"]
    def one(pid):
        path=f"/v3/product-modification/pigu-external-id/{pid}/auto-check-errors"
        r=_api_get(path,token)
        body=None
        try: body=r.json()
        except Exception: body={"raw":r.text[:1200]}
        return pid,r.status_code,body
    items=[]; http=Counter(); error_kinds=Counter()
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs={pool.submit(one,p):p for p in clean}
        for f in as_completed(futs):
            pid=futs[f]
            try:
                p,status,body=f.result()
                http[str(status)]+=1
                errors=[]
                if isinstance(body,list): errors=body
                elif isinstance(body,dict):
                    for key in ("errors","items","data"):
                        if isinstance(body.get(key),list):
                            errors=body[key]; break
                    if not errors and body:
                        errors=[body]
                for e in errors:
                    if isinstance(e,dict):
                        label=str(e.get("validator") or e.get("code") or e.get("type") or e.get("message") or "UNKNOWN")
                    else:
                        label=str(e)
                    error_kinds[label[:180]]+=1
                items.append({"pigu_external_id":p,"http":status,"error_count":len(errors),"errors":errors})
            except Exception as e:
                http["EXCEPTION"]+=1
                items.append({"pigu_external_id":pid,"http":0,"error_count":1,
                              "errors":[{"type":type(e).__name__,"message":str(e)[:500]}]})
    items.sort(key=lambda x:int(x["pigu_external_id"]))
    return {"status":"PASS","checked":len(items),"http_counts":dict(http),
            "zero_errors":sum(1 for x in items if x["http"]==200 and x["error_count"]==0),
            "with_errors":sum(1 for x in items if x["http"]==200 and x["error_count"]>0),
            "error_kinds":dict(error_kinds),"writes":0,"items":items}
