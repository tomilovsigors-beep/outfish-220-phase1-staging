from __future__ import annotations
import csv, io, json
from collections import Counter
from current_product_attribute_source_audit_v11 import load_latest_artifact

def run(db):
    b=load_latest_artifact(db,'v11-required-field-source-detail.csv')
    if not b: raise RuntimeError('v11 detail unavailable')
    rows=list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))
    c=Counter(); samples={}
    for r in rows:
        if r.get('semantic')!='unknown': continue
        k=(r.get('category_id',''),r.get('field_id',''),r.get('title_en',''),r.get('title_lt',''),r.get('title_lv',''),r.get('title_ee',''),r.get('title_fi',''),r.get('title_ru',''))
        c[k]+=1; samples.setdefault(k,r)
    top=[]
    for k,n in c.most_common(80):
        top.append({'category_id':k[0],'field_id':k[1],'occurrences':n,'title_en':k[2],'title_lt':k[3],'title_lv':k[4],'title_ee':k[5],'title_fi':k[6],'title_ru':k[7]})
    out={'status':'PASS','unknown_occurrences':sum(c.values()),'unique_unknown_category_fields':len(c),'top_unknown_fields':top,'safety':{'writes':0,'marketplace_mutations':0}}
    print('ATTRIBUTE_UNKNOWN_PROBE_V11B '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
