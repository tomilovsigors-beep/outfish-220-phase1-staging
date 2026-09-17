from __future__ import annotations

import json
import os
import re
from urllib.parse import urljoin

import requests

BASE = "https://pmpapi.pigugroup.eu"
CANDIDATE_SPECS = [
    "/openapi.json",
    "/swagger.json",
    "/docs/openapi.json",
    "/docs/swagger.json",
    "/api-docs",
    "/v3/api-docs",
]


def _auth():
    u = os.getenv("PMP_DOCS_USERNAME", "").strip()
    p = os.getenv("PMP_DOCS_PASSWORD", "")
    if not u or not p:
        raise RuntimeError("PMP docs credentials are not configured")
    return (u, p)


def _get(path_or_url: str, *, timeout: int = 20):
    url = path_or_url if path_or_url.startswith("http") else urljoin(BASE, path_or_url)
    r = requests.get(url, auth=_auth(), timeout=timeout, allow_redirects=True, headers={"User-Agent": "outfish-phh-readonly-discovery/1.0"})
    return r


def _extract_spec_urls(html: str) -> list[str]:
    urls = []
    patterns = [
        r"url\s*:\s*['\"]([^'\"]+)['\"]",
        r"urls\s*:\s*\[(.*?)\]",
        r"['\"]([^'\"]*(?:openapi|swagger)[^'\"]*\.json[^'\"]*)['\"]",
    ]
    for pat in patterns:
        for m in re.finditer(pat, html, re.I | re.S):
            if pat.startswith("urls"):
                for u in re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)):
                    urls.append(u)
            else:
                urls.append(m.group(1))
    out = []
    seen = set()
    for u in urls:
        u = u.strip()
        if not u:
            continue
        full = urljoin(BASE + "/docs", u)
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


def _summarize_openapi(spec: dict) -> dict:
    paths = spec.get("paths") or {}
    read_ops = []
    write_ops = []
    category_like = []
    for path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        methods = []
        for method, meta in ops.items():
            lm = str(method).lower()
            if lm not in {"get", "post", "put", "patch", "delete"}:
                continue
            methods.append(lm.upper())
            item = {
                "method": lm.upper(),
                "path": path,
                "operationId": (meta or {}).get("operationId") if isinstance(meta, dict) else None,
                "summary": (meta or {}).get("summary") if isinstance(meta, dict) else None,
                "tags": (meta or {}).get("tags") if isinstance(meta, dict) else None,
            }
            (read_ops if lm == "get" else write_ops).append(item)
            hay = " ".join([path, str(item.get("operationId") or ""), str(item.get("summary") or ""), " ".join(item.get("tags") or [])]).lower()
            if any(k in hay for k in ("categor", "attribute", "field", "parameter", "property", "dictionary", "value")):
                category_like.append(item)
    sec = spec.get("components", {}).get("securitySchemes", {}) if isinstance(spec.get("components"), dict) else {}
    return {
        "openapi": spec.get("openapi"),
        "swagger": spec.get("swagger"),
        "title": (spec.get("info") or {}).get("title") if isinstance(spec.get("info"), dict) else None,
        "version": (spec.get("info") or {}).get("version") if isinstance(spec.get("info"), dict) else None,
        "servers": spec.get("servers") or [],
        "security_schemes": {k: {kk: vv for kk, vv in v.items() if kk not in {"description"}} for k, v in sec.items()} if isinstance(sec, dict) else {},
        "path_count": len(paths),
        "read_operation_count": len(read_ops),
        "write_operation_count": len(write_ops),
        "category_like_read_operations": [x for x in category_like if x["method"] == "GET"],
        "all_read_operations": read_ops,
    }


def discover() -> dict:
    result = {"base": BASE, "docs_auth_configured": bool(os.getenv("PMP_DOCS_USERNAME") and os.getenv("PMP_DOCS_PASSWORD")), "docs": {}, "spec_candidates": []}
    docs = _get("/docs")
    result["docs"] = {"status": docs.status_code, "content_type": docs.headers.get("content-type"), "final_url": docs.url}
    html = docs.text if docs.ok else ""
    candidates = _extract_spec_urls(html) + [urljoin(BASE, p) for p in CANDIDATE_SPECS]
    seen = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            r = _get(url)
            row = {"url": url, "status": r.status_code, "content_type": r.headers.get("content-type")}
            if r.ok:
                try:
                    spec = r.json()
                    if isinstance(spec, dict) and ("paths" in spec or "openapi" in spec or "swagger" in spec):
                        row["is_openapi"] = True
                        row["summary"] = _summarize_openapi(spec)
                        result["selected_spec_url"] = url
                        result["openapi_summary"] = row["summary"]
                        result["spec_candidates"].append(row)
                        break
                except Exception:
                    pass
            result["spec_candidates"].append(row)
        except Exception as e:
            result["spec_candidates"].append({"url": url, "error": f"{type(e).__name__}: {e}"})
    result["status"] = "PASS" if result.get("openapi_summary") else "NO_OPENAPI_FOUND"
    return result
