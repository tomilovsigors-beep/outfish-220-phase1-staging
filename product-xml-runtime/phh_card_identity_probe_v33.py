from __future__ import annotations
import json
from phh_master_audit_v30 import lookup_ean
from pmp_api_probe import _api_login

EANS=[
"4751043574714","4751043571034","4751043574748","4056264353693","4052471169300","4056264724080","4063606359107","4771999386913","4771999387125",
"021563111460","3123840015779","5902194521840","4053838323892","806074351195","806161702309","806311761312","4771999255738","4771999255745",
"4771999255752","4771999255776","4751043570284","4751043570291","4751043570307","4063606358810","5907670793827","4751043572980","4065812535597",
"4065812535603","4065812535610","4048159716249","6927595784594","4751043571013","4751043571010"
]

def slim_item(x):
    if not isinstance(x,dict): return {"raw_type":type(x).__name__}
    m=x.get("modification") or {}
    p=x.get("product") or {}
    return {
      "top_id":x.get("id"),
      "top_name":x.get("name") or x.get("title"),
      "top_sku":x.get("sku"),
      "top_ean":x.get("ean"),
      "modification":{
        "id":m.get("id"),"app_name":m.get("app_name"),"sku":m.get("sku"),"ean":m.get("ean"),
        "ean_codes":m.get("ean_codes"),"name":m.get("name") or m.get("title"),
        "manufacturer_code":m.get("manufacturer_code"),"pigu_external_id":m.get("pigu_external_id"),
        "product_id":m.get("product_id")
      },
      "product":{
        "id":p.get("id"),"name":p.get("name") or p.get("title"),"brand":p.get("brand"),
        "manufacturer":p.get("manufacturer"),"category":p.get("category")
      },
      "keys":sorted(list(x.keys()))
    }

def run():
    lr=_api_login("v3"); lr.raise_for_status(); token=lr.json()["token"]
    rows=[]
    for ean in EANS:
        r=lookup_ean(token,ean)
        items=[slim_item(x) for x in (r.get("items") or []) if isinstance(x,dict) and ((x.get("modification") or {}).get("app_name")=="220.lv")]
        rows.append({"ean":ean,"exists_220":r.get("exists_220"),"http":r.get("http"),"items_220":items})
    print("PHH_CARD_IDENTITY_PROBE_V33 "+json.dumps(rows,ensure_ascii=False,separators=(",",":")),flush=True)
    print("PHH_CARD_IDENTITY_PROBE_V33_SUMMARY "+json.dumps({"status":"PASS","eans":len(EANS),"exists":sum(1 for x in rows if x["exists_220"]),"errors":sum(1 for x in rows if x["http"] not in (200,404)),"writes":{"phh":0,"master":0,"shopify":0}},sort_keys=True),flush=True)
    return rows
