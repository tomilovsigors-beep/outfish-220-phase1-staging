from __future__ import annotations

import json
from collections import defaultdict
import current_product_category_audit as v3

TARGET_IDS={'9050','9053','17962','8972','17977','8708','19576','20458'}


def run(db):
    tax_summary,cats,attrs=v3._latest_taxonomy(db)
    by={str(c.get('category_id')):c for c in cats}
    ab=defaultdict(list)
    for a in attrs:
        cid=str(a.get('category_id') or '')
        if cid in TARGET_IDS:
            ab[cid].append(a)
    out=[]
    for cid in sorted(TARGET_IDS,key=lambda x:int(x)):
        c=by.get(cid) or {}
        raw={k:v for k,v in c.items() if k not in {'attributes'} and v not in (None,'')}
        req=[]; optional=[]
        for a in ab.get(cid,[]):
            x={k:a.get(k) for k in ('field_id','required','title_en','title_lv','title_lt','title_ee','title_fi','title_ru') if a.get(k) not in (None,'')}
            if str(a.get('required')).casefold() in {'true','1','yes'}: req.append(x)
            else: optional.append(x)
        out.append({'category_id':cid,'category':raw,'required_attributes':req,'optional_attribute_count':len(optional),'attribute_count':len(ab.get(cid,[]))})
    print('V7_TAXONOMY_DUPLICATE_LEAF_DIFF '+json.dumps({'taxonomy_categories_fetched':tax_summary.get('categories_fetched'),'categories':out},ensure_ascii=False,sort_keys=True),flush=True)
    return out
