from __future__ import annotations
import csv, io, json, os
import psycopg

def run():
    db=os.getenv('DATABASE_URL')
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute("select artifacts->>'v11-required-field-source-detail.csv' from phh_attribute_source_v11_snapshots order by created_at desc limit 1")
            row=cur.fetchone()
            if not row or not row[0]: raise RuntimeError('v11 detail artifact unavailable')
            rows=list(csv.DictReader(io.StringIO(row[0])))
            cur.execute('''create table if not exists phh_manual_input_rows_v11(
              sku text not null, ean text not null, category_id text not null, category_name text,
              field_id text not null, semantic text, title_en text, title_lt text, title_lv text,
              title_ee text, title_fi text, title_ru text, source_status text, source text,
              candidate_value text, primary key(sku,ean,category_id,field_id))''')
            cur.execute('delete from phh_manual_input_rows_v11')
            vals=[]
            for r in rows:
                vals.append((r.get('220_sku') or '',r.get('220_ean') or '',r.get('category_id') or '',r.get('category_name') or '',r.get('field_id') or '',r.get('semantic') or '',r.get('title_en') or '',r.get('title_lt') or '',r.get('title_lv') or '',r.get('title_ee') or '',r.get('title_fi') or '',r.get('title_ru') or '',r.get('source_status') or '',r.get('source') or '',r.get('candidate_value') or ''))
            cur.executemany('''insert into phh_manual_input_rows_v11(sku,ean,category_id,category_name,field_id,semantic,title_en,title_lt,title_lv,title_ee,title_fi,title_ru,source_status,source,candidate_value) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',vals)
        c.commit()
    out={'status':'PASS','rows':len(rows),'safety':{'marketplace_mutations':0,'master_writes':0,'staging_db_rows':len(rows)}}
    print('PHH_MANUAL_INPUT_MATERIALIZE_V11R '+json.dumps(out,sort_keys=True),flush=True)
    return out
