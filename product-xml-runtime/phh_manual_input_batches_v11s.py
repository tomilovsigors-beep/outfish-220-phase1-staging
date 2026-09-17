from __future__ import annotations
import json, os
import psycopg

def run():
    db=os.getenv('DATABASE_URL')
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''select sku,ean,category_id,category_name,field_id,semantic,title_en,title_lv,title_lt,title_ee,title_fi,title_ru,source_status,source,candidate_value
                           from phh_manual_input_rows_v11 order by sku,ean,field_id''')
            cols=[d.name for d in cur.description]
            rows=[dict(zip(cols,r)) for r in cur.fetchall()]
    size=100
    for i in range(0,len(rows),size):
        batch={'offset':i,'rows':rows[i:i+size]}
        print('PHH_MANUAL_BATCH_V11S '+json.dumps(batch,ensure_ascii=False,separators=(',',':')),flush=True)
    out={'status':'PASS','rows':len(rows),'batches':(len(rows)+size-1)//size,'safety':{'writes':0,'marketplace_mutations':0}}
    print('PHH_MANUAL_BATCHES_V11S_COMPLETE '+json.dumps(out,sort_keys=True),flush=True)
    return out
