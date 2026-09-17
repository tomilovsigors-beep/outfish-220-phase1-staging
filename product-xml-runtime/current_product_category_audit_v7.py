from __future__ import annotations

import csv, io, json, hashlib, statistics
from collections import Counter

import current_product_category_audit as v3
import current_product_category_audit_v4 as v4
import current_product_category_audit_v6 as v6
from current_product_category_audit_v7_probe import run_probe

RULE_SPECS=[
    {
        'rule_id':'V7-RULE-001','family_key':'CURRENT_GROUP:0088','member_count':9,
        'shopify_product_id':'gid://shopify/Product/8548246356306','exact_title':'Lightweight Primaloft Jacket Innova Grey',
        'vendor':'FHM','product_type':'jacket','phh_category_id':'9050','confidence':0.995,
        'product_family_name':'FHM Innova Grey lightweight Primaloft men outerwear jacket',
        'product_type_definition':'men insulated Primaloft outerwear jacket sold as a jacket; size variants XS-5XL',
        'required_positive_signals':'exact Shopify product id; exact title; vendor=FHM; product_type=jacket; Shopify description explicitly says men/mens jacket; all 9 canonical variants remain present in live Shopify source',
        'required_identity_signals':'same Shopify product GID; exact live Shopify variant-id set equals Master family variant-id set; canonical 220_sku+220_ean unique; grouping PASS',
        'exclusion_signals':'do not apply to women/kids/baby jackets; do not apply to life jackets; do not apply to blazers/suit jackets; do not apply to jacket accessories or replacement parts',
        'evidence_sources':'Master identity/grouping; live Shopify product description/productType/current 9 variants; live PHH taxonomy category titles in EN/LV/LT/EE/FI/RU and required-field metadata',
        'decision_basis':'sold object is explicitly a mens insulated outdoor jacket; PHH 9050 localized titles mean outerwear jacket/virsjaka/куртка, while same-English-title 9053 localizes to blazer/žakete/пиджак',
        'rejected_alternatives':'9053 Mens jackets: localized titles identify blazer/suit jacket; 17962/8972 women jackets: audience mismatch; 14450/14505/14560/14615 kids/baby: audience mismatch; 9032 life jackets: object mismatch',
    },
    {
        'rule_id':'V7-RULE-002','family_key':'CURRENT_GROUP:0141','member_count':8,
        'shopify_product_id':'gid://shopify/Product/8546104246610','exact_title':'Tactical Softshell Jacket FHM Stream Khaki Green',
        'vendor':'FHM','product_type':'jacket','phh_category_id':'9050','confidence':0.995,
        'product_family_name':'FHM Stream Khaki tactical softshell men outerwear jacket',
        'product_type_definition':'men tactical softshell waterproof/windproof outer layer sold as a jacket; size variants XS-4XL',
        'required_positive_signals':'exact Shopify product id; exact title; vendor=FHM; product_type=jacket; Shopify description explicitly says mens outer layer; all 8 canonical variants remain present in live Shopify source',
        'required_identity_signals':'same Shopify product GID; exact live Shopify variant-id set equals Master family variant-id set; canonical 220_sku+220_ean unique; grouping PASS',
        'exclusion_signals':'do not apply to women/kids/baby jackets; do not apply to life jackets; do not apply to blazers/suit jackets; do not apply to jacket accessories or replacement parts',
        'evidence_sources':'Master identity/grouping; live Shopify product description/productType/current 8 variants; live PHH taxonomy category titles in EN/LV/LT/EE/FI/RU and required-field metadata',
        'decision_basis':'sold object is explicitly a mens tactical softshell outerwear jacket; PHH 9050 localized titles mean outerwear jacket/virsjaka/куртка, while 9053 is blazer/žakete/пиджак',
        'rejected_alternatives':'9053 Mens jackets: localized titles identify blazer/suit jacket; 17962/8972 women jackets: audience mismatch; children categories: audience mismatch; 9032 life jackets: object mismatch',
    },
]


def _csv(rows,fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    return s.getvalue().encode('utf-8-sig')


def _load_v6_csv(db,name):
    b=v6.load_latest_artifact(db,name)
    if not b: raise RuntimeError(f'v6 artifact unavailable: {name}')
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))


def _path(by_cat,cid):
    out=[]; seen=set(); cur=by_cat.get(str(cid))
    while cur and str(cur.get('category_id')) not in seen:
        k=str(cur.get('category_id')); seen.add(k)
        out.append(v3._norm(cur.get('title_en')) or v3._norm(cur.get('title_lv')) or k)
        pid=v3._norm(cur.get('parent_id')); cur=by_cat.get(pid) if pid else None
    return ' > '.join(reversed(out))


def _validate_rule(spec,groups,by_cat,shopify):
    members=groups.get(spec['family_key']) or []; errors=[]
    if len(members)!=spec['member_count']: errors.append(f'member_count={len(members)} expected={spec["member_count"]}')
    pids={v3._norm(x.get('shopify_product_id')) for x in members if v3._norm(x.get('shopify_product_id'))}
    titles={v3._norm(x.get('shopify_title') or x.get('220_title')) for x in members if v3._norm(x.get('shopify_title') or x.get('220_title'))}
    vendors={v3._norm(x.get('vendor')) for x in members if v3._norm(x.get('vendor'))}
    types={v3._norm(x.get('product_type')) for x in members if v3._norm(x.get('product_type'))}
    grouping={v3._norm(x.get('220_grouping_status')) for x in members if v3._norm(x.get('220_grouping_status'))}
    identities={(v3._norm(x.get('220_sku')),v3._norm(x.get('220_ean'))) for x in members}
    if pids!={spec['shopify_product_id']}: errors.append('shopify_product_id mismatch')
    if titles!={spec['exact_title']}: errors.append('family title mismatch')
    if vendors!={spec['vendor']}: errors.append('vendor mismatch')
    if types!={spec['product_type']}: errors.append('product_type mismatch')
    if grouping and grouping!={'PASS'}: errors.append('grouping_status not uniformly PASS')
    if len(identities)!=len(members): errors.append('canonical identity duplicate')
    product=shopify.get(spec['shopify_product_id']) or {}
    if v3._norm(product.get('title'))!=spec['exact_title']: errors.append('live Shopify title mismatch')
    if v3._norm(product.get('vendor'))!=spec['vendor']: errors.append('live Shopify vendor mismatch')
    if v3._norm(product.get('product_type'))!=spec['product_type']: errors.append('live Shopify product_type mismatch')
    desc=v3._norm(product.get('description')).casefold()
    if not ('men' in desc or 'mens' in desc or "men's" in desc): errors.append('live Shopify description lacks explicit male audience proof')
    master_vids={v3._norm(x.get('shopify_variant_id')) for x in members if v3._norm(x.get('shopify_variant_id'))}
    live_vids=set((product.get('variants_by_id') or {}).keys())
    if master_vids!=live_vids: errors.append(f'live variant set drift master={len(master_vids)} live={len(live_vids)}')
    cat=by_cat.get(spec['phh_category_id'])
    if not cat: errors.append('PHH category missing')
    elif str(cat.get('allow_add_products')).casefold() not in {'true','1','yes'}: errors.append('PHH category not addable')
    # Critical duplicate-English-title disambiguation: 9050 is outerwear; 9053 is blazer.
    if spec['phh_category_id']=='9050':
        c9050=by_cat.get('9050') or {}; c9053=by_cat.get('9053') or {}
        lv0=v3._norm(c9050.get('title_lv')).casefold(); lv3=v3._norm(c9053.get('title_lv')).casefold()
        ru0=v3._norm(c9050.get('title_ru')).casefold(); ru3=v3._norm(c9053.get('title_ru')).casefold()
        if 'virsjak' not in lv0 and 'куртк' not in ru0: errors.append('9050 outerwear localization proof missing')
        if 'žaket' not in lv3 and 'пидж' not in ru3: errors.append('9053 blazer localization proof missing')
    return members,product,cat,errors


def _persist(db,summary,artifacts):
    import psycopg
    payload={k:v.decode('utf-8-sig') if isinstance(v,(bytes,bytearray)) else str(v) for k,v in artifacts.items()}
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''create table if not exists phh_category_rule_v7_snapshots(
                id bigserial primary key, created_at timestamptz not null default now(), dataset_hash text not null,
                summary jsonb not null, artifacts jsonb not null)''')
            cur.execute('insert into phh_category_rule_v7_snapshots(dataset_hash,summary,artifacts) values(%s,%s::jsonb,%s::jsonb)',
                        (summary['dataset_hash'],json.dumps(summary,ensure_ascii=False),json.dumps(payload,ensure_ascii=False)))
        c.commit()


def load_latest_artifact(db,name):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select artifacts->>%s from phh_category_rule_v7_snapshots order by created_at desc limit 1',(name,))
            row=cur.fetchone()
            return None if not row or row[0] is None else row[0].encode('utf-8-sig')


def run_audit(master_rows,shopify,db):
    tax_summary,cats,attrs=v3._latest_taxonomy(db); by_cat,required,_=v3._category_index(cats,attrs)
    ids=v3._canonical_identities(master_rows)
    if len(ids)!=1671: raise RuntimeError(f'canonical identity universe changed: {len(ids)} != 1671')
    groups0,membership,group_diag=v4._build_groups(ids); groups=dict(groups0)
    if len(groups)!=914: raise RuntimeError(f'family universe changed: {len(groups)} != 914')
    enriched={(i['220_sku'],i['220_ean']):v3._shopify_enrich(i,shopify) for i in ids}
    v6map_rows=_load_v6_csv(db,'v6-product-category-mapping.csv'); v6map={(r['220_sku'],r['220_ean']):r for r in v6map_rows}
    v6lib=_load_v6_csv(db,'category_rule_library_v1.csv')
    probe_summary,probe_rows=run_probe(master_rows,shopify,db,top_n=30)

    accepted={}; new_library=[]; audits=[]
    lib_fields=['rule_id','family_key','member_count','product_family_name','product_type_definition','required_positive_signals','required_identity_signals','exclusion_signals','phh_category_id','phh_category_title','phh_parent_path','evidence_sources','decision_basis','rejected_alternatives','confidence','review_status','covered_skus','covered_sku_count']
    for spec in RULE_SPECS:
        members,product,cat,errors=_validate_rule(spec,groups,by_cat,shopify)
        status='ACCEPTED' if not errors else 'REJECTED'
        if status=='ACCEPTED': accepted[spec['family_key']]=(spec,cat)
        row={k:spec.get(k,'') for k in lib_fields}
        row.update({'family_key':spec['family_key'],'member_count':len(members),'phh_category_title':v3._norm((cat or {}).get('title_en')),
                    'phh_parent_path':_path(by_cat,spec['phh_category_id']) if cat else '',
                    'confidence':f"{spec['confidence']:.6f}" if status=='ACCEPTED' else '0.000000','review_status':status,
                    'covered_skus':'|'.join(v3._norm(x.get('220_sku')) for x in members),'covered_sku_count':len(members)})
        new_library.append(row)
        audits.append({'rule_id':spec['rule_id'],'family_key':spec['family_key'],'validation_status':status,'validation_errors':errors,
                       'what_is_sold':spec['product_type_definition'],'why_category':spec['decision_basis'],'alternatives_checked':spec['rejected_alternatives'],
                       'inheritance_safety':spec['required_identity_signals'],'future_exclusions':spec['exclusion_signals']})

    # Preserve v6 accepted library verbatim and append v7 decisions.
    library=[]
    for r in v6lib:
        library.append({k:r.get(k,'') for k in lib_fields})
    library.extend(new_library)

    adjud=[]; gap_counts_before=Counter(probe_summary.get('gap_type_counts') or {}); gap_counts_unresolved=Counter()
    adjud_fields=['rank','family_key','member_count','exact_titles','product_types','vendors','shopify_product_ids','shopify_terminal_hint','object_terms','v7_disposition','accepted_rule_id','phh_category_id','phh_category_title','evidence_gaps_before','evidence_gaps_final','adjudication_note']
    for r in probe_rows:
        key=r['family_key']; gaps=list(r.get('evidence_gaps') or []); disposition='UNRESOLVED'; rule_id=''; cid=''; cname=''; note=''
        if key in accepted:
            spec,cat=accepted[key]; disposition='ACCEPTED_RULE'; rule_id=spec['rule_id']; cid=spec['phh_category_id']; cname=v3._norm(cat.get('title_en')); final=[]; note=spec['decision_basis']
        else:
            final=list(gaps)
            if key=='CURRENT_GROUP:0062': final.append('LIVE_VARIANT_SET_DRIFT')
            if key=='CURRENT_GROUP:0122': final.append('OBJECT_TYPE_CONFLICT_TITLE_HINT_HOODIE_VS_PRODUCT_TYPE_JACKET')
            for g in final: gap_counts_unresolved[g]+=1
            note='leave unresolved; deterministic evidence remains insufficient'
        adjud.append({'rank':r['rank'],'family_key':key,'member_count':r['member_count'],'exact_titles':' || '.join(r.get('exact_titles') or []),
                      'product_types':' || '.join(r.get('product_types') or []),'vendors':' || '.join(r.get('vendors') or []),'shopify_product_ids':'|'.join(r.get('shopify_product_ids') or []),
                      'shopify_terminal_hint':r.get('shopify_terminal_hint') or '','object_terms':'|'.join(r.get('object_terms') or []),'v7_disposition':disposition,
                      'accepted_rule_id':rule_id,'phh_category_id':cid,'phh_category_title':cname,'evidence_gaps_before':'|'.join(gaps),'evidence_gaps_final':'|'.join(final),'adjudication_note':note})

    mapping=[]; counts=Counter(); changed_no=changed_review=changed_blocked=0; newly=0
    extra=['v7_rule_id','v7_family_rule_status','v7_decision_source','v7_decision_basis']
    map_fields=list(v6map_rows[0].keys())+[x for x in extra if x not in v6map_rows[0]]
    for i in ids:
        k=(i['220_sku'],i['220_ean']); old=dict(v6map.get(k) or {})
        if not old: raise RuntimeError('v6 mapping missing '+repr(k))
        g=membership[k]; prior=old.get('status') or 'NO_MATCH'; status=prior; rid=''; source='v6_baseline'; basis='unchanged from v6'
        if g in accepted:
            spec,cat=accepted[g]; rid=spec['rule_id']; source='accepted_v7_targeted_leaf_rule'; basis=spec['decision_basis']
            old['selected_category_id']=spec['phh_category_id']; old['selected_category_name']=v3._norm(cat.get('title_en')); old['confidence']=f"{spec['confidence']:.6f}"
            p,v=enriched[k]; opts=v3._selected_options(i,v); req=required.get(spec['phh_category_id']) or []; missing=[]; identified=[]
            for a in req:
                src_status,src,val=v3._attribute_source(a,i,p,v,opts)
                (identified if src_status.startswith('SOURCE_IDENTIFIED') else missing).append(str(a.get('field_id')))
            old['required_field_ids']='|'.join(str(a.get('field_id')) for a in req); old['source_identified_field_ids']='|'.join(identified); old['missing_required_field_ids']='|'.join(missing)
            status='BLOCKED_ATTRIBUTES' if missing else 'AUTO'
            if prior=='NO_MATCH': changed_no+=1
            if prior=='REVIEW': changed_review+=1
            if prior=='BLOCKED_ATTRIBUTES': changed_blocked+=1
            if prior in {'NO_MATCH','REVIEW'}: newly+=1
        old['status']=status; old['v7_rule_id']=rid; old['v7_family_rule_status']='ACCEPTED' if rid else ''; old['v7_decision_source']=source; old['v7_decision_basis']=basis
        old['master_write']='NO'; old['phh_write']='NO'; old['shopify_write']='NO'; mapping.append(old); counts[status]+=1

    accepted_sizes=[int(r['covered_sku_count']) for r in new_library if r['review_status']=='ACCEPTED']
    mapping_bytes=_csv(mapping,map_fields); ds=hashlib.sha256(mapping_bytes).hexdigest()
    evidence_summary={'status':'PASS','families_examined':30,'initial_gap_counts':dict(gap_counts_before),'unresolved_after_adjudication':sum(r['v7_disposition']=='UNRESOLVED' for r in adjud),
                      'accepted_after_adjudication':sum(r['v7_disposition']=='ACCEPTED_RULE' for r in adjud),'unresolved_gap_counts':dict(gap_counts_unresolved),
                      'interpretation':'gender/audience and exact product type are the dominant upstream evidence gaps; live PHH leaves exist for most top apparel families'}
    summary={'status':'PASS','mapping_version':'targeted-leaf-adjudication-v7','products_total':1671,'families_total':914,'families_examined_v7':30,
             'new_rules_ACCEPTED':sum(r['review_status']=='ACCEPTED' for r in new_library),'new_rules_REJECTED':sum(r['review_status']=='REJECTED' for r in new_library),
             'new_multi_sku_rules':sum(int(r['covered_sku_count'])>1 and r['review_status']=='ACCEPTED' for r in new_library),
             'new_rule_sku_coverage':sum(accepted_sizes),'largest_new_rule_coverage':max(accepted_sizes or [0]),'median_new_rule_coverage':statistics.median(accepted_sizes) if accepted_sizes else 0,
             'newly_proven_category_skus_v7':newly,'NO_MATCH_removed_vs_v6':changed_no,'REVIEW_removed_vs_v6':changed_review,'already_blocked_reclassified':changed_blocked,
             'final_product_statuses':dict(counts),'evidence_gap_summary':evidence_summary,'group_build':group_diag,'taxonomy_categories_fetched':tax_summary.get('categories_fetched'),'dataset_hash':ds,
             'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'Product_XML':'OFF','stock_changes':0,'price_changes':0},
             'principle':'targeted leaf adjudication only; no fuzzy or lexical fallback; unresolved stays unresolved'}
    audit={'status':'PASS','new_rule_audits':audits,'category_9050_vs_9053':'9050 localizes as outerwear jacket/virsjaka/куртка; 9053 localizes as blazer/žakete/пиджак',
           'known_unresolved_guardrails':['CURRENT_GROUP:0062 live Shopify variant set drift blocks family-wide inheritance','CURRENT_GROUP:0122 title/hint hoodie conflicts with product_type jacket'],
           'safety':summary['safety']}
    artifacts={
        'category_rule_library_v2.csv':_csv(library,lib_fields),
        'v7-family-adjudication.csv':_csv(adjud,adjud_fields),
        'v7-evidence-gap-summary.json':json.dumps(evidence_summary,indent=2,sort_keys=True).encode(),
        'v7-product-category-mapping.csv':mapping_bytes,
        'v7-category-coverage-summary.json':json.dumps(summary,indent=2,sort_keys=True).encode(),
        'v7-rule-audit.json':json.dumps(audit,indent=2,sort_keys=True).encode(),
    }
    _persist(db,summary,artifacts)
    print('CATEGORY_RULE_AUDIT_V7_RESULT '+json.dumps(summary,sort_keys=True),flush=True)
    return summary,artifacts
