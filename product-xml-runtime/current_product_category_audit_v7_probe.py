from __future__ import annotations

import csv, io, json
from collections import Counter

import current_product_category_audit as v3
import current_product_category_audit_v4 as v4
import current_product_category_audit_v6 as v6

TOP_N=30
GENERIC_TYPES={'','product','products','item','items','general','other'}
ACCESSORY_WORDS=('accessory','accessories','adapter','replacement','cover','liner','insole','insert','case','bag','wall','net','strap','holder','stand','part','parts')
GENDER_WORDS=('women','woman','female','men','man','male','girls','girl','boys','boy','kids','kid','children','child')


def _rows_from_artifact(db,name):
    b=v6.load_latest_artifact(db,name)
    if not b:
        raise RuntimeError(f'v6 artifact unavailable: {name}')
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))


def _json_or_empty(v):
    try:
        x=json.loads(v or '[]')
        return x if isinstance(x,list) else []
    except Exception:
        return []


def _cat_path(by_cat,cid):
    out=[]; seen=set(); cur=by_cat.get(str(cid))
    while cur and str(cur.get('category_id')) not in seen:
        k=str(cur.get('category_id')); seen.add(k)
        out.append(v3._norm(cur.get('title_en')) or v3._norm(cur.get('title_lv')) or k)
        pid=v3._norm(cur.get('parent_id'))
        cur=by_cat.get(pid) if pid else None
    return ' > '.join(reversed(out))


def _gap_types(reg_row,members,candidates):
    titles={v3._norm(x.get('shopify_title') or x.get('220_title')) for x in members if v3._norm(x.get('shopify_title') or x.get('220_title'))}
    vendors={v3._norm(x.get('vendor')) for x in members if v3._norm(x.get('vendor'))}
    ptypes={v3._norm(x.get('product_type')) for x in members if v3._norm(x.get('product_type')).casefold() not in GENERIC_TYPES}
    pids={v3._norm(x.get('shopify_product_id')) for x in members if v3._norm(x.get('shopify_product_id'))}
    txt=' '.join(list(titles)+list(ptypes)+[v3._norm(reg_row.get('shopify_hint'))]).casefold()
    gaps=[]
    if not ptypes:
        gaps.append('MISSING_EXACT_PRODUCT_TYPE')
    if len(titles)>1 or len(vendors)>1 or len(ptypes)>1 or len(pids)>1:
        gaps.append('FAMILY_HOMOGENEITY_INSUFFICIENT')
    if any(w in txt for w in ACCESSORY_WORDS):
        gaps.append('ACCESSORY_VS_MAIN_PRODUCT_AMBIGUITY')
    cand_titles=' '.join(v3._norm(c.get('category_name')) for c in candidates).casefold()
    if any(w in cand_titles for w in GENDER_WORDS) and not any(w in txt for w in GENDER_WORDS):
        gaps.append('GENDER_AUDIENCE_UNPROVEN')
    if not candidates:
        gaps.append('NO_PROVEN_PHH_LEAF_CANDIDATE')
    else:
        gaps.append('EXACT_PHH_LEAF_NOT_PROVEN')
    return gaps


def run_probe(master_rows,shopify,db,top_n=TOP_N):
    tax_summary,cats,attrs=v3._latest_taxonomy(db)
    by_cat,_,_=v3._category_index(cats,attrs)
    ids=v3._canonical_identities(master_rows)
    groups0,membership,diag=v4._build_groups(ids)
    groups=dict(groups0)
    queue=_rows_from_artifact(db,'v6-family-priority-queue.csv')
    queue=[r for r in queue if r.get('v6_disposition')!='ACCEPTED_RULE']
    queue.sort(key=lambda r:(-int(r.get('member_count') or 0),int(r.get('priority_rank') or 10**9)))
    selected=queue[:top_n]
    gap_counts=Counter(); out=[]
    for rank,r in enumerate(selected,1):
        key=r.get('family_key') or ''
        members=groups.get(key) or []
        cands=[]
        for c in _json_or_empty(r.get('v4_candidates'))[:5]:
            cid=str(c.get('category_id') or '')
            cat=by_cat.get(cid) or {}
            cands.append({
                'category_id':cid,
                'category_name':c.get('category_name') or v3._norm(cat.get('title_en')),
                'score':c.get('score'),
                'allow_add_products':cat.get('allow_add_products'),
                'path':_cat_path(by_cat,cid) if cid else '',
            })
        gaps=_gap_types(r,members,cands)
        gap_counts.update(gaps)
        exact_titles=sorted({v3._norm(x.get('shopify_title') or x.get('220_title')) for x in members if v3._norm(x.get('shopify_title') or x.get('220_title'))})
        ptypes=sorted({v3._norm(x.get('product_type')) for x in members if v3._norm(x.get('product_type'))})
        vendors=sorted({v3._norm(x.get('vendor')) for x in members if v3._norm(x.get('vendor'))})
        pids=sorted({v3._norm(x.get('shopify_product_id')) for x in members if v3._norm(x.get('shopify_product_id'))})
        variant_titles=sorted({v3._norm(x.get('variant_title')) for x in members if v3._norm(x.get('variant_title'))})
        rec={
            'rank':rank,'family_key':key,'member_count':len(members),
            'member_skus':[v3._norm(x.get('220_sku')) for x in members],
            'exact_titles':exact_titles,'product_types':ptypes,'vendors':vendors,'shopify_product_ids':pids,
            'variant_titles':variant_titles[:30],
            'shopify_hint':v3._norm(r.get('shopify_hint')),
            'brand':v3._norm(r.get('brand')),
            'candidate_leafs':cands,
            'evidence_gaps':gaps,
            'v6_priority_rank':r.get('priority_rank'),
        }
        out.append(rec)
        print('V7_FAMILY_DIAG '+json.dumps(rec,ensure_ascii=False,sort_keys=True),flush=True)
    summary={
        'status':'PASS','probe_version':'targeted-leaf-adjudication-v7-probe','products_total':len(ids),
        'families_total':len(groups),'families_examined':len(out),'top_n_requested':top_n,
        'gap_type_counts':dict(gap_counts),'group_build':diag,'taxonomy_categories_fetched':tax_summary.get('categories_fetched'),
        'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'Product_XML':'OFF','stock_changes':0,'price_changes':0},
    }
    print('V7_TARGETED_LEAF_PROBE_RESULT '+json.dumps(summary,sort_keys=True),flush=True)
    return summary,out
