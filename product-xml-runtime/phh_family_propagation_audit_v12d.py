from __future__ import annotations
import json, os, re, hashlib
from collections import Counter, defaultdict
import psycopg

VARIANT_SENSITIVE={'size','clothing size','color','colour','weight','weight kg','height','length','width'}
SAFE_FAMILY_SEMANTICS={'season','material','audience','model','hood','jacket_length','pattern','closure','brand'}

def norm(s):
    return re.sub(r'\s+',' ',str(s or '').strip())

def family(sku):
    s=norm(sku)
    return re.sub(r'-(?:XS|S|M|L|XL|2XL|3XL|4XL|5XL|6XL|XXL|XXXL|XXXXL)$','',s,flags=re.I)

def run():
    with psycopg.connect(os.environ['DATABASE_URL']) as c:
        with c.cursor() as cur:
            cur.execute("select payload from phh_category_assignment_verify_v12a_snapshots order by id desc limit 1")
            payload=cur.fetchone()[0]
            approved={(str(r['sku']),str(r['ean'])) for r in payload['detail'] if r['verification_status']=='PASS_OBJECT_MATCH'}
            cur.execute("""select sku,ean,category_id,field_id,coalesce(title_en,''),coalesce(semantic,''),source_status,source,candidate_value
                           from phh_manual_input_rows_v11 order by category_id,sku,ean,field_id""")
            cols=[d.name for d in cur.description]
            rows=[dict(zip(cols,r)) for r in cur.fetchall()]
    rows=[r for r in rows if (str(r['sku']),str(r['ean'])) in approved]
    by=defaultdict(list)
    for r in rows:
        by[(str(r['category_id']),family(r['sku']),str(r['field_id']))].append(r)

    candidates=[]; rejected=[]; stats=Counter()
    for (cid,fam,fid),rs in sorted(by.items()):
        title=norm(rs[0].get('title_en'))
        sem=norm(rs[0].get('semantic')).casefold()
        key=(sem or title.casefold())
        exact=[r for r in rs if r.get('source_status')=='STRUCTURED_EXACT' and norm(r.get('candidate_value'))]
        missing=[r for r in rs if r.get('source_status')!='STRUCTURED_EXACT' or not norm(r.get('candidate_value'))]
        vals=sorted({norm(r.get('candidate_value')) for r in exact if norm(r.get('candidate_value'))})
        if not missing:
            stats['already_complete_group']+=1; continue
        if key in VARIANT_SENSITIVE or title.casefold() in VARIANT_SENSITIVE:
            stats['variant_sensitive_rejected']+=len(missing); continue
        if sem not in SAFE_FAMILY_SEMANTICS:
            stats['semantic_not_allowlisted']+=len(missing); continue
        if len(exact)<2:
            stats['insufficient_exact_siblings']+=len(missing); continue
        if len(vals)!=1:
            stats['conflicting_exact_siblings']+=len(missing)
            rejected.append({'category_id':cid,'family':fam,'field_id':fid,'semantic':sem,'title_en':title,'reason':'CONFLICTING_EXACT_SIBLINGS','values':vals,'exact_siblings':len(exact),'missing_siblings':len(missing)})
            continue
        value=vals[0]
        for r in missing:
            candidates.append({'sku':str(r['sku']),'ean':str(r['ean']),'category_id':cid,'family':fam,'field_id':fid,'semantic':sem,'title_en':title,'value':value,'source':'UNANIMOUS_STRUCTURED_EXACT_SIBLINGS','evidence_count':len(exact),'provenance':'DETERMINISTIC_FAMILY_PROPAGATION_CANDIDATE'})
        stats['candidate_cells']+=len(missing)
        stats['candidate_groups']+=1

    summary={
      'status':'PASS','validated_products':len(approved),'candidate_cells':len(candidates),
      'candidate_products':len({(x['sku'],x['ean']) for x in candidates}),
      'candidate_groups':stats['candidate_groups'],
      'by_attribute':dict(Counter(x['title_en'] or x['semantic'] for x in candidates)),
      'stats':dict(stats),
      'policy':'candidate only; same category + same normalized SKU family + >=2 unanimous STRUCTURED_EXACT siblings + semantic allowlist; variant-sensitive fields excluded',
      'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'manual_workbook_writes':0,'Product_XML':'OFF'}
    }
    payload_out={'summary':summary,'candidates':candidates,'rejected':rejected}
    summary['dataset_hash']=hashlib.sha256(json.dumps(payload_out,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with psycopg.connect(os.environ['DATABASE_URL']) as c:
        with c.cursor() as cur:
            cur.execute("""create table if not exists phh_family_propagation_v12d_snapshots(
              id bigserial primary key, created_at timestamptz default now(), summary jsonb not null, payload jsonb not null)""")
            cur.execute("insert into phh_family_propagation_v12d_snapshots(summary,payload) values(%s::jsonb,%s::jsonb)",
                        (json.dumps(summary,ensure_ascii=False),json.dumps(payload_out,ensure_ascii=False)))
        c.commit()
    print('PHH_FAMILY_PROPAGATION_AUDIT_V12D '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
    if candidates:
        print('PHH_FAMILY_PROPAGATION_SAMPLE_V12D '+json.dumps(candidates[:40],ensure_ascii=False,sort_keys=True),flush=True)
    return payload_out
