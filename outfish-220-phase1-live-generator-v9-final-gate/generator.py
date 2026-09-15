#!/usr/bin/env python3
"""Outfish 220 Phase 1 live stock/price generator (READ ONLY).

Purpose
-------
Refresh the approved non-FHM 220 pilot mapping from live Shopify in one atomic run.
The marketplace SKU/EAN in the pilot input are locked identity. Shopify is read only
and supplies current price + physical inventory (`on_hand`).

Safety
------
- no Shopify mutations
- no Google Sheet writes
- no PMP writes
- never replaces the existing 220 feed
- fail closed on missing/duplicate Shopify SKU or LOWA/Fjord overlap
- FHM rows are rejected from this Phase 1 runtime
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from xml.sax.saxutils import escape

STORE_LOCATION_ID = "gid://shopify/Location/84891861330"
LOWA_LOCATION_ID = "gid://shopify/Location/107817075026"
FJORD_LOCATION_ID = "gid://shopify/Location/107805770066"

DEFAULT_API_VERSION = "2026-07"
DEFAULT_STORE_DOMAIN = "153ac6-2.myshopify.com"

SHOPIFY_VARIANTS_QUERY = r"""
query Variants($first: Int!, $after: String) {
  productVariants(first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      sku
      barcode
      price
      product { id title vendor status }
      inventoryItem {
        id
        inventoryLevels(first: 20) {
          nodes {
            location { id name }
            quantities(names: ["on_hand"]) { name quantity }
          }
        }
      }
    }
  }
}
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def num(v) -> Optional[float]:
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def intnum(v) -> Optional[int]:
    x = num(v)
    return None if x is None else int(x)


def money(v: float) -> str:
    return f"{float(v):.2f}".rstrip("0").rstrip(".")


def digits(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


def marketplace_qty(selected: int) -> int:
    return 0 if selected <= 0 else max(selected, 3)


def route_stock(store_raw: int, lowa_raw: int, fjord_raw: int) -> dict:
    # Physical inventory is Shopify on_hand. Negative physical values are treated as 0.
    store = max(int(store_raw or 0), 0)
    lowa = max(int(lowa_raw or 0), 0)
    fjord = max(int(fjord_raw or 0), 0)

    # Strict hard gate: overlapping positive supplier stock is never guessed.
    if lowa > 0 and fjord > 0:
        return {
            "status": "BLOCKED_LOCATION_CONFLICT",
            "selected_stock": None,
            "marketplace_stock": None,
            "collectionhours": None,
            "stock_source": None,
        }
    if store > 0:
        return {
            "status": "PASS",
            "selected_stock": store,
            "marketplace_stock": marketplace_qty(store),
            "collectionhours": 24,
            "stock_source": "STORE",
        }
    if lowa > 0:
        return {
            "status": "PASS",
            "selected_stock": lowa,
            "marketplace_stock": marketplace_qty(lowa),
            "collectionhours": 48,
            "stock_source": "LOWA",
        }
    if fjord > 0:
        return {
            "status": "PASS",
            "selected_stock": fjord,
            "marketplace_stock": marketplace_qty(fjord),
            "collectionhours": 72,
            "stock_source": "FJORD_NANSEN",
        }
    return {
        "status": "PASS_ZERO_STOCK_NEEDS_LEGACY_HOURS",
        "selected_stock": 0,
        "marketplace_stock": 0,
        "collectionhours": None,
        "stock_source": "NONE",
    }


def read_csv(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: List[dict], fields: Optional[List[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        if not fields:
            return
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)



def load_approved_exclusions(path: Path | None) -> dict:
    if not path:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for item in data.get("approved_exclusions", []):
        sku = str(item.get("shopify_sku") or "").strip()
        if not sku:
            continue
        out[sku] = {
            "reason": item.get("reason") or "APPROVED_EXCLUSION",
            "approved_scope": item.get("approved_scope") or "PHASE1",
            "evidence": item.get("evidence") or "",
        }
    return out


def validate_pilot_input(rows: List[dict]) -> List[dict]:
    problems = []
    seen_marketplace = set()
    seen_shopify = set()
    for i, r in enumerate(rows, start=2):
        sku = (r.get("sku") or "").strip()
        ean = digits(r.get("ean") or "")
        shopify_sku = (r.get("shopify_sku") or "").strip()
        vendor = (r.get("vendor") or "").strip()

        if not sku:
            problems.append({"row": i, "status": "BLOCKED_INPUT_MISSING_MARKETPLACE_SKU"})
        if not re.fullmatch(r"\d{11,13}", ean):
            problems.append({"row": i, "sku": sku, "status": "BLOCKED_INPUT_INVALID_EAN", "ean": ean})
        if not shopify_sku:
            problems.append({"row": i, "sku": sku, "status": "BLOCKED_INPUT_MISSING_SHOPIFY_SKU"})
        if "FHM" in vendor.upper():
            problems.append({"row": i, "sku": sku, "status": "BLOCKED_FHM_OUT_OF_PHASE1"})
        if sku in seen_marketplace:
            problems.append({"row": i, "sku": sku, "status": "BLOCKED_INPUT_DUPLICATE_MARKETPLACE_SKU"})
        if shopify_sku in seen_shopify:
            problems.append({"row": i, "sku": sku, "shopify_sku": shopify_sku, "status": "BLOCKED_INPUT_DUPLICATE_SHOPIFY_SKU"})
        seen_marketplace.add(sku)
        seen_shopify.add(shopify_sku)
    return problems


def oauth_token(domain: str, client_id: str, client_secret: str) -> str:
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(
        f"https://{domain}/admin/oauth/access_token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["access_token"]


def graphql(domain: str, api_version: str, token: str, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        f"https://{domain}/admin/api/{api_version}/graphql.json",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("errors"):
        raise RuntimeError(f"Shopify GraphQL errors: {payload['errors']}")
    return payload["data"]


def fetch_shopify_live(target_skus: set[str], domain: str, api_version: str) -> Tuple[List[dict], dict]:
    client_id = os.environ.get("SHOPIFY_CLIENT_ID")
    client_secret = os.environ.get("SHOPIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET must be present in the runtime environment")

    token = oauth_token(domain, client_id, client_secret)
    snapshot = []
    after = None
    pages = 0
    scanned = 0
    matched_nodes = 0

    # Scan the full variant catalogue so duplicate target SKUs cannot hide on later pages.
    while True:
        data = graphql(domain, api_version, token, SHOPIFY_VARIANTS_QUERY, {"first": 250, "after": after})
        conn = data["productVariants"]
        pages += 1
        for node in conn["nodes"]:
            scanned += 1
            sku = (node.get("sku") or "").strip()
            if sku in target_skus:
                snapshot.append(node)
                matched_nodes += 1
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]

    meta = {
        "source": "SHOPIFY_ADMIN_GRAPHQL_LIVE",
        "store_domain": domain,
        "api_version": api_version,
        "fetched_at": now_iso(),
        "pages_scanned": pages,
        "variants_scanned": scanned,
        "target_shopify_skus": len(target_skus),
        "matching_nodes": matched_nodes,
    }
    return snapshot, meta


def load_fixture_snapshot(path: Path) -> Tuple[List[dict], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "nodes" in payload:
        return payload["nodes"], payload.get("meta", {"source": "FIXTURE_JSON"})
    if isinstance(payload, list):
        return payload, {"source": "FIXTURE_JSON"}
    raise ValueError("Fixture must be a JSON list or {'nodes': [...], 'meta': {...}}")


def location_on_hand(node: dict) -> dict:
    q = {"STORE": 0, "LOWA": 0, "FJORD_NANSEN": 0}
    raw = {"STORE": 0, "LOWA": 0, "FJORD_NANSEN": 0}
    levels = (((node.get("inventoryItem") or {}).get("inventoryLevels") or {}).get("nodes") or [])
    for lvl in levels:
        loc_id = ((lvl.get("location") or {}).get("id") or "")
        quantities = {x.get("name"): x.get("quantity") for x in (lvl.get("quantities") or [])}
        val = int(quantities.get("on_hand") or 0)
        key = None
        if loc_id == STORE_LOCATION_ID:
            key = "STORE"
        elif loc_id == LOWA_LOCATION_ID:
            key = "LOWA"
        elif loc_id == FJORD_LOCATION_ID:
            key = "FJORD_NANSEN"
        if key:
            raw[key] = val
            q[key] = max(val, 0)
    return {"clamped": q, "raw": raw}


def merge_live(pilot_rows: List[dict], snapshot_nodes: List[dict], approved_exclusions: dict | None = None) -> Tuple[List[dict], List[dict], List[dict], List[dict]]:
    by_sku: Dict[str, List[dict]] = defaultdict(list)
    for n in snapshot_nodes:
        sku = (n.get("sku") or "").strip()
        if sku:
            by_sku[sku].append(n)

    refreshed = []
    blockers = []
    warnings = []
    exclusions = []
    approved_exclusions = approved_exclusions or {}

    for src in pilot_rows:
        market_sku = (src.get("sku") or "").strip()
        market_ean = digits(src.get("ean") or "")
        shopify_sku = (src.get("shopify_sku") or "").strip()
        candidates = by_sku.get(shopify_sku, [])

        base = {
            "sku": market_sku,
            "ean": market_ean,
            "shopify_sku": shopify_sku,
            "legacy_stock": intnum(src.get("stock")),
            "legacy_collectionhours": intnum(src.get("collectionhours")),
            "legacy_price_before": num(src.get("price_before")),
            "legacy_price_after": num(src.get("price_after")),
            "legacy_current_220_price": num(src.get("current_220_price")) if src.get("current_220_price") not in (None, "") else (num(src.get("price_after")) or num(src.get("price_before"))),
            "mapping_vendor": src.get("vendor"),
            "mapping_status": src.get("match_status"),
        }

        if len(candidates) == 0:
            if shopify_sku in approved_exclusions:
                meta = approved_exclusions[shopify_sku]
                rec = {
                    **base,
                    "status": "EXCLUDED_APPROVED_SHOPIFY_NOT_FOUND",
                    "exclusion_reason": meta.get("reason"),
                    "exclusion_evidence": meta.get("evidence"),
                }
                refreshed.append(rec)
                exclusions.append(rec)
                continue
            rec = {**base, "status": "BLOCKED_SHOPIFY_MATCH_NOT_FOUND"}
            refreshed.append(rec); blockers.append(rec); continue
        if len(candidates) > 1:
            rec = {**base, "status": "BLOCKED_SHOPIFY_SKU_DUPLICATE", "candidate_count": len(candidates)}
            refreshed.append(rec); blockers.append(rec); continue

        n = candidates[0]
        loc = location_on_hand(n)
        routed = route_stock(loc["raw"]["STORE"], loc["raw"]["LOWA"], loc["raw"]["FJORD_NANSEN"])
        if routed["status"] == "PASS_ZERO_STOCK_NEEDS_LEGACY_HOURS":
            legacy_hours = base.get("legacy_collectionhours")
            if legacy_hours is None:
                routed = {**routed, "status": "BLOCKED_ZERO_STOCK_COLLECTIONHOURS_UNDEFINED"}
            else:
                routed = {**routed, "status": "PASS", "collectionhours": int(legacy_hours)}
        shopify_price = num(n.get("price"))
        current_220 = base["legacy_current_220_price"]

        if shopify_price is None:
            status = "BLOCKED_SHOPIFY_PRICE_MISSING"
            required_price = None
        else:
            required_price = max(shopify_price, current_220) if current_220 is not None else shopify_price
            status = routed["status"]

        product = n.get("product") or {}
        rec = {
            **base,
            "status": status,
            "shopify_variant_id": n.get("id"),
            "shopify_product_id": product.get("id"),
            "shopify_title": product.get("title"),
            "shopify_vendor": product.get("vendor"),
            "shopify_product_status": product.get("status"),
            "shopify_barcode": digits(n.get("barcode") or ""),
            "shopify_price": shopify_price,
            "required_220_selling_price": required_price,
            "generated_price_lv": required_price,
            "generated_price_lt": current_220,
            "generated_price_ee": current_220,
            "generated_price_fi": current_220,
            "price_raise_required": bool(shopify_price is not None and current_220 is not None and current_220 < shopify_price),
            "store_on_hand_raw": loc["raw"]["STORE"],
            "lowa_on_hand_raw": loc["raw"]["LOWA"],
            "fjord_on_hand_raw": loc["raw"]["FJORD_NANSEN"],
            "store_on_hand": loc["clamped"]["STORE"],
            "lowa_on_hand": loc["clamped"]["LOWA"],
            "fjord_on_hand": loc["clamped"]["FJORD_NANSEN"],
            "selected_stock": routed["selected_stock"],
            "marketplace_stock": routed["marketplace_stock"],
            "collectionhours": routed["collectionhours"],
            "stock_source": routed["stock_source"],
        }
        refreshed.append(rec)
        if status.startswith("BLOCKED"):
            blockers.append(rec)
        if any(v < 0 for v in loc["raw"].values()):
            warnings.append({"sku": market_sku, "shopify_sku": shopify_sku, "warning": "NEGATIVE_ON_HAND_CLAMPED_TO_ZERO", "raw": loc["raw"]})
        if product.get("status") != "ACTIVE":
            warnings.append({"sku": market_sku, "shopify_sku": shopify_sku, "warning": "SHOPIFY_PRODUCT_NOT_ACTIVE", "shopify_product_status": product.get("status")})
        if base["mapping_vendor"] and product.get("vendor") and str(base["mapping_vendor"]).strip() != str(product.get("vendor")).strip():
            warnings.append({"sku": market_sku, "shopify_sku": shopify_sku, "warning": "SHOPIFY_VENDOR_CHANGED", "mapping_vendor": base["mapping_vendor"], "live_vendor": product.get("vendor")})

    return refreshed, blockers, warnings, exclusions


def stock_price_xml(rows: List[dict]) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', '<products>']
    for r in rows:
        status = str(r.get("status", ""))
        if status.startswith("BLOCKED") or status.startswith("EXCLUDED_APPROVED"):
            continue
        price = r.get("required_220_selling_price")
        stock = r.get("marketplace_stock")
        hours = r.get("collectionhours")
        if price is None or stock is None or hours is None:
            continue
        market_prices = {
            "lt": r.get("generated_price_lt"),
            "lv": r.get("generated_price_lv"),
            "ee": r.get("generated_price_ee"),
            "fi": r.get("generated_price_fi"),
        }
        if any(v is None for v in market_prices.values()):
            continue
        parts += [
            "  <product>",
            f"    <sku>{escape(str(r['sku']))}</sku>",
            f"    <ean>{escape(str(r['ean']))}</ean>",
        ]
        for cc in ["lt", "lv", "ee", "fi"]:
            price_s = money(market_prices[cc])
            parts += [
                f"    <price-before-discount-{cc}>{price_s}</price-before-discount-{cc}>",
                f"    <price-after-discount-{cc}>{price_s}</price-after-discount-{cc}>",
            ]
        parts += [
            f"    <stock>{int(stock)}</stock>",
            f"    <collectionhours>{int(hours)}</collectionhours>",
            "  </product>",
        ]
    parts.append("</products>")
    return "\n".join(parts) + "\n"


def diff_rows(rows: List[dict]) -> List[dict]:
    out = []
    for r in rows:
        out.append({
            "sku": r.get("sku"),
            "ean": r.get("ean"),
            "shopify_sku": r.get("shopify_sku"),
            "status": r.get("status"),
            "shopify_product_status": r.get("shopify_product_status"),
            "legacy_stock": r.get("legacy_stock"),
            "generated_stock": r.get("marketplace_stock"),
            "legacy_collectionhours": r.get("legacy_collectionhours"),
            "generated_collectionhours": r.get("collectionhours"),
            "legacy_220_price": r.get("legacy_current_220_price"),
            "shopify_price": r.get("shopify_price"),
            "generated_price_lt": r.get("generated_price_lt"),
            "generated_price_lv": r.get("generated_price_lv"),
            "generated_price_ee": r.get("generated_price_ee"),
            "generated_price_fi": r.get("generated_price_fi"),
            "price_raise_required_lv": r.get("price_raise_required"),
            "stock_source": r.get("stock_source"),
        })
    return out


def validate_generated(rows: List[dict], pilot_rows: List[dict], xml_text: str) -> List[dict]:
    checks = []
    src_by_sku = {r["sku"].strip(): r for r in pilot_rows}
    exported_skus = set(re.findall(r"<sku>(.*?)</sku>", xml_text))

    identity_ok = True
    blocked_excluded = True
    price_floor_ok = True
    stock_rule_ok = True

    for r in rows:
        src = src_by_sku.get(r["sku"])
        if not src or r["ean"] != digits(src.get("ean") or ""):
            identity_ok = False
        status = str(r.get("status", ""))
        if (status.startswith("BLOCKED") or status.startswith("EXCLUDED_APPROVED")) and r["sku"] in exported_skus:
            blocked_excluded = False
        if not str(r.get("status", "")).startswith(("BLOCKED", "EXCLUDED_APPROVED")):
            sp = r.get("shopify_price")
            gp = r.get("required_220_selling_price")
            if sp is not None and (gp is None or gp < sp):
                price_floor_ok = False
            sel = r.get("selected_stock")
            mp = r.get("marketplace_stock")
            if sel is not None:
                expected = 0 if sel <= 0 else max(sel, 3)
                if mp != expected:
                    stock_rule_ok = False

    checks += [
        {"check": "LOCKED_MARKETPLACE_SKU_EAN", "pass": identity_ok},
        {"check": "BLOCKED_OR_APPROVED_EXCLUSIONS_ABSENT_FROM_XML", "pass": blocked_excluded},
        {"check": "PRICE_NEVER_BELOW_SHOPIFY", "pass": price_floor_ok},
        {"check": "MARKETPLACE_STOCK_RULE", "pass": stock_rule_ok},
    ]
    return checks


def run(args) -> int:
    pilot_path = Path(args.pilot_csv)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    pilot = read_csv(pilot_path)
    approved_exclusions = load_approved_exclusions(Path(args.approved_exclusions) if args.approved_exclusions else None)

    input_blockers = validate_pilot_input(pilot)
    if input_blockers:
        (out / "input-blockers.json").write_text(json.dumps(input_blockers, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": "BLOCKED_INPUT", "count": len(input_blockers)}, indent=2))
        return 2

    target_skus = {(r.get("shopify_sku") or "").strip() for r in pilot}
    if args.live:
        domain = os.environ.get("SHOPIFY_STORE_DOMAIN", DEFAULT_STORE_DOMAIN)
        api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
        nodes, source_meta = fetch_shopify_live(target_skus, domain, api_version)
    elif args.fixture_json:
        nodes, source_meta = load_fixture_snapshot(Path(args.fixture_json))
    else:
        raise RuntimeError("Choose --live or --fixture-json")

    # Persist exactly what the merger consumed. This makes every candidate run reproducible.
    snapshot_path = out / "shopify-live-snapshot.json"
    snapshot_path.write_text(json.dumps({"meta": source_meta, "nodes": nodes}, ensure_ascii=False, indent=2), encoding="utf-8")

    refreshed, blockers, warnings, exclusions = merge_live(pilot, nodes, approved_exclusions)
    refreshed_path = out / "refreshed-master.json"
    refreshed_path.write_text(json.dumps(refreshed, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = [
        "sku", "ean", "shopify_sku", "status", "shopify_product_status", "shopify_vendor",
        "legacy_stock", "marketplace_stock", "stock_source", "collectionhours",
        "store_on_hand_raw", "lowa_on_hand_raw", "fjord_on_hand_raw",
        "legacy_220_price", "shopify_price", "generated_220_price", "price_raise_required",
    ]
    diffs = diff_rows(refreshed)
    write_csv(out / "diff-report.csv", diffs, fields=fields)
    (out / "diff-report.json").write_text(json.dumps(diffs, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "blockers.json").write_text(json.dumps(blockers, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "warnings.json").write_text(json.dumps(warnings, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "approved-exclusions-applied.json").write_text(json.dumps(exclusions, ensure_ascii=False, indent=2), encoding="utf-8")

    xml = stock_price_xml(refreshed)
    xml_path = out / "stock-price-candidate.xml"
    xml_path.write_text(xml, encoding="utf-8")

    checks = validate_generated(refreshed, pilot, xml)
    all_checks = all(x["pass"] for x in checks)
    exported = sum(
        1 for r in refreshed
        if not str(r.get("status", "")).startswith(("BLOCKED", "EXCLUDED_APPROVED"))
        and r.get("required_220_selling_price") is not None
    )
    missing = [r["shopify_sku"] for r in refreshed if r.get("status") == "BLOCKED_SHOPIFY_MATCH_NOT_FOUND"]

    gate = {
        "mode": "PHASE1_LIVE_READ_ONLY" if args.live else "PHASE1_FIXTURE_TEST",
        "publish_allowed": (len(blockers) == 0 and all_checks and (exported + len(exclusions)) == len(pilot)),
        "pilot_rows": len(pilot),
        "exported_rows": exported,
        "blocker_count": len(blockers),
        "approved_exclusion_count": len(exclusions),
        "warning_count": len(warnings),
        "missing_shopify_skus": missing,
        "approved_excluded_shopify_skus": [r.get("shopify_sku") for r in exclusions],
        "validation_checks": checks,
        "safety": {
            "shopify_writes": False,
            "google_sheet_writes": False,
            "pmp_writes": False,
            "existing_220_feed_replaced": False,
            "fhm_in_scope": False,
            "approved_exclusions_are_not_published": True,
        },
    }
    gate_path = out / "publish-gate.json"
    gate_path.write_text(json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "generator": "outfish-220-phase1-live-generator-v5",
        "generated_at": now_iso(),
        "source_meta": source_meta,
        "pilot_input": str(pilot_path),
        "pilot_input_sha256": sha256_file(pilot_path),
        "approved_exclusions": args.approved_exclusions,
        "artifacts": {
            "shopify-live-snapshot.json": sha256_file(snapshot_path),
            "refreshed-master.json": sha256_file(refreshed_path),
            "stock-price-candidate.xml": sha256_file(xml_path),
            "publish-gate.json": sha256_file(gate_path),
        },
        "gate": gate,
    }
    (out / "run-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "pilot_rows": len(pilot),
        "snapshot_nodes": len(nodes),
        "exported_rows": exported,
        "blockers": len(blockers),
        "approved_exclusions": len(exclusions),
        "warnings": len(warnings),
        "publish_allowed": gate["publish_allowed"],
        "missing_shopify_skus": missing,
        "approved_excluded_shopify_skus": [r.get("shopify_sku") for r in exclusions],
    }, indent=2))
    return 0


def self_test() -> int:
    # Deterministic routing + identity + price-floor smoke tests.
    assert marketplace_qty(0) == 0
    assert marketplace_qty(1) == 3
    assert marketplace_qty(7) == 7
    assert route_stock(1, 9, 0)["stock_source"] == "STORE"
    assert route_stock(-1, 4, 0)["stock_source"] == "LOWA"
    assert route_stock(0, 0, 6)["collectionhours"] == 72
    assert route_stock(2, 3, 4)["status"] == "BLOCKED_LOCATION_CONFLICT"
    assert route_stock(0, 0, 0)["status"] == "PASS_ZERO_STOCK_NEEDS_LEGACY_HOURS"
    pilot_test = [{
        "sku":"MISSING1","ean":"1234567890123","shopify_sku":"MISSING1","stock":"0",
        "collectionhours":"24","price_before":"10","price_after":"10",
        "current_220_price":"10","vendor":"Test","match_status":"MATCH_EXPLICIT_220_MAPPING"
    }]
    rr, bb, ww, xx = merge_live(pilot_test, [], {"MISSING1":{"reason":"TEST"}})
    assert len(bb) == 0 and len(xx) == 1 and rr[0]["status"].startswith("EXCLUDED_APPROVED")
    print("SELF_TEST PASS: 9/9")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot-csv", default="pilot_input_674.csv")
    ap.add_argument("--output-dir", default="output")
    ap.add_argument("--approved-exclusions", default="approved_exclusions.json")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--fixture-json")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        raise SystemExit(self_test())
    if not args.live and not args.fixture_json:
        ap.error("choose --live or --fixture-json")
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
