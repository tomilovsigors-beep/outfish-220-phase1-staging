from __future__ import annotations

import csv, io, json
from collections import Counter

import current_product_category_audit as v3
import current_product_category_audit_v4 as v4
import current_product_category_audit_v5 as v5


def _latest_v5_registry(db):
    b=v5.load_latest_artifact(db,'family_category_registry.csv')
    if not b: raise RuntimeError('v5 family registry unavailable')
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))


def _score_priority(r):
    mc=int(r.get('member_count') or 0)
    titles=bool((r.get('representative_titles') or '').strip())
    sh=bool((r.get('shopify_hint') or '').strip())
    pt=bool((r.get('product_type') or '').strip())
    brand=bool((r.get('brand') or '').strip())
    # coverage dominates; evidence richness breaks ties
    return (mc, int(pt)+int(sh)+int(titles)+int(brand), int(sh), int(pt), int(titles))


def _cat_path(cats,cid):
    by={str(c.get('category_id')):c for c in cats}
    parts=[]; seen=set(); cur=by.get(str(cid))
    while cur and str(cur.get('category_id')) not in seen:
        seen.add(str(cur.get('category_id')))
        parts.append(v3._norm(cur.get('title_en')) or v3._norm(cur.get('title_lv')) or str(cur.get('category_id')))
        pid=v3._norm(cur.get('parent_id'))
        cur=by.get(pid) if pid else None
    return ' > '.join(reversed(parts))


def run_probe(master_rows,shopify,db,limit=40):
    registry=_latest_v5_registry(db)
    unresolved=[r for r in registry if r.get('status')=='UNRESOLVED']
    unresolved.sort(key=_score_priority,reverse=True)
    tax_summary,cats,attrs=v3._latest_taxonomy(db)
    top=[]
    for r in unresolved[:limit]:
        candidates=[]
        try:
            candidates=json.loads(r.get('v4_candidates') or '[]')
        except Exception:
            candidates=[]
        cand_out=[]
        for c in candidates[:3]:
            cid=str(c.get('category_id') or '')
            cand_out.append({'category_id':cid,'category_name':c.get('category_name'),'score':c.get('score'),'path':_cat_path(cats,cid) if cid else ''})
        top.append({
            'family_key':r.get('family_key'),'member_count':int(r.get('member_count') or 0),
            'member_skus':(r.get('member_skus') or '').split('|')[:20],
            'representative_titles':(r.get('representative_titles') or '').split(' || ')[:12],
            'shopify_hint':(r.get('shopify_hint') or '').split(' || ')[:8],
            'product_type':(r.get('product_type') or '').split(' || ')[:8],
            'brand':(r.get('brand') or '').split(' || ')[:8],
            'v4_statuses':r.get('v4_statuses'),'top_candidates':cand_out,
            'priority_tuple':_score_priority(r)
        })
    return {'status':'PASS','unresolved_total':len(unresolved),'returned':len(top),'top_families':top,'phh_writes':0,'master_writes':0,'shopify_writes':0,'product_xml_publication':'OFF'}
