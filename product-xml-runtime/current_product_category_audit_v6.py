from __future__ import annotations

import csv, io, json, hashlib, statistics
from collections import Counter

import current_product_category_audit as v3
import current_product_category_audit_v4 as v4
import current_product_category_audit_v5 as v5

BASELINE={'AUTO':0,'BLOCKED_ATTRIBUTES':43,'REVIEW':84,'BLOCKED_TAXONOMY':0,'NO_MATCH':1544}

RULE_SPECS=[{
    'rule_id':'V6-RULE-001',
    'expected_family_key':'CURRENT_GROUP:0174',
    'shopify_product_id':'gid://shopify/Product/9780060193106',
    'exact_title':'Women Winter Rubber Boots Dry Walker S-TRACK black EVA',
    'vendor':'Dry Walker',
    'product_type':'Rubber Boots',
    'expected_member_count':7,
    'phh_category_id':'5669',
    'product_family_name':'Dry Walker S-TRACK women winter rubber boots black EVA',
    'product_type_definition':'women winter waterproof rubber boots sold as footwear; size variants 36-42',
    'required_positive_signals':'exact Shopify product id; exact family title; vendor=Dry Walker; product_type=Rubber Boots; all members are size variants; title explicitly says Women',
    'required_identity_signals':'same Shopify product GID; canonical 220_sku+220_ean for every member; grouping PASS',
    'exclusion_signals':'do not apply to men/kids boots; do not apply to liners/insoles/accessories/replacement parts; do not apply to generic boots without rubber-boot evidence',
    'evidence_sources':'Master exact product/variant links; authoritative 220_title; Shopify live product title/vendor/productType/tags/7 variants; official PHH category list; live PHH taxonomy existence/addability check',
    'decision_basis':'sold object is explicitly women rubber boots; PHH has a dedicated women rubber-boots leaf, which is more specific than generic women boots',
    'rejected_alternatives':'21778 Rubber boots for men: gender mismatch; 2018 Women\'s boots: less specific because dedicated women rubber-boots leaf exists; 6749 Men\'s boots: gender mismatch',
    'confidence':0.995,
}]

SEMANTIC_TRAPS=('wind protection wall','bivy bag','mosquito net for tent','hammock mosquito net','blanket','ponco','poncho','adapter','replacement','cover','accessory')


def _csv(rows,fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    return s.getvalue().encode('utf-8-sig')


def _load_csv(db,name):
    b=v5.load_latest_artifact(db,name)
    if not b: raise RuntimeError(f'v5 artifact unavailable: {name}')
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))


def _path(by_cat,cid):
    out=[]; seen=set(); cur=by_cat.get(str(cid))
    while cur and str(cur.get('category_id')) not in seen:
        k=str(cur.get('category_id')); seen.add(k)
        out.append(v3._norm(cur.get('title_en')) or v3._norm(cur.get('title_lv')) or k)
        pid=v3._norm(cur.get('parent_id')); cur=by_cat.get(pid) if pid else None
    return ' > '.join(reversed(out))


def _evidence_score(r):
    score=0
    for f,w in [('representative_titles',2),('shopify_hint',3),('product_type',3),('brand',1)]:
        if v3._norm(r.get(f)): score+=w
    return score


def _validate_rule(spec,groups,by_cat):
    g=groups.get(spec['expected_family_key']) or []
    errors=[]
    if len(g)!=spec['expected_member_count']: errors.append(f'member_count={len(g)} expected={spec["expected_member_count"]}')
    pids={v3._norm(x.get('shopify_product_id')) for x in g}; pids.discard('')
    titles={v3._norm(x.get('shopify_title') or x.get('220_title')) for x in g}; titles.discard('')
    vendors={v3._norm(x.get('vendor')) for x in g}; vendors.discard('')
    types={v3._norm(x.get('product_type')) for x in g}; types.discard('')
    grouping={v3._norm(x.get('220_grouping_status')) for x in g}; grouping.discard('')
    if pids!={spec['shopify_product_id']}: errors.append('shopify_product_id mismatch')
    if titles!={spec['exact_title']}: errors.append('family title mismatch')
    if vendors!={spec['vendor']}: errors.append('vendor mismatch')
    if types!={spec['product_type']}: errors.append('product_type mismatch')
    if grouping and grouping!={'PASS'}: errors.append('grouping_status not uniformly PASS')
    if len({(v3._norm(x.get('220_sku')),v3._norm(x.get('220_ean'))) for x in g})!=len(g): errors.append('canonical identity duplicate')
    cat=by_cat.get(spec['phh_category_id'])
    if not cat: errors.append('PHH category missing from live taxonomy')
    elif str(cat.get('allow_add_products')).casefold() not in {'true','1','yes'}: errors.append('PHH category not addable')
    return g,cat,errors


def _persist(db,summary,artifacts):
    import psycopg
    payload={k:v.decode('utf-8-sig') for k,v in artifacts.items()}
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''create table if not exists phh_category_rule_v6_snapshots(
                id bigserial primary key, created_at timestamptz not null default now(), dataset_hash text not null,
                summary jsonb not null, artifacts jsonb not null)''')
            cur.execute('insert into phh_category_rule_v6_snapshots(dataset_hash,summary,artifacts) values(%s,%s::jsonb,%s::jsonb)',
                        (summary['dataset_hash'],json.dumps(summary,ensure_ascii=False),json.dumps(payload,ensure_ascii=False)))
        c.commit()


def load_latest_artifact(db,name):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select artifacts->>%s from phh_category_rule_v6_snapshots order by created_at desc limit 1',(name,))
            row=cur.fetchone()
            return None if not row or row[0] is None else row[0].encode('utf-8-sig')


def run_audit(master_rows,shopify,db):
    tax_summary,cats,attrs=v3._latest_taxonomy(db); by_cat,required,_=v3._category_index(cats,attrs)
    ids=v3._canonical_identities(master_rows)
    if len(ids)!=1671: raise RuntimeError(f'canonical identity universe changed: {len(ids)} != 1671')
    groups0,membership,group_diag=v4._build_groups(ids); groups=dict(groups0)
    if len(groups)!=914: raise RuntimeError(f'family universe changed: {len(groups)} != 914')
    enriched={(i['220_sku'],i['220_ean']):v3._shopify_enrich(i,shopify) for i in ids}
    v5reg=_load_csv(db,'family_category_registry.csv'); v5map_rows=_load_csv(db,'v5-product-category-mapping.csv')
    v5map={(r['220_sku'],r['220_ean']):r for r in v5map_rows}

    unresolved=[r for r in v5reg if r.get('status')=='UNRESOLVED']
    unresolved.sort(key=lambda r:(-int(r.get('member_count') or 0),-_evidence_score(r),r.get('family_key','')))
    v5review=[r for r in v5reg if r.get('status')=='REVIEW']
    investigate_keys=[]
    for r in unresolved[:40]+v5review:
        if r.get('family_key') and r['family_key'] not in investigate_keys: investigate_keys.append(r['family_key'])

    accepted={}; library=[]; audits=[]
    lib_fields=['rule_id','family_key','member_count','product_family_name','product_type_definition','required_positive_signals','required_identity_signals','exclusion_signals','phh_category_id','phh_category_title','phh_parent_path','evidence_sources','decision_basis','rejected_alternatives','confidence','review_status','covered_skus','covered_sku_count']
    for spec in RULE_SPECS:
        members,cat,errors=_validate_rule(spec,groups,by_cat)
        status='ACCEPTED' if not errors else 'REJECTED'
        if status=='ACCEPTED': accepted[spec['expected_family_key']]=(spec,cat)
        row={**{k:spec.get(k,'') for k in lib_fields},'family_key':spec['expected_family_key'],'member_count':len(members),
             'phh_category_title':v3._norm((cat or {}).get('title_en')),'phh_parent_path':_path(by_cat,spec['phh_category_id']) if cat else '',
             'confidence':f"{spec['confidence']:.6f}" if status=='ACCEPTED' else '0.000000','review_status':status,
             'covered_skus':'|'.join(v3._norm(x.get('220_sku')) for x in members),'covered_sku_count':len(members)}
        library.append(row)
        audits.append({'rule_id':spec['rule_id'],'family_key':spec['expected_family_key'],'validation_status':status,'validation_errors':errors,
                       'what_is_sold':spec['product_type_definition'],'source_evidence':spec['evidence_sources'],'why_category':spec['decision_basis'],
                       'alternatives_checked':spec['rejected_alternatives'],'inheritance_safety':spec['required_identity_signals'],'future_exclusions':spec['exclusion_signals']})

    priority=[]; review=[]
    pri_fields=['priority_rank','family_key','member_count','representative_titles','shopify_hint','product_type','brand','v5_status','evidence_score','semantic_trap','v4_candidates','v6_disposition','evidence_gap']
    reg_by={r.get('family_key'):r for r in v5reg}
    for rank,r in enumerate(unresolved,start=1):
        text=' '.join([r.get('representative_titles',''),r.get('product_type',''),r.get('shopify_hint','')]).casefold()
        trap='|'.join(x for x in SEMANTIC_TRAPS if x in text)
        disp='ACCEPTED_RULE' if r.get('family_key') in accepted else ('INVESTIGATED_UNRESOLVED' if r.get('family_key') in investigate_keys else 'QUEUED')
        gap='' if disp=='ACCEPTED_RULE' else 'exact sellable product type -> exact live PHH leaf not proven by deterministic evidence'
        row={'priority_rank':rank,'family_key':r.get('family_key'),'member_count':r.get('member_count'),'representative_titles':r.get('representative_titles'),
             'shopify_hint':r.get('shopify_hint'),'product_type':r.get('product_type'),'brand':r.get('brand'),'v5_status':'UNRESOLVED',
             'evidence_score':_evidence_score(r),'semantic_trap':trap,'v4_candidates':r.get('v4_candidates'),'v6_disposition':disp,'evidence_gap':gap}
        priority.append(row)
        if r.get('family_key') in investigate_keys: review.append(row)
    for r in v5review:
        text=' '.join([r.get('representative_titles',''),r.get('product_type',''),r.get('shopify_hint','')]).casefold()
        review.append({'priority_rank':'','family_key':r.get('family_key'),'member_count':r.get('member_count'),'representative_titles':r.get('representative_titles'),
             'shopify_hint':r.get('shopify_hint'),'product_type':r.get('product_type'),'brand':r.get('brand'),'v5_status':'REVIEW',
             'evidence_score':_evidence_score(r),'semantic_trap':'|'.join(x for x in SEMANTIC_TRAPS if x in text),'v4_candidates':r.get('v4_candidates'),
             'v6_disposition':'UNRESOLVED','evidence_gap':'v5 REVIEW adjudicated conservatively: no accepted deterministic rule in v6 evidence set'})

    mapping=[]; counts=Counter(); method=Counter(); changed_from_no=0; changed_from_review=0; newly_proven=0
    map_fields=list(v5map_rows[0].keys())+['v6_rule_id','v6_family_rule_status','v6_decision_source','v6_decision_basis']
    for i in ids:
        k=(i['220_sku'],i['220_ean']); old=dict(v5map.get(k) or {})
        if not old: raise RuntimeError('v5 mapping row missing for canonical identity '+repr(k))
        g=membership[k]; status=old.get('status') or 'NO_MATCH'; rule_id=''; source='v5_baseline'; basis='unchanged from v5'
        if g in accepted:
            spec,cat=accepted[g]; rule_id=spec['rule_id']; source='accepted_deterministic_family_rule'; basis=spec['decision_basis']
            old['selected_category_id']=spec['phh_category_id']; old['selected_category_name']=v3._norm(cat.get('title_en')); old['confidence']=f"{spec['confidence']:.6f}"
            p,v=enriched[k]; opts=v3._selected_options(i,v); req=required.get(spec['phh_category_id']) or []; missing=[]; identified=[]
            for a in req:
                src_status,src,val=v3._attribute_source(a,i,p,v,opts)
                (identified if src_status.startswith('SOURCE_IDENTIFIED') else missing).append(str(a.get('field_id')))
            old['required_field_ids']='|'.join(str(a.get('field_id')) for a in req)
            old['source_identified_field_ids']='|'.join(identified); old['missing_required_field_ids']='|'.join(missing)
            new_status='BLOCKED_ATTRIBUTES' if missing else 'AUTO'
            if status=='NO_MATCH': changed_from_no+=1
            if status=='REVIEW': changed_from_review+=1
            if status in {'NO_MATCH','REVIEW'}: newly_proven+=1
            status=new_status; method['accepted_multi_sku_rule']+=1
        old['status']=status; old['v6_rule_id']=rule_id; old['v6_family_rule_status']='ACCEPTED' if rule_id else ''
        old['v6_decision_source']=source; old['v6_decision_basis']=basis; old['master_write']='NO'; old['phh_write']='NO'; old['shopify_write']='NO'
        mapping.append(old); counts[status]+=1

    accepted_sizes=[int(r['covered_sku_count']) for r in library if r['review_status']=='ACCEPTED']
    multi=[n for n in accepted_sizes if n>1]; single=[n for n in accepted_sizes if n==1]
    map_bytes=_csv(mapping,map_fields); ds=hashlib.sha256(map_bytes).hexdigest()
    summary={'status':'PASS','mapping_version':'deterministic-category-rule-v6','products_total':1671,
             'baseline':{'v5_AUTO':0,'v5_BLOCKED_ATTRIBUTES':43,'v5_REVIEW':84,'v5_BLOCKED_TAXONOMY':0,'v5_NO_MATCH':1544},
             'families_total':914,'unresolved_families_input':881,'families_investigated_v6':len(set(investigate_keys)),
             'rules_ACCEPTED':sum(r['review_status']=='ACCEPTED' for r in library),'rules_REVIEW':sum(r['review_status']=='REVIEW' for r in library),'rules_REJECTED':sum(r['review_status']=='REJECTED' for r in library),
             'accepted_multi_sku_rules':len(multi),'accepted_singleton_rules':len(single),'sku_covered_by_accepted_rules_total':sum(accepted_sizes),
             'sku_covered_by_multi_sku_rules':sum(multi),'largest_accepted_rule_sku_coverage':max(accepted_sizes or [0]),
             'median_accepted_rule_coverage':statistics.median(accepted_sizes) if accepted_sizes else 0,
             'newly_proven_category_skus_v6':newly_proven,'NO_MATCH_removed_vs_v5':changed_from_no,'REVIEW_removed_vs_v5':changed_from_review,
             'final_product_statuses':dict(counts),'decision_method_counts':dict(method),'group_build':group_diag,
             'taxonomy_categories_fetched':tax_summary.get('categories_fetched'),'dataset_hash':ds,
             'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'Product_XML':'OFF','stock_changes':0,'price_changes':0},
             'principle':'DO NOT classify harder. PROVE reusable rules better.'}
    rule_audit={'status':'PASS','accepted_rule_audits':audits,'investigated_family_keys':investigate_keys,
                'semantic_guard':'accessory/cover/wall/bivy/adapter/replacement proximity never implies the main-product category',
                'attribute_policy':'required field IDs may be assessed; units/enums/types are not invented because live PHH category API does not expose them',
                'writes':{'master':0,'phh':0,'shopify':0,'stock':0,'price':0},'product_xml':'OFF'}
    artifacts={'category_rule_library_v1.csv':_csv(library,lib_fields),'v6-family-priority-queue.csv':_csv(priority,pri_fields),
               'v6-family-rule-review.csv':_csv(review,pri_fields),'v6-product-category-mapping.csv':map_bytes,
               'v6-category-coverage-summary.json':json.dumps(summary,ensure_ascii=False,sort_keys=True,indent=2).encode(),
               'v6-rule-audit.json':json.dumps(rule_audit,ensure_ascii=False,sort_keys=True,indent=2).encode()}
    _persist(db,summary,artifacts)
    return summary,artifacts
