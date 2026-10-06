from __future__ import annotations

import html as html_lib
import json
import os
import re
from urllib.parse import urljoin

import requests

BASE = "https://pmpapi.pigugroup.eu"
_API_TOKEN_CACHE = None
_API_TOKEN_CACHE_SOURCE = None


def _docs_creds():
    u = os.getenv("PMP_DOCS_USERNAME", "").strip()
    p = os.getenv("PMP_DOCS_PASSWORD", "")
    if not u or not p:
        raise RuntimeError("PMP docs credentials are not configured")
    return u, p


def _api_creds():
    u = os.getenv("PMP_API_USERNAME", "").strip()
    p = os.getenv("PMP_API_PASSWORD", "")
    return (u, p) if u and p else (None, None)


def _docs_get(path, timeout=25):
    return requests.get(urljoin(BASE, path), auth=_docs_creds(), timeout=timeout, headers={"User-Agent": "outfish-phh-readonly-discovery/8.1"})


def _api_get(path, token, timeout=25):
    return requests.get(urljoin(BASE, path), timeout=timeout, headers={"User-Agent": "outfish-phh-readonly-discovery/8.1", "Accept": "application/json", "Authorization": "Pigu-mp " + token})


def _token_response(token, source="cache"):
    r=requests.Response()
    r.status_code=200
    r._content=json.dumps({"token":token,"source":source}).encode("utf-8")
    r.headers["content-type"]="application/json"
    return r

# PHH access tokens are documented as valid for 30 days.
# Keep a 29-day authenticated token across deploys. This cache is private to the
# service's DATABASE_URL; never print or expose token values in diagnostics.
def _db_auth_state():
    db=os.getenv("DATABASE_URL","").strip()
    if not db: return None
    try:
        import psycopg
        with psycopg.connect(db,connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("""create table if not exists phh_service_auth_cache (
                    cache_key text primary key,
                    token text,
                    token_expires_at timestamptz,
                    retry_after timestamptz
                )""")
                cur.execute("""select token,
                    coalesce(token_expires_at > now(),false),
                    coalesce(retry_after > now(),false)
                    from phh_service_auth_cache where cache_key='pmp_v3'""")
                row=cur.fetchone()
        return {'token':row[0] if row and row[1] else None,
                'cooldown':bool(row[2]) if row else False}
    except Exception:
        return None


def _db_auth_write(token=None,cooldown=False):
    db=os.getenv("DATABASE_URL","").strip()
    if not db: return
    try:
        import psycopg
        with psycopg.connect(db,connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("""create table if not exists phh_service_auth_cache (
                    cache_key text primary key,
                    token text,
                    token_expires_at timestamptz,
                    retry_after timestamptz
                )""")
                if token:
                    cur.execute("""insert into phh_service_auth_cache
                        (cache_key,token,token_expires_at,retry_after)
                        values('pmp_v3',%s,now()+interval '29 days',NULL)
                        on conflict(cache_key) do update set
                        token=excluded.token,token_expires_at=excluded.token_expires_at,
                        retry_after=NULL""",(token,))
                elif cooldown:
                    cur.execute("""insert into phh_service_auth_cache
                        (cache_key,retry_after) values('pmp_v3',now()+interval '60 minutes')
                        on conflict(cache_key) do update set
                        retry_after=excluded.retry_after""")
    except Exception:
        pass


def _api_login(version="v3", timeout=25, allow_network_login=True):
    global _API_TOKEN_CACHE,_API_TOKEN_CACHE_SOURCE
    env_token=os.getenv("PMP_API_TOKEN","").strip()
    if env_token:
        _API_TOKEN_CACHE=env_token; _API_TOKEN_CACHE_SOURCE="env"
        return _token_response(env_token,"env")
    if _API_TOKEN_CACHE:
        return _token_response(_API_TOKEN_CACHE,_API_TOKEN_CACHE_SOURCE or "memory")
    state=_db_auth_state()
    if state and state.get('token'):
        _API_TOKEN_CACHE=state['token']; _API_TOKEN_CACHE_SOURCE="database"
        return _token_response(_API_TOKEN_CACHE,"database")
    if not allow_network_login or (state and state.get('cooldown')):
        return None
    u,p=_api_creds()
    if not u or not p: return None
    r=requests.post(urljoin(BASE,f"/{version}/login"),
        json={"username":u,"password":p},timeout=timeout,
        headers={"User-Agent":"outfish-phh-auth/48","Accept":"application/json"})
    if r.ok:
        try:
            body=r.json()
            token=body.get("token") if isinstance(body,dict) else None
            if token:
                _API_TOKEN_CACHE=token; _API_TOKEN_CACHE_SOURCE="network"
                _db_auth_write(token=token)
        except Exception:
            pass
    elif r.status_code==429:
        _db_auth_write(cooldown=True)
    return r


def _embedded_spec(text):
    variants = [text, html_lib.unescape(text)]
    for s in list(variants):
        variants += [m.group(1) for m in re.finditer(r"<script[^>]*>(.*?)</script>", s, re.I | re.S)]
    decoder = json.JSONDecoder()
    for src in variants:
        for needle in ('{"openapi"', '{"swagger"', '{"paths"', '{\\"openapi\\"', '{\\"paths\\"'):
            start = 0
            while True:
                i = src.find(needle, start)
                if i < 0:
                    break
                blob = src[i:i + 3_000_000]
                for candidate in (blob, blob.replace('\\"', '"').replace('\\/', '/')):
                    try:
                        obj, _ = decoder.raw_decode(candidate)
                        if isinstance(obj, dict) and isinstance(obj.get("paths"), dict) and len(obj["paths"]) > 5:
                            return obj
                    except Exception:
                        pass
                start = i + 1
        for m in re.finditer(r'"((?:\\.|[^"\\]){10000,})"', src, re.S):
            try:
                decoded = json.loads('"' + m.group(1) + '"')
                if isinstance(decoded, str) and '"paths"' in decoded:
                    obj = json.loads(decoded)
                    if isinstance(obj, dict) and isinstance(obj.get("paths"), dict):
                        return obj
            except Exception:
                pass
    return None


def _shape(v, depth=0):
    if depth > 4:
        return type(v).__name__
    if isinstance(v, dict):
        return {str(k): _shape(x, depth + 1) for k, x in list(v.items())[:40]}
    if isinstance(v, list):
        return {"type": "list", "length": len(v), "sample": _shape(v[0], depth + 1) if v else None}
    if isinstance(v, str):
        return {"type": "str", "sample": v[:120]}
    if v is None:
        return None
    return {"type": type(v).__name__, "sample": v}


def _semantic_paths(paths):
    words = ("categor", "field", "attribute", "propert", "parameter", "value", "dictionary", "product")
    out = []
    for path, ops in sorted(paths.items()):
        hay = path.lower() + " " + json.dumps(ops, ensure_ascii=False).lower()
        if not any(w in hay for w in words):
            continue
        methods = []
        for method in ("get", "post", "put", "patch", "delete"):
            op = (ops or {}).get(method)
            if not isinstance(op, dict):
                continue
            methods.append({
                "method": method.upper(), "summary": op.get("summary"), "operationId": op.get("operationId"),
                "parameters": [{"name": p.get("name"), "in": p.get("in"), "required": p.get("required"), "description": p.get("description"), "schema": p.get("schema")} for p in (op.get("parameters") or []) if isinstance(p, dict)],
                "responses": {code: ((resp or {}).get("content") or {}) for code, resp in (op.get("responses") or {}).items() if isinstance(resp, dict)},
            })
        if methods:
            out.append({"path": path, "operations": methods})
    return out


def discover():
    docs = _docs_get("/docs")
    text = docs.text if docs.ok else ""
    result = {"docs": {"status": docs.status_code, "bytes": len(docs.content), "content_type": docs.headers.get("content-type")}}
    spec = _embedded_spec(text)
    if not spec:
        result["status"] = "EMBEDDED_SPEC_NOT_PARSED"
        result["openapi_summary"] = {"embedded_spec_parsed": False}
        return result

    paths = spec.get("paths") or {}
    schemas = (spec.get("components") or {}).get("schemas") or {}
    category_op = (paths.get("/{version}/categories") or {}).get("get") or {}
    login_op = (paths.get("/{version}/login") or {}).get("post") or {}
    schema_words = ("categor", "field", "attribute", "propert", "parameter", "value", "dictionary")
    semantic_schemas = {k: v for k, v in schemas.items() if any(w in k.lower() for w in schema_words)}

    login_info = {"attempted": False, "reason": "No cached or PMP_API_TOKEN token available; discovery does not perform network login"}
    token = None
    try:
        lr = _api_login("v3", allow_network_login=False)
        if lr is not None:
            login_info = {"attempted": True, "status": lr.status_code, "content_type": lr.headers.get("content-type"), "bytes": len(lr.content)}
            if lr.ok:
                try:
                    body = lr.json()
                    token = body.get("token") if isinstance(body, dict) else None
                    login_info["token_received"] = bool(token)
                except Exception:
                    login_info["token_received"] = False
            else:
                login_info["response_sample"] = lr.text[:300]
    except Exception as e:
        login_info = {"attempted": True, "error": f"{type(e).__name__}: {e}"}

    cat_info = {"attempted": False, "reason": "API token unavailable"}
    if token:
        try:
            cr = _api_get("/v3/categories?limit=100&offset=0", token)
            cat_info = {"attempted": True, "status": cr.status_code, "content_type": cr.headers.get("content-type"), "bytes": len(cr.content)}
            if cr.ok:
                data = cr.json()
                cat_info["json_shape"] = _shape(data)
                if isinstance(data, dict):
                    cat_info["top_level_keys"] = list(data.keys())[:30]
            else:
                cat_info["response_sample"] = cr.text[:300]
        except Exception as e:
            cat_info = {"attempted": True, "error": f"{type(e).__name__}: {e}"}

    full_export = {"attempted": False, "reason": "API token unavailable"}
    if token:
        try:
            from pmp_category_exporter import export_all
            export_summary, _ = export_all("v3", 100, persist=True, token=token)
            full_export = dict(export_summary)
            full_export["attempted"] = True
        except Exception as e:
            full_export = {"attempted": True, "status": "ERROR", "error": f"{type(e).__name__}: {e}", "phh_marketplace_writes": 0}

    result["openapi_summary"] = {
        "source": "embedded_openapi", "embedded_spec_parsed": True, "openapi": spec.get("openapi"),
        "title": (spec.get("info") or {}).get("title"), "version": (spec.get("info") or {}).get("version"), "path_count": len(paths),
        "login_operation": {"summary": login_op.get("summary"), "description": login_op.get("description"), "requestBody": login_op.get("requestBody"), "responses": login_op.get("responses")},
        "category_operation": {"summary": category_op.get("summary"), "parameters": category_op.get("parameters"), "responses": category_op.get("responses")},
        "semantic_paths": _semantic_paths(paths), "semantic_schemas": semantic_schemas,
        "api_login_probe": login_info, "categories_probe": cat_info, "full_category_export": full_export,
        "security_schemes": (spec.get("components") or {}).get("securitySchemes") or {},
    }
    result["status"] = "PASS"
    return result
