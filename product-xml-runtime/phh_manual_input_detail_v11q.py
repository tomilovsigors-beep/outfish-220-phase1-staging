from __future__ import annotations
import csv, io, json, os
from collections import defaultdict
import psycopg

def run():
    db=os.getenv('DATABASE_URL')
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute("select artifacts->>'v11-required-field-source-detail.csv' from phh_attribute_source_v11_snapshots order by created_at desc limit 1")
            row=cur.fetchone()
    if not row or not row[0]: raise RuntimeError('v11 required field detail missing')
    rows=list(csv.DictReader(io.StringIO(row[0])))
    grouped=defaultdict(list)
    for r in rows:
        k=(r['220_sku'],r['220_ean'],r.get('category_id') or '',r.get('category_name') or '')
        grouped[k].append({
          'field_id':r.get('field_id') or '',
          'semantic':r.get('semantic') or '',
          'title_en':r.get('title_en') or '',
          'title_lt':r.get('title_lt') or '',
          'title_lv':r.get('title_lv') or '',
          'title_ee':r.get('title_ee') or '',
          'title_fi':r.get('title_fi') or '',
          'title_ru':r.get('title_ru') or '',
          'source_status':r.get('source_status') or '',
          'source':r.get('source') or '',
          'candidate_value':r.get('candidate_value') or ''
        })
    for (sku,ean,cid,cname),fs in sorted(grouped.items()):
        out={'sku':sku,'ean':ean,'category_id':cid,'category_name':cname,'fields':fs}
        print('PHH_MANUAL_DETAIL_ROW_V11Q '+json.dumps(out,ensure_ascii=False,separators=(',',':')),flush=True)
    summary={'status':'PASS','product_count':len(grouped),'detail_count':len(rows),'safety':{'writes':0,'marketplace_mutations':0}}
    print('PHH_MANUAL_DETAIL_V11Q '+json.dumps(summary,separators=(',',':')),flush=True)
    return summary
