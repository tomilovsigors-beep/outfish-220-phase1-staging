"""Read-only reuse of existing content-staging image evidence.

Only exact SKU+GTIN records are joined. This module never claims neutral main
background unless the stored audit explicitly says PASS.
"""
from __future__ import annotations
import csv, io, json
from collections import defaultdict


def _s(v): return "" if v is None else str(v).strip()


def load_latest(db):
    import psycopg
    with psycopg.connect(db,connect_timeout=10) as con:
        with con.cursor() as cur:
            cur.execute("""select artifacts from product_xml_content_snapshots
                           order by created_at desc limit 1""")
            row=cur.fetchone()
    if not row: return {},{"status":"NO_SNAPSHOT"}
    payload=row[0]
    if not isinstance(payload,dict): payload=json.loads(payload)
    def data(name):
        v=payload.get(name)
        if isinstance(v,str): return v.encode("utf-8")
        return bytes(v or b"")
    dataset=list(csv.DictReader(io.StringIO(data("content-dataset.csv").decode("utf-8-sig"))))
    images=list(csv.DictReader(io.StringIO(data("image-audit.csv").decode("utf-8-sig"))))
    by_identity={}
    for row in dataset:
        key=(_s(row.get("220_sku")),_s(row.get("220_ean")))
        if not all(key): continue
        if key in by_identity:
            by_identity[key]=None
        else:
            by_identity[key]={"dataset":row,"images":[]}
    for im in images:
        key=(_s(im.get("220_sku")),_s(im.get("220_ean")))
        item=by_identity.get(key)
        if not item: continue
        item["images"].append(im)
    out={}
    counts=defaultdict(int)
    for key,item in by_identity.items():
        if not item: counts["AMBIGUOUS_IDENTITY"]+=1; continue
        usable=[]
        for im in item["images"]:
            try: width=int(float(im.get("width") or 0)); height=int(float(im.get("height") or 0))
            except Exception: width=height=0
            if (im.get("usable")=="YES" and im.get("https_pass")=="YES" and
                    im.get("direct_pass")=="YES" and width>=600 and height>=600):
                usable.append({"url":_s(im.get("url")),"width":width,"height":height,
                               "background_status":_s(im.get("background_status"))})
        backgrounds=[x["background_status"] for x in usable]
        main_neutral=bool(usable and backgrounds[0] in ("PASS","BACKGROUND_PASS"))
        out[key]={"usable_images":usable,"usable_count":len(usable),
                  "two_images_verified":len(usable)>=2,
                  "main_neutral_verified":main_neutral,
                  "variant_id":_s(item["dataset"].get("shopify_variant_id"))}
        counts["IDENTITIES"]+=1
        if len(usable)>=2: counts["TWO_IMAGES_600_DIRECT"]+=1
        if main_neutral: counts["MAIN_NEUTRAL_VERIFIED"]+=1
    return out,{"status":"PASS","counts":dict(counts),"writes":0}
