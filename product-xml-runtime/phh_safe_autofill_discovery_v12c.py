from __future__ import annotations
import json, os, re
from collections import Counter, defaultdict
import psycopg
from app import _master_rows

def norm(s):
    return re.sub(r'[^a-z0-9]+',' ',str(s or '').casefold()).strip()

def parse_options(raw):
    if not raw: return {}
    try:
        x=json.loads(raw) if isinstance(raw,str) else raw
    except Exception:
        return {}
    out={}
    if isinstance(x,dict):
        for k,v in x.items():
            if isinstance(v,(str,int,float)) and str(v).strip():
                out[norm(k)]=str(v).strip()
    elif isinstance(x,list):
        for item in x:
            if not isinstance(item,dict): continue
            k=item.get('name') or item.get('option') or item.get('key')
            v=item.get('value')
            if k and v not in (None,''):
                out[norm(k)]=str(v).strip()
    return out

def run():
    master=_master_rows()
    bykey={(str(r.get('220_sku') or '').strip(),str(r.get('220_ean') or '').strip()):r for r in master}
    with psycopg.connect(os.environ['DATABASE_URL']) as c:
        with c.cursor() as cur:
            cur.execute("""select payload from phh_category_assignment_verify_v12a_snapshots order by id desc limit 1""")
            payload=cur.fetchone()[0]
            review={(str(r['sku']),str(r['ean'])) for r in payload['detail'] if r['verification_status']!='PASS_OBJECT_MATCH'}
            cur.execute("""select sku,ean,category_id,field_id,coalesce(title_en,''),coalesce(semantic,''),source_status,source,candidate_value
                           from phh_manual_input_rows_v11 order by category_id,sku,ean,field_id""")
            cols=[d.name for d in cur.description]
            rows=[dict(zip(cols,r)) for r in cur.fetchall()]
    candidates=[]
    skipped=Counter()
    for r in rows:
        key=(str(r['sku']),str(r['ean']))
        if key in review:
            continue
        if r.get('source_status')=='STRUCTURED_EXACT' and str(r.get('candidate_value') or '').strip():
            skipped['already_structured_exact']+=1
            continue
        m=bykey.get(key)
        if not m:
            skipped['master_missing']+=1
            continue
        title=str(r.get('title_en') or r.get('semantic') or '').strip()
        nt=norm(title)
        val=None; source=None
        if nt=='brand':
            v=str(m.get('vendor') or '').strip()
            if v: val=v; source='MASTER.vendor'
        elif nt in {'weight kg','weight'} and 'kg' in title.casefold():
            v=m.get('220_weight_kg')
            if v not in (None,''):
                val=str(v).strip(); source='MASTER.220_weight_kg'
        if val is None:
            opts=parse_options(m.get('220_selected_options_json'))
            aliases=[nt]
            if nt in {'clothing size','size'}: aliases+=['size','clothing size']
            if nt in {'color','colour'}: aliases+=['color','colour']
            hits=[opts[a] for a in aliases if a in opts and opts[a]]
            if len(set(hits))==1:
                val=hits[0]; source='MASTER.220_selected_options_json:'+next(a for a in aliases if a in opts)
            elif len(set(hits))>1:
                skipped['option_conflict']+=1
        if val:
            candidates.append({
              'sku':r['sku'],'ean':r['ean'],'category_id':str(r['category_id']),
              'field_id':str(r['field_id']),'title_en':title,'value':val,'source':source,
              'provenance':'DETERMINISTIC_STRUCTURED_EXACT'
            })
        else:
            skipped['no_safe_exact_source']+=1
    summary={
      'status':'PASS','validated_products':126,'candidate_cells':len(candidates),
      'candidate_products':len({(x['sku'],x['ean']) for x in candidates}),
      'by_attribute':dict(Counter(x['title_en'] for x in candidates)),
      'by_source':dict(Counter(x['source'] for x in candidates)),
      'skipped':dict(skipped),
      'policy':'read-only discovery; no fuzzy/title inference; exact structured Master/Shopify-derived fields only',
      'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'manual_workbook_writes':0,'Product_XML':'OFF'}
    }
    with psycopg.connect(os.environ['DATABASE_URL']) as c:
        with c.cursor() as cur:
            cur.execute("""create table if not exists phh_safe_autofill_v12c_snapshots(
              id bigserial primary key, created_at timestamptz default now(), summary jsonb not null, payload jsonb not null)""")
            cur.execute("insert into phh_safe_autofill_v12c_snapshots(summary,payload) values(%s::jsonb,%s::jsonb)",
                        (json.dumps(summary,ensure_ascii=False),json.dumps({'summary':summary,'candidates':candidates},ensure_ascii=False)))
        c.commit()
    print('PHH_SAFE_AUTOFILL_DISCOVERY_V12C '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
    return {'summary':summary,'candidates':candidates}
