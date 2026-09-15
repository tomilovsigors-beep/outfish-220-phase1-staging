#!/usr/bin/env python3
from __future__ import annotations
import json, urllib.request
from pathlib import Path
import xml.etree.ElementTree as ET

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
    for p in root.findall('.//product'):
        sku = _text(p, 'sku')
        if not sku:
            continue
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
    return {'rows': out, 'duplicates': sorted(set(duplicates))}

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
    }
