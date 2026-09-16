from __future__ import annotations
import csv, io, json, re
from collections import Counter, defaultdict

SPECIAL_CANONICAL_BLOCKED = {"NH21MSD08L", "NH21MSD08R"}
EAN_RE = re.compile(r"^\d{11,13}$")


def norm(v):
    return "" if v is None else str(v).strip()


def canonical_master(rows):
    out = {}
    for r in rows:
        sku = norm(r.get("220_sku"))
        status = norm(r.get("220_status"))
        if sku and (status == "ACTIVE_220" or sku in SPECIAL_CANONICAL_BLOCKED):
            if sku in out:
                raise RuntimeError(f"duplicate canonical Master SKU {sku}")
            out[sku] = r
    return out


def _cdata(v):
    return "<![CDATA[" + norm(v).replace("]]>", "]]\]\]><![CDATA[>") + "]]>>" if False else "<![CDATA[" + norm(v).replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _tag(name, value, indent, cdata=True):
    p = " " * indent
    if cdata:
        return f"{p}<{name}>{_cdata(value)}</{name}>"
    return f"{p}<{name}>{norm(value)}</{name}>"


def _csv_bytes(rows, fields):
    s = io.StringIO(newline="")
    w = csv.DictWriter(s, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    return s.getvalue().encode("utf-8-sig")


def _options(variant):
    out = {}
    for x in (variant or {}).get("selected_options") or []:
        n = norm(x.get("name")).casefold()
        v = norm(x.get("value"))
        if n:
            out[n] = v
    return out


def _color_and_size(variant):
    o = _options(variant)
    color = ""
    size = ""
    for k, v in o.items():
        if k in {"color", "colour", "krāsa", "krasa", "spalva", "värv", "varv"}:
            color = v
        if k in {"size", "izmērs", "izmers", "dydis", "suurus", "koko"}:
            size = v
    return color, size


def _category_record(r, source, category_mapping):
    source_id = norm((source or {}).get("category_id"))
    ptype = norm(r.get("product_type")) or norm((source or {}).get("product_type"))
    for key in (source_id, ptype):
        if key and key in category_mapping:
            return category_mapping[key]
    return None


def _package_value(r, variant, name):
    direct = norm(r.get(f"220_package_{name}"))
    if direct:
        return direct
    if name == "weight":
        w = ((variant or {}).get("weight") or {})
        value = w.get("value")
        if value not in (None, ""):
            return norm(value)
    return ""


def _image_metadata_ok(images, resolved_main):
    if not images or not resolved_main:
        return False
    checks = []
    main_in_meta = False
    for im in images:
        url = norm(im.get("url"))
        if url == resolved_main:
            main_in_meta = True
        mime = norm(im.get("mime_type")).lower()
        width = im.get("width")
        height = im.get("height")
        https_ok = url.lower().startswith("https://")
        type_ok = mime in {"image/jpeg", "image/jpg", "image/png"} or bool(re.search(r"\.(jpe?g|png)(?:\?|$)", url, re.I))
        size_ok = isinstance(width, int) and isinstance(height, int) and width >= 1000 and height >= 1000
        checks.append(https_ok and type_ok and size_ok)
    return main_in_meta and bool(checks) and all(checks[:max(1, min(2, len(checks)))])


def build(master_rows, shopify_by_product_id, category_mapping=None, category_fields=None):
    category_mapping = category_mapping or {}
    category_fields = category_fields or {}
    canonical = canonical_master(master_rows)
    rows = list(canonical.values())
    ean_counts = Counter(norm(r.get("220_ean")) for r in rows if norm(r.get("220_ean")))
    sku_counts = Counter(norm(r.get("220_sku")) for r in rows if norm(r.get("220_sku")))
    readiness = []
    candidates = []

    for r in rows:
        sku = norm(r.get("220_sku"))
        ean = norm(r.get("220_ean"))
        pid = norm(r.get("shopify_product_id"))
        vid = norm(r.get("shopify_variant_id"))
        source = shopify_by_product_id.get(pid) if pid and vid else None
        variant = ((source or {}).get("variants_by_id") or {}).get(vid)
        title = norm(r.get("220_title"))
        description = norm((source or {}).get("description"))
        master_main = norm(r.get("220_main_image_url"))
        shopify_main = norm(r.get("shopify_main_image_url")) or norm((source or {}).get("main_image_url"))
        resolved_main = master_main or shopify_main
        source_images = list((source or {}).get("images") or [])
        image_urls = []
        if resolved_main:
            image_urls.append(resolved_main)
        for im in source_images:
            url = norm(im.get("url"))
            if url and url not in image_urls:
                image_urls.append(url)

        reasons = []
        if not title:
            reasons.append("MISSING_220_TITLE")
        if not description:
            reasons.append("MISSING_DESCRIPTION")
        if not resolved_main:
            reasons.append("MISSING_MAIN_IMAGE")
        if len(image_urls) < 2:
            reasons.append("INSUFFICIENT_IMAGES")
        if image_urls:
            metadata_ok = _image_metadata_ok(source_images, resolved_main)
            direct_ok = bool((source or {}).get("images_direct_no_redirect_verified"))
            background_ok = bool((source or {}).get("main_background_verified"))
            if not metadata_ok or not direct_ok or not background_ok:
                reasons.append("IMAGE_REQUIREMENTS_NOT_VERIFIED")

        cat = _category_record(r, source, category_mapping)
        if not cat:
            reasons.append("CATEGORY_MAPPING_REQUIRED")
            category_id = category_name = ""
        else:
            category_id = norm(cat.get("category_id"))
            category_name = norm(cat.get("category_name"))
            if not category_id or not category_name:
                reasons.append("CATEGORY_MAPPING_REQUIRED")

        required_props = category_fields.get(category_id) if cat else None
        if required_props is None:
            reasons.append("CATEGORY_FIELDS_FILE_REQUIRED")
            props = {}
        else:
            props = dict((source or {}).get("properties") or {})
            props.update(cat.get("properties") or {})
            if any(not norm(props.get(p)) for p in required_props):
                reasons.append("MISSING_REQUIRED_PROPERTIES")

        if not EAN_RE.fullmatch(ean):
            reasons.append("INVALID_EAN")
        elif ean_counts[ean] > 1:
            reasons.append("DUPLICATE_EAN")
        if not sku or sku_counts[sku] > 1:
            reasons.append("INVALID_OR_DUPLICATE_SUPPLIER_CODE")

        if not variant:
            reasons.append("INVALID_MODIFICATION_GROUPING")
            color = size = ""
        else:
            color, size = _color_and_size(variant)

        is_fashion = bool((cat or {}).get("fashion_package_exception"))
        package = {}
        for dim, blocker in (
            ("weight", "MISSING_PACKAGE_WEIGHT"),
            ("length", "MISSING_PACKAGE_LENGTH"),
            ("height", "MISSING_PACKAGE_HEIGHT"),
            ("width", "MISSING_PACKAGE_WIDTH"),
        ):
            package[dim] = _package_value(r, variant, dim)
            if cat and not is_fashion and not package[dim]:
                reasons.append(blocker)

        reasons = list(dict.fromkeys(reasons))
        eligibility = "READY_PRODUCT_XML" if not reasons else "BLOCKED_PRODUCT_XML"
        row = {
            "220_sku": sku,
            "220_ean": ean,
            "shopify_product_id": pid,
            "shopify_variant_id": vid,
            "shopify_title": norm(r.get("shopify_title")) or norm((source or {}).get("title")),
            "220_title": title,
            "description_status": "PRESENT" if description else "MISSING",
            "shopify_main_image_url": shopify_main,
            "220_main_image_url": master_main,
            "resolved_main_image": resolved_main,
            "image_count": len(image_urls),
            "category_id": category_id,
            "category_name": category_name,
            "colour": color,
            "modification_title": size or norm((variant or {}).get("title")),
            "weight": package["weight"],
            "length": package["length"],
            "height": package["height"],
            "width": package["width"],
            "product_xml_eligibility": eligibility,
            "blocker_reasons": "|".join(reasons),
        }
        readiness.append(row)
        if not reasons:
            candidates.append({
                "row": row,
                "source": source,
                "variant": variant,
                "properties": props,
                "images": image_urls,
                "manufacturer_code": norm(r.get("220_manufacturer_code")),
            })

    groups = defaultdict(list)
    for x in candidates:
        row = x["row"]
        groups[(row["shopify_product_id"] or row["220_sku"], row["colour"])].append(x)

    invalid_group_skus = set()
    for items in groups.values():
        if len(items) > 1:
            if any(not norm(x["row"].get("modification_title")) for x in items):
                invalid_group_skus.update(x["row"]["220_sku"] for x in items)
            codes = [x["row"]["220_sku"] for x in items]
            if len(codes) != len(set(codes)):
                invalid_group_skus.update(codes)
    if invalid_group_skus:
        for row in readiness:
            if row["220_sku"] in invalid_group_skus:
                rs = [x for x in row["blocker_reasons"].split("|") if x]
                if "INVALID_MODIFICATION_GROUPING" not in rs:
                    rs.append("INVALID_MODIFICATION_GROUPING")
                row["blocker_reasons"] = "|".join(rs)
                row["product_xml_eligibility"] = "BLOCKED_PRODUCT_XML"
        candidates = [x for x in candidates if x["row"]["220_sku"] not in invalid_group_skus]
        groups = defaultdict(list)
        for x in candidates:
            groups[(x["row"]["shopify_product_id"] or x["row"]["220_sku"], x["row"]["colour"])].append(x)

    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<products>"]
    for _, items in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        first = items[0]
        row = first["row"]
        source = first["source"] or {}
        xml_lines.append("  <product>")
        xml_lines.append(_tag("category-id", row["category_id"], 4))
        xml_lines.append(_tag("category-name", row["category_name"], 4))
        xml_lines.append(_tag("title", row["220_title"], 4))
        xml_lines.append(_tag("long-description", source.get("description", ""), 4))
        xml_lines.append("    <properties>")
        for name, value in sorted(first["properties"].items()):
            if not norm(value):
                continue
            xml_lines.append("      <property>")
            xml_lines.append(_tag("name", name, 8))
            xml_lines.append("        <values>")
            xml_lines.append(_tag("value", value, 10))
            xml_lines.append("        </values>")
            xml_lines.append("      </property>")
        xml_lines.append("    </properties>")
        xml_lines.append("    <colours>")
        xml_lines.append("      <colour>")
        xml_lines.append("        <images>")
        for url in first["images"]:
            xml_lines.append("          <image>")
            xml_lines.append(_tag("url", url, 12))
            xml_lines.append("          </image>")
        xml_lines.append("        </images>")
        xml_lines.append("        <modifications>")
        multiple = len(items) > 1
        for x in items:
            rr = x["row"]
            xml_lines.append("          <modification>")
            if multiple:
                xml_lines.append(_tag("modification-title", rr["modification_title"], 12))
            for dim in ("weight", "length", "height", "width"):
                if rr[dim]:
                    xml_lines.append(_tag(dim, rr[dim], 12, cdata=False))
            xml_lines.append("            <attributes>")
            xml_lines.append("              <barcodes>")
            xml_lines.append(_tag("barcode", rr["220_ean"], 16))
            xml_lines.append("              </barcodes>")
            xml_lines.append(_tag("supplier-code", rr["220_sku"], 14))
            if x["manufacturer_code"]:
                xml_lines.append(_tag("manufacturer-code", x["manufacturer_code"], 14))
            xml_lines.append("            </attributes>")
            xml_lines.append("          </modification>")
        xml_lines.append("        </modifications>")
        xml_lines.append("      </colour>")
        xml_lines.append("    </colours>")
        xml_lines.append("  </product>")
    xml_lines.append("</products>")
    xml = ("\n".join(xml_lines) + "\n").encode("utf-8")

    blockers = [{
        "220_sku": r["220_sku"],
        "220_ean": r["220_ean"],
        "product_xml_eligibility": r["product_xml_eligibility"],
        "blocker_reasons": r["blocker_reasons"],
    } for r in readiness if r["blocker_reasons"]]
    counts = Counter()
    for r in readiness:
        for reason in filter(None, r["blocker_reasons"].split("|")):
            counts[reason] += 1
    fields = list(readiness[0]) if readiness else []
    validation = {
        "canonical_identities": len(readiness),
        "ready_product_xml": sum(r["product_xml_eligibility"] == "READY_PRODUCT_XML" for r in readiness),
        "blocked_product_xml": sum(r["product_xml_eligibility"] == "BLOCKED_PRODUCT_XML" for r in readiness),
        "dry_run_product_groups": len(groups),
        "publish_gate": "PASS" if readiness and not blockers else "BLOCKED",
        "blocker_breakdown": dict(sorted(counts.items())),
        "category_fields_loaded": bool(category_fields),
    }
    return {
        "product-xml-readiness.csv": _csv_bytes(readiness, fields),
        "product-xml-blockers.csv": _csv_bytes(blockers, ["220_sku", "220_ean", "product_xml_eligibility", "blocker_reasons"]),
        "product-xml-dry-run.xml": xml,
        "product-xml-validation.json": json.dumps(validation, indent=2, sort_keys=True).encode(),
    }
