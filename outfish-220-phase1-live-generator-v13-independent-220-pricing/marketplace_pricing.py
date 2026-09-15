#!/usr/bin/env python3
from pathlib import Path
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen
import csv
import xml.etree.ElementTree as ET

def _num(v):
    try: return Decimal(str(v).strip())
    except (InvalidOperation, ValueError, TypeError): return None

def _money(v):
    q=v.quantize(Decimal("0.01")); s=format(q,"f").rstrip("0").rstrip("."); return s or "0"

def fetch_production_prices(url, timeout=60):
    req=Request(url, headers={"User-Agent":"outfish-220-staging-v13/1.0"})
    with urlopen(req, timeout=timeout) as r: raw=r.read()
    root=ET.fromstring(raw); out={}
    for p in root.findall(".//product"):
        sku=(p.findtext("sku") or "").strip()
        if not sku: continue
        before=_num(p.findtext("price-before-discount")); after=_num(p.findtext("price-after-discount"))
        if before is None or after is None: raise RuntimeError(f"production price missing/invalid for {sku}")
        out[sku]={"before":before,"after":after}
    return out

def load_overrides(path):
    if not path.exists(): return {}
    out={}
    with path.open("r",encoding="utf-8-sig",newline="") as f:
        reader=csv.DictReader(f); required={"sku","price-before-discount","price-after-discount"}
        missing=required-set(reader.fieldnames or [])
        if missing: raise RuntimeError(f"override CSV missing columns: {sorted(missing)}")
        for i,row in enumerate(reader,start=2):
            sku=(row.get("sku") or "").strip()
            if not sku: continue
            if sku in out: raise RuntimeError(f"duplicate override SKU {sku} at line {i}")
            before=_num(row.get("price-before-discount")); after=_num(row.get("price-after-discount"))
            if before is None or after is None or before<=0 or after<=0: raise RuntimeError(f"invalid override prices for {sku}")
            if before<after: raise RuntimeError(f"before-discount < after-discount for {sku}")
            out[sku]={"before":before,"after":after,"reason":(row.get("reason") or "").strip()}
    return out

def apply_independent_220_pricing(candidate_path, production_url, overrides_path):
    prod=fetch_production_prices(production_url); overrides=load_overrides(overrides_path)
    tree=ET.parse(candidate_path); root=tree.getroot()
    candidate_skus={(p.findtext("sku") or "").strip() for p in root.findall(".//product")}
    unknown=sorted(set(overrides)-candidate_skus)
    if unknown: raise RuntimeError(f"override SKU not in Phase1 candidate: {unknown[:10]}")
    preserved=[]; overridden=[]; blocked=[]
    for p in root.findall(".//product"):
        sku=(p.findtext("sku") or "").strip(); bnode=p.find("price-before-discount"); anode=p.find("price-after-discount")
        if not sku or bnode is None or anode is None:
            blocked.append({"sku":sku,"reason":"CANDIDATE_PRICE_FIELDS_MISSING"}); continue
        if sku not in prod:
            blocked.append({"sku":sku,"reason":"SKU_NOT_IN_PRODUCTION"}); continue
        source=overrides.get(sku) or prod[sku]; bnode.text=_money(source["before"]); anode.text=_money(source["after"])
        if sku in overrides: overridden.append({"sku":sku,"price-before-discount":bnode.text,"price-after-discount":anode.text,"reason":source.get("reason","")})
        else: preserved.append(sku)
    if blocked: raise RuntimeError(f"independent 220 pricing blocked {len(blocked)} rows; sample={blocked[:5]}")
    tree.write(candidate_path,encoding="utf-8",xml_declaration=True)
    return {"policy":"INDEPENDENT_220_PRICING","shopify_price_used":False,"candidate_rows":len(candidate_skus),"production_prices_preserved":len(preserved),"explicit_price_overrides":len(overridden),"override_rows":overridden,"unknown_overrides":unknown}
