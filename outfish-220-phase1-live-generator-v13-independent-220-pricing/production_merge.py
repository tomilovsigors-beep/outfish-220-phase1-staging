#!/usr/bin/env python3
from pathlib import Path
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import json

def _children_signature(node):
    return [(c.tag, (c.text or "").strip()) for c in list(node)]

def _parse_bytes(raw):
    return ET.fromstring(raw)

def _read_candidate(path: Path):
    return ET.parse(path).getroot()

def _sku(node):
    return (node.findtext("sku") or "").strip()

def _ean(node):
    return (node.findtext("ean") or "").strip()

def fetch_production(url: str, timeout: int = 60):
    req = Request(url, headers={"User-Agent": "outfish-220-staging-v12/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()

def merge_candidate_into_production(candidate_path: Path, production_url: str, output_path: Path):
    prod_raw = fetch_production(production_url)
    prod_root = _parse_bytes(prod_raw)
    cand_root = _read_candidate(candidate_path)

    prod_nodes = list(prod_root.findall(".//product"))
    cand_nodes = list(cand_root.findall(".//product"))

    prod_by_sku = {}
    duplicates_prod = []
    for p in prod_nodes:
        s = _sku(p)
        if s in prod_by_sku:
            duplicates_prod.append(s)
        prod_by_sku[s] = p

    cand_by_sku = {}
    duplicates_cand = []
    for p in cand_nodes:
        s = _sku(p)
        if s in cand_by_sku:
            duplicates_cand.append(s)
        cand_by_sku[s] = p

    candidate_only = sorted(set(cand_by_sku) - set(prod_by_sku))
    overlap = sorted(set(cand_by_sku) & set(prod_by_sku))

    checks = []
    def add(name, passed, **extra):
        checks.append({"check": name, "pass": bool(passed), **extra})

    add("NO_DUPLICATE_PRODUCTION_SKU", not duplicates_prod, count=len(duplicates_prod))
    add("NO_DUPLICATE_CANDIDATE_SKU", not duplicates_cand, count=len(duplicates_cand))
    add("CANDIDATE_SUBSET_OF_PRODUCTION", not candidate_only, count=len(candidate_only))
    add("ALL_CANDIDATE_ROWS_OVERLAP", len(overlap) == len(cand_nodes),
        overlap=len(overlap), candidate_rows=len(cand_nodes))

    # Replace in-place to preserve production row ordering.
    replaced = 0
    ean_changes = []
    candidate_signatures = {s: _children_signature(n) for s, n in cand_by_sku.items()}
    untouched_before = {s: _children_signature(n) for s, n in prod_by_sku.items()
                        if s not in cand_by_sku}

    # Assumes product nodes are direct children of the root, which is true for current feed.
    # Fail closed if not.
    direct_products = list(prod_root.findall("product"))
    add("PRODUCTION_PRODUCTS_ARE_DIRECT_ROOT_CHILDREN",
        len(direct_products) == len(prod_nodes),
        direct=len(direct_products), total=len(prod_nodes))

    if all(x["pass"] for x in checks):
        for i, p in enumerate(list(prod_root)):
            if p.tag != "product":
                continue
            s = _sku(p)
            if s in cand_by_sku:
                if _ean(p) != _ean(cand_by_sku[s]):
                    ean_changes.append({
                        "sku": s,
                        "production": _ean(p),
                        "candidate": _ean(cand_by_sku[s]),
                    })
                prod_root.remove(p)
                # insert candidate at the exact production position
                new_node = ET.fromstring(ET.tostring(cand_by_sku[s], encoding="utf-8"))
                prod_root.insert(i, new_node)
                replaced += 1

    add("NO_EAN_CHANGES_ON_REPLACED_ROWS", not ean_changes, count=len(ean_changes))
    add("REPLACED_ROW_COUNT_MATCHES_CANDIDATE",
        replaced == len(cand_nodes), replaced=replaced, candidate_rows=len(cand_nodes))

    merged_nodes = list(prod_root.findall(".//product"))
    merged_by_sku = {_sku(p): p for p in merged_nodes}

    add("MERGED_ROW_COUNT_EQUALS_PRODUCTION",
        len(merged_nodes) == len(prod_nodes),
        merged_rows=len(merged_nodes), production_rows=len(prod_nodes))
    add("MERGED_SKU_SET_EQUALS_PRODUCTION",
        set(merged_by_sku) == set(prod_by_sku),
        missing=len(set(prod_by_sku)-set(merged_by_sku)),
        extra=len(set(merged_by_sku)-set(prod_by_sku)))

    candidate_mismatches = []
    for s, sig in candidate_signatures.items():
        if s not in merged_by_sku or _children_signature(merged_by_sku[s]) != sig:
            candidate_mismatches.append(s)
    add("MERGED_CANDIDATE_ROWS_EQUAL_APPROVED_CANDIDATE",
        not candidate_mismatches, count=len(candidate_mismatches))

    untouched_mismatches = []
    for s, sig in untouched_before.items():
        if s not in merged_by_sku or _children_signature(merged_by_sku[s]) != sig:
            untouched_mismatches.append(s)
    add("UNTOUCHED_PRODUCTION_ROWS_PRESERVED",
        not untouched_mismatches, count=len(untouched_mismatches))

    passed = all(x["pass"] for x in checks)
    if passed:
        tree = ET.ElementTree(prod_root)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)

    report = {
        "pass": passed,
        "production_rows": len(prod_nodes),
        "candidate_rows": len(cand_nodes),
        "merged_rows": len(merged_nodes) if passed else None,
        "overlap_rows": len(overlap),
        "replaced_rows": replaced,
        "untouched_rows": len(prod_nodes) - replaced,
        "candidate_only": candidate_only,
        "ean_changes": ean_changes,
        "checks": checks,
    }
    return report
