"""Catalog-wide Shopify category -> PHH leaf rules.

Fail-closed: only a unique exact English leaf-title match to an addable PHH
category is AUTO. Everything else is REVIEW/NO_MATCH. No marketplace writes.
"""
from collections import Counter, defaultdict
import re, unicodedata


def norm(value):
    s=str(value or "").strip()
    s=unicodedata.normalize("NFKD",s.casefold())
    s="".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+"," ",s).strip()


def terminal(full_name):
    parts=[x.strip() for x in re.split(r"\s*(?:>|/|→|›)\s*",str(full_name or "")) if x.strip()]
    return parts[-1] if parts else str(full_name or "").strip()


def build_rules(variants, phh_categories):
    by_title=defaultdict(list)
    for cat in phh_categories:
        if str(cat.get("allow_add_products")).strip().casefold() not in {"true","1","yes"}:
            continue
        title=str(cat.get("title_en") or "").strip()
        key=norm(title)
        if key:
            by_title[key].append(cat)
    groups=defaultdict(lambda:{"variants":0,"vendors":Counter()})
    for v in variants:
        if str(v.get("vendor") or "").strip().casefold()=="fhm":
            continue
        cid=str(v.get("shopify_category_id") or "").strip()
        name=str(v.get("shopify_category_name") or "").strip()
        key=(cid,name)
        groups[key]["variants"]+=1
        groups[key]["vendors"][str(v.get("vendor") or "").strip()]+=1
    rules={}
    rows=[]
    for (shop_id,shop_name),meta in groups.items():
        leaf=terminal(shop_name)
        matches=by_title.get(norm(leaf),[]) if leaf else []
        if len(matches)==1:
            cat=matches[0]
            status="AUTO_EXACT_LEAF"
            phh_id=str(cat.get("category_id") or "").strip()
            phh_title=str(cat.get("title_en") or "").strip()
            confidence="1.000000"
            rules[(shop_id,shop_name)]={"status":status,"phh_category_id":phh_id,
                                       "phh_category_title":phh_title,"confidence":1.0}
        elif len(matches)>1:
            status="REVIEW_DUPLICATE_PHH_LEAF"
            phh_id=""; phh_title=""; confidence=""
        else:
            status="NO_EXACT_LEAF_MATCH"
            phh_id=""; phh_title=""; confidence=""
        rows.append({
            "shopify_category_id":shop_id,"shopify_category_name":shop_name,
            "shopify_terminal":leaf,"variant_count":meta["variants"],
            "vendor_count":len(meta["vendors"]),"phh_category_id":phh_id,
            "phh_category_title":phh_title,"status":status,"confidence":confidence,
            "basis":"unique exact normalized English terminal leaf title" if phh_id else "",
            "phh_write":"NO"
        })
    rows.sort(key=lambda x:(0 if x["status"]=="AUTO_EXACT_LEAF" else 1,-x["variant_count"],x["shopify_category_name"]))
    return rules,rows
