from __future__ import annotations

import json
import os
import re
from urllib.parse import urljoin, urlparse

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
KEYWORDS = ("categor", "attribute", "field", "parameter", "property", "dictionary", "value", "product", "xml")


def _auth():
    u = os.getenv("PMP_DOCS_USERNAME", "").strip()
    p = os.getenv("PMP_DOCS_PASSWORD", "")
    if not u or not p:
        raise RuntimeError("PMP docs credentials are not configured")
    return (u, p)


def _get(path_or_url: str, *, timeout: int = 20):
    url = path_or_url if path_or_url.startswith("http") else urljoin(BASE, path_or_url)
    r = requests.get(url, auth=_auth(), timeout=timeout, allow_redirects=True, headers={"User-Agent": "outfish-phh-readonly-discovery/2.0"})
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


def _extract_assets(html: str) -> list[str]:
    found = []
    for pat in (r'<script[^>]+src=["\']([^"\']+)', r'<link[^>]+href=["\']([^"\']+)'):
        for m in re.finditer(pat, html, re.I):
            u = urljoin(BASE + "/docs", m.group(1).strip())
            if urlparse(u).netloc == urlparse(BASE).netloc and u not in found:
                found.append(u)
    return found


def _candidate_paths(text: str) -> list[str]:
    values = set()
    # quoted URL/path-like strings
    for m in re.finditer(r"[\"']((?:https?://[^\"']+|/[A-Za-z0-9_{}?=&.\-/]+))[\"']", text):
        s = m.group(1)
        if any(k in s.lower() for k in KEYWORDS):
            values.add(s)
    # HTTP method + route sequences in rendered docs or bundles
    for m in re.finditer(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_{}?=&.\-/]+)", text, re.I):
        route = f"{m.group(1).upper()} {m.group(2)}"
        if any(k in route.lower() for k in KEYWORDS):
            values.add(route)
    return sorted(values)[:300]


def _keyword_contexts(text: str) -> list[str]:
    # sanitize and return compact unique contexts; credentials are never in response content.
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))
    out = []
    seen = set()
    for m in re.finditer(r"(?i)(categor(?:y|ies)|attributes?|fields?|parameters?|properties|values?|products?|xml)", plain):
        a = max(0, m.start() - 120); b = min(len(plain), m.end() + 220)
        s = plain[a:b].strip()
        if s not in seen:
            seen.add(s); out.append(s)
        if len(out) >= 80:
            break
    return out


def _summarize_openapi(spec: dict) -> dict:
    paths = spec.get("paths") or {}
    read_ops = []
    write_ops = []
    category_like = []
    for path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        for method, meta in ops.items():
            lm = str(method).lower()
            if lm not in {"get", "post", "put", "patch", "delete"}:
                continue
            item = {
                "method": lm.upper(),
                "path": path,
                "operationId": (meta or {}).get("operationId") if isinstance(meta, dict) else None,
                "summary": (meta or {}).get("summary") if isinstance(meta, dict) else None,
                "tags": (meta or {}).get("tags") if isinstance(meta, dict) else None,
            }
            (read_ops if lm == "get" else write_ops).append(item)
            hay = " ".join([path, str(item.get("operationId") or ""), str(item.get("summary") or ""), " ".join(item.get("tags") or [])]).lower()
            if any(k in hay for k in KEYWORDS):
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
    result = {
        "base": BASE,
        "docs_auth_configured": bool(os.getenv("PMP_DOCS_USERNAME") and os.getenv("PMP_DOCS_PASSWORD")),
        "docs": {}, "spec_candidates": [], "docs_assets": [], "candidate_paths": [], "keyword_contexts": []
    }
    docs = _get("/docs")
    result["docs"] = {"status": docs.status_code, "content_type": docs.headers.get("content-type"), "final_url": docs.url, "bytes": len(docs.content)}
    html = docs.text if docs.ok else ""
    result["candidate_paths"] = _candidate_paths(html)
    result["keyword_contexts"] = _keyword_contexts(html)

    assets = _extract_assets(html)
    for u in assets[:30]:
        row = {"url": u}
        try:
            r = _get(u, timeout=20)
            row.update(status=r.status_code, content_type=r.headers.get("content-type"), bytes=len(r.content))
            if r.ok and len(r.content) <= 8_000_000 and any(x in (r.headers.get("content-type") or "").lower() for x in ("javascript", "json", "text")):
                txt = r.text
                cps = _candidate_paths(txt)
                if cps: row["candidate_paths"] = cps[:150]
                ctx = _keyword_contexts(txt)
                if ctx: row["keyword_contexts"] = ctx[:30]
                result["candidate_paths"].extend(cps)
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
        result["docs_assets"].append(row)
    result["candidate_paths"] = sorted(set(result["candidate_paths"]))[:500]

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
    result["status"] = "PASS" if (result.get("openapi_summary") or result.get("candidate_paths")) else "NO_API_PATHS_FOUND"
    return result
