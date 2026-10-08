from __future__ import annotations
import json, os, time
from urllib.parse import urljoin
import requests
from pmp_api_probe import BASE, _api_login
from phh_master_audit_v30 import scan_offers, _offer_skus, _s
from app import _shopify_token

TARGET_SKUS=["STAL/02/35-38","OUTF420","ss11474"]
EXCLUDED_SKUS={"80066","BU HEMONET blk","BU ZEHA ora","ss8371M","37W332B-4L","CNK2300SD014","NH19Y001-Z","000056-0023-XS","000121-0002-S","000121-0066-2XL","000121-0066-3XL","000121-0066-L","000121-0066-M","000121-0066-XL","000121-0222-2XL","000121-0223-3XL","1948","3574","RASUBE","TB626"}

def _phh_headers(token):
    return {"User-Agent":"outfish-owner-stock-batch3/1.0","Accept":"application/json","Content-Type":"application/json","Authorization":"Pigu-mp "+token}

def _shopify_live():
    token=_shopify_token()
    shop=os.getenv("SHOPIFY_SHOP_DOMAIN","153ac6-2.myshopify.com").strip()
    q='''query StockOwnerBatch($query:String!){productVariants(first:10,query:$query){edges{node{id sku price inventoryQuantity product{title status}}}}}'''
    out={}
    for sku in TARGET_SKUS:
        r=requests.post(f"https://{shop}/admin/api/2026-07/graphql.json",
            headers={"X-Shopify-Access-Token":token,"Content-Type":"application/json"},
            json={"query":q,"variables":{"query":f"sku:{sku}"}},timeout=60)
        r.raise_for_status(); p=r.json()
        if p.get("errors"): raise RuntimeError(json.dumps(p["errors"])[:1000])
        matches=[]
        for e in (((p.get("data") or {}).get("productVariants") or {}).get("edges") or []):
            n=(e or {}).get("node") or {}
            if str(n.get("sku") or "").strip()==sku: matches.append(n)
        if len(matches)!=1: raise RuntimeError(f"{sku}: expected 1 Shopify variant, got {len(matches)}")
        n=matches[0]; qty=int(n.get("inventoryQuantity")); p=n.get("product") or {}
        if sku in EXCLUDED_SKUS: raise RuntimeError(f"{sku}: blocked by owner exclusion list")
        if qty<0: raise RuntimeError(f"{sku}: negative Shopify stock {qty}")
        if str(p.get("status") or "").upper()!="ACTIVE": raise RuntimeError(f"{sku}: Shopify status not ACTIVE")
        price=float(n.get("price") or 0)
        if price<10: raise RuntimeError(f"{sku}: price below 10 EUR")
        out[sku]={"qty":qty,"title":p.get("title"),"status":p.get("status"),"price":price}
    return out

def run():
    shop=_shopify_live()
    lr=_api_login("v3",timeout=30); lr.raise_for_status(); token=lr.json()["token"]
    before_offers=scan_offers(token)
    rows=[]
    for sku in TARGET_SKUS:
        matches=[o for o in before_offers if sku in _offer_skus(o)]
        if len(matches)!=1: raise RuntimeError(f"{sku}: expected 1 PHH offer, got {len(matches)}")
        o=matches[0]; oid=int(o["id"]); before=int(o.get("amount") or 0)
        rows.append({"sku":sku,"offer_id":oid,"phh_status":_s(o.get("status")),"before":before,"target":shop[sku]["qty"],"title":shop[sku]["title"],"shopify_status":shop[sku]["status"]})
    print("OWNER_STOCK_BATCH3_PREFLIGHT "+json.dumps(rows,ensure_ascii=False,separators=(",",":")),flush=True)

    write_enabled=os.getenv("OWNER_STOCK_BATCH3_WRITE","0").strip()=="1"
    to_write=[x for x in rows if x["before"]!=x["target"]]
    if not write_enabled:
        print("OWNER_STOCK_BATCH3_DRY_RUN "+json.dumps({"write_enabled":False,"would_write":[{"id":x["offer_id"],"amount":x["target"]} for x in to_write]},separators=(",",":")),flush=True)
        return {"status":"DRY_RUN","rows":rows,"phh_writes":0,"shopify_writes":0}
    if to_write:
        payload=[{"id":x["offer_id"],"amount":x["target"]} for x in to_write]
        r=requests.patch(urljoin(BASE,"/v3/offers"),headers=_phh_headers(token),json=payload,timeout=60)
        if r.status_code!=200: raise RuntimeError(f"PATCH HTTP {r.status_code}: {r.text[:1000]}")
        print("OWNER_STOCK_BATCH3_PATCH "+json.dumps({"payload":payload,"http":r.status_code},separators=(",",":")),flush=True)
    else:
        print("OWNER_STOCK_BATCH3_PATCH "+json.dumps({"payload":[],"http":"NOOP"},separators=(",",":")),flush=True)

    time.sleep(1)
    after_offers=scan_offers(token)
    byid={int(o["id"]):o for o in after_offers if o.get("id") is not None}
    report=[]
    for x in rows:
        o=byid.get(x["offer_id"])
        after=None if o is None else int(o.get("amount") or 0)
        report.append({**x,"after":after,"verified":after==x["target"],"status_after":None if o is None else _s(o.get("status"))})
    print("OWNER_STOCK_BATCH3_RESULT "+json.dumps(report,ensure_ascii=False,separators=(",",":")),flush=True)
    if not all(x["verified"] for x in report): raise RuntimeError("post-write verification failed")
    return {"status":"PASS","rows":report,"phh_writes":len(to_write),"shopify_writes":0}

if __name__=="__main__": run()
