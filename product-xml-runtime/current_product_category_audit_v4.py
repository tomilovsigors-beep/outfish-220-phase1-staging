from __future__ import annotations

import csv, io, json, re, hashlib
from collections import Counter, defaultdict

import current_product_category_audit as v3

GENERIC_PRODUCT_TYPES={'','product','products','item','items','general','other'}
GENERIC_LEAF_PREFIXES=('other ','products with damaged packaging','placement of technical services','installation service')
SIZE_SUFFIX=re.compile(r'[-_](?:xxxs|xxs|xs|s|m|l|xl|xxl|2xl|3xl|4xl|5xl|6xl|7xl|\d{1,3}(?:\.5)?)$',re.I)


def load_latest_artifact(db,name):
    return v3.load_latest_artifact(db,name)


def _meaningful_type(x):
    x=v3._norm(x)
    return '' if v3._key(x) in GENERIC_PRODUCT_TYPES else x


def _terminal_category(x):
    x=v3._norm(x)
    if not x: return ''
    parts=[p.strip() for p in re.split(r'\s*(?:>|/|→|›)\s*',x) if p.strip()]
    return parts[-1] if parts else x


def _sku_family(sku):
    sku=v3._norm(sku)
    prev=None
    while sku and sku!=prev:
        prev=sku; sku=SIZE_SUFFIX.sub('',sku)
    return sku


class _UF:
    def __init__(self,n): self.p=list(range(n))
    def find(self,x):
        while self.p[x]!=x:
            self.p[x]=self.p[self.p[x]]; x=self.p[x]
        return x
    def union(self,a,b):
        a=self.find(a); b=self.find(b)
        if a!=b: self.p[b]=a


def _build_groups(ids):
    uf=_UF(len(ids)); exact=defaultdict(list)
    for idx,i in enumerate(ids):
        pid=v3._norm(i.get('shopify_product_id'))
        if pid: exact[('PID',pid)].append(idx)
        vendor=v3._key(i.get('vendor'))
        st=v3._key(i.get('shopify_title'))
        if st: exact[('SHOPIFY_TITLE_VENDOR',st,vendor)].append(idx)
        at=v3._key(i.get('220_title'))
        if at: exact[('220_TITLE_VENDOR',at,vendor)].append(idx)
    for members in exact.values():
        for j in members[1:]: uf.union(members[0],j)

    fam=defaultdict(list)
    for idx,i in enumerate(ids):
        stem=_sku_family(i.get('220_sku'))
        if stem: fam[stem].append(idx)
    safe_family_count=0; rejected_family_count=0
    for stem,members in fam.items():
        if len(members)<2: continue
        title_keys={v3._key(ids[j].get('shopify_title') or ids[j].get('220_title') or ids[j].get('220_title_candidate')) for j in members}
        title_keys.discard('')
        vendors={v3._key(ids[j].get('vendor')) for j in members}; vendors.discard('')
        # Family propagation is permitted only with no contradictory known title/vendor evidence.
        if len(title_keys)<=1 and len(vendors)<=1 and title_keys:
            for j in members[1:]: uf.union(members[0],j)
            safe_family_count+=1
        else:
            rejected_family_count+=1

    roots=defaultdict(list)
    for idx,i in enumerate(ids): roots[uf.find(idx)].append(i)
    groups={}
    membership={}
    for n,(root,members) in enumerate(sorted(roots.items(),key=lambda kv:min((x['220_sku'],x['220_ean']) for x in kv[1])),start=1):
        key=f'CURRENT_GROUP:{n:04d}'
        groups[key]=members
        for i in members: membership[(i['220_sku'],i['220_ean'])]=key
    return groups,membership,{'safe_sku_family_merges':safe_family_count,'rejected_sku_family_families':rejected_family_count}


def _category_meta(cats):
    out=[]
    for c in cats:
        if str(c.get('allow_add_products')).casefold() not in {'true','1','yes'}: continue
        en=v3._norm(c.get('title_en')) or v3._norm(c.get('title_lv')) or v3._norm(c.get('title_lt'))
        en_key=v3._key(en); en_tokens=v3._tokens(en)
        all_tokens=v3._tokens(*[c.get(f) for f in v3.LANG_FIELDS])
        out.append({'c':c,'en':en,'en_key':en_key,'en_tokens':en_tokens,'all_tokens':all_tokens,'single':len(en_tokens)==1,'guarded':any(en_key.startswith(v3._key(p)) for p in GENERIC_LEAF_PREFIXES)})
    return out


def _group_evidence(members,enriched):
    titles=[]; types=[]; cats=[]; vendors=[]
    for i in members:
        p,_=enriched[(i['220_sku'],i['220_ean'])]
        for x in (i.get('220_title'),i.get('shopify_title'),i.get('220_title_candidate'),p.get('title')):
            if v3._norm(x): titles.append(v3._norm(x))
        pt=_meaningful_type(p.get('product_type') or i.get('product_type'))
        if pt: types.append(pt)
        sc=_terminal_category(p.get('category_name'))
        if sc: cats.append(sc)
        if v3._norm(i.get('vendor') or p.get('vendor')): vendors.append(v3._norm(i.get('vendor') or p.get('vendor')))
    return {'titles':sorted(set(titles)),'types':sorted(set(types)),'shop_categories':sorted(set(cats)),'vendors':sorted(set(vendors))}


def _score(i,p,group_ev,metas):
    titles=[v3._norm(x) for x in (i.get('220_title'),i.get('shopify_title'),i.get('220_title_candidate'),p.get('title')) if v3._norm(x)]
    group_titles=group_ev['titles']; title_tokens=v3._tokens(*(titles+group_titles)); title_keys={v3._key(x) for x in titles+group_titles if v3._key(x)}
    pt=_meaningful_type(p.get('product_type') or i.get('product_type')); pt_key=v3._key(pt); pt_tokens=v3._tokens(pt)
    group_type_keys={v3._key(x) for x in group_ev['types'] if v3._key(x)}
    terminal=_terminal_category(p.get('category_name')); terminal_key=v3._key(terminal)
    group_cat_keys={v3._key(x) for x in group_ev['shop_categories'] if v3._key(x)}
    legacy=v3._norm(i.get('220_category_id'))
    scored=[]
    for m in metas:
        c=m['c']; cid=str(c.get('category_id')); leaf=m['en_tokens']; lk=m['en_key']
        exact_type=bool(pt_key and pt_key==lk)
        group_exact_type=bool(lk and lk in group_type_keys)
        exact_shop=bool(terminal_key and terminal_key==lk)
        group_exact_shop=bool(lk and lk in group_cat_keys)
        exact_title=bool(lk and lk in title_keys)
        legacy_exact=bool(legacy and legacy==cid)
        overlap=title_tokens & leaf
        overlap_n=len(overlap); coverage=overlap_n/max(1,len(leaf))
        multilingual_overlap=len(title_tokens & m['all_tokens'])

        # Single-token leaves are too ambiguous for incidental lexical matching.
        if m['single'] and not (exact_type or group_exact_type or exact_shop or group_exact_shop or exact_title or legacy_exact):
            continue
        # Generic/special categories need explicit exact evidence.
        if m['guarded'] and not (exact_type or exact_shop or legacy_exact):
            continue
        # Multi-token lexical candidate needs >=2 exact English leaf tokens; one word is context only.
        lexical_ok=(not m['single'] and overlap_n>=2 and coverage>=0.5)
        if not (lexical_ok or exact_type or group_exact_type or exact_shop or group_exact_shop or exact_title or legacy_exact):
            continue

        strong=[]; score=0.0
        if lexical_ok:
            score=max(score,0.52+0.28*coverage)
            if coverage>=0.8: strong.append('high_english_leaf_overlap')
        if exact_type: score=max(score,0.96); strong.append('exact_product_type')
        if group_exact_type: score=max(score,0.90); strong.append('group_exact_product_type')
        if exact_shop: score=max(score,0.96); strong.append('exact_terminal_shopify_category')
        if group_exact_shop: score=max(score,0.90); strong.append('group_exact_terminal_shopify_category')
        if exact_title: score=max(score,0.93); strong.append('exact_title_category')
        if legacy_exact: score=max(score,0.97); strong.append('existing_master_category_hint')
        if multilingual_overlap>=2 and score: score=min(0.99,score+0.01)
        evidence={'english_leaf_overlap':overlap_n,'english_leaf_coverage':round(coverage,4),'multilingual_support_tokens':multilingual_overlap,'strong_signals':strong,'single_token_guard':m['single'],'generic_guard':m['guarded']}
        scored.append((round(score,6),c,evidence))
    scored.sort(key=lambda x:(-x[0],str(x[1].get('category_id'))))
    return scored[:5]


def run_audit(master_rows,shopify,db):
    tax_summary,cats,attrs=v3._latest_taxonomy(db); by_cat,required,_=v3._category_index(cats,attrs)
    ids=v3._canonical_identities(master_rows)
    if len(ids)!=1671: raise RuntimeError(f'canonical identity universe changed: {len(ids)} != 1671')
    groups,membership,group_diag=_build_groups(ids); metas=_category_meta(cats)
    enriched={}
    for i in ids: enriched[(i['220_sku'],i['220_ean'])]=v3._shopify_enrich(i,shopify)
    group_ev={g:_group_evidence(m,enriched) for g,m in groups.items()}
    raw={}
    for i in ids:
        k=(i['220_sku'],i['220_ean']); p,_=enriched[k]; g=membership[k]
        raw[k]=_score(i,p,group_ev[g],metas)

    # Group consensus is corroboration only, never a category decision on its own.
    consensus={}
    for g,members in groups.items():
        votes=Counter(); scores=defaultdict(list)
        for i in members:
            top=raw[(i['220_sku'],i['220_ean'])][:1]
            if top:
                s,c,_=top[0]; cid=str(c.get('category_id')); votes[cid]+=1; scores[cid].append(s)
        if votes:
            cid,count=votes.most_common(1)[0]; ratio=count/len(members); avg=sum(scores[cid])/len(scores[cid])
            if len(members)>=2 and count>=2 and ratio>=0.75 and avg>=0.70: consensus[g]={'category_id':cid,'count':count,'size':len(members),'ratio':ratio,'avg_score':round(avg,6)}

    mapping=[]; gaps=[]; exceptions=[]; status_counts=Counter(); selected_counts=Counter(); score_buckets=Counter(); input_coverage=Counter(); group_status=Counter()
    blocked_sample=[]; review_sample=[]; no_match_sample=[]
    for i in ids:
        k=(i['220_sku'],i['220_ean']); p,v=enriched[k]; g=membership[k]; scored=raw[k]; gc=consensus.get(g); legacy=v3._norm(i.get('220_category_id'))
        meaningful_pt=_meaningful_type(p.get('product_type') or i.get('product_type'))
        if v3._norm(i.get('shopify_product_id')): input_coverage['shopify_product_link']+=1
        if v3._norm(i.get('220_title')): input_coverage['authoritative_220_title']+=1
        if meaningful_pt: input_coverage['meaningful_product_type']+=1
        if v3._norm(p.get('category_name')): input_coverage['shopify_category_hint']+=1
        if legacy: input_coverage['existing_220_category_hint']+=1
        top=scored[0] if scored else None; second=scored[1] if len(scored)>1 else None; selected=top[1] if top else None
        confidence=top[0] if top else 0.0; margin=(confidence-second[0]) if top and second else confidence; strong=list(top[2].get('strong_signals') or []) if top else []
        if top and gc and str(top[1].get('category_id'))==gc['category_id']:
            strong.append('group_consensus'); confidence=min(0.99,round(confidence+0.02,6))
        status='NO_MATCH'; reason='no safe live PHH category candidate under conservative v4 rules'
        legacy_int=int(legacy) if legacy.isdigit() else None
        if legacy_int in v3.LEGACY_MISSING or legacy_int in v3.LEGACY_NONADDABLE:
            status='BLOCKED_TAXONOMY'; reason='existing Master category hint is missing or non-addable in live PHH taxonomy'; selected=by_cat.get(legacy)
        elif top:
            exact_now=any(x in strong for x in ('exact_product_type','exact_terminal_shopify_category','exact_title_category','existing_master_category_hint'))
            exact_group=any(x in strong for x in ('group_exact_product_type','group_exact_terminal_shopify_category'))
            corroborated=(exact_now and (exact_group or 'group_consensus' in strong or 'high_english_leaf_overlap' in strong) and confidence>=0.90 and margin>=0.10)
            if corroborated: status='AUTO'; reason='exact item evidence corroborated independently by group/context evidence'
            else: status='REVIEW'; reason='plausible category, but exact independent corroboration is insufficient for AUTO'
        req=required.get(str(selected.get('category_id'))) if selected else []; req=req or []; missing=[]; identified=[]; opts=v3._selected_options(i,v)
        for a in req:
            src_status,src,val=v3._attribute_source(a,i,p,v,opts)
            gaps.append({'220_sku':i['220_sku'],'220_ean':i['220_ean'],'group_key':g,'category_id':str(selected.get('category_id')) if selected else '','category_name':v3._norm(selected.get('title_en')) if selected else '','field_id':v3._norm(a.get('field_id')),'required':'TRUE','field_title_lt':v3._norm(a.get('title_lt')),'field_title_lv':v3._norm(a.get('title_lv')),'field_title_ee':v3._norm(a.get('title_ee')),'field_title_fi':v3._norm(a.get('title_fi')),'field_title_ru':v3._norm(a.get('title_ru')),'semantic':v3._semantic_field(a),'source_status':src_status,'source':src,'source_value':val,'category_decision_status':status})
            (identified if src_status.startswith('SOURCE_IDENTIFIED') else missing).append(str(a.get('field_id')))
        if status=='AUTO' and missing:
            status='BLOCKED_ATTRIBUTES'; reason='category is exact/corroborated but required PHH fields lack a safe structured source'
            for row in gaps[-len(req):] if req else []: row['category_decision_status']=status
        if i.get('duplicate_conflict_fields'):
            status='REVIEW'; reason='Master duplicate identity rows contain conflicting classification evidence'
        candidates=[]
        for s,c,ev in scored:
            ev=dict(ev)
            if gc and str(c.get('category_id'))==gc['category_id']: ev['group_consensus']=gc
            candidates.append({'category_id':str(c.get('category_id')),'category_name':v3._norm(c.get('title_en')),'score':s,'evidence':ev})
        row={'220_sku':i['220_sku'],'220_ean':i['220_ean'],'group_key':g,'group_size':len(groups[g]),'brand':v3._norm(i.get('vendor') or p.get('vendor')),'product_type':meaningful_pt,'master_220_title':v3._norm(i.get('220_title')),'shopify_title':v3._norm(i.get('shopify_title') or p.get('title')),'variant_title':v3._norm(i.get('variant_title') or v.get('title')),'shopify_category_hint':v3._norm(p.get('category_name')),'existing_220_category_hint':legacy,'selected_category_id':str(selected.get('category_id')) if selected else '','selected_category_name':v3._norm(selected.get('title_en')) if selected else '','confidence':f'{confidence:.6f}','margin_to_second':f'{margin:.6f}','status':status,'reason':reason,'strong_signals':'|'.join(sorted(set(strong))),'required_field_ids':'|'.join(str(a.get('field_id')) for a in req),'source_identified_field_ids':'|'.join(identified),'missing_required_field_ids':'|'.join(missing),'candidate_json':json.dumps(candidates,ensure_ascii=False,sort_keys=True,separators=(',',':')),'master_rows':i.get('master_rows'),'duplicate_conflict_fields':i.get('duplicate_conflict_fields'),'phh_write':'NO','master_write':'NO','shopify_write':'NO'}
        mapping.append(row); status_counts[status]+=1
        if selected: selected_counts[str(selected.get('category_id'))+' '+v3._norm(selected.get('title_en'))]+=1
        score_buckets['0']+=confidence==0; score_buckets['0-0.49']+=0<confidence<0.5; score_buckets['0.50-0.77']+=0.5<=confidence<0.78; score_buckets['0.78-0.89']+=0.78<=confidence<0.90; score_buckets['0.90+']+=confidence>=0.90
        sample={'220_sku':i['220_sku'],'group_key':g,'title':row['master_220_title'] or row['shopify_title'],'product_type':meaningful_pt,'shopify_category_hint':row['shopify_category_hint'],'selected_category_id':row['selected_category_id'],'selected_category_name':row['selected_category_name'],'confidence':row['confidence'],'strong_signals':row['strong_signals'],'missing_required_field_ids':row['missing_required_field_ids']}
        if status=='BLOCKED_ATTRIBUTES' and len(blocked_sample)<30: blocked_sample.append(sample)
        elif status=='REVIEW' and confidence>=0.85 and len(review_sample)<30: review_sample.append(sample)
        elif status=='NO_MATCH' and len(no_match_sample)<30: no_match_sample.append(sample)
        if status!='AUTO': exceptions.append(row)

    for g,members in groups.items():
        member_status=[next(r['status'] for r in mapping if r['220_sku']==i['220_sku'] and r['220_ean']==i['220_ean']) for i in members]
        group_status[Counter(member_status).most_common(1)[0][0]]+=1
    map_fields=['220_sku','220_ean','group_key','group_size','brand','product_type','master_220_title','shopify_title','variant_title','shopify_category_hint','existing_220_category_hint','selected_category_id','selected_category_name','confidence','margin_to_second','status','reason','strong_signals','required_field_ids','source_identified_field_ids','missing_required_field_ids','candidate_json','master_rows','duplicate_conflict_fields','phh_write','master_write','shopify_write']
    gap_fields=['220_sku','220_ean','group_key','category_id','category_name','field_id','required','field_title_lt','field_title_lv','field_title_ee','field_title_fi','field_title_ru','semantic','source_status','source','source_value','category_decision_status']
    mapping_bytes=v3._csv_bytes(mapping,map_fields); dataset_hash=hashlib.sha256(mapping_bytes).hexdigest()
    summary={'status':'PASS','mapping_version':'family-aware-v4','identity_count':len(ids),'group_count':len(groups),'group_consensus_count':len(consensus),'group_build':group_diag,'group_status_counts':dict(group_status),'taxonomy_categories_fetched':tax_summary.get('categories_fetched'),'mapping_status_counts':dict(status_counts),'input_coverage':dict(input_coverage),'score_buckets':dict(score_buckets),'top_selected_categories':dict(selected_counts.most_common(20)),'blocked_attributes_sample':blocked_sample,'high_confidence_review_sample':review_sample,'no_match_sample':no_match_sample,'exception_count':len(exceptions),'attribute_gap_rows':len(gaps),'dataset_hash':dataset_hash,'phh_writes':0,'master_writes':0,'shopify_writes':0,'product_xml_publication':'OFF','classification_method':'exact family-aware grouping + conservative English leaf evidence + terminal Shopify category + single-token/generic guards; no fuzzy identity matching'}
    artifacts={'current-product-category-mapping.csv':mapping_bytes,'current-product-category-exceptions.csv':v3._csv_bytes(exceptions,map_fields),'current-product-attribute-gap.csv':v3._csv_bytes(gaps,gap_fields),'current-product-category-summary.json':json.dumps(summary,ensure_ascii=False,sort_keys=True,indent=2).encode()}
    v3._persist(db,summary,artifacts); return summary,artifacts
