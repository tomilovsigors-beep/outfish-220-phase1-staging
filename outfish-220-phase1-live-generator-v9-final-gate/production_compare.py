#!/usr/bin/env python3
from __future__ import annotations
import json, urllib.request
from pathlib import Path
import xml.etree.ElementTree as ET

EXPECTED_TAG_ORDER = [
    'sku','ean',
    'price-before-discount-lt','price-after-discount-lt',
    'price-before-discount-lv','price-after-discount-lv',
    'price-before-discount-ee','price-after-discount-ee',
    'price-before-discount-fi','price-after-discount-fi',
    'stock','collectionhours',
]

PRICE_TAGS = [
    'price-before-discount-lt','price-after-discount-lt',
    'price-before-discount-lv','price-after-discount-lv',
    'price-before-discount-ee','price-after-discount-ee',
    'price-before-discount-fi','price-after-discount-fi',
]

def _text(node, tag):
    x = node.find(tag)
    return '' if x is None or x.text is None else x.text.strip()

def _num(v):
    try: return float(v)
    except Exception: return None

def parse_feed_bytes(data: bytes) -> dict[str, dict]:
    root = ET.fromstring(data)
    out = {}
    duplicates = []
    orders = {}
    for p in root.findall('.//product'):
        sku = _text(p, 'sku')
        if not sku:
            continue
        orders[sku] = [child.tag for child in list(p)]
        row = {
            'sku': sku,
            'ean': _text(p, 'ean'),
            'stock': _text(p, 'stock'),
            'collectionhours': _text(p, 'collectionhours'),
        }
        for t in PRICE_TAGS:
            row[t] = _text(p, t)
        if sku in out:
            duplicates.append(sku)
        out[sku] = row
    return {'rows': out, 'duplicates': sorted(set(duplicates)), 'orders': orders}

def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent':'outfish-220-staging-v8/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def compare(candidate_path: Path, production_url: str, expected_export_rows: int = 672) -> dict:
    cand = parse_feed_bytes(candidate_path.read_bytes())
    prod = parse_feed_bytes(fetch_bytes(production_url))
    c, p = cand['rows'], prod['rows']
    cset, pset = set(c), set(p)
    overlap = sorted(cset & pset)
    candidate_only = sorted(cset - pset)
    candidate_order_mismatches = [
        {'sku': sku, 'order': cand['orders'].get(sku)}
        for sku in sorted(cset)
        if cand['orders'].get(sku) != EXPECTED_TAG_ORDER
    ]
    production_order_mismatches = [
        {'sku': sku, 'order': prod['orders'].get(sku)}
        for sku in overlap
        if prod['orders'].get(sku) != EXPECTED_TAG_ORDER
    ]
    cross_feed_order_mismatches = [
        {'sku': sku, 'production_order': prod['orders'].get(sku), 'candidate_order': cand['orders'].get(sku)}
        for sku in overlap
        if prod['orders'].get(sku) != cand['orders'].get(sku)
    ]
    ean_changes=[]; stock_changes=[]; hours_changes=[]; price_changes=[]; price_decreases=[]
    for sku in overlap:
        a, b = c[sku], p[sku]
        if a['ean'] != b['ean']:
            ean_changes.append({'sku':sku,'production':b['ean'],'candidate':a['ean']})
        if a['stock'] != b['stock']:
            stock_changes.append({'sku':sku,'production':b['stock'],'candidate':a['stock']})
        if a['collectionhours'] != b['collectionhours']:
            hours_changes.append({'sku':sku,'production':b['collectionhours'],'candidate':a['collectionhours']})
        for tag in PRICE_TAGS:
            if a[tag] != b[tag]:
                price_changes.append({'sku':sku,'field':tag,'production':b[tag],'candidate':a[tag]})
                av, bv = _num(a[tag]), _num(b[tag])
                if av is not None and bv is not None and av < bv:
                    price_decreases.append({'sku':sku,'field':tag,'production':bv,'candidate':av})
    checks = [
        {'check':'EXPECTED_EXPORT_ROW_COUNT','pass':len(c)==expected_export_rows,'actual':len(c),'expected':expected_export_rows},
        {'check':'NO_DUPLICATE_CANDIDATE_SKU','pass':not cand['duplicates'],'duplicates':cand['duplicates']},
        {'check':'NO_CANDIDATE_SKU_MISSING_FROM_CURRENT_PRODUCTION','pass':not candidate_only,'skus':candidate_only[:100]},
        {'check':'NO_EAN_CHANGES_FOR_SAME_SKU','pass':not ean_changes,'count':len(ean_changes)},
        {'check':'NO_MARKET_PRICE_DECREASES','pass':not price_decreases,'count':len(price_decreases)},
        {'check':'CANDIDATE_EXACT_TAG_ORDER','pass':not candidate_order_mismatches,'count':len(candidate_order_mismatches)},
        {'check':'PRODUCTION_EXACT_TAG_ORDER_MATCHES_EXPECTED','pass':not production_order_mismatches,'count':len(production_order_mismatches)},
        {'check':'CANDIDATE_TAG_ORDER_EQUALS_PRODUCTION','pass':not cross_feed_order_mismatches,'count':len(cross_feed_order_mismatches)},
    ]
    return {
        'production_url': production_url,
        'candidate_rows': len(c),
        'production_rows': len(p),
        'overlap_rows': len(overlap),
        'candidate_only_rows': len(candidate_only),
        'production_only_rows': len(pset-cset),
        'duplicate_candidate_skus': cand['duplicates'],
        'duplicate_production_skus': prod['duplicates'],
        'ean_change_count': len(ean_changes),
        'stock_change_count': len(stock_changes),
        'collectionhours_change_count': len(hours_changes),
        'price_field_change_count': len(price_changes),
        'price_decrease_count': len(price_decreases),
        'checks': checks,
        'pass': all(x['pass'] for x in checks),
        'candidate_only': candidate_only,
        'ean_changes': ean_changes,
        'stock_changes': stock_changes,
        'collectionhours_changes': hours_changes,
        'price_changes': price_changes,
        'price_decreases': price_decreases,
        'candidate_order_mismatches': candidate_order_mismatches,
        'production_order_mismatches': production_order_mismatches,
        'cross_feed_order_mismatches': cross_feed_order_mismatches,
        'expected_tag_order': EXPECTED_TAG_ORDER,
    }
