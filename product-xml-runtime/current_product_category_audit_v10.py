from __future__ import annotations

import csv, io, json, hashlib
from collections import Counter

import current_product_category_audit as v3
import current_product_category_audit_v4 as v4
import current_product_category_audit_v8 as v8
from current_product_identity_audit_v9 import load_latest_artifact as load_v9

BASELINE={'total_products':1671,'AUTO':0,'BLOCKED_ATTRIBUTES':76,'REVIEW':77,'BLOCKED_TAXONOMY':0,'NO_MATCH':1518}

RULES=[
 {'rule_id':'V10-RULE-001','family_key':'CURRENT_GROUP:0089','member_count':9,'category_id':'9050','sold_object':'outerwear jacket','audience':'MEN','confidence':0.995,
  'basis':'Identity PASS + exact 9/9 SKU set; FHM Lightweight Primaloft Jacket; Shopify subtype Puffer Jackets; male audience; live PHH 9050 localized titles denote men outerwear jackets, while 9053 localized titles denote blazers.'},
 {'rule_id':'V10-RULE-002','family_key':'CURRENT_GROUP:0090','member_count':9,'category_id':'9050','sold_object':'outerwear jacket','audience':'MEN','confidence':0.995,
  'basis':'Identity PASS + exact 9/9 SKU set; FHM Primaloft Jacket; Shopify subtype Puffer Jackets; male audience; live PHH 9050 is men outerwear, not blazer 9053.'},
 {'rule_id':'V10-RULE-003','family_key':'CURRENT_GROUP:0102','member_count':9,'category_id':'9050','sold_object':'outerwear jacket','audience':'MEN','confidence':0.995,
  'basis':'Identity PASS + exact 9/9 SKU set; Waterproof Insulated Jacket FHM Mist 20000 mm; Shopify subtype Sport Jackets; male audience; live PHH 9050 localized titles denote men outerwear jackets.'},
 {'rule_id':'V10-RULE-004','family_key':'CURRENT_GROUP:0114','member_count':8,'category_id':'17977','sold_object':'trousers','audience':'MEN','confidence':0.995,
  'basis':'Identity PASS + exact 8/8 SKU set; Hiking Pants FHM Flow; Shopify Pants / Activewear Pants; male audience; live PHH 17977 is Men\'s trousers; women/kids/motorcycle alternatives excluded.'},
 {'rule_id':'V10-RULE-005','family_key':'CURRENT_GROUP:0115','member_count':8,'category_id':'19576','sold_object':'shorts','audience':'MEN','confidence':0.995,
  'basis':'Identity PASS + exact 8/8 SKU set; Hiking and Fishing Shorts FHM Flow; Shopify subtype Shorts; male audience; live PHH 19576 is Men\'s shorts.'},
 {'rule_id':'V10-RULE-006','family_key':'CURRENT_GROUP:0116','member_count':8,'category_id':'19576','sold_object':'shorts','audience':'MEN','confidence':0.995,
  'basis':'Identity PASS + exact 8/8 SKU set; Hiking and fishing shorts FHM Flow; Shopify product type/subtype Shorts; male audience; live PHH 19576 is Men\'s shorts.'},
 {'rule_id':'V10-RULE-007','family_key':'CURRENT_GROUP:0117','member_count':8,'category_id':'19576','sold_object':'shorts','audience':'MEN','confidence':0.995,
  'basis':'Identity PASS + exact 8/8 SKU set; Hiking Shorts FHM Flow; Shopify product type/subtype Shorts; male audience; live PHH 19576 is Men\'s shorts.'},
 {'rule_id':'V10-RULE-008','family_key':'CURRENT_GROUP:0139','member_count':8,'category_id':'9050','sold_object':'outerwear jacket','audience':'MEN','confidence':0.995,
  'basis':'Identity PASS + exact 8/8 SKU set; Softshell Jacket FHM Nuk; Shopify subtype Windbreakers; male audience; live PHH 9050 is men outerwear, not blazer 9053.'},
]

UNRESOLVED={
 'CURRENT_GROUP:0080':'Identity PASS, but authoritative 220 family title says Shirt while Shopify product type/subtype says jacket / Puffer Jackets; sold object conflict must be resolved before choosing PHH 5726 vs 9050.',
 'CURRENT_GROUP:0040':"Identity PASS and MEN hoodie object proven, but no exact live PHH men's hoodie leaf proven; 5720 Sweaters for men is not forced as substitute.",
 'CURRENT_GROUP:0056':'Identity PASS and trousers object proven, but audience remains unknown while PHH separates men/women/children.',
}


def _csv(rows,fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows); return s.getvalue().encode('utf-8-sig')

def _load_csv_bytes(b):
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))

def _persist(db,summary,artifacts):
    import psycopg
    payload={k:v.decode('utf-8-sig') for k,v in artifacts.items()}
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''create table if not exists phh_category_rule_v10_snapshots(id bigserial primary key,created_at timestamptz not null default now(),dataset_hash text not null,summary jsonb not null,artifacts jsonb not null)''')
            cur.execute('insert into phh_category_rule_v10_snapshots(dataset_hash,summary,artifacts) values(%s,%s::jsonb,%s::jsonb)',(summary['dataset_hash'],json.dumps(summary,ensure_ascii=False),json.dumps(payload,ensure_ascii=False)))
        c.commit()

def load_latest_artifact(db,name):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select artifacts->>%s from phh_category_rule_v10_snapshots order by created_at desc limit 1',(name,)); row=cur.fetchone()
            return None if not row or row[0] is None else row[0].encode('utf-8-sig')

def _assert_category(by_cat,cid):
    c=by_cat.get(cid)
    if not c: raise RuntimeError(f'PHH category {cid} missing')
    if str(c.get('allow_add_products')).casefold() not in {'true','1','yes'}: raise RuntimeError(f'PHH category {cid} not addable')
    if cid=='17977' and v3._norm(c.get('title_en'))!="Men's trousers": raise RuntimeError('17977 title changed')
    if cid=='19576' and v3._norm(c.get('title_en'))!="Men's shorts": raise RuntimeError('19576 title changed')
    if cid=='9050':
        loc=' | '.join(v3._norm(c.get(k)) for k in ('title_lt','title_lv','title_ru','title_fi'))
        required_fragments=('Vyriškos striukės','Vīriešu virsjakas','Мужские куртки','Miesten ulkoilutakit')
        if any(x not in loc for x in required_fragments): raise RuntimeError('9050 localized outerwear semantics changed: '+loc)
    return c

def run_audit(master_rows,shopify,db):
    tax_summary,cats,attrs=v3._latest_taxonomy(db); by_cat,required,_=v3._category_index(cats,attrs)
    ids=v3._canonical_identities(master_rows)
    if len(ids)!=1671: raise RuntimeError(f'canonical universe changed {len(ids)}')
    groups,membership,group_diag=v4._build_groups(ids)
    if len(groups)!=914: raise RuntimeError(f'family universe changed {len(groups)}')

    qbytes=load_v9(db,'v9-leaf-ready-queue.csv')
    if not qbytes: raise RuntimeError('v9 leaf-ready queue unavailable')
    qrows=_load_csv_bytes(qbytes); qby={r['family_key']:r for r in qrows}
    accepted=[]; audit=[]
    for spec in RULES:
        key=spec['family_key']; q=qby.get(key); members=groups.get(key) or []; errors=[]
        if not q: errors.append('missing from v9 leaf-ready queue')
        else:
            if q.get('identity_status')!='IDENTITY_PASS': errors.append('identity not PASS')
            if q.get('sku_set_status')!='EXACT_SET': errors.append('SKU set not exact')
            if q.get('audience')!='MEN': errors.append('audience not MEN')
        if len(members)!=spec['member_count']: errors.append(f'member_count={len(members)}')
        canon=[(v3._norm(x.get('220_sku')),v3._norm(x.get('220_ean'))) for x in members]
        if len(set(canon))!=len(canon): errors.append('canonical conflict')
        cat=_assert_category(by_cat,spec['category_id'])
        if errors: raise RuntimeError(spec['rule_id']+' validation failed: '+json.dumps(errors))
        accepted.append((spec,members,cat,q))
        audit.append({'rule_id':spec['rule_id'],'family_key':key,'member_count':len(members),'identity_status':q.get('identity_status'),'sku_set_status':q.get('sku_set_status'),'audience':q.get('audience'),'product_type':q.get('product_type'),'product_subtype':q.get('product_subtype'),'phh_category_id':spec['category_id'],'phh_category_title':v3._norm(cat.get('title_en')),'decision':'ACCEPTED','basis':spec['basis'],'confidence':f"{spec['confidence']:.6f}"})

    for key,reason in UNRESOLVED.items():
        q=qby.get(key) or {}; members=groups.get(key) or []
        audit.append({'rule_id':'','family_key':key,'member_count':len(members),'identity_status':q.get('identity_status'),'sku_set_status':q.get('sku_set_status'),'audience':q.get('audience'),'product_type':q.get('product_type'),'product_subtype':q.get('product_subtype'),'phh_category_id':'','phh_category_title':'','decision':'UNRESOLVED','basis':reason,'confidence':''})

    bmap=v8.load_latest_artifact(db,'v8-product-category-mapping.csv')
    if not bmap: raise RuntimeError('v8 baseline mapping unavailable')
    baseline_rows=_load_csv_bytes(bmap); baseline={(r['220_sku'],r['220_ean']):r for r in baseline_rows}
    if len(baseline)!=1671: raise RuntimeError(f'v8 baseline mapping size={len(baseline)}')
    blib=v8.load_latest_artifact(db,'category_rule_library_v1.csv')
    prior_lib=_load_csv_bytes(blib) if blib else []

    accepted_by_identity={}
    for spec,members,cat,q in accepted:
        for m in members: accepted_by_identity[(v3._norm(m.get('220_sku')),v3._norm(m.get('220_ean')))]=(spec,cat)

    enriched={(i['220_sku'],i['220_ean']):v3._shopify_enrich(i,shopify) for i in ids}
    final=[]; counts=Counter(); transitions=Counter(); target_cat_counts=Counter(); attr_missing_counts=Counter()
    base_fields=list(baseline_rows[0].keys())
    extra=['v10_rule_id','v10_family_key','v10_decision_source','v10_decision_basis']
    map_fields=base_fields+[x for x in extra if x not in base_fields]
    for i in ids:
        k=(i['220_sku'],i['220_ean']); old=dict(baseline[k]); prior=old.get('status') or 'NO_MATCH'; status=prior
        rid=''; fkey=membership.get(k,''); source='v8_baseline'; basis='unchanged from v8 baseline'
        if k in accepted_by_identity:
            spec,cat=accepted_by_identity[k]; rid=spec['rule_id']; fkey=spec['family_key']; source='accepted_v10_identity_pass_exact_leaf_rule'; basis=spec['basis']
            old['selected_category_id']=spec['category_id']; old['selected_category_name']=v3._norm(cat.get('title_en')); old['confidence']=f"{spec['confidence']:.6f}"
            p,v=enriched[k]; opts=v3._selected_options(i,v); req=required.get(spec['category_id']) or []; missing=[]; identified=[]
            for a in req:
                src_status,src,val=v3._attribute_source(a,i,p,v,opts)
                fid=str(a.get('field_id'))
                if src_status.startswith('SOURCE_IDENTIFIED'): identified.append(fid)
                else: missing.append(fid); attr_missing_counts[fid]+=1
            old['required_field_ids']='|'.join(str(a.get('field_id')) for a in req)
            old['source_identified_field_ids']='|'.join(identified); old['missing_required_field_ids']='|'.join(missing)
            status='BLOCKED_ATTRIBUTES' if missing else 'AUTO'
            target_cat_counts[spec['category_id']]+=1
        old['status']=status; old['v10_rule_id']=rid; old['v10_family_key']=fkey if rid else ''; old['v10_decision_source']=source; old['v10_decision_basis']=basis
        transitions[(prior,status)]+=1; counts[status]+=1; final.append(old)

    # Preserve all 1671 and never degrade an already safer baseline product through unrelated rules.
    if len(final)!=1671: raise RuntimeError('final mapping size changed')
    if sum(counts.values())!=1671: raise RuntimeError('status count mismatch')

    lib_fields=['rule_id','family_key','brand','family_name','member_count','covered_skus','covered_eans','shopify_product_id','identity_gate_result','family_homogeneity_result','sold_object','product_subtype','audience','positive_signals','identity_signals','exclusion_signals','phh_category_id','phh_category_title','phh_parent_path','rejected_alternatives','decision_basis','confidence','status','source_version']
    library=[]
    for r in prior_lib: library.append({k:r.get(k,'') for k in lib_fields})
    for spec,members,cat,q in accepted:
        library.append({'rule_id':spec['rule_id'],'family_key':spec['family_key'],'brand':'FHM','family_name':q.get('family_name',''),'member_count':len(members),'covered_skus':'|'.join(v3._norm(x.get('220_sku')) for x in members),'covered_eans':'|'.join(v3._norm(x.get('220_ean')) for x in members),'shopify_product_id':'','identity_gate_result':'PASS','family_homogeneity_result':'PASS','sold_object':spec['sold_object'],'product_subtype':q.get('product_subtype',''),'audience':'MEN','positive_signals':f"v9 exact SKU-set; live Shopify subtype {q.get('product_subtype','')}; MEN audience; exact live PHH leaf semantics",'identity_signals':'canonical 220_sku+220_ean unique; v9 IDENTITY_PASS; exact Master/live SKU set; Shopify barcode auxiliary only','exclusion_signals':'exact family only; do not propagate by title similarity; reject other audiences and specialized object leaves; for 9050 reject blazer leaf 9053','phh_category_id':spec['category_id'],'phh_category_title':v3._norm(cat.get('title_en')),'phh_parent_path':'','rejected_alternatives':'9053 blazer excluded for outerwear rules; other-audience/specialized leaves excluded where applicable','decision_basis':spec['basis'],'confidence':f"{spec['confidence']:.6f}",'status':'ACCEPTED','source_version':'v10'})

    adjud_fields=['rule_id','family_key','member_count','identity_status','sku_set_status','audience','product_type','product_subtype','phh_category_id','phh_category_title','decision','basis','confidence']
    transition_rows=[{'from_status':a,'to_status':b,'count':n} for (a,b),n in sorted(transitions.items())]
    summary={'status':'PASS','version':'v10-phh-leaf-adjudication','canonical_products':1671,'families_adjudicated':len(audit),'rules_accepted':len(RULES),'new_rule_sku_coverage':sum(x['member_count'] for x in RULES),'unresolved_families':len(UNRESOLVED),'baseline':BASELINE,'final_status_counts':dict(counts),'target_category_sku_counts':dict(target_cat_counts),'transitions':{f'{a}->{b}':n for (a,b),n in transitions.items()},'no_match_removed':BASELINE['NO_MATCH']-counts.get('NO_MATCH',0),'review_removed':BASELINE['REVIEW']-counts.get('REVIEW',0),'blocked_attributes_increase':counts.get('BLOCKED_ATTRIBUTES',0)-BASELINE['BLOCKED_ATTRIBUTES'],'auto_increase':counts.get('AUTO',0)-BASELINE['AUTO'],'required_attribute_missing_field_occurrences':sum(attr_missing_counts.values()),'taxonomy_categories':tax_summary.get('categories_fetched'),'group_build':group_diag,'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','stock_changes':0,'price_changes':0},'principle':'Identity PASS is necessary; category rule is accepted only when sold object, audience and exact live PHH leaf semantics agree. Ambiguous Shirt-vs-Jacket, hoodie taxonomy gap and unknown audience stay unresolved.'}

    artifacts={
      'category_rule_library_v10.csv':_csv(library,lib_fields),
      'v10-family-adjudication.csv':_csv(audit,adjud_fields),
      'v10-product-category-mapping.csv':_csv(final,map_fields),
      'v10-status-transitions.csv':_csv(transition_rows,['from_status','to_status','count']),
    }
    summary['dataset_hash']=hashlib.sha256(artifacts['v10-product-category-mapping.csv']).hexdigest()
    artifacts['v10-category-coverage-summary.json']=json.dumps(summary,indent=2,sort_keys=True,ensure_ascii=False).encode('utf-8')
    artifacts['v10-rule-audit.json']=json.dumps({'status':'PASS','accepted_rules':audit,'unresolved':UNRESOLVED,'target_categories':{cid:{k:v3._norm(by_cat[cid].get(k)) for k in ('title_en','title_lt','title_lv','title_ru','title_fi')} for cid in sorted({x['category_id'] for x in RULES})}},indent=2,sort_keys=True,ensure_ascii=False).encode('utf-8')
    _persist(db,summary,artifacts)
    print('CATEGORY_RULE_AUDIT_V10_RESULT '+json.dumps(summary,sort_keys=True,ensure_ascii=False),flush=True)
    for a in audit: print('V10_ADJUDICATION '+json.dumps(a,sort_keys=True,ensure_ascii=False),flush=True)
    return summary,artifacts
