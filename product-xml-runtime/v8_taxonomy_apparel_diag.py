from __future__ import annotations
import json, re
import current_product_category_audit as v3

TERMS=('hoodie','hooded','sweatshirt','sweater','jumper','pullover','fleece','trouser','pants','jogger')

def run(db):
    summary,cats,attrs=v3._latest_taxonomy(db)
    rows=[]
    for c in cats:
        if str(c.get('allow_add_products')).casefold() not in {'true','1','yes'}: continue
        titles={k:v3._norm(c.get(k)) for k in ('title_en','title_lv','title_lt','title_ee','title_fi','title_ru')}
        text=' '.join(titles.values()).casefold()
        matched=[t for t in TERMS if t in text]
        if not matched: continue
        rows.append({'category_id':str(c.get('category_id')),'parent_id':str(c.get('parent_id') or ''),'matched':matched,**titles})
    rows.sort(key=lambda x:(x['title_en'],x['category_id']))
    obj={'status':'PASS','taxonomy_categories':summary.get('categories_fetched'),'rows':rows,'count':len(rows)}
    print('V8_APPAREL_TAXONOMY_DIAG '+json.dumps(obj,ensure_ascii=False,sort_keys=True),flush=True)
    return obj
