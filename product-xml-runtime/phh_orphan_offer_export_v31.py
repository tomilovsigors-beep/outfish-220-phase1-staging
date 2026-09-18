from __future__ import annotations
import json
from collections import defaultdict
from pmp_api_probe import _api_login, _api_get

SELLER_ID = "9990696"


def _s(v):
    return str(v or "").strip()


def _offer_eans(o):
    m = (o or {}).get("modification") or {}
    out = []
    for k in ("ean",):
        x = _s(m.get(k))
        if x and x not in out:
            out.append(x)
    for x in (m.get("ean_codes") or []):
        x = _s(x)
        if x and x not in out:
            out.append(x)
    return out


def scan_offers(token):
    items = []
    offset = 0
    limit = 100
    while offset < 50000:
        r = _api_get(f"/v2/sellers/{SELLER_ID}/offers?app_name=220.lv&limit={limit}&offset={offset}", token, timeout=30)
        r.raise_for_status()
        d = r.json()
        batch = (d.get("offers") if isinstance(d, dict) else None) or (d.get("items") if isinstance(d, dict) else None) or (d if isinstance(d, list) else [])
        if not batch:
            break
        items.extend(batch)
        if len(batch) < limit:
            break
        offset += len(batch)
    return items


def run():
    lr = _api_login("v3")
    if lr is None:
        raise RuntimeError("PHH API credentials unavailable")
    lr.raise_for_status()
    token = lr.json()["token"]
    offers = scan_offers(token)

    rows = []
    by_ean = defaultdict(list)
    for o in offers:
        m = (o or {}).get("modification") or {}
        eans = _offer_eans(o)
        rec = {
            "offer_id": _s(o.get("id")),
            "offer_status": _s(o.get("status")),
            "offer_amount": o.get("amount"),
            "offer_price": o.get("sell_price"),
            "modification_id": _s(m.get("id")),
            "pigu_external_id": _s(m.get("pigu_external_id")),
            "live_sku": _s(m.get("sku")),
            "manufacturer_code": _s(m.get("manufacturer_code") or o.get("manufacturer_code")),
            "eans": eans,
        }
        rows.append(rec)
        for e in eans:
            by_ean[e].append(rec)

    return {
        "status": "PASS",
        "seller_id": SELLER_ID,
        "app_name": "220.lv",
        "offer_count": len(rows),
        "unique_eans": len(by_ean),
        "writes": {"phh": 0, "master": 0, "shopify": 0},
        "offers": rows,
    }
