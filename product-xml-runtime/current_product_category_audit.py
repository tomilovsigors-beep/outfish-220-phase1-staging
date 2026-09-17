from __future__ import annotations

import csv, io, json, os, re, hashlib
from collections import Counter, defaultdict


def _norm(v):
    return re.sub(r'\s+', ' ', str(v or '').strip())


def _key(v):
    return re.sub(r'[^a-z0-9]+', ' ', _norm(v).casefold()).strip()

STOP={'and','or','the','for','with','of','a','an','in','on','to','by','from','product','products','item','items','fhm'}
def _tokens(*vals):
    return {x for x in re.findall(r'[a-z0-9]+',' '.join(_norm(v).casefold() for v in vals)) if len(x)>1 and x not in STOP}


def _csv_bytes(rows, fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    return s.getvalue().encode('utf-8-sig')


def _latest_taxonomy(db):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select summary,artifacts from phh_category_export_snapshots order by created_at desc limit 1')
            row=cur.fetchone()
            if not row: raise RuntimeError('no PHH category export snapshot')
    summary, arts=row
    if not isinstance(summary,dict): summary=json.loads(summary)
    if not isinstance(arts,dict): arts=json.loads(arts)
    cats=list(csv.DictReader(io.StringIO(arts['pmp-categories.csv'])))
    attrs=list(csv.DictReader(io.StringIO(arts['pmp-category-attributes.csv'])))
    return summary,cats,attrs


def _canonical_identities(master_rows):
    by=defaultdict(list)
    for rn,r in enumerate(master_rows,start=2):
        sku=_norm(r.get('220_sku')); ean=_norm(r.get('220_ean'))
        if sku and ean: by[(sku,ean)].append((rn,r))
    out=[]
    for (sku,ean), rows in sorted(by.items()):
        merged={'220_sku':sku,'220_ean':ean,'master_rows':','.join(str(x[0]) for x in rows),'duplicate_row_count':len(rows)}
        fields=['shopify_product_id','shopify_variant_id','shopify_sku','shopify_barcode','shopify_title','220_title','variant_title','vendor','product_type','shopify_status','220_status','match_status','validation_status','notes','220_selected_options_json','220_title_candidate','220_title_candidate_status','220_weight_kg']
        conflicts=[]
        for f in fields:
            vals=[]
            for _,r in rows:
                v=_norm(r.get(f))
                if v and v not in vals: vals.append(v)
            merged[f]=vals[0] if vals else ''
            if len(vals)>1: conflicts.append(f)
        merged['duplicate_conflict_fields']='|'.join(conflicts)
        out.append(merged)
    return out


def _shopify_enrich(identity, shopify):
    pid=_norm(identity.get('shopify_product_id')); vid=_norm(identity.get('shopify_variant_id'))
    p=shopify.get(pid) if pid else None
    v=(p.get('variants_by_id') or {}).get(vid) if p and vid else None
    return p or {}, v or {}


def _selected_options(identity, variant):
    out={}
    raw=_norm(identity.get('220_selected_options_json'))
    if raw:
        try:
            j=json.loads(raw)
            if isinstance(j,dict):
                for k,v in j.items(): out[_key(k)]=_norm(v)
            elif isinstance(j,list):
                for x in j:
                    if isinstance(x,dict) and x.get('name'): out[_key(x.get('name'))]=_norm(x.get('value'))
        except Exception: pass
    for x in variant.get('selected_options') or []:
        if isinstance(x,dict) and x.get('name'): out.setdefault(_key(x.get('name')),_norm(x.get('value')))
    return out


def _group_key(i):
    pid=_norm(i.get('shopify_product_id'))
    if pid: return 'SHOPIFY_PRODUCT:'+pid
    title=_norm(i.get('220_title')) or _norm(i.get('220_title_candidate')) or _norm(i.get('shopify_title'))
    if title: return 'TITLE:'+_key(title)
    sku=_norm(i.get('220_sku'))
    family=re.sub(r'[-_](?:xs|s|m|l|xl|xxl|2xl|3xl|4xl|5xl|6xl|\d{1,3})$','',sku,flags=re.I)
    return 'SKU_FAMILY:'+family


def _category_index(cats, attrs):
    by_id={str(c['category_id']):c for c in cats}
    required=defaultdict(list)
    for a in attrs:
        if str(a.get('required')).casefold() in {'true','1','yes'}:
            required[str(a.get('category_id'))].append(a)
    addable=[]
    for c in cats:
        if str(c.get('allow_add_products')).casefold() not in {'true','1','yes'}: continue
        leaf=_norm(c.get('title_en')) or _norm(c.get('title_lv')) or _norm(c.get('title_lt'))
        path=_norm(c.get('category_path'))
        ct=_tokens(leaf,path)
        addable.append((c,ct,_tokens(leaf)))
    return by_id,required,addable


def _candidate_scores(i,p,group_members,addable):
    title_parts=[i.get('220_title'),i.get('220_title_candidate'),i.get('shopify_title'),p.get('title')]
    product_type=_norm(p.get('product_type')) or _norm(i.get('product_type'))
    shopcat=_norm(p.get('category_name'))
    variant=_norm(i.get('variant_title'))
    base_tokens=_tokens(*title_parts,product_type,shopcat,variant,i.get('vendor'))
    title_tokens=_tokens(*title_parts)
    type_tokens=_tokens(product_type)
    shopcat_tokens=_tokens(shopcat)
    group_tokens=set()
    for g in group_members:
        group_tokens |= _tokens(g.get('220_title'),g.get('220_title_candidate'),g.get('shopify_title'),g.get('product_type'))
    scored=[]
    for c,ct,leaf_tokens in addable:
        if not ct: continue
        inter=len(base_tokens & ct)
        coverage=inter/max(1,len(base_tokens))
        leaf_cov=len(title_tokens & leaf_tokens)/max(1,len(leaf_tokens)) if leaf_tokens else 0
        type_cov=len(type_tokens & leaf_tokens)/max(1,len(leaf_tokens)) if type_tokens and leaf_tokens else 0
        shop_cov=len(shopcat_tokens & ct)/max(1,len(shopcat_tokens)) if shopcat_tokens else 0
        group_cov=len(group_tokens & leaf_tokens)/max(1,len(leaf_tokens)) if group_tokens and leaf_tokens else 0
        score=0.30*coverage+0.30*leaf_cov+0.16*type_cov+0.16*shop_cov+0.08*group_cov
        leaf=_norm(c.get('title_en'))
        if product_type and _key(product_type)==_key(leaf): score=max(score,0.93)
        if shopcat and (_key(shopcat)==_key(leaf) or _key(shopcat).endswith(_key(leaf))): score=max(score,0.90)
        tkey=_key(' '.join(_norm(x) for x in title_parts if _norm(x)))
        if leaf and _key(leaf) and _key(leaf) in tkey: score=max(score,0.84)
        if inter: scored.append((min(0.99,round(score,6)),c,{'token_overlap':inter,'leaf_coverage':round(leaf_cov,4),'product_type_coverage':round(type_cov,4),'shopify_category_coverage':round(shop_cov,4),'group_coverage':round(group_cov,4)}))
    scored.sort(key=lambda x:(-x[0],str(x[1].get('category_id'))))
    return scored[:5]

FIELD_SEMANTICS={
 'brand':['prekes zenklas','prekes zenklo','zimols','kaubamark','merkki','brend','brand'],
 'color':['spalva','krasa','varv','vari','cvet','color','colour'],
 'size':['dydis','izmers','suurus','koko','razmer','size'],
 'weight':['svoris','svars','kaal','paino','ves','weight'],
 'model':['modelis','model','mudel','malli','model'],
 'material':['medziaga','materials','materjal','materiaali','material'],
}

def _semantic_field(a):
    text=_key(' '.join(_norm(a.get(k)) for k in ('title_lt','title_lv','title_ee','title_fi','title_ru')))
    for sem,keys in FIELD_SEMANTICS.items():
        if any(k in text for k in keys): return sem
    return ''


def _attribute_source(a,i,p,v,opts):
    sem=_semantic_field(a)
    if sem=='brand':
        val=_norm(i.get('vendor')) or _norm(p.get('vendor'))
        return ('SOURCE_IDENTIFIED','master.vendor/shopify.vendor',val) if val else ('MISSING_SOURCE','','')
    if sem=='model':
        return ('MISSING_SOURCE','','')
    if sem=='weight':
        val=_norm(i.get('220_weight_kg'))
        if val: return ('SOURCE_IDENTIFIED_UNIT_UNPROVEN','master.220_weight_kg',val)
        w=v.get('weight') or {}; val=_norm(w.get('value'))
        return ('SOURCE_IDENTIFIED_UNIT_UNPROVEN','shopify.variant.weight',val) if val else ('MISSING_SOURCE','','')
    if sem in {'color','size'}:
        candidates={'color':['color','colour','spalva','krasa','varv','vari'],'size':['size','dydis','izmers','suurus','koko']}[sem]
        for k,val in opts.items():
            if any(x in k for x in candidates) and _norm(val):
                return ('SOURCE_IDENTIFIED_ALLOWED_VALUES_UNPROVEN','selected_options',_norm(val))
        return ('MISSING_SOURCE','','')
    if sem=='material':
        return ('POTENTIAL_UNSTRUCTURED_SOURCE','title/description','','')
    return ('MISSING_SOURCE','','')


def _persist(db,summary,artifacts):
    import psycopg
    payload={k:v.decode('utf-8-sig') for k,v in artifacts.items()}
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''create table if not exists phh_product_mapping_snapshots(
                id bigserial primary key, created_at timestamptz not null default now(),
                dataset_hash text not null, summary jsonb not null, artifacts jsonb not null)''')
            cur.execute('insert into phh_product_mapping_snapshots(dataset_hash,summary,artifacts) values(%s,%s::jsonb,%s::jsonb)',(summary['dataset_hash'],json.dumps(summary,ensure_ascii=False),json.dumps(payload,ensure_ascii=False)))
        c.commit()


def load_latest_artifact(db,name):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select artifacts->>%s from phh_product_mapping_snapshots order by created_at desc limit 1',(name,))
            row=cur.fetchone()
            if not row or row[0] is None: return None
            return row[0].encode('utf-8-sig')


def run_audit(master_rows,shopify,db):
    tax_summary,cats,attrs=_latest_taxonomy(db)
    by_cat,required,addable=_category_index(cats,attrs)
    ids=_canonical_identities(master_rows)
    if len(ids)!=1671: raise RuntimeError(f'canonical identity universe changed: {len(ids)} != 1671')
    groups=defaultdict(list)
    for i in ids: groups[_group_key(i)].append(i)
    legacy_missing={157,3551,8384,17867,3221,3203,21879}; legacy_nonaddable={20868}
    mapping=[]; gaps=[]; exceptions=[]
    status_counts=Counter(); group_counts=Counter()
    for i in ids:
        p,v=_shopify_enrich(i,shopify); opts=_selected_options(i,v); gkey=_group_key(i); members=groups[gkey]
        scored=_candidate_scores(i,p,members,addable)
        top=scored[0] if scored else None; second=scored[1] if len(scored)>1 else None
        confidence=top[0] if top else 0.0
        margin=(top[0]-second[0]) if top and second else confidence
        status='NO_MATCH'; reason='no safe live PHH category candidate'
        selected=None
        if top:
            selected=top[1]
            if confidence>=0.78 and margin>=0.12: status='AUTO'; reason='high lexical/category evidence with clear margin'
            elif confidence>=0.50 and margin>=0.08: status='REVIEW'; reason='plausible category but confidence below AUTO threshold'
            else: status='REVIEW'; reason='multiple or weak category candidates'
        req=required.get(str(selected.get('category_id'))) if selected else []
        req=req or []
        missing=[]; identified=[]
        for a in req:
            src_status,src,val=_attribute_source(a,i,p,v,opts)
            row={'220_sku':i['220_sku'],'220_ean':i['220_ean'],'group_key':gkey,'category_id':str(selected.get('category_id')) if selected else '',
                 'category_name':_norm(selected.get('title_en')) if selected else '','field_id':_norm(a.get('field_id')),'required':'TRUE',
                 'field_title_lt':_norm(a.get('title_lt')),'field_title_lv':_norm(a.get('title_lv')),'field_title_ee':_norm(a.get('title_ee')),'field_title_fi':_norm(a.get('title_fi')),'field_title_ru':_norm(a.get('title_ru')),
                 'semantic':_semantic_field(a),'source_status':src_status,'source':src,'source_value':val}
            gaps.append(row)
            if src_status.startswith('SOURCE_IDENTIFIED'): identified.append(str(a.get('field_id')))
            else: missing.append(str(a.get('field_id')))
        if status=='AUTO' and missing:
            status='BLOCKED_ATTRIBUTES'; reason='category is high-confidence but required PHH fields lack a safe source'
        if i.get('duplicate_conflict_fields'):
            status='REVIEW'; reason='Master duplicate identity rows contain conflicting classification evidence'
        candidates=[{'category_id':str(c.get('category_id')),'category_name':_norm(c.get('title_en')),'score':s,'evidence':ev} for s,c,ev in scored]
        row={'220_sku':i['220_sku'],'220_ean':i['220_ean'],'group_key':gkey,'group_size':len(members),
             'brand':_norm(i.get('vendor')) or _norm(p.get('vendor')),'product_type':_norm(p.get('product_type')) or _norm(i.get('product_type')),
             'master_220_title':_norm(i.get('220_title')),'master_title_candidate':_norm(i.get('220_title_candidate')),'shopify_title':_norm(i.get('shopify_title')) or _norm(p.get('title')),
             'variant_title':_norm(i.get('variant_title')) or _norm(v.get('title')),'shopify_category_hint':_norm(p.get('category_name')),
             'selected_category_id':str(selected.get('category_id')) if selected else '','selected_category_name':_norm(selected.get('title_en')) if selected else '',
             'confidence':f'{confidence:.6f}','margin_to_second':f'{margin:.6f}','status':status,'reason':reason,
             'required_field_ids':'|'.join(str(a.get('field_id')) for a in req),'source_identified_field_ids':'|'.join(identified),'missing_required_field_ids':'|'.join(missing),
             'candidate_json':json.dumps(candidates,ensure_ascii=False,sort_keys=True,separators=(',',':')),
             'master_rows':i.get('master_rows'),'duplicate_conflict_fields':i.get('duplicate_conflict_fields'),'phh_write':'NO','master_write':'NO','shopify_write':'NO'}
        mapping.append(row); status_counts[status]+=1; group_counts[gkey]+=1
        if status!='AUTO': exceptions.append(row)
    map_fields=['220_sku','220_ean','group_key','group_size','brand','product_type','master_220_title','master_title_candidate','shopify_title','variant_title','shopify_category_hint','selected_category_id','selected_category_name','confidence','margin_to_second','status','reason','required_field_ids','source_identified_field_ids','missing_required_field_ids','candidate_json','master_rows','duplicate_conflict_fields','phh_write','master_write','shopify_write']
    gap_fields=['220_sku','220_ean','group_key','category_id','category_name','field_id','required','field_title_lt','field_title_lv','field_title_ee','field_title_fi','field_title_ru','semantic','source_status','source','source_value']
    raw_hash=hashlib.sha256(_csv_bytes(mapping,map_fields)).hexdigest()
    summary={'status':'PASS','identity_count':len(ids),'group_count':len(groups),'addable_live_categories':len(addable),'taxonomy_categories_fetched':tax_summary.get('categories_fetched'),'mapping_status_counts':dict(status_counts),'exception_count':len(exceptions),'attribute_gap_rows':len(gaps),'dataset_hash':raw_hash,'legacy_missing_category_ids':sorted(legacy_missing),'legacy_nonaddable_category_ids':sorted(legacy_nonaddable),'phh_writes':0,'master_writes':0,'shopify_writes':0,'product_xml_publication':'OFF','classification_method':'deterministic lexical evidence over Master + exact Shopify links + live PHH taxonomy; no identity fuzzy matching'}
    artifacts={'current-product-category-mapping.csv':_csv_bytes(mapping,map_fields),'current-product-category-exceptions.csv':_csv_bytes(exceptions,map_fields),'current-product-attribute-gap.csv':_csv_bytes(gaps,gap_fields),'current-product-category-summary.json':json.dumps(summary,ensure_ascii=False,sort_keys=True,indent=2).encode()}
    _persist(db,summary,artifacts)
    return summary,artifacts
