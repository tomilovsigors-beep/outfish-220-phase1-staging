from __future__ import annotations

import csv, io, json, hashlib
from collections import Counter, defaultdict

import current_product_category_audit as v3
import current_product_category_audit_v10 as v10

BASELINE={'total_products':1671,'AUTO':0,'BLOCKED_ATTRIBUTES':143,'REVIEW':77,'BLOCKED_TAXONOMY':0,'NO_MATCH':1451}
PRIORITY_CATEGORIES=('9050','19576','17977')

CONTRACT_FIELDS=['category_id','category_title','field_id','field_title','required','field_type','unit','cardinality','allowed_values_mode','allowed_values_count','allowed_values_reference','authority_source','authority_source_version','authority_evidence','authority_status','value_format','normalization_constraints','safe_to_resolve','blocking_reason']
ALLOWED_FIELDS=['category_id','field_id','value_id','value_label','localized_labels','unit','status','source']
DRIFT_FIELDS=['category_id','field_id','drift_type','previous_required','current_required','previous_titles','current_titles','previous_snapshot','current_snapshot']
RULE_FIELDS=['rule_id','category_scope','field_id','source_system','source_field','input_pattern','normalization','output_mode','allowed_value_id','value','positive_conditions','exclusions','authority','confidence','status']
OCC_FIELDS=['220_sku','220_ean','category_id','category_title','field_id','field_title','required','authority_status','field_type','unit','allowed_values_mode','resolution_class','source','source_field','raw_value','raw_unit','normalized_value','transformation','evidence','blocking_reason','category_frozen','category_evidence_conflict']
COVERAGE_FIELDS=['category_id','category_title','field_id','field_title','products_requiring','resolved_exact','resolved_deterministic','missing','ambiguous','dictionary_blocked','unit_unknown','type_unknown','conflicting','manual_review','coverage_percent']
READINESS_FIELDS=['220_sku','220_ean','category_id','category_title','required_fields','resolved_fields','blocked_fields','attribute_readiness','blocking_classes','category_frozen','category_evidence_conflict']
EXCEPTION_FIELDS=['exception_type','category_id','field_id','220_sku','220_ean','details','recommended_review']


def _csv(rows,fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows); return s.getvalue().encode('utf-8-sig')

def _rows(b):
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))

def _json_bytes(obj):
    return json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2).encode('utf-8')

def _titles(a):
    keys=('title_en','title_lt','title_lv','title_ee','title_fi','title_ru')
    return {k:v3._norm(a.get(k)) for k in keys if v3._norm(a.get(k))}

def _title(a):
    for k in ('title_en','title_lt','title_lv','title_ee','title_fi','title_ru'):
        if v3._norm(a.get(k)): return v3._norm(a.get(k))
    return ''

def _snapshot_pair(db):
    import psycopg
    out=[]
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select id,created_at,artifacts from phh_category_export_snapshots order by created_at desc limit 2')
            for sid,created,arts in cur.fetchall():
                if not isinstance(arts,dict): arts=json.loads(arts)
                out.append((str(sid),created.isoformat(),arts))
    return out

def _snapshot_attrs(snap):
    if not snap: return []
    _,_,arts=snap
    raw=arts.get('pmp-category-attributes.csv')
    if not raw: return []
    return list(csv.DictReader(io.StringIO(raw)))

def _schema_drift(prev,curr):
    if not curr: return []
    prev_id,prev_ts,_=prev if prev else ('','','')
    cur_id,cur_ts,_=curr
    pa={(str(r.get('category_id')),str(r.get('field_id'))):r for r in _snapshot_attrs(prev)} if prev else {}
    ca={(str(r.get('category_id')),str(r.get('field_id'))):r for r in _snapshot_attrs(curr)}
    rows=[]
    for key in sorted(set(pa)|set(ca)):
        p=pa.get(key); c=ca.get(key); cid,fid=key
        if p is None: typ='FIELD_ADDED'
        elif c is None: typ='FIELD_REMOVED'
        elif str(p.get('required')).casefold()!=str(c.get('required')).casefold(): typ='REQUIRED_CHANGED'
        elif _titles(p)!=_titles(c): typ='TITLE_CHANGED'
        else: continue
        rows.append({'category_id':cid,'field_id':fid,'drift_type':typ,'previous_required':v3._norm((p or {}).get('required')),'current_required':v3._norm((c or {}).get('required')),'previous_titles':json.dumps(_titles(p or {}),ensure_ascii=False,sort_keys=True),'current_titles':json.dumps(_titles(c or {}),ensure_ascii=False,sort_keys=True),'previous_snapshot':f'{prev_id}@{prev_ts}' if prev else 'NONE','current_snapshot':f'{cur_id}@{cur_ts}'})
    return rows

def _persist(db,summary,artifacts):
    import psycopg
    payload={k:v.decode('utf-8-sig') if k.endswith('.csv') else v.decode('utf-8') for k,v in artifacts.items()}
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''create table if not exists phh_attribute_v11_snapshots(id bigserial primary key,created_at timestamptz not null default now(),dataset_hash text not null,summary jsonb not null,artifacts jsonb not null)''')
            cur.execute('insert into phh_attribute_v11_snapshots(dataset_hash,summary,artifacts) values(%s,%s::jsonb,%s::jsonb)',(summary['dataset_hash'],json.dumps(summary,ensure_ascii=False),json.dumps(payload,ensure_ascii=False)))
        c.commit()

def load_latest_artifact(db,name):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select artifacts->>%s from phh_attribute_v11_snapshots order by created_at desc limit 1',(name,)); row=cur.fetchone()
            return None if not row or row[0] is None else row[0].encode('utf-8-sig' if name.endswith('.csv') else 'utf-8')

def run_audit(master_rows,shopify,db):
    # Frozen v10 product/category baseline.
    b=v10.load_latest_artifact(db,'v10-product-category-mapping.csv')
    if not b: raise RuntimeError('v10 mapping unavailable')
    mapping=_rows(b)
    if len(mapping)!=1671: raise RuntimeError(f'v10 mapping size changed: {len(mapping)}')
    blocked=[r for r in mapping if r.get('status')=='BLOCKED_ATTRIBUTES']
    if len(blocked)!=143: raise RuntimeError(f'BLOCKED_ATTRIBUTES baseline changed: {len(blocked)} != 143')

    tax_summary,cats,attrs=v3._latest_taxonomy(db)
    by_cat={str(c.get('category_id')):c for c in cats}
    req=defaultdict(list)
    for a in attrs:
        if str(a.get('required')).casefold() in {'true','1','yes'}:
            req[str(a.get('category_id'))].append(a)

    snaps=_snapshot_pair(db)
    curr=snaps[0] if snaps else None; prev=snaps[1] if len(snaps)>1 else None
    drift=_schema_drift(prev,curr)
    drift_keys={(r['category_id'],r['field_id']) for r in drift}

    # Authority discovery result: current official PHH taxonomy export exposes IDs, required flag and localized titles only.
    # No official type/unit/cardinality/allowed-value/value-id artifact is present in the known project snapshots.
    categories=sorted({r.get('selected_category_id','') for r in blocked if r.get('selected_category_id')})
    field_occ=Counter(); contracts=[]; contract_by={}
    for cid in categories:
        cat=by_cat.get(cid) or {}; ctitle=v3._norm(cat.get('title_en')) or v3._norm(next(iter(_titles(cat).values()),''))
        for a in req.get(cid,[]):
            fid=str(a.get('field_id')); field_occ[(cid,fid)]=sum(1 for r in blocked if r.get('selected_category_id')==cid)
            row={'category_id':cid,'category_title':ctitle,'field_id':fid,'field_title':_title(a),'required':'TRUE','field_type':'','unit':'','cardinality':'','allowed_values_mode':'UNKNOWN','allowed_values_count':'','allowed_values_reference':'','authority_source':'official PHH live category schema snapshot','authority_source_version':curr[1] if curr else 'unknown','authority_evidence':'Official schema confirms category_id/field_id/required/localized titles only. No official type/unit/cardinality/allowed-value dictionary available in current snapshots or project artifacts.','authority_status':'AUTHORITY_MISSING','value_format':'','normalization_constraints':'','safe_to_resolve':'FALSE','blocking_reason':'PHH field type/unit/cardinality/allowed-values contract is not authoritative.'}
            contracts.append(row); contract_by[(cid,fid)]=row

    # Empty until an authoritative PHH values dictionary exists.
    allowed=[]; rules=[]

    ids=v3._canonical_identities(master_rows)
    imap={(i['220_sku'],i['220_ean']):i for i in ids}
    enriched={(i['220_sku'],i['220_ean']):v3._shopify_enrich(i,shopify) for i in ids}
    occurrences=[]; readiness=[]; exceptions=[]; class_counts=Counter(); source_inventory=Counter()
    per_product=defaultdict(list)

    for r in blocked:
        sku,ean=r['220_sku'],r['220_ean']; cid=r.get('selected_category_id',''); cat=by_cat.get(cid) or {}; ctitle=v3._norm(cat.get('title_en'))
        i=imap.get((sku,ean),{}); p,v=enriched.get((sku,ean),({},{}))
        # Inventory source presence only; do not map values to fields without field authority.
        if v3._norm(i.get('220_title')): source_inventory['AUTHORITATIVE_TITLE_PRESENT']+=1
        if v3._norm(i.get('vendor') or p.get('vendor')): source_inventory['VENDOR_PRESENT']+=1
        if v3._norm(p.get('product_type') or i.get('product_type')): source_inventory['PRODUCT_TYPE_PRESENT']+=1
        if v3._norm(p.get('category_name')): source_inventory['SHOPIFY_CATEGORY_PRESENT']+=1
        if v.get('selected_options'): source_inventory['SHOPIFY_OPTION_PRESENT']+=1
        for a in req.get(cid,[]):
            fid=str(a.get('field_id')); contract=contract_by[(cid,fid)]
            conflict='FALSE'
            resolution='PHH_TYPE_UNKNOWN'
            if (cid,fid) in drift_keys:
                resolution='REQUIRES_MANUAL_REVIEW'
            row={'220_sku':sku,'220_ean':ean,'category_id':cid,'category_title':ctitle,'field_id':fid,'field_title':_title(a),'required':'TRUE','authority_status':contract['authority_status'],'field_type':'','unit':'','allowed_values_mode':'UNKNOWN','resolution_class':resolution,'source':'','source_field':'','raw_value':'','raw_unit':'','normalized_value':'','transformation':'','evidence':'Source inventory collected, but no product value may be bound to this PHH field until an official field contract exists.','blocking_reason':contract['blocking_reason'] if resolution!='REQUIRES_MANUAL_REVIEW' else 'PHH schema drift detected for this category/field; manual review required before resolution.','category_frozen':'TRUE','category_evidence_conflict':conflict}
            occurrences.append(row); per_product[(sku,ean)].append(row); class_counts[resolution]+=1

    # Category decisions are frozen; v11 generates conflicts only on explicit contradictory evidence. None is inferred here.
    for d in drift:
        exceptions.append({'exception_type':'PHH_SCHEMA_DRIFT','category_id':d['category_id'],'field_id':d['field_id'],'220_sku':'','220_ean':'','details':json.dumps(d,ensure_ascii=False,sort_keys=True),'recommended_review':'Review authoritative PHH schema drift before any downstream attribute mapping.'})
    for c in contracts:
        exceptions.append({'exception_type':'ATTRIBUTE_AUTHORITY_MISSING','category_id':c['category_id'],'field_id':c['field_id'],'220_sku':'','220_ean':'','details':c['blocking_reason'],'recommended_review':'Obtain official PHH Categories fields and values export/API/documentation for type/unit/cardinality/allowed values.'})

    for r in blocked:
        key=(r['220_sku'],r['220_ean']); rows=per_product[key]
        resolved=sum(1 for x in rows if x['resolution_class'] in {'RESOLVED_EXACT','RESOLVED_DETERMINISTIC'})
        blocked_n=len(rows)-resolved
        classes=sorted({x['resolution_class'] for x in rows if x['resolution_class'] not in {'RESOLVED_EXACT','RESOLVED_DETERMINISTIC'}})
        status='ATTRIBUTE_READY' if rows and blocked_n==0 else ('ATTRIBUTE_PARTIAL' if resolved else 'ATTRIBUTE_BLOCKED')
        readiness.append({'220_sku':key[0],'220_ean':key[1],'category_id':r.get('selected_category_id',''),'category_title':r.get('selected_category_name',''),'required_fields':len(rows),'resolved_fields':resolved,'blocked_fields':blocked_n,'attribute_readiness':status,'blocking_classes':'|'.join(classes),'category_frozen':'TRUE','category_evidence_conflict':'FALSE'})

    coverage=[]
    by_cf=defaultdict(list)
    for x in occurrences: by_cf[(x['category_id'],x['field_id'])].append(x)
    for (cid,fid),rows in sorted(by_cf.items()):
        c=contract_by[(cid,fid)]; cc=Counter(x['resolution_class'] for x in rows); resolved=cc['RESOLVED_EXACT']+cc['RESOLVED_DETERMINISTIC']; total=len(rows)
        coverage.append({'category_id':cid,'category_title':c['category_title'],'field_id':fid,'field_title':c['field_title'],'products_requiring':total,'resolved_exact':cc['RESOLVED_EXACT'],'resolved_deterministic':cc['RESOLVED_DETERMINISTIC'],'missing':cc['MISSING_SOURCE_VALUE'],'ambiguous':cc['AMBIGUOUS_SOURCE_VALUE'],'dictionary_blocked':cc['PHH_DICTIONARY_MISSING'],'unit_unknown':cc['PHH_UNIT_UNKNOWN'],'type_unknown':cc['PHH_TYPE_UNKNOWN'],'conflicting':cc['CONFLICTING_SOURCES'],'manual_review':cc['REQUIRES_MANUAL_REVIEW'],'coverage_percent':round(100*resolved/total,2) if total else 0.0})

    ready_counts=Counter(r['attribute_readiness'] for r in readiness)
    cat_products=Counter(r.get('selected_category_id','') for r in blocked)
    priority={}
    for cid in PRIORITY_CATEGORIES:
        prods=cat_products[cid]; fids=req.get(cid,[]); occ=[x for x in occurrences if x['category_id']==cid]; res=sum(1 for x in occ if x['resolution_class'] in {'RESOLVED_EXACT','RESOLVED_DETERMINISTIC'}); rdy=Counter(x['attribute_readiness'] for x in readiness if x['category_id']==cid)
        priority[cid]={'product_count':prods,'required_fields':len(fids),'total_occurrences':len(occ),'resolved':res,'blocked':len(occ)-res,'ATTRIBUTE_READY':rdy['ATTRIBUTE_READY'],'ATTRIBUTE_PARTIAL':rdy['ATTRIBUTE_PARTIAL'],'ATTRIBUTE_BLOCKED':rdy['ATTRIBUTE_BLOCKED']}

    authority_counts=Counter(c['authority_status'] for c in contracts)
    summary={'status':'PASS','version':'v11-attribute-authority','baseline':BASELINE,'products_investigated':len(blocked),'unique_categories':len(categories),'unique_required_field_ids':len({c['field_id'] for c in contracts}),'unique_category_field_contracts':len(contracts),'authority':{'AUTHORITY_COMPLETE':authority_counts['AUTHORITY_COMPLETE'],'AUTHORITY_PARTIAL':authority_counts['AUTHORITY_PARTIAL'],'AUTHORITY_MISSING':authority_counts['AUTHORITY_MISSING'],'fields_with_known_type':sum(bool(c['field_type']) for c in contracts),'fields_with_known_unit':sum(bool(c['unit']) for c in contracts),'fields_with_allowed_value_dictionary':sum(c['allowed_values_mode'] not in {'','UNKNOWN'} for c in contracts),'official_categories_fields_values_source_found':False,'finding':'Current official PHH taxonomy schema confirms only field_id, required and localized titles. No authoritative type/unit/cardinality/allowed-value/value-id export was found in project snapshots/repo or public official search during this run.'},'occurrences':{'total_required_field_occurrences':len(occurrences),**{k:class_counts[k] for k in ('RESOLVED_EXACT','RESOLVED_DETERMINISTIC','MISSING_SOURCE_VALUE','AMBIGUOUS_SOURCE_VALUE','PHH_DICTIONARY_MISSING','PHH_UNIT_UNKNOWN','PHH_TYPE_UNKNOWN','NO_ALLOWED_VALUE_MATCH','CONFLICTING_SOURCES','REQUIRES_MANUAL_REVIEW')}},'readiness':dict(ready_counts),'priority_categories':priority,'source_inventory_presence':dict(source_inventory),'schema_drift_rows':len(drift),'schema_drift_types':dict(Counter(x['drift_type'] for x in drift)),'category_evidence_conflicts':0,'values_proven_from_master_shopify':0,'reusable_resolution_rules_accepted':0,'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','stock_changes':0,'price_changes':0},'principle':'CATEGORY IS FROZEN. FIELD SEMANTICS FIRST. VALUE SECOND. NO ENUM OR UNIT GUESSING. ATTRIBUTE_READY IS NOT PUBLISH-READY.'}

    artifacts={
      'phh_attribute_contract_registry.csv':_csv(contracts,CONTRACT_FIELDS),
      'phh_attribute_allowed_values.csv':_csv(allowed,ALLOWED_FIELDS),
      'v11-phh-attribute-schema-drift.csv':_csv(drift,DRIFT_FIELDS),
      'attribute_resolution_rules.csv':_csv(rules,RULE_FIELDS),
      'v11-product-required-attributes.csv':_csv(occurrences,OCC_FIELDS),
      'v11-required-attribute-coverage.csv':_csv(coverage,COVERAGE_FIELDS),
      'v11-product-attribute-readiness.csv':_csv(readiness,READINESS_FIELDS),
      'v11-attribute-exceptions.csv':_csv(exceptions,EXCEPTION_FIELDS),
    }
    digest=hashlib.sha256()
    for name in sorted(artifacts): digest.update(name.encode()); digest.update(artifacts[name])
    summary['dataset_hash']=digest.hexdigest()
    audit={'status':'PASS','category_decisions_frozen':True,'category_remaps':0,'category_evidence_conflicts':0,'authority_contracts':len(contracts),'accepted_resolution_rules':0,'writes':summary['safety'],'checks':['v10 baseline 1671 preserved','BLOCKED_ATTRIBUTES universe exactly 143','live required fields loaded from PHH snapshot','previous/current PHH schema compared','no type/unit/enum inferred from field titles','no product value bound without authoritative field contract']}
    artifacts['v11-attribute-summary.json']=_json_bytes(summary)
    artifacts['v11-rule-audit.json']=_json_bytes(audit)
    _persist(db,summary,artifacts)
    print('ATTRIBUTE_AUDIT_V11_RESULT '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
    return summary,artifacts
