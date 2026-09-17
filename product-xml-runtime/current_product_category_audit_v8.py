from __future__ import annotations

import csv, io, json, hashlib, statistics
from collections import Counter

import current_product_category_audit as v3
import current_product_category_audit_v4 as v4
import current_product_category_audit_v7 as v7
from current_product_category_audit_v8_probe import run_probe

RULE={
    'rule_id':'V8-RULE-001',
    'family_key':'CURRENT_GROUP:0057',
    'brand':'Outfish',
    'family_name':'Pants Wave Blue',
    'member_count':9,
    'shopify_product_id':'gid://shopify/Product/8558732902738',
    'sold_object':'trousers',
    'product_subtype':'general trousers / pants; not motorcycle trousers',
    'audience':'MEN',
    'phh_category_id':'17977',
    'confidence':0.995,
    'positive_signals':'exact Shopify product; title Pants Wave Blue; Shopify category Pants > Trousers; tags Mens Clothes + Mens Trousers + Trousers; exact live SKU-set 9/9',
    'identity_signals':'canonical 220_sku+220_ean unique; same Shopify product; exact Master SKU set equals live Shopify SKU set; EAN differences are auxiliary only and do not replace canonical identity',
    'exclusion_signals':'exact family membership required; do not apply to women, boys, girls, babies, motorcycle trousers, shorts, jackets, accessories, or other Outfish families; do not apply only from product_type/title similarity',
    'rejected_alternatives':'11705 Motorcycle trousers: object/use subtype not proven and inconsistent with ordinary apparel context; 8708 Trousers for women: audience mismatch; 14460 Trousers for boys: audience mismatch; 14530 Trousers for girls: audience mismatch; 14600 Pants for babies: audience mismatch',
    'decision_basis':'sold object is ordinary trousers and male audience is independently explicit in Shopify tags/category context; live addable PHH 17977 is the exact men trousers leaf; specialized and other-audience leaves are rejected',
}


def _csv(rows,fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    return s.getvalue().encode('utf-8-sig')


def _load_csv(db,name):
    b=v7.load_latest_artifact(db,name)
    if not b: raise RuntimeError(f'v7b artifact unavailable: {name}')
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))


def _family_signature(members):
    payload=sorted((v3._norm(x.get('220_sku')),v3._norm(x.get('220_ean')),v3._norm(x.get('shopify_product_id')),v3._norm(x.get('shopify_title') or x.get('220_title')),v3._norm(x.get('vendor'))) for x in members)
    return hashlib.sha256(json.dumps(payload,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def _validate_rule(groups,by_cat,shopify):
    members=groups.get(RULE['family_key']) or []; errors=[]; evidence={}
    if len(members)!=RULE['member_count']: errors.append(f'member_count={len(members)} expected={RULE["member_count"]}')
    canonical=[(v3._norm(x.get('220_sku')),v3._norm(x.get('220_ean'))) for x in members]
    if len(set(canonical))!=len(canonical): errors.append('canonical identity conflict')
    pids={v3._norm(x.get('shopify_product_id')) for x in members if v3._norm(x.get('shopify_product_id'))}
    if pids!={RULE['shopify_product_id']}: errors.append('Shopify product identity mismatch')
    titles={v3._norm(x.get('shopify_title') or x.get('220_title')) for x in members if v3._norm(x.get('shopify_title') or x.get('220_title'))}
    if titles!={RULE['family_name']}: errors.append('family title mismatch')
    vendors={v3._norm(x.get('vendor')) for x in members if v3._norm(x.get('vendor'))}
    if vendors!={RULE['brand']}: errors.append('brand mismatch')

    product=shopify.get(RULE['shopify_product_id']) or {}
    if v3._norm(product.get('title'))!=RULE['family_name']: errors.append('live Shopify title mismatch')
    if v3._norm(product.get('vendor'))!=RULE['brand']: errors.append('live Shopify vendor mismatch')
    category=v3._norm(product.get('category_name')).casefold()
    if 'pants' not in category or 'trousers' not in category: errors.append('live Shopify category lacks Pants > Trousers proof')
    tags={v3._norm(t).casefold() for t in (product.get('_v8_tags') or [])}
    if 'mens clothes' not in tags or 'mens trousers' not in tags or 'trousers' not in tags: errors.append('live Shopify tags lack explicit men trousers proof')

    live=list((product.get('variants_by_id') or {}).values())
    live_by_sku={v3._norm(x.get('sku')):x for x in live if v3._norm(x.get('sku'))}
    master_skus={v3._norm(x.get('220_sku')) for x in members if v3._norm(x.get('220_sku'))}
    if master_skus!=set(live_by_sku): errors.append(f'exact SKU-set mismatch master={len(master_skus)} live={len(live_by_sku)}')
    option_values={}
    for v in live:
        for o in v.get('selected_options') or []:
            n=v3._norm(o.get('name')).casefold(); val=v3._norm(o.get('value'))
            if n: option_values.setdefault(n,set()).add(val)
    # Family may vary by standard variant dimensions; fabric/color must not split this family into different sold objects.
    if set(option_values)-{'size','color','colour','fabric'}: errors.append('unsupported variant option dimension')
    for n in ('color','colour','fabric'):
        if n in option_values and len(option_values[n])>1: errors.append(f'heterogeneous {n} option values')
    if 'size' not in option_values or len(option_values['size'])<2: errors.append('size-variant family proof missing')
    evidence['option_values']={k:sorted(v) for k,v in option_values.items()}

    cat=by_cat.get(RULE['phh_category_id'])
    if not cat: errors.append('PHH 17977 missing')
    elif str(cat.get('allow_add_products')).casefold() not in {'true','1','yes'}: errors.append('PHH 17977 not addable')
    elif v3._norm(cat.get('title_en'))!="Men's trousers": errors.append('PHH 17977 title changed')
    evidence['family_signature']=_family_signature(members)
    evidence['master_skus']=sorted(master_skus)
    evidence['live_skus']=sorted(live_by_sku)
    return members,product,cat,errors,evidence


def _persist(db,summary,artifacts):
    import psycopg
    payload={k:(v.decode('utf-8-sig') if isinstance(v,(bytes,bytearray)) else str(v)) for k,v in artifacts.items()}
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''create table if not exists phh_category_rule_v8_snapshots(
                id bigserial primary key, created_at timestamptz not null default now(), dataset_hash text not null,
                summary jsonb not null, artifacts jsonb not null)''')
            cur.execute('insert into phh_category_rule_v8_snapshots(dataset_hash,summary,artifacts) values(%s,%s::jsonb,%s::jsonb)',
                        (summary['dataset_hash'],json.dumps(summary,ensure_ascii=False),json.dumps(payload,ensure_ascii=False)))
        c.commit()


def load_latest_artifact(db,name):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select artifacts->>%s from phh_category_rule_v8_snapshots order by created_at desc limit 1',(name,))
            row=cur.fetchone()
            return None if not row or row[0] is None else row[0].encode('utf-8-sig')


def run_audit(master_rows,shopify,db,token=None,shop_domain=None):
    tax_summary,cats,attrs=v3._latest_taxonomy(db); by_cat,required,_=v3._category_index(cats,attrs)
    ids=v3._canonical_identities(master_rows)
    if len(ids)!=1671: raise RuntimeError(f'canonical universe changed {len(ids)}')
    groups0,membership,group_diag=v4._build_groups(ids); groups=dict(groups0)
    if len(groups)!=914: raise RuntimeError(f'family universe changed {len(groups)}')

    probe_summary,probe_rows=run_probe(master_rows,shopify,db,token=token,shop_domain=shop_domain,top_n=30)
    members,product,cat,errors,evidence=_validate_rule(groups,by_cat,shopify)
    accepted=not errors
    if not accepted: raise RuntimeError('V8-RULE-001 validation failed: '+json.dumps(errors))

    baseline_rows=_load_csv(db,'v7-product-category-mapping.csv')
    baseline={(r['220_sku'],r['220_ean']):r for r in baseline_rows}
    baseline_lib=_load_csv(db,'category_rule_library_v2.csv')
    enriched={(i['220_sku'],i['220_ean']):v3._shopify_enrich(i,shopify) for i in ids}

    lib_fields=['rule_id','family_key','brand','family_name','member_count','covered_skus','covered_eans','shopify_product_id','identity_gate_result','family_homogeneity_result','sold_object','product_subtype','audience','positive_signals','identity_signals','exclusion_signals','phh_category_id','phh_category_title','phh_parent_path','rejected_alternatives','decision_basis','confidence','status','source_version']
    library=[]
    for r in baseline_lib:
        library.append({
            'rule_id':r.get('rule_id',''),'family_key':r.get('family_key',''),'brand':'','family_name':r.get('product_family_name',''),'member_count':r.get('member_count',''),
            'covered_skus':r.get('covered_skus',''),'covered_eans':'','shopify_product_id':'','identity_gate_result':'PRESERVED_PRIOR_RULE','family_homogeneity_result':'PRESERVED_PRIOR_RULE',
            'sold_object':r.get('product_type_definition',''),'product_subtype':'','audience':'','positive_signals':r.get('required_positive_signals',''),'identity_signals':r.get('required_identity_signals',''),
            'exclusion_signals':r.get('exclusion_signals',''),'phh_category_id':r.get('phh_category_id',''),'phh_category_title':r.get('phh_category_title',''),'phh_parent_path':r.get('phh_parent_path',''),
            'rejected_alternatives':r.get('rejected_alternatives',''),'decision_basis':r.get('decision_basis',''),'confidence':r.get('confidence',''),'status':r.get('review_status',''),'source_version':'prior-v6-v7'
        })
    new_rule={k:RULE.get(k,'') for k in lib_fields}
    new_rule.update({'covered_skus':'|'.join(v3._norm(x.get('220_sku')) for x in members),'covered_eans':'|'.join(v3._norm(x.get('220_ean')) for x in members),
                     'identity_gate_result':'PASS','family_homogeneity_result':'PASS','phh_category_title':v3._norm(cat.get('title_en')),
                     'phh_parent_path':v7._path(by_cat,RULE['phh_category_id']),'confidence':f"{RULE['confidence']:.6f}",'status':'ACCEPTED','source_version':'v8'})
    library.append(new_rule)

    adjud=[]; priority=[]; identity_audit=[]; dispositions=Counter(); ean_counts=Counter()
    for r in probe_rows:
        key=r['family_key']; ident=r['identity']; ean_counts.update(ident.get('ean_counts') or {})
        priority.append({'rank':r['rank'],'family_key':key,'member_count':r['member_count'],'brand':r['brand'],'title':' || '.join(r.get('titles') or []),'product_types':'|'.join(r.get('product_types') or []),
                         'shopify_product_ids':'|'.join(r.get('shopify_product_ids') or []),'identity_gate_result':ident.get('identity_gate_result'),'exact_sku_set':str(bool(ident.get('exact_sku_set'))).upper(),
                         'family_homogeneity_result':ident.get('family_homogeneity_result'),'audience_evidence':'|'.join(r.get('audience_evidence') or []),'object_terms':'|'.join(r.get('object_terms') or [])})
        identity_audit.append({'rank':r['rank'],'family_key':key,'member_count':r['member_count'],'brand':r['brand'],'identity_gate_result':ident.get('identity_gate_result'),
                               'canonical_unique':ident.get('canonical_unique'),'exact_sku_set':ident.get('exact_sku_set'),'master_sku_count':ident.get('master_sku_count'),'live_sku_count':ident.get('live_sku_count'),
                               'missing_live_skus':ident.get('missing_live_skus'),'extra_live_skus':ident.get('extra_live_skus'),'family_homogeneity_result':ident.get('family_homogeneity_result'),
                               'ean_counts':ident.get('ean_counts'),'ean_rows':ident.get('ean_rows')})
        if key==RULE['family_key']:
            disp='ACCEPTED'; reason=RULE['decision_basis']; cid=RULE['phh_category_id']
        elif key=='CURRENT_GROUP:0040':
            disp='UNRESOLVED'; reason="identity and men hoodie object proven, but no exact live PHH men's hoodie leaf was proven; 5720 Sweaters for men is not forced as a substitute"; cid=''
        elif key=='CURRENT_GROUP:0056':
            disp='UNRESOLVED'; reason='identity and trousers object proven, but gender/audience is unproven while PHH separates men/women/children'; cid=''
        else:
            disp='UNRESOLVED'; reason='identity gate and/or family evidence insufficient for exact reusable inheritance'; cid=''
        dispositions[disp]+=1
        adjud.append({'rank':r['rank'],'family_key':key,'member_count':r['member_count'],'brand':r['brand'],'disposition':disp,'phh_category_id':cid,
                      'identity_gate_result':ident.get('identity_gate_result'),'family_homogeneity_result':ident.get('family_homogeneity_result'),'audience_evidence':'|'.join(r.get('audience_evidence') or []),
                      'sold_object_terms':'|'.join(r.get('object_terms') or []),'reason':reason})

    mapping=[]; counts=Counter(); removed_no=removed_review=blocked_inc=0; covered=0
    map_fields=list(baseline_rows[0].keys())+[x for x in ['v8_rule_id','v8_family_rule_status','v8_decision_source','v8_decision_basis'] if x not in baseline_rows[0]]
    accepted_keys={(v3._norm(x.get('220_sku')),v3._norm(x.get('220_ean'))) for x in members}
    for i in ids:
        k=(i['220_sku'],i['220_ean']); old=dict(baseline.get(k) or {})
        if not old: raise RuntimeError('v7b baseline mapping missing '+repr(k))
        prior=old.get('status') or 'NO_MATCH'; status=prior; rid=''; source='v7b_baseline'; basis='unchanged from v7b'
        if k in accepted_keys:
            rid=RULE['rule_id']; source='accepted_v8_exact_family_rule'; basis=RULE['decision_basis']; covered+=1
            old['selected_category_id']=RULE['phh_category_id']; old['selected_category_name']=v3._norm(cat.get('title_en')); old['confidence']=f"{RULE['confidence']:.6f}"
            p,v=enriched[k]; opts=v3._selected_options(i,v); req=required.get(RULE['phh_category_id']) or []; missing=[]; identified=[]
            for a in req:
                src_status,src,val=v3._attribute_source(a,i,p,v,opts)
                (identified if src_status.startswith('SOURCE_IDENTIFIED') else missing).append(str(a.get('field_id')))
            old['required_field_ids']='|'.join(str(a.get('field_id')) for a in req); old['source_identified_field_ids']='|'.join(identified); old['missing_required_field_ids']='|'.join(missing)
            status='BLOCKED_ATTRIBUTES' if missing else 'AUTO'
            if prior=='NO_MATCH': removed_no+=1
            if prior=='REVIEW': removed_review+=1
            if prior!='BLOCKED_ATTRIBUTES' and status=='BLOCKED_ATTRIBUTES': blocked_inc+=1
        old['status']=status; old['v8_rule_id']=rid; old['v8_family_rule_status']='ACCEPTED' if rid else ''; old['v8_decision_source']=source; old['v8_decision_basis']=basis
        old['master_write']='NO'; old['phh_write']='NO'; old['shopify_write']='NO'; mapping.append(old); counts[status]+=1

    mapping_bytes=_csv(mapping,map_fields); ds=hashlib.sha256(mapping_bytes).hexdigest()
    accepted_sizes=[RULE['member_count']]
    summary={'status':'PASS','mapping_version':'targeted-family-leaf-adjudication-v8','products_total':1671,'families_total':914,
             'baseline_product_statuses':{'AUTO':0,'BLOCKED_ATTRIBUTES':67,'REVIEW':77,'BLOCKED_TAXONOMY':0,'NO_MATCH':1527},
             'families_investigated':len(probe_rows),'FHM_families_investigated':probe_summary.get('FHM_families_examined',0),'other_apparel_families_investigated':probe_summary.get('other_apparel_families_examined',0),
             'rules_ACCEPTED':1,'rules_REVIEW':0,'rules_UNRESOLVED_REJECTED':len(probe_rows)-1,'new_multi_sku_rules':1,'new_rule_sku_coverage':covered,
             'largest_rule_coverage':max(accepted_sizes),'median_rule_coverage':statistics.median(accepted_sizes),'identity_gate_counts':probe_summary.get('identity_gate_counts',{}),
             'ean_relationship_counts':dict(ean_counts),'canonical_conflicts_accepted_members':0,'final_product_statuses':dict(counts),'NO_MATCH_removed_vs_v7b':removed_no,'REVIEW_removed_vs_v7b':removed_review,
             'BLOCKED_ATTRIBUTES_increase_vs_v7b':blocked_inc,'taxonomy_categories_fetched':tax_summary.get('categories_fetched'),'dataset_hash':ds,
             'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','stock_changes':0,'price_changes':0},
             'principle':'reuse proven identity model; adjudicate exact leafs; scale evidence, not similarity'}
    rule_audit={'status':'PASS','rule_id':RULE['rule_id'],'family_key':RULE['family_key'],'validation_errors':errors,'validation_evidence':evidence,'rule':new_rule,'rejected_alternatives':RULE['rejected_alternatives'],'safety':summary['safety']}
    id_obj={'status':'PASS','summary':{'families':len(identity_audit),'identity_gate_counts':probe_summary.get('identity_gate_counts',{}),'ean_relationship_counts':dict(ean_counts),'accepted_canonical_conflicts':0},'families':identity_audit,'safety':summary['safety']}

    priority_fields=['rank','family_key','member_count','brand','title','product_types','shopify_product_ids','identity_gate_result','exact_sku_set','family_homogeneity_result','audience_evidence','object_terms']
    adjud_fields=['rank','family_key','member_count','brand','disposition','phh_category_id','identity_gate_result','family_homogeneity_result','audience_evidence','sold_object_terms','reason']
    artifacts={
        'category_rule_library_v1.csv':_csv(library,lib_fields),
        'v8-family-priority-queue.csv':_csv(priority,priority_fields),
        'v8-family-adjudication.csv':_csv(adjud,adjud_fields),
        'v8-product-category-mapping.csv':mapping_bytes,
        'v8-category-coverage-summary.json':json.dumps(summary,indent=2,sort_keys=True).encode(),
        'v8-identity-audit.json':json.dumps(id_obj,indent=2,sort_keys=True).encode(),
        'v8-rule-audit.json':json.dumps(rule_audit,indent=2,sort_keys=True).encode(),
    }
    _persist(db,summary,artifacts)
    print('CATEGORY_RULE_AUDIT_V8_RESULT '+json.dumps(summary,sort_keys=True),flush=True)
    print('V8_RULE_AUDIT '+json.dumps(rule_audit,sort_keys=True),flush=True)
    return summary,artifacts
