from __future__ import annotations

import csv, io, json, hashlib, re, unicodedata
from collections import Counter, defaultdict

import current_product_category_audit as v3
from current_product_category_audit_v10 import load_latest_artifact as load_v10


def _norm(v):
    return re.sub(r'\s+',' ',str(v or '').strip())

def _fold(v):
    s=unicodedata.normalize('NFKD',_norm(v).casefold())
    return ''.join(ch for ch in s if not unicodedata.combining(ch))

def _csv(rows,fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows); return s.getvalue().encode('utf-8-sig')

def _load(b):
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))

def _persist(db,summary,artifacts):
    import psycopg
    payload={k:v.decode('utf-8-sig') for k,v in artifacts.items()}
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''create table if not exists phh_attribute_source_v11_snapshots(
              id bigserial primary key, created_at timestamptz not null default now(),
              dataset_hash text not null, summary jsonb not null, artifacts jsonb not null)''')
            cur.execute('insert into phh_attribute_source_v11_snapshots(dataset_hash,summary,artifacts) values(%s,%s::jsonb,%s::jsonb)',
                        (summary['dataset_hash'],json.dumps(summary,ensure_ascii=False),json.dumps(payload,ensure_ascii=False)))
        c.commit()

def load_latest_artifact(db,name):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select artifacts->>%s from phh_attribute_source_v11_snapshots order by created_at desc limit 1',(name,)); row=cur.fetchone()
            return None if not row or row[0] is None else row[0].encode('utf-8-sig')

SEMANTIC_PATTERNS={
 'brand':['brand','prekes zenkl','zimol','kaubamark','merkki','brend'],
 'color':['color','colour','spalv','kras','varv','vari','cvet'],
 'size':['clothing size','size','dyd','izmer','suurus','koko','razmer'],
 'season':['season','sezon','sezona','seson','hooaj','kausi'],
 'material':['material','medziag','materjal','materiaali'],
 'audience':['intended','gender','audience','kam skirta','skirta','paredz','sugu','sukupuol','pol'],
 'model':['clothing model','modelis','model','mudel','malli'],
 'hood':['hood','kapuc','kapuuts','huppu'],
 'jacket_length':['jacket length','striukes ilg','jakas gar','jope pikk','takin pitu','dlina kurt'],
 'weight':['weight','svor','svar','kaal','paino','ves'],
 'pattern':['pattern','rast','rakst','muster','kuosi'],
 'closure':['closure','fastening','uzseg','aizdar','kinnitu','kiinnitys','zastezh'],
}

def _semantic(a):
    text=_fold(' | '.join(_norm(a.get(k)) for k in ('title_en','title_lt','title_lv','title_ee','title_fi','title_ru')))
    for sem, pats in SEMANTIC_PATTERNS.items():
        if any(_fold(p) in text for p in pats): return sem
    return 'unknown'

def _titles(a):
    return {k:_norm(a.get(k)) for k in ('title_en','title_lt','title_lv','title_ee','title_fi','title_ru') if _norm(a.get(k))}

def _selected_options(i,v):
    return v3._selected_options(i,v)

def _option_value(opts,names):
    aliases=[_fold(x) for x in names]
    for k,val in opts.items():
        fk=_fold(k)
        if any(a==fk or a in fk for a in aliases):
            if _norm(val): return _norm(val),k
    return '',''

def _structured_source(sem,i,p,v,opts,rule_meta):
    if sem=='brand':
        val=_norm(i.get('vendor')) or _norm(p.get('vendor'))
        return ('STRUCTURED_EXACT','master.vendor/shopify.vendor',val) if val else ('MISSING','','')
    if sem=='color':
        val,key=_option_value(opts,['color','colour','spalva','krasa','varv','vari'])
        return ('STRUCTURED_EXACT','selected_options:'+key,val) if val else ('MISSING','','')
    if sem=='size':
        val,key=_option_value(opts,['size','dydis','izmers','suurus','koko','razmer'])
        return ('STRUCTURED_EXACT','selected_options:'+key,val) if val else ('MISSING','','')
    if sem=='season':
        val,key=_option_value(opts,['season','sezonas','sezona','hooaeg','kausi'])
        if val: return ('STRUCTURED_EXACT','selected_options:'+key,val)
        return ('TEXT_EVIDENCE_ONLY','title/tags','')
    if sem=='material':
        val,key=_option_value(opts,['material','fabric','medziaga','materials','materjal','materiaali'])
        if val: return ('STRUCTURED_EXACT','selected_options:'+key,val)
        return ('TEXT_EVIDENCE_ONLY','title/description','')
    if sem=='audience':
        val,key=_option_value(opts,['gender','audience','intended','sex','kam'])
        if val: return ('STRUCTURED_EXACT','selected_options:'+key,val)
        aud=_norm(rule_meta.get('audience'))
        if aud: return ('RULE_EVIDENCE_VALUE_VOCAB_UNPROVEN','accepted_category_rule.audience',aud)
        return ('MISSING','','')
    if sem=='model':
        val,key=_option_value(opts,['model','style','fit'])
        if val: return ('STRUCTURED_EXACT','selected_options:'+key,val)
        sold=_norm(rule_meta.get('sold_object'))
        return ('RULE_EVIDENCE_VALUE_VOCAB_UNPROVEN','accepted_category_rule.sold_object',sold) if sold else ('TEXT_EVIDENCE_ONLY','title/product_type','')
    if sem=='hood':
        val,key=_option_value(opts,['hood','kapuce','kapuuts','huppu'])
        return ('STRUCTURED_EXACT','selected_options:'+key,val) if val else ('MISSING','','')
    if sem=='jacket_length':
        val,key=_option_value(opts,['jacket length','length','ilgis','garums','pikkus','pituus'])
        return ('STRUCTURED_EXACT','selected_options:'+key,val) if val else ('MISSING','','')
    if sem=='weight':
        val=_norm(i.get('220_weight_kg'))
        if val: return ('STRUCTURED_UNIT_CONTEXT_UNPROVEN','master.220_weight_kg',val)
        w=v.get('weight') if isinstance(v,dict) else None
        if isinstance(w,dict) and _norm(w.get('value')):
            return ('STRUCTURED_UNIT_CONTEXT_UNPROVEN','shopify.variant.weight',_norm(w.get('value')))
        return ('MISSING','','')
    if sem in {'pattern','closure'}:
        val,key=_option_value(opts,[sem])
        return ('STRUCTURED_EXACT','selected_options:'+key,val) if val else ('MISSING','','')
    return ('MISSING','','')

def run_audit(master_rows,shopify,db):
    mbytes=load_v10(db,'v10-product-category-mapping.csv')
    if not mbytes: raise RuntimeError('v10 mapping unavailable')
    mapping=_load(mbytes)
    if len(mapping)!=1671: raise RuntimeError(f'v10 mapping size changed: {len(mapping)}')
    b=[r for r in mapping if r.get('status')=='BLOCKED_ATTRIBUTES']
    if len(b)!=143: raise RuntimeError(f'v10 BLOCKED_ATTRIBUTES changed: {len(b)} != 143')

    tax_summary,cats,attrs=v3._latest_taxonomy(db)
    req_by_cat=defaultdict(list)
    for a in attrs:
        if str(a.get('required')).casefold() in {'true','1','yes'}:
            req_by_cat[str(a.get('category_id'))].append(a)

    ids=v3._canonical_identities(master_rows)
    iby={(i['220_sku'],i['220_ean']):i for i in ids}
    if len(iby)!=1671: raise RuntimeError('canonical identity universe changed')

    libbytes=load_v10(db,'category_rule_library_v10.csv')
    library=_load(libbytes) if libbytes else []
    rule_by_id={r.get('rule_id'):r for r in library if r.get('rule_id')}

    detail=[]; products=[]; field_occ=Counter(); sem_occ=Counter(); status_occ=Counter(); cat_products=Counter(); cat_required=defaultdict(set)
    source_complete=0; strict_complete=0
    for mr in b:
        k=(mr['220_sku'],mr['220_ean']); i=iby.get(k)
        if not i: raise RuntimeError('missing canonical identity '+repr(k))
        p,v=v3._shopify_enrich(i,shopify); opts=_selected_options(i,v)
        cid=str(mr.get('selected_category_id') or '')
        req=req_by_cat.get(cid) or []
        rid=mr.get('v10_rule_id') or mr.get('v8_rule_id') or mr.get('rule_id') or ''
        rule_meta=rule_by_id.get(rid,{})
        missing=[]; nonstrict=[]; exact=[]; sourceable=[]
        for a in req:
            fid=str(a.get('field_id')); sem=_semantic(a); st,src,val=_structured_source(sem,i,p,v,opts,rule_meta)
            field_occ[(cid,fid,sem,st)]+=1; sem_occ[sem]+=1; status_occ[st]+=1; cat_required[cid].add(fid)
            if st=='MISSING' or st=='TEXT_EVIDENCE_ONLY': missing.append(fid)
            else: sourceable.append(fid)
            if st=='STRUCTURED_EXACT': exact.append(fid)
            else: nonstrict.append(fid)
            detail.append({'220_sku':k[0],'220_ean':k[1],'category_id':cid,'category_name':mr.get('selected_category_name',''),'rule_id':rid,'field_id':fid,'semantic':sem,'title_en':_norm(a.get('title_en')),'title_lt':_norm(a.get('title_lt')),'title_lv':_norm(a.get('title_lv')),'title_ee':_norm(a.get('title_ee')),'title_fi':_norm(a.get('title_fi')),'title_ru':_norm(a.get('title_ru')),'source_status':st,'source':src,'candidate_value':val,'candidate_product_feature_name':_norm(a.get('title_lv')) or _norm(a.get('title_lt')) or _norm(a.get('title_en')),'publish_action':'NONE_READ_ONLY'})
        comp='SOURCE_COMPLETE_CANDIDATE' if req and not missing else 'SOURCE_GAPS'
        strict='STRICT_STRUCTURED_COMPLETE' if req and not nonstrict else 'NOT_STRICT_COMPLETE'
        if comp=='SOURCE_COMPLETE_CANDIDATE': source_complete+=1
        if strict=='STRICT_STRUCTURED_COMPLETE': strict_complete+=1
        cat_products[cid]+=1
        products.append({'220_sku':k[0],'220_ean':k[1],'category_id':cid,'category_name':mr.get('selected_category_name',''),'rule_id':rid,'required_count':len(req),'sourceable_count':len(sourceable),'strict_exact_count':len(exact),'missing_or_text_only_count':len(missing),'source_complete_candidate':comp,'strict_complete':strict,'sourceable_field_ids':'|'.join(sourceable),'missing_or_text_only_field_ids':'|'.join(missing),'publication_status':'BLOCKED_ATTRIBUTES','publication_reason':'No PHH mutation/dry-run performed; candidate name/value contract is proven, marketplace acceptance of values is not.'})

    field_rows=[]
    for (cid,fid,sem,st),n in sorted(field_occ.items()):
        sample=next((d for d in detail if d['category_id']==cid and d['field_id']==fid and d['source_status']==st),{})
        field_rows.append({'category_id':cid,'field_id':fid,'semantic':sem,'source_status':st,'product_occurrences':n,'field_title':sample.get('candidate_product_feature_name','')})

    cat_rows=[]
    for cid,n in sorted(cat_products.items(),key=lambda x:(-x[1],x[0])):
        ps=[x for x in products if x['category_id']==cid]
        cat_rows.append({'category_id':cid,'blocked_products':n,'required_field_count':len(cat_required[cid]),'source_complete_candidates':sum(x['source_complete_candidate']=='SOURCE_COMPLETE_CANDIDATE' for x in ps),'strict_structured_complete':sum(x['strict_complete']=='STRICT_STRUCTURED_COMPLETE' for x in ps),'avg_sourceable':round(sum(int(x['sourceable_count']) for x in ps)/n,3),'avg_required':round(sum(int(x['required_count']) for x in ps)/n,3)})

    summary={'status':'PASS','version':'v11-required-attribute-source-audit','canonical_products':1671,'blocked_attributes_products':len(b),'source_complete_candidates':source_complete,'strict_structured_complete':strict_complete,'source_status_occurrences':dict(status_occ),'semantic_occurrences':dict(sem_occ),'categories':cat_rows,'taxonomy_categories':tax_summary.get('categories_fetched'),'publication_status_changes':0,'final_status_policy':'All 143 remain BLOCKED_ATTRIBUTES until value acceptance is independently validated; this audit only prepares candidate product_features name/value sources.','contract_basis':{'category_required_fields':'PHH categories API field_id + required + localized titles','product_feature_payload':'ProductImportRequest product_features [{name,value}]','value_id_required':False,'enum_dictionary_available_in_openapi':False},'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','stock_changes':0,'price_changes':0}}
    raw=json.dumps({'products':products,'detail':detail,'summary':summary},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    summary['dataset_hash']=hashlib.sha256(raw).hexdigest()
    artifacts={
      'v11-required-field-source-detail.csv':_csv(detail,['220_sku','220_ean','category_id','category_name','rule_id','field_id','semantic','title_en','title_lt','title_lv','title_ee','title_fi','title_ru','source_status','source','candidate_value','candidate_product_feature_name','publish_action']),
      'v11-product-attribute-readiness.csv':_csv(products,['220_sku','220_ean','category_id','category_name','rule_id','required_count','sourceable_count','strict_exact_count','missing_or_text_only_count','source_complete_candidate','strict_complete','sourceable_field_ids','missing_or_text_only_field_ids','publication_status','publication_reason']),
      'v11-field-gap-matrix.csv':_csv(field_rows,['category_id','field_id','semantic','source_status','product_occurrences','field_title']),
      'v11-category-readiness.csv':_csv(cat_rows,['category_id','blocked_products','required_field_count','source_complete_candidates','strict_structured_complete','avg_sourceable','avg_required']),
      'v11-attribute-source-summary.json':json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True).encode('utf-8'),
    }
    _persist(db,summary,artifacts)
    print('ATTRIBUTE_SOURCE_AUDIT_V11_RESULT '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
    return summary,artifacts
