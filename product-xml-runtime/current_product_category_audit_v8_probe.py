from __future__ import annotations

import csv, io, json, re
from collections import Counter

import current_product_category_audit as v3
import current_product_category_audit_v4 as v4
import current_product_category_audit_v5 as v5
import current_product_category_audit_v7 as v7
import current_product_category_audit_v7b as v7b
from current_product_category_audit_v7_probe import _object_terms, _taxonomy_neighbors, _terminal_hint

TOP_N=30
APPAREL_TERMS=('jacket','coat','trousers','pants','shorts','hoodie','sweatshirt','fleece','shirt','jersey','thermal','underwear','boots','boot','bib','softshell','rainwear','clothing','apparel')
AUDIENCE_WORDS={
    'MEN':('men','mens',"men's",'male'),
    'WOMEN':('women','womens',"women's",'woman','female'),
    'CHILDREN':('kids','kid','children','child','boys','boy','girls','girl','baby','babies'),
    'UNISEX':('unisex',),
}
GENERIC_TYPES={'','product','products','item','items','general','other'}


def _csv_rows(db,name):
    b=v5.load_latest_artifact(db,name)
    if not b:
        # v7 artifacts live in v7 snapshot table, not v5
        b=v7.load_latest_artifact(db,name)
    if not b: raise RuntimeError(f'artifact unavailable: {name}')
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))


def _accepted_family_keys(db):
    b=v7.load_latest_artifact(db,'category_rule_library_v2.csv')
    if not b: return set()
    rows=list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))
    return {r.get('family_key') for r in rows if (r.get('review_status') or r.get('status'))=='ACCEPTED' and r.get('family_key')}


def _is_apparel(row,members):
    txt=' '.join([
        v3._norm(row.get('representative_titles')),v3._norm(row.get('shopify_hint')),v3._norm(row.get('product_type')),v3._norm(row.get('brand')),
        *[v3._norm(x.get('shopify_title') or x.get('220_title')) for x in members],
        *[v3._norm(x.get('product_type')) for x in members],
    ]).casefold()
    return any(re.search(r'(?<![a-z])'+re.escape(w)+r'(?![a-z])',txt) for w in APPAREL_TERMS)


def _ean_relation(master_ean, live_barcode, vendor):
    me=v3._norm(master_ean); lb=v3._norm(live_barcode)
    if not me or not lb: return 'MISSING'
    if me==lb: return 'EXACT'
    if vendor.casefold()=='fhm' and lb.startswith('46') and me.startswith('475'):
        return 'LEGACY_46_TO_475_CANDIDATE'
    if lb.startswith('475') and me.startswith('475'):
        return 'DIFFERENT_475'
    return 'OTHER_MISMATCH'


def _audience_evidence(product,members):
    txt=' '.join([
        v3._norm(product.get('title')),v3._norm(product.get('description')),v3._norm(product.get('product_type')),
        v3._norm(product.get('category_name')),' '.join(product.get('_v8_tags') or []),
        *[v3._norm(x.get('220_title')) for x in members],
    ]).casefold()
    out=[]
    for label,words in AUDIENCE_WORDS.items():
        if any(re.search(r'(?<![a-z])'+re.escape(w)+r'(?![a-z])',txt) for w in words): out.append(label)
    return out


def _homogeneity(members,product):
    pids={v3._norm(x.get('shopify_product_id')) for x in members if v3._norm(x.get('shopify_product_id'))}
    titles={v3._norm(x.get('shopify_title') or x.get('220_title')) for x in members if v3._norm(x.get('shopify_title') or x.get('220_title'))}
    types={v3._norm(x.get('product_type')) for x in members if v3._norm(x.get('product_type'))}
    vendors={v3._norm(x.get('vendor')) for x in members if v3._norm(x.get('vendor'))}
    live_opts=set()
    for x in (product.get('variants_by_id') or {}).values():
        for o in x.get('selected_options') or []:
            n=v3._norm(o.get('name')).casefold()
            if n: live_opts.add(n)
    allowed={'size','color','colour','material','style','title'}
    unsupported=sorted(x for x in live_opts if x not in allowed)
    result='PASS' if len(pids)==1 and len(titles)==1 and len(types)<=1 and len(vendors)<=1 and not unsupported else 'REVIEW'
    return result,{'product_ids':sorted(pids),'titles':sorted(titles),'product_types':sorted(types),'vendors':sorted(vendors),'live_option_names':sorted(live_opts),'unsupported_option_names':unsupported}


def _identity_packet(row,members,shopify):
    pids=sorted({v3._norm(x.get('shopify_product_id')) for x in members if v3._norm(x.get('shopify_product_id'))})
    product=shopify.get(pids[0]) if len(pids)==1 else {}
    product=product or {}
    live_variants=list((product.get('variants_by_id') or {}).values())
    live_by_sku={v3._norm(x.get('sku')):x for x in live_variants if v3._norm(x.get('sku'))}
    master_skus=[v3._norm(x.get('220_sku')) for x in members]
    master_set=set(master_skus); live_set=set(live_by_sku)
    canonical=[(v3._norm(x.get('220_sku')),v3._norm(x.get('220_ean'))) for x in members]
    canonical_unique=len(set(canonical))==len(canonical)
    ean_rows=[]; ec=Counter()
    vendor=v3._norm(row.get('brand') or (members[0].get('vendor') if members else ''))
    for m in members:
        sku=v3._norm(m.get('220_sku')); lv=live_by_sku.get(sku) or {}
        rel=_ean_relation(m.get('220_ean'),lv.get('barcode'),vendor); ec[rel]+=1
        ean_rows.append({'sku':sku,'master_ean':v3._norm(m.get('220_ean')),'shopify_barcode':v3._norm(lv.get('barcode')),'relationship':rel})
    homog,hdetail=_homogeneity(members,product)
    exact_sku=(master_set==live_set and len(master_set)==len(members))
    identity='PASS' if canonical_unique and len(pids)==1 and exact_sku else 'REVIEW'
    return product,{
        'identity_gate_result':identity,'canonical_unique':canonical_unique,'master_sku_count':len(master_set),'live_sku_count':len(live_set),
        'exact_sku_set':exact_sku,'missing_live_skus':sorted(master_set-live_set),'extra_live_skus':sorted(live_set-master_set),
        'ean_counts':dict(ec),'ean_rows':ean_rows,'family_homogeneity_result':homog,'homogeneity_detail':hdetail,
    }


def enrich_selected(shopify,token,shop_domain,product_ids):
    # Reuse v7b's exact SKU/barcode enrichment, then fetch tags without any writes.
    v7b.enrich_live_variant_identity(shopify,token,shop_domain,product_ids)
    query='''query V8ProductEvidence($ids:[ID!]!){nodes(ids:$ids){... on Product{id tags}}}'''
    import requests
    for i in range(0,len(product_ids),50):
        batch=product_ids[i:i+50]
        r=requests.post(f'https://{shop_domain}/admin/api/2026-07/graphql.json',headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},json={'query':query,'variables':{'ids':batch}},timeout=60)
        r.raise_for_status(); payload=r.json()
        if payload.get('errors'): raise RuntimeError('Shopify v8 evidence query errors: '+json.dumps(payload['errors'])[:1200])
        for node in (payload.get('data') or {}).get('nodes') or []:
            if node and node.get('id') in shopify: shopify[node['id']]['_v8_tags']=node.get('tags') or []
    return shopify


def run_probe(master_rows,shopify,db,token=None,shop_domain=None,top_n=TOP_N):
    tax_summary,cats,attrs=v3._latest_taxonomy(db); by_cat,_,_=v3._category_index(cats,attrs)
    ids=v3._canonical_identities(master_rows); groups0,membership,group_diag=v4._build_groups(ids); groups=dict(groups0)
    registry=_csv_rows(db,'family_category_registry.csv'); accepted=_accepted_family_keys(db)
    rows=[]
    for r in registry:
        key=r.get('family_key') or ''; status=r.get('status') or ''
        if status not in {'UNRESOLVED','REVIEW'} or key in accepted: continue
        members=groups.get(key) or []
        if not members or not _is_apparel(r,members): continue
        brand=(r.get('brand') or '').strip(); member_count=len(members)
        # Coverage remains primary. FHM is a tie-breaker / evidence-model priority, not a reason to override coverage.
        rows.append((member_count,1 if brand.casefold()=='fhm' else 0,key,r,members))
    rows.sort(key=lambda x:(-x[0],-x[1],x[2]))
    selected=rows[:top_n]
    pids=sorted({v3._norm(m.get('shopify_product_id')) for _,_,_,_,members in selected for m in members if v3._norm(m.get('shopify_product_id'))})
    if token and shop_domain: enrich_selected(shopify,token,shop_domain,pids)

    out=[]; identity_counts=Counter(); ean_counts=Counter(); fhm_n=other_n=0
    for rank,(_,_,key,r,members) in enumerate(selected,1):
        product,ident=_identity_packet(r,members,shopify)
        identity_counts[ident['identity_gate_result']]+=1; ean_counts.update(ident['ean_counts'])
        brand=v3._norm(r.get('brand') or (members[0].get('vendor') if members else ''))
        if brand.casefold()=='fhm': fhm_n+=1
        else: other_n+=1
        terms=_object_terms(r,members); neighbors=_taxonomy_neighbors(cats,by_cat,terms,limit=20)
        audience=_audience_evidence(product,members)
        rec={
            'rank':rank,'family_key':key,'source_family_status':r.get('status'),'member_count':len(members),'brand':brand,
            'member_skus':[v3._norm(x.get('220_sku')) for x in members],
            'member_eans':[v3._norm(x.get('220_ean')) for x in members],
            'shopify_product_ids':sorted({v3._norm(x.get('shopify_product_id')) for x in members if v3._norm(x.get('shopify_product_id'))}),
            'titles':sorted({v3._norm(x.get('shopify_title') or x.get('220_title')) for x in members if v3._norm(x.get('shopify_title') or x.get('220_title'))}),
            'product_types':sorted({v3._norm(x.get('product_type')) for x in members if v3._norm(x.get('product_type'))}),
            'shopify_category':v3._norm(product.get('category_name')),'shopify_terminal_hint':_terminal_hint(r.get('shopify_hint')),
            'shopify_tags':product.get('_v8_tags') or [],'audience_evidence':audience,'object_terms':terms,
            'identity':ident,'live_pHH_leaf_neighbors':neighbors,
        }
        out.append(rec)
        print('V8_FAMILY_DIAG '+json.dumps(rec,ensure_ascii=False,sort_keys=True),flush=True)
    summary={'status':'PASS','probe_version':'v8-targeted-identity-leaf-probe','products_total':len(ids),'families_total':len(groups),
             'families_examined':len(out),'FHM_families_examined':fhm_n,'other_apparel_families_examined':other_n,
             'identity_gate_counts':dict(identity_counts),'ean_relationship_counts':dict(ean_counts),'group_build':group_diag,
             'taxonomy_categories_fetched':tax_summary.get('categories_fetched'),
             'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','stock_changes':0,'price_changes':0}}
    print('V8_IDENTITY_LEAF_PROBE_RESULT '+json.dumps(summary,sort_keys=True),flush=True)
    return summary,out
