from __future__ import annotations

import csv, io, json, os, re
from collections import defaultdict

import current_product_category_audit as v3
from current_product_identity_audit_v9 import load_latest_artifact

CONTROLLED_TERMS={
    'shirt': ('shirt','shirts','blouse','blouses'),
    'jacket': ('jacket','jackets','outerwear'),
    'trouser': ('trouser','trousers','pants'),
    'short': ('short','shorts'),
    'hoodie': ('hoodie','hoodies','sweatshirt','sweater','jumper','pullover'),
    'rain': ('rain','waterproof'),
    'bib': ('bib','overall','overalls'),
}


def _rows(b):
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))


def _tokens(text):
    s=v3._norm(text).casefold()
    out=[]
    for key,words in CONTROLLED_TERMS.items():
        if any(re.search(r'(?<![a-z])'+re.escape(w)+r'(?![a-z])',s) for w in words):
            out.append(key)
    return sorted(set(out))


def run(db):
    b=load_latest_artifact(db,'v9-leaf-ready-queue.csv')
    if not b: raise RuntimeError('v9-leaf-ready-queue.csv unavailable')
    ready=_rows(b)
    tsum,cats,attrs=v3._latest_taxonomy(db)
    addable=[]
    for c in cats:
        if str(c.get('allow_add_products')).casefold() not in {'true','1','yes'}: continue
        titles={k:v3._norm(c.get(k)) for k in ('title_en','title_lv','title_lt','title_ee','title_fi','title_ru')}
        text=' '.join(titles.values()).casefold()
        addable.append((c,titles,text))
    packets=[]
    for r in ready:
        evidence=' '.join([r.get('family_name',''),r.get('product_type',''),r.get('product_subtype',''),r.get('audience','')])
        terms=_tokens(evidence)
        candidates=[]
        for c,titles,text in addable:
            matched=[]
            for term in terms:
                for w in CONTROLLED_TERMS[term]:
                    if w in text:
                        matched.append(term); break
            if matched:
                candidates.append({
                    'category_id':str(c.get('category_id')),
                    'parent_id':str(c.get('parent_id') or ''),
                    'matched_terms':sorted(set(matched)),
                    **titles,
                })
        # Keep only concise candidate neighborhood for logs/artifact. No category choice here.
        candidates.sort(key=lambda x:(-len(x['matched_terms']),x.get('title_en',''),x['category_id']))
        packet={
            'family_key':r.get('family_key'),
            'brand':r.get('brand'),
            'family_name':r.get('family_name'),
            'member_count':int(r.get('member_count') or 0),
            'identity_status':r.get('identity_status'),
            'product_type':r.get('product_type'),
            'product_subtype':r.get('product_subtype'),
            'audience':r.get('audience'),
            'remaining_non_identity_gaps':r.get('remaining_non_identity_gaps'),
            'controlled_terms':terms,
            'candidate_count':len(candidates),
            'candidate_neighborhood':candidates[:40],
        }
        packets.append(packet)
        print('V10_LEAF_PACKET '+json.dumps(packet,ensure_ascii=False,sort_keys=True),flush=True)
    summary={
        'status':'PASS',
        'mode':'READ_ONLY_PROBE',
        'families':len(packets),
        'sku_represented':sum(x['member_count'] for x in packets),
        'taxonomy_categories':tsum.get('categories_fetched'),
        'assignments_made':0,
        'product_breakdown_changed':False,
        'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','stock_changes':0,'price_changes':0},
        'principle':'Identity PASS is necessary but not sufficient. Probe exact live PHH leaf neighborhood before any category rule.'
    }
    print('V10_LEAF_PROBE_RESULT '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
    return summary,packets
