from __future__ import annotations
import json, os
from collections import defaultdict
import current_product_category_audit as v3

CATEGORIES={'9050','19576','17977','434','20523','433','5669','11810','4391'}

def run():
    _,_,attrs=v3._latest_taxonomy(os.getenv('DATABASE_URL'))
    out=defaultdict(list)
    for a in attrs:
        cid=str(a.get('category_id'))
        if cid in CATEGORIES and str(a.get('required')).casefold() in {'true','1','yes'}:
            out[cid].append({k:a.get(k) for k in ('field_id','title_en','title_lt','title_lv','title_ee','title_fi','title_ru')})
    payload={cid:sorted(rows,key=lambda x:int(x['field_id'])) for cid,rows in sorted(out.items())}
    print('PMP_REQUIRED_FIELDS_V11N '+json.dumps(payload,ensure_ascii=False,separators=(',',':')),flush=True)
    return payload
