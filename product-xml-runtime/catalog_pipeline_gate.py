"""Fail-closed, read-only catalog state reducer for the Outfish PHH pipeline.

Consumes reconciled evidence; it does not fetch credentials, write to PHH, or
interpret an HTTP 404 as conclusive proof of catalog absence.
"""
from __future__ import annotations
from collections import Counter
from hashlib import sha256
import json

STATES = ("READY", "EXISTING", "NEEDS_DATA", "BLOCKED", "EXCLUDED", "SUBMITTED", "VERIFIED")
LOCALES = ("lt", "lv", "ee", "ru", "fi")


def _s(value):
    return "" if value is None else str(value).strip()


def _ean13(value):
    code = "".join(c for c in _s(value) if c.isdigit())
    if len(code) == 12 and code == _s(value):
        code = "0" + code
    if len(code) != 13 or code != ("0" + _s(value) if len(_s(value)) == 12 else _s(value)):
        return ""
    digits = [int(c) for c in code]
    checksum = (10 - sum(d * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:-1])) % 10) % 10
    return code if checksum == digits[-1] else ""


def _truth(value):
    return value is True


def classify(item):
    """Use explicit proof flags, never inference from missing API results.

    Required evidence fields are per-variant and must be freshly sourced:
    sku, barcode, vendor, shopify_active, price_eur, phh_identity,
    category_id, category_confirmed, required_attributes, feature_values_confirmed,
    images, main_image_neutral_verified, package_verified,
    locales: {lt/lv/ee/ru/fi: {title, description_html, supplier_code}},
    and, for any PHH record, its verified publication evidence.
    """
    sku = _s(item.get("sku"))
    ean = _ean13(item.get("barcode"))
    key = sku + "|" + ean
    reasons = []
    vendor = _s(item.get("vendor")).casefold()
    phh = item.get("phh_identity") or {}
    phh_status = _s(phh.get("status")).upper()
    if vendor == "fhm":
        return {"key": key, "sku": sku, "ean": ean, "state": "EXCLUDED", "reasons": ["EXCLUDED_FHM"]}
    if item.get("excluded_by_policy") is True:
        return {"key": key, "sku": sku, "ean": ean, "state": "EXCLUDED", "reasons": ["POLICY_EXCLUSION"]}

    if phh_status == "EXISTING" and _truth(phh.get("identity_verified")):
        state = "VERIFIED" if _truth(phh.get("listing_verified")) and _truth(phh.get("all_fields_verified")) else "EXISTING"
        return {"key": key, "sku": sku, "ean": ean, "state": state, "reasons": [], "phh_product_id": _s(phh.get("product_id"))}
    if phh_status == "SUBMITTED" and _truth(phh.get("operation_id_verified")):
        return {"key": key, "sku": sku, "ean": ean, "state": "SUBMITTED", "reasons": [], "operation_id": _s(phh.get("operation_id"))}

    if not sku: reasons.append("MISSING_SKU")
    if not ean: reasons.append("INVALID_OR_MISSING_EAN")
    if item.get("sku_unique") is not True: reasons.append("SKU_NOT_CONFIRMED_UNIQUE")
    if item.get("ean_unique") is not True: reasons.append("EAN_NOT_CONFIRMED_UNIQUE")
    if item.get("shopify_active") is not True: reasons.append("SHOPIFY_NOT_CONFIRMED_ACTIVE")
    try:
        price = float(item.get("price_eur"))
        if price < 10: reasons.append("PRICE_BELOW_10_EUR")
    except (TypeError, ValueError):
        reasons.append("PRICE_UNKNOWN")
    if phh_status != "ABSENT_CONFIRMED" or phh.get("exhaustive_check") is not True:
        reasons.append("PHH_IDENTITY_UNRESOLVED")
    if phh.get("http_status") in (404, 429, 503) and phh_status != "EXISTING":
        reasons.append("PHH_ENDPOINT_NOT_CONCLUSIVE")
    if item.get("category_confirmed") is not True or not _s(item.get("category_id")):
        reasons.append("CATEGORY_NOT_CONFIRMED")
    for f in item.get("required_attributes") or []:
        if not _s(f.get("value")):
            reasons.append("ATTRIBUTE_MISSING:" + _s(f.get("field_id")))
        elif f.get("dictionary_required") and f.get("dictionary_confirmed") is not True:
            reasons.append("ATTRIBUTE_DICTIONARY_UNVERIFIED:" + _s(f.get("field_id")))
    if item.get("required_attributes_complete") is not True:
        reasons.append("REQUIRED_ATTRIBUTE_COVERAGE_UNKNOWN")
    locales = item.get("locales") or {}
    for loc in LOCALES:
        content = locales.get(loc) or {}
        title = _s(content.get("title"))
        html = _s(content.get("description_html"))
        if not title or not html.startswith("<h2>" + title + "</h2><p><br></p>") or _s(content.get("supplier_code")) != sku:
            reasons.append("LOCALE_INVALID:" + loc.upper())
    images = item.get("images") or []
    if len(images) < 2 or not all(isinstance(im, dict) and _s(im.get("url")).startswith("https://") and "?" not in _s(im.get("url")) and (im.get("width") or 0) >= 600 and (im.get("height") or 0) >= 600 for im in images[:2]):
        reasons.append("IMAGE_REQUIREMENTS_UNKNOWN")
    if item.get("main_image_neutral_verified") is not True:
        reasons.append("MAIN_BACKGROUND_NOT_VERIFIED")
    if item.get("package_verified") is not True:
        reasons.append("PACKAGE_NOT_VERIFIED")
    if item.get("manufacturer_verified") is not True:
        reasons.append("MANUFACTURER_NOT_VERIFIED")
    if item.get("content_approved") is not True:
        reasons.append("CONTENT_APPROVAL_MISSING")

    reasons = sorted(set(reasons))
    hard = ("PHH_IDENTITY_", "PHH_ENDPOINT_", "SKU_NOT_", "EAN_NOT_", "SHOPIFY_NOT_", "PRICE_BELOW_", "INVALID_OR_MISSING_EAN")
    state = "BLOCKED" if any(r.startswith(hard) for r in reasons) else "NEEDS_DATA" if reasons else "READY"
    return {"key": key, "sku": sku, "ean": ean, "state": state, "reasons": reasons}


def audit(items):
    """Deterministic batches; no PHH writes or unlocked CREATE operations."""
    results = [classify(x) for x in items]
    collisions = Counter(x["key"] for x in results if x["sku"])
    for x in results:
        if x["sku"] and collisions[x["key"]] > 1 and x["state"] not in ("EXCLUDED", "EXISTING", "VERIFIED"):
            x["state"] = "BLOCKED"
            x["reasons"] = sorted(set(x["reasons"] + ["DUPLICATE_IDENTITY_IN_BATCH"]))
    counts = Counter(x["state"] for x in results)
    digest = sha256(json.dumps(results, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {"checked": len(results), "counts": {s: counts[s] for s in STATES},
            "ready_to_submit": counts["READY"], "dataset_hash": digest, "rows": results,
            "phh_writes": 0}
