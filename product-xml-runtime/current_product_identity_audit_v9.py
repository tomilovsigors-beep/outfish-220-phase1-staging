from __future__ import annotations

import csv, io, json, os, re, hashlib, time
from collections import Counter, defaultdict

import requests
import current_product_category_audit as v3
import current_product_category_audit_v4 as v4
import current_product_category_audit_v5 as v5
import current_product_category_audit_v8 as v8
from current_product_category_audit_v8_probe import _is_apparel, AUDIENCE_WORDS

TOP_N=30
SPECIAL={'CURRENT_GROUP:0040','CURRENT_GROUP:0056'}
BASELINE={'total_products':1671,'AUTO':0,'BLOCKED_ATTRIBUTES':76,'REVIEW':77,'BLOCKED_TAXONOMY':0,'NO_MATCH':1518}
GAP_CODES={
'MISSING_SHOPIFY_PRODUCT_LINK','MISSING_VARIANT_ID','MISSING_SHOPIFY_SKU','MISSING_SHOPIFY_BARCODE','SKU_SET_INCOMPLETE','SKU_SET_CONFLICT','DUPLICATE_SKU','CANONICAL_IDENTITY_CONFLICT','AMBIGUOUS_PRODUCT_MEMBERSHIP','FAMILY_NOT_HOMOGENEOUS','AUDIENCE_UNKNOWN','PRODUCT_TYPE_UNKNOWN','SUBTYPE_UNKNOWN','LIVE_PRODUCT_NOT_FOUND','HISTORICAL_ONLY_EVIDENCE','OTHER'}


def _csv(rows, fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    return s.getvalue().encode('utf-8-sig')


def _registry(db):
    b=v5.load_latest_artifact(db,'family_category_registry.csv')
    if not b: raise RuntimeError('family_category_registry.csv unavailable')
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))


def _graphql(token, domain, query, variables):
    r=requests.post(f'https://{domain}/admin/api/2026-07/graphql.json',headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},json={'query':query,'variables':variables},timeout=60)
    r.raise_for_status(); p=r.json()
    if p.get('errors'): raise RuntimeError('Shopify GraphQL errors: '+json.dumps(p['errors'],ensure_ascii=False)[:2500])
    return p.get('data') or {}

VARIANT_QUERY='''query V9VariantBySku($q:String!){productVariants(first:20,query:$q){nodes{id sku barcode title selectedOptions{name value} product{id title vendor productType tags status category{name fullName}}}}}'''
PRODUCT_QUERY='''query V9Products($ids:[ID!]!){nodes(ids:$ids){... on Product{id title vendor productType tags status category{name fullName} variants(first:250){nodes{id sku barcode title selectedOptions{name value}}}}}}'''


def _lookup_skus(token,domain,skus):
    out={}; ambiguous={}
    for sku in skus:
        data=_graphql(token,domain,VARIANT_QUERY,{'q':f'sku:{sku}'})
        nodes=(data.get('productVariants') or {}).get('nodes') or []
        exact=[n for n in nodes if v3._norm(n.get('sku'))==sku]
        if len(exact)==1: out[sku]=exact[0]
        elif len(exact)>1: ambiguous[sku]=exact
        time.sleep(0.03)
    return out,ambiguous


def _fetch_products(token,domain,ids):
    out={}
    for i in range(0,len(ids),50):
        data=_graphql(token,domain,PRODUCT_QUERY,{'ids':ids[i:i+50]})
        for n in data.get('nodes') or []:
            if n and n.get('id'): out[n['id']]=n
    return out


def _audience(product):
    txt=' '.join([v3._norm(product.get('title')),v3._norm(product.get('productType')),v3._norm(((product.get('category') or {}).get('fullName'))), ' '.join(product.get('tags') or [])]).casefold()
    found=[]
    for label,words in AUDIENCE_WORDS.items():
        if any(re.search(r'(?<![a-z])'+re.escape(w)+r'(?![a-z])',txt) for w in words): found.append(label)
    return found


def _ean_class(master_ean,barcode,brand):
    me=v3._norm(master_ean); bc=v3._norm(barcode)
    if not me: return 'MISSING_MASTER_EAN'
    if not bc: return 'MISSING_SHOPIFY_BARCODE'
    if me==bc: return 'EXACT'
    if brand.casefold()=='fhm' and bc.startswith('46') and me.startswith('475'): return 'LEGACY_46_TO_475_CANDIDATE'
    if bc.startswith('475') and me.startswith('475'): return 'DIFFERENT_475'
    return 'OTHER_MISMATCH'


def _homogeneity(members,product):
    titles={v3._norm(x.get('220_title') or x.get('shopify_title')) for x in members if v3._norm(x.get('220_title') or x.get('shopify_title'))}
    vendors={v3._norm(x.get('vendor')) for x in members if v3._norm(x.get('vendor'))}
    pts={v3._norm(x.get('product_type')) for x in members if v3._norm(x.get('product_type')) and v3._norm(x.get('product_type')).casefold() not in {'product','products','item','items','general','other'}}
    option_names=set(); per_non_size=defaultdict(set)
    for x in ((product.get('variants') or {}).get('nodes') or []):
        for o in x.get('selectedOptions') or []:
            n=v3._norm(o.get('name')).casefold(); val=v3._norm(o.get('value'))
            if n:
                option_names.add(n)
                if n not in {'size','title'}: per_non_size[n].add(val)
    unsafe_names=option_names-{'size','color','colour','material','fabric','style','title'}
    # Multiple colors/materials are acceptable variant dimensions; they do not by themselves make sold objects heterogeneous.
    pass_flag=len(vendors)<=1 and len(titles)<=1 and len(pts)<=1 and not unsafe_names
    return ('PASS' if pass_flag else 'REVIEW'), {'titles':sorted(titles),'vendors':sorted(vendors),'product_types':sorted(pts),'option_names':sorted(option_names),'unsafe_option_names':sorted(unsafe_names),'non_size_values':{k:sorted(v) for k,v in per_non_size.items()}}


def _sku_set(expected, observed):
    e=set(expected); o=set(observed); inter=e&o; missing=e-o; extra=o-e
    if e==o: status='EXACT_SET'
    elif inter and missing and not extra: status='SUBSET_WITH_EXPLAINED_GAPS'
    elif e.issubset(o) and extra: status='SUPERSET_WITH_EXPLAINED_EXTRAS'
    elif inter: status='AMBIGUOUS_SET'
    else: status='CONFLICTING_SET'
    return status,inter,missing,extra


def _persist(db,summary,artifacts):
    import psycopg
    payload={k:(v.decode('utf-8-sig') if isinstance(v,(bytes,bytearray)) else str(v)) for k,v in artifacts.items()}
    ds=hashlib.sha256(artifacts['v9-family-identity-audit.csv']).hexdigest()
    summary['dataset_hash']=ds
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''create table if not exists phh_identity_v9_snapshots(id bigserial primary key,created_at timestamptz not null default now(),dataset_hash text not null,summary jsonb not null,artifacts jsonb not null)''')
            cur.execute('insert into phh_identity_v9_snapshots(dataset_hash,summary,artifacts) values(%s,%s::jsonb,%s::jsonb)',(ds,json.dumps(summary,ensure_ascii=False),json.dumps(payload,ensure_ascii=False)))
        c.commit()
    return ds


def load_latest_artifact(db,name):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select artifacts->>%s from phh_identity_v9_snapshots order by created_at desc limit 1',(name,)); row=cur.fetchone()
            return None if not row or row[0] is None else row[0].encode('utf-8-sig')


def run_audit(master_rows,db,token,shop_domain,top_n=TOP_N):
    ids=v3._canonical_identities(master_rows)
    if len(ids)!=1671: raise RuntimeError(f'canonical universe changed: {len(ids)}')
    groups0,membership,group_diag=v4._build_groups(ids); groups=dict(groups0)
    if len(groups)!=914: raise RuntimeError(f'family universe changed: {len(groups)}')
    global_canonical=Counter((v3._norm(x.get('220_sku')),v3._norm(x.get('220_ean'))) for x in ids)
    reg=_registry(db)
    candidates=[]
    for r in reg:
        key=r.get('family_key') or ''; status=r.get('status') or ''
        if status not in {'UNRESOLVED','REVIEW'}: continue
        members=groups.get(key) or []
        if not members or not _is_apparel(r,members): continue
        brand=v3._norm(r.get('brand') or members[0].get('vendor'))
        pts=[v3._norm(x.get('product_type')) for x in members if v3._norm(x.get('product_type')) and v3._norm(x.get('product_type')).casefold() not in {'product','products','item','items','general','other'}]
        pids={v3._norm(x.get('shopify_product_id')) for x in members if v3._norm(x.get('shopify_product_id'))}
        links=sum(bool(v3._norm(x.get('shopify_product_id'))) for x in members)
        score=(100 if brand.casefold()=='fhm' else 0)+len(members)*10+(10 if pts else 0)+(8 if len(pids)==1 else 0)+(min(links,len(members)))
        candidates.append((score,len(members),1 if brand.casefold()=='fhm' else 0,key,r,members))
    candidates.sort(key=lambda x:(-x[2],-x[1],-x[0],x[3]))
    selected=candidates[:top_n]
    # Always preserve the two known identity-PASS special cases in the audit/leaf queue even if FHM-first priority pushes them below top_n.
    chosen={x[3] for x in selected}
    for item in candidates:
        if item[3] in SPECIAL and item[3] not in chosen:
            selected.append(item); chosen.add(item[3])

    all_skus=sorted({v3._norm(m.get('220_sku')) for *_,members in selected for m in members if v3._norm(m.get('220_sku'))})
    by_sku,ambiguous=_lookup_skus(token,shop_domain,all_skus)
    product_ids=sorted({v3._norm(v.get('product',{}).get('id')) for v in by_sku.values() if v3._norm(v.get('product',{}).get('id'))})
    products=_fetch_products(token,shop_domain,product_ids)

    priority=[]; audits=[]; gaps_rows=[]; migration=[]; splits=[]; leaf_ready=[]
    outcome=Counter(); ean_counts=Counter(); gap_counts=Counter(); brand_counts=Counter(); total_skus=0
    newly_product_links=0; newly_variant_memberships=0; upgraded=0; upgraded_skus=0; exact_set_fams=0; subset_fams=0
    family_rank=0

    for _,_,_,key,r,members in selected:
        family_rank+=1; total_skus+=len(members)
        brand=v3._norm(r.get('brand') or members[0].get('vendor')); brand_counts['FHM' if brand.casefold()=='fhm' else ('Outfish' if brand.casefold()=='outfish' else 'OTHER')]+=1
        family_name=sorted({v3._norm(x.get('220_title') or x.get('shopify_title')) for x in members if v3._norm(x.get('220_title') or x.get('shopify_title'))})
        expected=[v3._norm(x.get('220_sku')) for x in members]
        canonical_conflicts=sum(global_canonical[(v3._norm(x.get('220_sku')),v3._norm(x.get('220_ean')))]!=1 for x in members)
        live_matches={sku:by_sku[sku] for sku in expected if sku in by_sku}
        pmap=defaultdict(list)
        for sku,v in live_matches.items(): pmap[v3._norm((v.get('product') or {}).get('id'))].append(sku)
        pmap.pop('',None)
        product_ids_family=sorted(pmap)
        duplicate_skus=[sku for sku in expected if sku in ambiguous]
        product=None; authoritative=False; membership_method=''; recovered_product=False
        if len(product_ids_family)==1:
            pid=product_ids_family[0]; product=products.get(pid) or {}
            authoritative=True; membership_method='EXACT_SKU_LOOKUP_PLUS_SINGLE_PRODUCT'
            existing_pids={v3._norm(x.get('shopify_product_id')) for x in members if v3._norm(x.get('shopify_product_id'))}
            recovered_product=(not existing_pids or existing_pids!={pid})
            if recovered_product: newly_product_links+=1
        observed=[]
        if product:
            observed=[v3._norm(x.get('sku')) for x in ((product.get('variants') or {}).get('nodes') or []) if v3._norm(x.get('sku'))]
        set_status,inter,missing,extra=_sku_set(expected,observed)
        if set_status=='EXACT_SET': exact_set_fams+=1
        elif set_status=='SUBSET_WITH_EXPLAINED_GAPS': subset_fams+=1
        homog,hdetail=_homogeneity(members,product or {})
        audience=_audience(product or {})
        product_type=v3._norm((product or {}).get('productType'))
        cat_full=v3._norm(((product or {}).get('category') or {}).get('fullName'))
        subtype=cat_full.split('>')[-1].strip() if cat_full else ''
        gaps=[]
        if canonical_conflicts: gaps.append('CANONICAL_IDENTITY_CONFLICT')
        if len(product_ids_family)>1: gaps.append('AMBIGUOUS_PRODUCT_MEMBERSHIP')
        if not product_ids_family:
            gaps.extend(['MISSING_SHOPIFY_PRODUCT_LINK','LIVE_PRODUCT_NOT_FOUND'])
        if duplicate_skus: gaps.append('DUPLICATE_SKU')
        if set_status in {'SUBSET_WITH_EXPLAINED_GAPS','AMBIGUOUS_SET'}: gaps.append('SKU_SET_INCOMPLETE')
        if set_status in {'SUPERSET_WITH_EXPLAINED_EXTRAS','AMBIGUOUS_SET','CONFLICTING_SET'}: gaps.append('SKU_SET_CONFLICT')
        if homog!='PASS': gaps.append('FAMILY_NOT_HOMOGENEOUS')
        if not product_type and not cat_full: gaps.append('PRODUCT_TYPE_UNKNOWN')
        if not subtype: gaps.append('SUBTYPE_UNKNOWN')
        if not audience: gaps.append('AUDIENCE_UNKNOWN')
        missing_vid=0; missing_sku=0; missing_bar=0
        member_rows=[]
        for m in members:
            sku=v3._norm(m.get('220_sku')); live=live_matches.get(sku) or {}; barcode=v3._norm(live.get('barcode')); rel=_ean_class(m.get('220_ean'),barcode,brand); ean_counts[rel]+=1
            if not v3._norm(m.get('shopify_variant_id')): missing_vid+=1
            if not v3._norm(live.get('sku')): missing_sku+=1
            if not barcode: missing_bar+=1
            if live and not v3._norm(m.get('shopify_variant_id')): newly_variant_memberships+=1
            method='PRODUCT_ID_PLUS_SKU' if live and len(product_ids_family)==1 else ('EXACT_SKU_LOOKUP_AMBIGUOUS_PRODUCT' if live else '')
            member_rows.append({'220_sku':sku,'220_ean':v3._norm(m.get('220_ean')),'master_title':v3._norm(m.get('220_title')),'master_product_type':v3._norm(m.get('product_type')),
                'master_shopify_product_id':v3._norm(m.get('shopify_product_id')),'master_shopify_variant_id':v3._norm(m.get('shopify_variant_id')),'shopify_product_id':v3._norm((live.get('product') or {}).get('id')),
                'shopify_product_title':v3._norm((live.get('product') or {}).get('title')),'shopify_variant_id':v3._norm(live.get('id')),'shopify_variant_sku':v3._norm(live.get('sku')),
                'shopify_barcode':barcode,'shopify_option_values':live.get('selectedOptions') or [],'membership_proven_by':method,'ean_relationship':rel})
            if rel=='LEGACY_46_TO_475_CANDIDATE':
                confirmed=bool(live and len(product_ids_family)==1)
                migration.append({'brand':brand,'family':key,'master_sku':sku,'master_ean':v3._norm(m.get('220_ean')),'shopify_barcode':barcode,'relationship_class':rel,
                    'migration_hypothesis':'known FHM auxiliary 46-prefix legacy barcode vs 475 Master EAN; no digit-conversion algorithm assumed','evidence':'exact live Shopify variant SKU and single-product family membership' if confirmed else 'prefix pattern only',
                    'confidence':'0.98' if confirmed else '0.50','reusable_scope':'FHM only; auxiliary evidence only; never canonical; do not transfer to Outfish/other brands','status':'CONFIRMED_AUXILIARY' if confirmed else 'REVIEW'})
            elif rel in {'DIFFERENT_475','OTHER_MISMATCH'}:
                migration.append({'brand':brand,'family':key,'master_sku':sku,'master_ean':v3._norm(m.get('220_ean')),'shopify_barcode':barcode,'relationship_class':rel,
                    'migration_hypothesis':'none','evidence':'exact live Shopify variant SKU comparison' if live else 'insufficient','confidence':'0.00','reusable_scope':'none','status':'REJECTED'})
        if missing_vid: gaps.append('MISSING_VARIANT_ID')
        if missing_sku: gaps.append('MISSING_SHOPIFY_SKU')
        if missing_bar: gaps.append('MISSING_SHOPIFY_BARCODE')
        gaps=sorted(set(gaps)); [gap_counts.update([g]) for g in gaps]

        # Special cases are known PASS from v8 and kept as such without spending v9 recovery budget.
        if key in SPECIAL:
            identity_status='IDENTITY_PASS'
            if key=='CURRENT_GROUP:0040': remaining=['TAXONOMY_LEAF_GAP']
            else: remaining=['AUDIENCE_GAP']
        else:
            remaining=[]
            pass_core=canonical_conflicts==0 and authoritative and homog=='PASS' and not duplicate_skus and set_status=='EXACT_SET'
            if pass_core: identity_status='IDENTITY_PASS'
            elif canonical_conflicts or duplicate_skus or set_status=='CONFLICTING_SET': identity_status='IDENTITY_FAIL'
            elif authoritative or live_matches: identity_status='IDENTITY_REVIEW'
            else: identity_status='IDENTITY_INCOMPLETE'
        outcome[identity_status]+=1
        old_v8_pass=(key in SPECIAL or key=='CURRENT_GROUP:0057')
        if identity_status=='IDENTITY_PASS' and not old_v8_pass:
            upgraded+=1; upgraded_skus+=len(members)
        if len(product_ids_family)>1:
            for pid,skus in sorted(pmap.items()):
                splits.append({'family_key':key,'current_member_count':len(members),'proposed_subgroup_key':f'{key}::{pid.split("/")[-1]}','proposed_members':'|'.join(sorted(skus)),'split_reason':'exact SKU lookup maps current family to multiple live Shopify products','evidence':pid,'confidence':'0.99','writes_performed':0})

        evidence_summary=f'{membership_method}; {set_status}; canonical_conflicts={canonical_conflicts}; homogeneity={homog}; recovered_product_link={recovered_product}'
        priority.append({'priority_rank':family_rank,'family_key':key,'brand':brand,'family_name':' || '.join(family_name),'member_count':len(members),'source_status':r.get('status'),'priority_reason':'FHM-first; member_count; recoverable exact SKU evidence'})
        audits.append({'family_key':key,'brand':brand,'family_name':' || '.join(family_name),'member_count':len(members),'identity_status':identity_status,'canonical_conflicts':canonical_conflicts,
            'authoritative_product_membership_proven':authoritative,'membership_proven_by':membership_method,'shopify_product_ids':'|'.join(product_ids_family),'shopify_product_title':v3._norm((product or {}).get('title')),
            'shopify_vendor':v3._norm((product or {}).get('vendor')),'shopify_product_type':product_type,'shopify_category':cat_full,'shopify_tags':'|'.join((product or {}).get('tags') or []),'shopify_status':v3._norm((product or {}).get('status')),
            'expected_master_skus':'|'.join(sorted(expected)),'observed_shopify_skus':'|'.join(sorted(observed)),'intersection':'|'.join(sorted(inter)),'missing_in_shopify':'|'.join(sorted(missing)),'extra_in_shopify':'|'.join(sorted(extra)),
            'duplicate_skus':'|'.join(sorted(duplicate_skus)),'coverage_ratio':f'{len(inter)/max(1,len(set(expected))):.6f}','sku_set_status':set_status,'family_homogeneity':homog,
            'audience':'|'.join(audience),'product_subtype':subtype,'evidence_gaps':'|'.join(gaps),'identity_evidence_summary':evidence_summary,'member_packet_json':json.dumps(member_rows,ensure_ascii=False,separators=(',',':'))})
        for g in gaps:
            gaps_rows.append({'family_key':key,'brand':brand,'member_count':len(members),'identity_status':identity_status,'gap_code':g,'evidence':'see v9-family-identity-audit.csv','recommended_next_read_only_action':'recover authoritative Shopify identity evidence' if g.startswith('MISSING_') or 'SKU_SET' in g else 'manual evidence adjudication'})
        if identity_status=='IDENTITY_PASS':
            leaf_ready.append({'family_key':key,'brand':brand,'family_name':' || '.join(family_name),'member_count':len(members),'identity_status':identity_status,'product_type':product_type,'product_subtype':subtype,
                'audience':'|'.join(audience),'identity_evidence_summary':evidence_summary,'sku_set_status':set_status,'ean_evidence_summary':json.dumps(Counter(x['ean_relationship'] for x in member_rows),sort_keys=True),
                'remaining_non_identity_gaps':'|'.join(remaining or ([] if audience and (product_type or subtype) else (['AUDIENCE_UNKNOWN'] if not audience else [])+(['PRODUCT_TYPE_UNKNOWN'] if not (product_type or subtype) else []))),
                'priority_score':len(members)*10+(25 if brand.casefold()=='fhm' else 0)})

    summary={'status':'PASS','audit_version':'identity-evidence-recovery-v9','baseline':BASELINE,'investigation':{'families_investigated':len(selected),'FHM_families_investigated':brand_counts['FHM'],'Outfish_families_investigated':brand_counts['Outfish'],'other_apparel_families_investigated':brand_counts['OTHER'],'total_SKU_represented':total_skus},
        'identity_outcome':dict(outcome),'recovery':{'newly_recovered_Shopify_product_links':newly_product_links,'newly_recovered_variant_memberships':newly_variant_memberships,'families_upgraded_to_IDENTITY_PASS':upgraded,'SKUs_represented_by_newly_PASS_families':upgraded_skus,'exact_SKU_set_families':exact_set_fams,'explained_subset_families':subset_fams},
        'EAN':dict(ean_counts),'gaps':dict(gap_counts),'split':{'families_needing_split':len({x['family_key'] for x in splits}),'proposed_subgroup_count':len(splits)},'leaf_readiness':{'families_ready_for_leaf_adjudication':len(leaf_ready),'SKU_represented_in_leaf_ready_queue':sum(int(x['member_count']) for x in leaf_ready)},
        'product_level_breakdown_recalculated':False,'product_level_breakdown':BASELINE,'group_build':group_diag,'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','stock_changes':0,'price_changes':0},
        'principle':'Recover identity first. Classify later. Canonical identity stays 220_sku + 220_ean. Brand-specific migration evidence is auxiliary and non-transferable. Missing evidence is a gap, not permission to guess.'}

    fields_priority=['priority_rank','family_key','brand','family_name','member_count','source_status','priority_reason']
    fields_audit=['family_key','brand','family_name','member_count','identity_status','canonical_conflicts','authoritative_product_membership_proven','membership_proven_by','shopify_product_ids','shopify_product_title','shopify_vendor','shopify_product_type','shopify_category','shopify_tags','shopify_status','expected_master_skus','observed_shopify_skus','intersection','missing_in_shopify','extra_in_shopify','duplicate_skus','coverage_ratio','sku_set_status','family_homogeneity','audience','product_subtype','evidence_gaps','identity_evidence_summary','member_packet_json']
    fields_gaps=['family_key','brand','member_count','identity_status','gap_code','evidence','recommended_next_read_only_action']
    fields_mig=['brand','family','master_sku','master_ean','shopify_barcode','relationship_class','migration_hypothesis','evidence','confidence','reusable_scope','status']
    fields_split=['family_key','current_member_count','proposed_subgroup_key','proposed_members','split_reason','evidence','confidence','writes_performed']
    fields_leaf=['family_key','brand','family_name','member_count','identity_status','product_type','product_subtype','audience','identity_evidence_summary','sku_set_status','ean_evidence_summary','remaining_non_identity_gaps','priority_score']
    artifacts={'v9-family-identity-priority.csv':_csv(priority,fields_priority),'v9-family-identity-audit.csv':_csv(audits,fields_audit),'v9-family-evidence-gaps.csv':_csv(gaps_rows,fields_gaps),'identity_migration_evidence.csv':_csv(migration,fields_mig),'v9-family-split-recommendations.csv':_csv(splits,fields_split),'v9-leaf-ready-queue.csv':_csv(leaf_ready,fields_leaf)}
    summary['dataset_hash']=hashlib.sha256(artifacts['v9-family-identity-audit.csv']).hexdigest()
    artifacts['v9-identity-summary.json']=json.dumps(summary,indent=2,sort_keys=True,ensure_ascii=False).encode('utf-8')
    _persist(db,summary,artifacts)
    print('IDENTITY_AUDIT_V9_RESULT '+json.dumps(summary,sort_keys=True,ensure_ascii=False),flush=True)
    for a in audits: print('V9_FAMILY_IDENTITY '+json.dumps({k:a[k] for k in ['family_key','brand','member_count','identity_status','shopify_product_ids','sku_set_status','coverage_ratio','audience','evidence_gaps']},sort_keys=True,ensure_ascii=False),flush=True)
    return summary,artifacts
