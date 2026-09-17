from __future__ import annotations
import csv, io, json, os
import current_product_category_audit as v3
from current_product_category_audit_v10 import load_latest_artifact as load_v10

def _load(b):
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))

def run():
    db=os.getenv('DATABASE_URL')
    rows=_load(load_v10(db,'v10-product-category-mapping.csv'))
    blocked=[r for r in rows if r.get('status')=='BLOCKED_ATTRIBUTES']
    _,_,attrs=v3._latest_taxonomy(db)
    req={}
    for a in attrs:
        if str(a.get('required')).casefold() in {'true','1','yes'}:
            req.setdefault(str(a.get('category_id')),[]).append({k:(a.get(k) or '') for k in ('field_id','title_en','title_lt','title_lv','title_ee','title_fi','title_ru')})
    out=[]
    for r in blocked:
        cid=str(r.get('selected_category_id') or '')
        out.append({'sku':r.get('220_sku'),'ean':r.get('220_ean'),'category_id':cid,'category_name':r.get('selected_category_name') or '','fields':req.get(cid,[])})
    payload={'status':'PASS','count':len(out),'products':out,'safety':{'writes':0}}
    print('PHH_MANUAL_INPUT_MAP_V11P '+json.dumps(payload,ensure_ascii=False,separators=(',',':')),flush=True)
    return payload
