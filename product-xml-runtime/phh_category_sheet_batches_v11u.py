from __future__ import annotations
import json, os
from collections import defaultdict
import psycopg

SEM_LABELS={'brand':'Brand','color':'Color','size':'Clothing Size','season':'Season','material':'Material','audience':'Intended For','model':'Clothing Model','hood':'Hood','jacket_length':'Jacket Length','weight':'Weight','pattern':'Pattern','closure':'Closure'}
SPECIAL={
('434','2211'):'Comfort Temperature',('434','2212'):'Extreme Temperature',('434','8888'):'Length (Unfolded)',('434','8891'):'Width (Unfolded)',('434','8897'):'Length (Packed)',('434','8900'):'Width (Packed)',('434','8912'):'Filling',('434','13034'):'Shape',('434','36620'):'Height (Packed)',
('17977','105398'):'Type',('20523','106788'):'Type',('20523','106798'):'DEN',('20523','106818'):'Quantity in Package',
('433','2181'):'Number of Persons',('433','2184'):'Waterproofness',('433','2190'):'Number of Entrances',('433','2192'):'Snow Protection',('433','2193'):'Mosquito Net',('433','2194'):'Ventilation',('433','2195'):'Pockets',('433','5924'):'Length',('433','5927'):'Width',('433','35951'):'Number of Layers',('433','79440'):'Height',('433','111938'):'Type',
('5669','98226'):'Country of Origin',('5669','106208'):'Heel Height',('5669','106213'):'Sole Type',
('11810','49845'):'Country of Origin',('11810','49875'):'Type',('11810','49880'):'Length',('11810','51315'):'Style',('11810','72515'):'Width',('11810','79375'):'Hammock Type',
('4391','10982'):'Glue Type',('4391','102011'):'Volume',('4391','104146'):'Aerosol Volume'}

def friendly(cid,r):
    fid=str(r['field_id']); sem=r.get('semantic') or ''
    if (cid,fid) in SPECIAL: return SPECIAL[(cid,fid)]
    if sem in SEM_LABELS: return SEM_LABELS[sem]
    return (r.get('title_en') or r.get('title_lv') or r.get('title_lt') or r.get('title_ee') or r.get('title_fi') or r.get('title_ru') or ('Field '+fid)).strip()

def run():
    db=os.getenv('DATABASE_URL')
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''select sku,ean,category_id,category_name,field_id,semantic,title_en,title_lv,title_lt,title_ee,title_fi,title_ru,source_status,source,candidate_value from phh_manual_input_rows_v11 order by category_id,sku,ean,field_id''')
            cols=[d.name for d in cur.description]
            rows=[dict(zip(cols,r)) for r in cur.fetchall()]
    bycat=defaultdict(list)
    for r in rows: bycat[str(r['category_id'])].append(r)
    for cid,rs in sorted(bycat.items()):
        field_defs=[]; seen=set(); prods={}
        for r in rs:
            fid=str(r['field_id'])
            if fid not in seen:
                seen.add(fid); field_defs.append({'field_id':fid,'label':friendly(cid,r),'semantic':r.get('semantic') or '','title_lv':r.get('title_lv') or '','title_lt':r.get('title_lt') or '','title_ee':r.get('title_ee') or '','title_fi':r.get('title_fi') or '','title_ru':r.get('title_ru') or ''})
            k=(r['sku'],r['ean'])
            p=prods.setdefault(k,{'sku':r['sku'],'ean':r['ean'],'category_name':r['category_name'],'values':{}})
            p['values'][fid]={'status':r['source_status'],'source':r['source'],'candidate':r['candidate_value']}
        payload={'category_id':cid,'category_name':rs[0]['category_name'],'fields':field_defs,'products':list(prods.values())}
        print('PHH_CATEGORY_SHEET_V11U '+json.dumps(payload,ensure_ascii=False,separators=(',',':')),flush=True)
    out={'status':'PASS','categories':len(bycat),'products':len(set((r['sku'],r['ean']) for r in rows)),'rows':len(rows),'safety':{'writes':0,'marketplace_mutations':0}}
    print('PHH_CATEGORY_SHEETS_V11U_COMPLETE '+json.dumps(out,sort_keys=True),flush=True)
    return out
