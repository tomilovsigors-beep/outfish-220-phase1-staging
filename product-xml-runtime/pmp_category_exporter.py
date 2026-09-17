from __future__ import annotations

import csv
import io
import json
import os
from collections import Counter
from urllib.parse import urljoin

import requests

BASE = "https://pmpapi.pigugroup.eu"
EXPECTED_ATTRIBUTE_KEYS = {
    "field_id", "required", "title_lt", "title_lv", "title_ee", "title_fi", "title_ru"
}


def _api_creds():
    u = os.getenv("PMP_API_USERNAME", "").strip()
    p = os.getenv("PMP_API_PASSWORD", "")
    return (u, p) if u and p else (None, None)


def _login(version="v3", timeout=25):
    u, p = _api_creds()
    if not u or not p:
        raise RuntimeError("PMP API credentials are not configured")
    return requests.post(
        urljoin(BASE, f"/{version}/login"),
        json={"username": u, "password": p}, timeout=timeout,
        headers={"User-Agent": "outfish-phh-category-exporter/1.1", "Accept": "application/json"},
    )


def _get(path, token, timeout=45):
    return requests.get(
        urljoin(BASE, path), timeout=timeout,
        headers={"User-Agent": "outfish-phh-category-exporter/1.1", "Accept": "application/json", "Authorization": "Pigu-mp " + token},
    )


def _csv_bytes(rows, fieldnames):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader(); w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def _category_path(category_id, by_id):
    parts, seen, current = [], set(), category_id
    while current is not None and current not in seen:
        seen.add(current)
        row = by_id.get(current)
        if not row:
            break
        title = row.get("title_en") or row.get("title_lv") or row.get("title_lt") or str(current)
        parts.append(str(title)); current = row.get("parent_id")
    return " > ".join(reversed(parts))


def _persist(summary, artifacts):
    db = os.getenv("DATABASE_URL")
    if not db:
        return False, "DATABASE_URL missing"
    try:
        import psycopg
        payload = {k: v.decode("utf-8-sig") for k, v in artifacts.items()}
        with psycopg.connect(db) as c:
            with c.cursor() as cur:
                cur.execute("""create table if not exists phh_category_export_snapshots (
                    id bigserial primary key,
                    created_at timestamptz not null default now(),
                    api_version text not null,
                    categories_fetched integer not null,
                    summary jsonb not null,
                    artifacts jsonb not null
                )""")
                cur.execute(
                    "insert into phh_category_export_snapshots(api_version,categories_fetched,summary,artifacts) values(%s,%s,%s::jsonb,%s::jsonb)",
                    (summary.get("api_version"), summary.get("categories_fetched"), json.dumps(summary, ensure_ascii=False), json.dumps(payload, ensure_ascii=False)),
                )
            c.commit()
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def export_all(version="v3", limit=100, persist=True, token=None):
    if not token:
        login = _login(version)
        if not login.ok:
            raise RuntimeError(f"PMP API login failed: HTTP {login.status_code}")
        body = login.json(); token = body.get("token") if isinstance(body, dict) else None
        if not token:
            raise RuntimeError("PMP API login succeeded but token is missing")

    categories, offset, total_count, page_count = [], 0, None, 0
    while total_count is None or offset < total_count:
        r = _get(f"/{version}/categories?limit={limit}&offset={offset}", token)
        if not r.ok:
            raise RuntimeError(f"categories page failed at offset={offset}: HTTP {r.status_code}")
        data = r.json(); page = data.get("category_list") or []; meta = data.get("meta") or {}
        if not isinstance(page, list):
            raise RuntimeError("category_list is not an array")
        if total_count is None:
            total_count = int(meta.get("total_count") or 0)
            if total_count <= 0:
                raise RuntimeError("invalid total_count")
        categories.extend(page); page_count += 1
        if not page:
            break
        offset += len(page)
        if page_count > 100:
            raise RuntimeError("pagination safety limit exceeded")

    ids = [c.get("category_id") for c in categories if c.get("category_id") is not None]
    counts = Counter(ids); duplicate_ids = sorted(k for k, v in counts.items() if v > 1)
    by_id = {c.get("category_id"): c for c in categories if c.get("category_id") is not None}

    attribute_rows, extra_attribute_keys, unique_field_ids, required_field_ids = [], set(), set(), set()
    categories_with_attributes = categories_with_required = max_attribute_count = 0
    for c in categories:
        attrs = c.get("attributes") or []
        if attrs:
            categories_with_attributes += 1
        if any(bool(a.get("required")) for a in attrs if isinstance(a, dict)):
            categories_with_required += 1
        max_attribute_count = max(max_attribute_count, len(attrs))
        path = _category_path(c.get("category_id"), by_id)
        for a in attrs:
            if not isinstance(a, dict):
                continue
            extra = set(a.keys()) - EXPECTED_ATTRIBUTE_KEYS; extra_attribute_keys.update(extra)
            fid = a.get("field_id")
            if fid is not None:
                unique_field_ids.add(fid)
                if a.get("required"):
                    required_field_ids.add(fid)
            attribute_rows.append({
                "category_id": c.get("category_id"), "category_path": path,
                "allow_add_products": c.get("allow_add_products"), "field_id": fid,
                "required": bool(a.get("required")), "title_lt": a.get("title_lt") or "",
                "title_lv": a.get("title_lv") or "", "title_ee": a.get("title_ee") or "",
                "title_fi": a.get("title_fi") or "", "title_ru": a.get("title_ru") or "",
                "extra_json": json.dumps({k: a.get(k) for k in sorted(extra)}, ensure_ascii=False, sort_keys=True),
            })

    category_rows, leaf_rows = [], []
    for c in categories:
        row = {
            "category_id": c.get("category_id"), "parent_id": c.get("parent_id"),
            "category_path": _category_path(c.get("category_id"), by_id),
            "allow_add_products": c.get("allow_add_products"),
            "attribute_count": len(c.get("attributes") or []),
            "required_attribute_count": sum(1 for a in (c.get("attributes") or []) if isinstance(a, dict) and a.get("required")),
            "title_en": c.get("title_en") or "", "title_lv": c.get("title_lv") or "",
            "title_lt": c.get("title_lt") or "", "title_ee": c.get("title_ee") or "",
            "title_fi": c.get("title_fi") or "", "title_ru": c.get("title_ru") or "", "title_pl": c.get("title_pl") or "",
        }
        category_rows.append(row)
        if c.get("allow_add_products") is True:
            leaf_rows.append(row)

    summary = {
        "status": "PASS" if len(categories) == total_count and not duplicate_ids else "REVIEW",
        "api_version": version, "total_count_reported": total_count, "categories_fetched": len(categories),
        "pages_fetched": page_count, "duplicate_category_ids": duplicate_ids,
        "leaf_addable_categories": len(leaf_rows), "non_addable_categories": len(categories) - len(leaf_rows),
        "categories_with_attributes": categories_with_attributes,
        "categories_with_required_attributes": categories_with_required,
        "attribute_rows": len(attribute_rows), "unique_field_ids": len(unique_field_ids),
        "unique_required_field_ids": len(required_field_ids), "max_attribute_count_per_category": max_attribute_count,
        "unexpected_attribute_keys": sorted(extra_attribute_keys),
        "live_api_exposes_type_unit_allowed_values": bool(extra_attribute_keys & {"type","unit","units","values","options","allowed_values","enum"}),
        "phh_marketplace_writes": 0,
    }
    category_fields = ["category_id","parent_id","category_path","allow_add_products","attribute_count","required_attribute_count","title_en","title_lv","title_lt","title_ee","title_fi","title_ru","title_pl"]
    attribute_fields = ["category_id","category_path","allow_add_products","field_id","required","title_lt","title_lv","title_ee","title_fi","title_ru","extra_json"]
    artifacts = {
        "pmp-categories-full.json": json.dumps({"summary": summary, "category_list": categories}, ensure_ascii=False, sort_keys=True, indent=2).encode(),
        "pmp-categories.csv": _csv_bytes(category_rows, category_fields),
        "pmp-leaf-categories.csv": _csv_bytes(leaf_rows, category_fields),
        "pmp-category-attributes.csv": _csv_bytes(attribute_rows, attribute_fields),
        "pmp-category-export-summary.json": json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2).encode(),
    }
    if persist:
        ok, err = _persist(summary, artifacts); summary["persistence_ok"] = ok; summary["persistence_error"] = err
    return summary, artifacts
