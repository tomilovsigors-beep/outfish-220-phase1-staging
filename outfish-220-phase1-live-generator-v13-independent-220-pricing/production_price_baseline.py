#!/usr/bin/env python3
from pathlib import Path
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

def _num(s):
    try:
        return Decimal(str(s).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None

def _money(v):
    if v is None:
        return ""
    q = v.quantize(Decimal("0.01"))
    s = format(q, "f").rstrip("0").rstrip(".")
    return s or "0"

def fetch_production_prices(url: str, timeout: int = 60):
    req = Request(url, headers={"User-Agent": "outfish-220-staging-v11/1.0"})
    with urlopen(req, timeout=timeout) as r:
        raw = r.read()
    root = ET.fromstring(raw)
    out = {}
    for p in root.findall(".//product"):
        sku = (p.findtext("sku") or "").strip()
        if not sku:
            continue
        before = _num(p.findtext("price-before-discount"))
        after = _num(p.findtext("price-after-discount"))
        out[sku] = {"before": before, "after": after}
    return out

def apply_production_before_discount(candidate_path: Path, production_url: str):
    prod = fetch_production_prices(production_url)
    tree = ET.parse(candidate_path)
    root = tree.getroot()

    changed = []
    blocked = []
    for p in root.findall(".//product"):
        sku = (p.findtext("sku") or "").strip()
        c_after = _num(p.findtext("price-after-discount"))
        c_before_node = p.find("price-before-discount")
        if not sku or c_after is None or c_before_node is None:
            blocked.append({"sku": sku, "reason": "CANDIDATE_PRICE_FIELDS_MISSING"})
            continue

        baseline = prod.get(sku)
        if not baseline:
            blocked.append({"sku": sku, "reason": "SKU_NOT_IN_PRODUCTION"})
            continue

        # Preserve the existing "before discount" anchor when it is above the new
        # selling price. If Shopify/live rules raise the selling price above it,
        # lift before-discount to the selling price so before >= after.
        p_before = baseline.get("before")
        new_before = max(p_before, c_after) if p_before is not None else c_after
        old_before = _num(c_before_node.text)
        c_before_node.text = _money(new_before)

        if old_before != new_before:
            changed.append({
                "sku": sku,
                "candidate_before_old": float(old_before) if old_before is not None else None,
                "production_before": float(p_before) if p_before is not None else None,
                "candidate_after": float(c_after),
                "candidate_before_new": float(new_before),
            })

    if blocked:
        raise RuntimeError(f"production price baseline blocked {len(blocked)} rows; sample={blocked[:5]}")

    tree.write(candidate_path, encoding="utf-8", xml_declaration=True)
    return {"changed_count": len(changed), "changed": changed}
