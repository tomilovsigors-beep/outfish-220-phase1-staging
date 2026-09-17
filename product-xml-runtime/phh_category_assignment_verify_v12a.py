from __future__ import annotations
import csv, io, json, os, re, hashlib
from collections import Counter, defaultdict
import psycopg
from app import _master_rows

EXPECTED={
 '11810':('hammock', ['hammock']),
 '17977':('trousers', ['trouser','pants']),
 '19576':('shorts', ['shorts']),
 '20523':('tights', ['tights','legging']),
 '433':('tent', ['tent']),
 '434':('sleeping bag', ['sleeping bag','sleeping-bag']),
 '4391':('glue', ['glue','adhesive']),
 '5669':('rubber boots', ['rubber boot','wellington','rain boot']),
 '9050':('men outerwear jacket', ['jacket','parka','coat','anorak','softshell']),
}

def _norm(x): return re.sub(r'\s+',' ',str(x or '').strip())
def _family(sku):
    s=_norm(sku)
    # remove common terminal apparel size tokens only; preserve product/color code
    return re.sub(r'-(?:XS|S|M|L|XL|2XL|3XL|4XL|5XL|6XL|XXL|XXXL|XXXXL)$','',s,flags=re.I)

def run():
    master=_master_rows(); bykey={(r.get('220_sku',''),r.get('220_ean','')):r for r in master}
    with psycopg.connect(os.environ['DATABASE_URL']) as c:
        with c.cursor() as cur:
            cur.execute('''select distinct sku,ean,category_id,category_name from phh_manual_input_rows_v11 order by category_id,sku,ean''')
            assigned=[{'sku':r[0],'ean':r[1],'category_id':str(r[2]),'category_name':r[3]} for r in cur.fetchall()]
    if len(assigned)!=143: raise RuntimeError(f'expected 143 assignments, got {len(assigned)}')
    detail=[]; fam=defaultdict(list)
    for a in assigned:
        m=bykey.get((a['sku'],a['ean'])) or {}
        title=_norm(m.get('220_title')) or _norm(m.get('shopify_title')) or _norm(m.get('220_title_candidate'))
        product_type=_norm(m.get('product_type'))
        hay=(title+' '+product_type).casefold()
        cid=a['category_id']; expected_label,words=EXPECTED.get(cid,('unknown',[]))
        hits=[w for w in words if w in hay]
        status='PASS_OBJECT_MATCH' if hits else 'REVIEW_NO_OBJECT_KEYWORD'
        # exact contradiction markers only; do not use fuzzy reassignment
        contradictions=[]
        object_markers={'jacket':'9050','shorts':'19576','trouser':'17977','pants':'17977','tent':'433','sleeping bag':'434','hammock':'11810','glue':'4391','tights':'20523','legging':'20523','rubber boot':'5669','rain boot':'5669'}
        for marker,mcid in object_markers.items():
            if marker in hay and mcid!=cid: contradictions.append(f'{marker}->{mcid}')
        if contradictions: status='REVIEW_CONTRADICTION'
        row={**a,'family':_family(a['sku']),'master_title':title,'product_type':product_type,'expected_object':expected_label,'matched_keywords':'|'.join(hits),'contradictions':'|'.join(contradictions),'verification_status':status}
        detail.append(row); fam[(cid,row['family'])].append(row)
    family_rows=[]
    for (cid,f),rows in sorted(fam.items()):
        sts=Counter(r['verification_status'] for r in rows)
        family_rows.append({'category_id':cid,'family':f,'products':len(rows),'sample_title':rows[0]['master_title'],'pass':sts['PASS_OBJECT_MATCH'],'review_no_keyword':sts['REVIEW_NO_OBJECT_KEYWORD'],'review_contradiction':sts['REVIEW_CONTRADICTION'],'family_status':'PASS' if sts['REVIEW_NO_OBJECT_KEYWORD']==0 and sts['REVIEW_CONTRADICTION']==0 else 'REVIEW'})
    sc=Counter(r['verification_status'] for r in detail)
    summary={'status':'PASS','products':len(detail),'families':len(family_rows),'pass_object_match':sc['PASS_OBJECT_MATCH'],'review_no_object_keyword':sc['REVIEW_NO_OBJECT_KEYWORD'],'review_contradiction':sc['REVIEW_CONTRADICTION'],'review_families':sum(r['family_status']=='REVIEW' for r in family_rows),'category_counts':dict(Counter(r['category_id'] for r in detail)),'policy':'verification only; no category changes; exact object keywords and exact contradiction markers only; review is not reassignment','safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF'}}
    payload={'summary':summary,'families':family_rows,'detail':detail}
    summary['dataset_hash']=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with psycopg.connect(os.environ['DATABASE_URL']) as c:
        with c.cursor() as cur:
            cur.execute('''create table if not exists phh_category_assignment_verify_v12a_snapshots(id bigserial primary key,created_at timestamptz default now(),summary jsonb not null,payload jsonb not null)''')
            cur.execute('insert into phh_category_assignment_verify_v12a_snapshots(summary,payload) values(%s::jsonb,%s::jsonb)',(json.dumps(summary,ensure_ascii=False),json.dumps(payload,ensure_ascii=False)))
        c.commit()
    print('PHH_CATEGORY_ASSIGNMENT_VERIFY_V12A '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
    for r in family_rows:
        if r['family_status']=='REVIEW': print('PHH_CATEGORY_REVIEW_FAMILY_V12A '+json.dumps(r,ensure_ascii=False,sort_keys=True),flush=True)
    return payload
