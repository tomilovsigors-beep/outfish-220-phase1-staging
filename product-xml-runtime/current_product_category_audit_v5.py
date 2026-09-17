from __future__ import annotations

import csv, io, json, hashlib
from collections import Counter, defaultdict

import current_product_category_audit as v3
import current_product_category_audit_v4 as v4

SEMANTIC_TRAP_TOKENS={
    'accessory','accessories','wall','walls','cover','covers','replacement','spare','repair','kit','kits',
    'part','parts','adapter','adapters','attachment','attachments','blanket','poncho','quilt','liner','liners',
    'bivy','bivvy','bivouac','sealant','adhesive'
}


def _csv_bytes(rows, fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    return s.getvalue().encode('utf-8-sig')


def _load_v4_mapping(db):
    b=v4.load_latest_artifact(db,'current-product-category-mapping.csv')
    if not b: return {}
    rows=list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))
    return {(r.get('220_sku',''),r.get('220_ean','')):r for r in rows}


def _leaf_index(cats):
    out={}
    for c in cats:
        if str(c.get('allow_add_products')).casefold() not in {'true','1','yes'}: continue
        title=v3._norm(c.get('title_en'))
        if not title: continue
        out[v3._key(title)]=c
    return out


def _title_has_leaf(title, leaf):
    tt=v3._tokens(title); lt=v3._tokens(leaf)
    return bool(lt) and lt.issubset(tt)


def _trap_signal(titles):
    toks=set()
    for t in titles: toks |= v3._tokens(t)
    return sorted(toks & SEMANTIC_TRAP_TOKENS)


def _family_packet(gkey,members,enriched,v4map):
    titles=[]; titles220=[]; shop_titles=[]; shop_hints=[]; product_types=[]; brands=[]; skus=[]; eans=[]; v4_statuses=[]; v4_candidates=[]
    for i in members:
        p,_=enriched[(i['220_sku'],i['220_ean'])]
        skus.append(i['220_sku']); eans.append(i['220_ean'])
        t220=v3._norm(i.get('220_title'))
        st=v3._norm(i.get('shopify_title') or p.get('title'))
        if t220: titles220.append(t220); titles.append(t220)
        if st: shop_titles.append(st); titles.append(st)
        sh=v3._norm(p.get('category_name'))
        if sh: shop_hints.append(sh)
        pt=v4._meaningful_type(p.get('product_type') or i.get('product_type'))
        if pt: product_types.append(pt)
        br=v3._norm(i.get('vendor') or p.get('vendor'))
        if br: brands.append(br)
        old=v4map.get((i['220_sku'],i['220_ean'])) or {}
        if old.get('status'): v4_statuses.append(old['status'])
        cj=old.get('candidate_json')
        if cj:
            try:
                arr=json.loads(cj)
                if arr: v4_candidates.append(arr[0])
            except Exception: pass
    return {
        'family_key':gkey,'member_count':len(members),'member_skus':sorted(set(skus)),'member_eans':sorted(set(eans)),
        'representative_titles':sorted(set(titles))[:12],'titles_220':sorted(set(titles220))[:12],
        'shopify_titles':sorted(set(shop_titles))[:12],'shopify_hints':sorted(set(shop_hints)),
        'product_types':sorted(set(product_types)),'brands':sorted(set(brands)),
        'v4_statuses':dict(Counter(v4_statuses)),'v4_top_candidates':v4_candidates[:12]
    }


def _decide_family(packet, leaf_idx):
    terminal_keys=[]; terminal_raw=[]
    for h in packet['shopify_hints']:
        term=v4._terminal_category(h); k=v3._key(term)
        if k and k in leaf_idx:
            terminal_keys.append(k); terminal_raw.append(term)
    type_keys=[]
    for pt in packet['product_types']:
        k=v3._key(pt)
        if k and k in leaf_idx: type_keys.append(k)

    terminals=set(terminal_keys); types=set(type_keys)
    titles=packet['representative_titles']; traps=_trap_signal(titles)
    candidate=None; sources=[]; basis=[]; rejected=[]

    # Exact product type is the strongest family semantic evidence.
    if len(types)==1:
        tk=next(iter(types)); candidate=leaf_idx[tk]; sources.append('exact_product_type')
        basis.append('all exact PHH-matching meaningful product_type evidence points to one leaf')
        if terminals=={tk}:
            sources.append('exact_terminal_shopify_category')
            basis.append('terminal Shopify category independently matches the same PHH leaf')
        elif terminals and terminals!={tk}:
            rejected.append('terminal Shopify category conflicts with exact product_type')
            return _review(packet,candidate,sources,basis,rejected,'conflicting exact family evidence')
    elif len(types)>1:
        return _unresolved(packet,'multiple exact PHH leaf matches from meaningful product_type')

    # Terminal Shopify category alone is accepted only with direct title semantics and no accessory/trap signal.
    if candidate is None and len(terminals)==1:
        tk=next(iter(terminals)); c=leaf_idx[tk]; leaf=v3._norm(c.get('title_en'))
        title_support=[t for t in titles if _title_has_leaf(t,leaf)]
        if title_support and not traps:
            candidate=c; sources.extend(['exact_terminal_shopify_category','exact_leaf_terms_in_family_title'])
            basis.append('terminal Shopify category exactly matches PHH leaf and family titles explicitly contain the leaf terms')
        else:
            why=[]
            if not title_support: why.append('no direct leaf-term support in family titles')
            if traps: why.append('semantic trap tokens: '+','.join(traps))
            return _review(packet,c,['exact_terminal_shopify_category'],['terminal Shopify category matches PHH leaf'],why,'terminal category not sufficiently corroborated')
    elif candidate is None and len(terminals)>1:
        return _unresolved(packet,'multiple exact terminal Shopify category matches point to different PHH leaves')

    if candidate is None:
        return _unresolved(packet,'no deterministic exact family-to-PHH leaf rule')

    leaf=v3._norm(candidate.get('title_en')); cid=str(candidate.get('category_id'))
    # Explicit accessory mismatch guard even when product_type maps exactly.
    if traps and 'exact_product_type' not in sources:
        return _review(packet,candidate,sources,basis,['semantic trap tokens: '+','.join(traps)],'semantic-trap guard')

    # Formal accepted rule must be reproducible for exact family membership only.
    confidence=0.99 if len(sources)>=2 else 0.96
    rule='apply only to exact members of '+packet['family_key']+' as produced by v4 deterministic grouping; category='+cid+'; evidence='+('|'.join(sources))
    return {
        'status':'ACCEPTED','category':candidate,'confidence':confidence,'evidence_sources':sources,
        'decision_basis':'; '.join(basis),'rejected_alternatives':'; '.join(rejected),
        'formal_rule':rule,'semantic_traps':'|'.join(traps)
    }


def _review(packet,candidate,sources,basis,rejected,reason):
    return {'status':'REVIEW','category':candidate,'confidence':0.80 if candidate else 0.0,'evidence_sources':sources,
            'decision_basis':reason+'; '+'; '.join(basis),'rejected_alternatives':'; '.join(rejected),'formal_rule':'','semantic_traps':'|'.join(_trap_signal(packet['representative_titles']))}


def _unresolved(packet,reason):
    return {'status':'UNRESOLVED','category':None,'confidence':0.0,'evidence_sources':[],'decision_basis':reason,
            'rejected_alternatives':'','formal_rule':'','semantic_traps':'|'.join(_trap_signal(packet['representative_titles']))}


def _persist(db,summary,artifacts):
    import psycopg
    payload={k:v.decode('utf-8-sig') for k,v in artifacts.items()}
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''create table if not exists phh_family_mapping_v5_snapshots(
                id bigserial primary key, created_at timestamptz not null default now(), dataset_hash text not null,
                summary jsonb not null, artifacts jsonb not null)''')
            cur.execute('insert into phh_family_mapping_v5_snapshots(dataset_hash,summary,artifacts) values(%s,%s::jsonb,%s::jsonb)',
                        (summary['dataset_hash'],json.dumps(summary,ensure_ascii=False),json.dumps(payload,ensure_ascii=False)))
        c.commit()


def load_latest_artifact(db,name):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select artifacts->>%s from phh_family_mapping_v5_snapshots order by created_at desc limit 1',(name,))
            row=cur.fetchone()
            return None if not row or row[0] is None else row[0].encode('utf-8-sig')


def run_audit(master_rows,shopify,db):
    tax_summary,cats,attrs=v3._latest_taxonomy(db); by_cat,required,_=v3._category_index(cats,attrs)
    ids=v3._canonical_identities(master_rows)
    if len(ids)!=1671: raise RuntimeError(f'canonical identity universe changed: {len(ids)} != 1671')
    groups,membership,group_diag=v4._build_groups(ids)
    enriched={(i['220_sku'],i['220_ean']):v3._shopify_enrich(i,shopify) for i in ids}
    v4map=_load_v4_mapping(db); leaf_idx=_leaf_index(cats)

    registry=[]; review_queue=[]; decisions={}; family_counts=Counter(); accepted_coverage=0
    reg_fields=['family_key','member_count','member_skus','representative_titles','shopify_hint','product_type','brand','v4_statuses','v4_candidates','proposed_phh_category_id','proposed_phh_category_title','evidence_sources','decision_basis','rejected_alternatives','formal_rule','semantic_traps','confidence','status']
    for g,members in groups.items():
        p=_family_packet(g,members,enriched,v4map); d=_decide_family(p,leaf_idx); decisions[g]=d; family_counts[d['status']]+=1
        c=d.get('category') or {}
        row={'family_key':g,'member_count':p['member_count'],'member_skus':'|'.join(p['member_skus']),
             'representative_titles':' || '.join(p['representative_titles']),'shopify_hint':' || '.join(p['shopify_hints']),
             'product_type':' || '.join(p['product_types']),'brand':' || '.join(p['brands']),
             'v4_statuses':json.dumps(p['v4_statuses'],sort_keys=True),'v4_candidates':json.dumps(p['v4_top_candidates'],ensure_ascii=False,sort_keys=True,separators=(',',':')),
             'proposed_phh_category_id':str(c.get('category_id') or ''),'proposed_phh_category_title':v3._norm(c.get('title_en')),
             'evidence_sources':'|'.join(d['evidence_sources']),'decision_basis':d['decision_basis'],'rejected_alternatives':d['rejected_alternatives'],
             'formal_rule':d['formal_rule'],'semantic_traps':d['semantic_traps'],'confidence':f"{d['confidence']:.6f}",'status':d['status']}
        registry.append(row)
        if d['status']!='ACCEPTED': review_queue.append(row)
        else: accepted_coverage+=len(members)

    mapping=[]; status_counts=Counter(); method_counts=Counter(); ambiguous=0; no_match_removed=0; review_removed=0
    map_fields=['220_sku','220_ean','family_key','family_status','family_member_count','brand','product_type','master_220_title','shopify_title','shopify_category_hint','selected_category_id','selected_category_name','confidence','status','decision_source','decision_basis','required_field_ids','source_identified_field_ids','missing_required_field_ids','v4_status','v4_selected_category_id','master_write','phh_write','shopify_write']
    for i in ids:
        key=(i['220_sku'],i['220_ean']); g=membership[key]; members=groups[g]; d=decisions[g]; p,v=enriched[key]; old=v4map.get(key) or {}
        selected=None; decision_source=''; basis=''; confidence=0.0
        legacy=v3._norm(i.get('220_category_id')); legacy_int=int(legacy) if legacy.isdigit() else None
        if legacy_int in v3.LEGACY_MISSING or legacy_int in v3.LEGACY_NONADDABLE:
            status='BLOCKED_TAXONOMY'; selected=by_cat.get(legacy); decision_source='existing_master_category_hint'; basis='legacy category missing/non-addable in live taxonomy'
        elif d['status']=='ACCEPTED':
            selected=d['category']; confidence=d['confidence']; decision_source='exact_accepted_family_rule'; basis=d['decision_basis']; status='AUTO'; method_counts['exact_family_inheritance']+=1
        elif old.get('status')=='BLOCKED_ATTRIBUTES' and old.get('selected_category_id'):
            selected=by_cat.get(old['selected_category_id']); confidence=float(old.get('confidence') or 0); decision_source='v4_strong_individual_evidence'; basis='preserved v4 provisionally accepted category; not reclassified without contrary evidence'; status='BLOCKED_ATTRIBUTES'; method_counts['preserved_v4_strong_individual']+=1
        else:
            status='REVIEW' if d['status']=='REVIEW' or old.get('status')=='REVIEW' else 'NO_MATCH'
            if status=='REVIEW': ambiguous+=1
            if d['status']=='REVIEW' and d.get('category'):
                selected=d['category']; confidence=d['confidence']; decision_source='family_review_only'; basis=d['decision_basis']
            elif old.get('status')=='REVIEW' and old.get('selected_category_id'):
                selected=by_cat.get(old['selected_category_id']); confidence=float(old.get('confidence') or 0); decision_source='v4_review_only'; basis='retained only for manual review; not inherited as accepted family rule'
            else:
                decision_source='none'; basis='no exact accepted family rule and no preserved strong individual evidence'

        req=required.get(str(selected.get('category_id'))) if selected else []; req=req or []; missing=[]; identified=[]; opts=v3._selected_options(i,v)
        for a in req:
            src_status,src,val=v3._attribute_source(a,i,p,v,opts)
            (identified if src_status.startswith('SOURCE_IDENTIFIED') else missing).append(str(a.get('field_id')))
        if status=='AUTO' and missing: status='BLOCKED_ATTRIBUTES'
        if old.get('status')=='NO_MATCH' and status!='NO_MATCH': no_match_removed+=1
        if old.get('status')=='REVIEW' and status not in {'REVIEW','NO_MATCH'}: review_removed+=1
        status_counts[status]+=1
        mapping.append({'220_sku':i['220_sku'],'220_ean':i['220_ean'],'family_key':g,'family_status':d['status'],'family_member_count':len(members),
                        'brand':v3._norm(i.get('vendor') or p.get('vendor')),'product_type':v4._meaningful_type(p.get('product_type') or i.get('product_type')),
                        'master_220_title':v3._norm(i.get('220_title')),'shopify_title':v3._norm(i.get('shopify_title') or p.get('title')),'shopify_category_hint':v3._norm(p.get('category_name')),
                        'selected_category_id':str(selected.get('category_id')) if selected else '','selected_category_name':v3._norm(selected.get('title_en')) if selected else '',
                        'confidence':f'{confidence:.6f}','status':status,'decision_source':decision_source,'decision_basis':basis,
                        'required_field_ids':'|'.join(str(a.get('field_id')) for a in req),'source_identified_field_ids':'|'.join(identified),'missing_required_field_ids':'|'.join(missing),
                        'v4_status':old.get('status',''),'v4_selected_category_id':old.get('selected_category_id',''),'master_write':'NO','phh_write':'NO','shopify_write':'NO'})

    mapping_bytes=_csv_bytes(mapping,map_fields); ds=hashlib.sha256(mapping_bytes).hexdigest()
    summary={'status':'PASS','mapping_version':'family-registry-v5','identity_count':len(ids),'family_group_count':len(groups),
             'family_status_counts':dict(family_counts),'sku_via_accepted_family_rules':accepted_coverage,
             'mapping_status_counts':dict(status_counts),'no_match_removed_vs_v4':no_match_removed,'review_removed_vs_v4':review_removed,
             'decision_method_counts':dict(method_counts),'ambiguous_cases_remaining':ambiguous,'group_build':group_diag,
             'taxonomy_categories_fetched':tax_summary.get('categories_fetched'),'dataset_hash':ds,
             'master_writes':0,'phh_writes':0,'shopify_writes':0,'product_xml_publication':'OFF',
             'principle':'better unresolved than wrongly mapped; no fuzzy fallback; accepted rules require deterministic exact family evidence'}
    artifacts={'family_category_registry.csv':_csv_bytes(registry,reg_fields),'family_category_review_queue.csv':_csv_bytes(review_queue,reg_fields),
               'v5-product-category-mapping.csv':mapping_bytes,'v5-category-coverage-summary.json':json.dumps(summary,ensure_ascii=False,sort_keys=True,indent=2).encode()}
    _persist(db,summary,artifacts)
    return summary,artifacts
