from __future__ import annotations
import csv, io, json, os, re
from collections import Counter, defaultdict
import requests
from app import _master_rows, _shopify_products, _shopify_token

SIZE_TOKENS={'xs','s','m','l','xl','2xl','3xl','4xl','5xl','6xl','xxl','xxxl','onesize','one-size','sm','lxl'}
def _norm(v): return re.sub(r'\s+',' ',str(v or '').strip())
def _csv(rows,fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields); w.writeheader(); w.writerows(rows); return s.getvalue()
def _family(sku):
    p=_norm(sku).split('-')
    return '-'.join(p[:-1]) if len(p)>=3 and p[-1].lower() in SIZE_TOKENS else _norm(sku)

def title_review(master):
    target=[r for r in master if _norm(r.get('220_title_candidate_status'))=='REVIEW_REQUIRED']
    by_title=defaultdict(list)
    for r in target: by_title[_norm(r.get('220_title_candidate')).casefold()].append(r)
    out=[]
    for r in target:
        cand=_norm(r.get('220_title_candidate')); peers=by_title[cand.casefold()]
        reasons=[]
        if len(cand)<15: reasons.append('ANOMALOUSLY_SHORT')
        if len(cand)>80: reasons.append('ANOMALOUSLY_LONG')
        letters=[c for c in cand if c.isalpha()]
        if len(letters)>=8 and sum(c.isupper() for c in letters)/len(letters)>.85: reasons.append('EXCESSIVE_ALL_CAPS')
        if len(peers)>1: reasons.append('DUPLICATE_CANDIDATE_TITLE')
        pids={_norm(x.get('shopify_product_id')) for x in peers if _norm(x.get('shopify_product_id'))}
        fams={_family(x.get('220_sku')) for x in peers}
        result='MANUAL_REVIEW_REQUIRED'; resolved=''; method='NO_SAFE_AUTOMATIC_TEXT_TRANSFORMATION'
        if reasons==['DUPLICATE_CANDIDATE_TITLE']:
            if len(pids)==1:
                result='AUTO_RESOLVED'; resolved=cand; method='SAME_EXACT_SHOPIFY_PRODUCT_VARIANTS'
            elif len(fams)==1:
                result='AUTO_RESOLVED'; resolved=cand; method='SAME_EXACT_SKU_FAMILY_VARIANTS'
            else:
                result='BLOCKED_NO_SAFE_SOURCE'; method='DUPLICATE_SPANS_MULTIPLE_PRODUCTS_OR_FAMILIES'
        elif 'DUPLICATE_CANDIDATE_TITLE' in reasons and len(fams)>1 and not pids:
            result='BLOCKED_NO_SAFE_SOURCE'; method='MULTIPLE_FLAGS_AND_NO_SAFE_PRODUCT_SOURCE'
        out.append({'220_sku':_norm(r.get('220_sku')),'220_ean':_norm(r.get('220_ean')),'220_title_candidate':cand,'review_reasons':'|'.join(reasons),'resolution_status':result,'resolved_title_candidate':resolved,'resolution_method':method,'authoritative_write':'NO'})
    return out

def package_sources(master,shopify):
    safe=[r for r in master if _norm(r.get('shopify_product_id')) and _norm(r.get('shopify_variant_id')) and _norm(r.get('shopify_product_id')) in shopify and _norm(r.get('shopify_variant_id')) in shopify[_norm(r.get('shopify_product_id'))].get('variants_by_id',{})]
    pids=sorted({_norm(r.get('shopify_product_id')) for r in safe})
    token=_shopify_token(); domain=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
    q='''query DimSources($ids:[ID!]!){nodes(ids:$ids){... on Product{id metafields(first:100){nodes{namespace key type value}} variants(first:100){nodes{id metafields(first:100){nodes{namespace key type value}}}}}}}'''
    byp={}
    for i in range(0,len(pids),25):
        ids=pids[i:i+25]
        rr=requests.post(f'https://{domain}/admin/api/2026-07/graphql.json',headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},json={'query':q,'variables':{'ids':ids}},timeout=90); rr.raise_for_status(); payload=rr.json()
        if payload.get('errors'): raise RuntimeError('Shopify metafield errors: '+json.dumps(payload['errors'])[:1500])
        for node in (payload.get('data') or {}).get('nodes') or []:
            if not node: continue
            byp[node['id']]={'product':(node.get('metafields') or {}).get('nodes') or [],'variants':{v['id']:(v.get('metafields') or {}).get('nodes') or [] for v in (node.get('variants') or {}).get('nodes') or []}}
    dimname=re.compile(r'(?i)(package|packaged|shipping|parcel|box|length|width|height|depth|dimension|weight)')
    numericdim=re.compile(r'(?i)(package|packaged|shipping|parcel|box).*(length|width|height|depth)|(length|width|height|depth).*(package|packaged|shipping|parcel|box)')
    out=[]
    for r in safe:
        pid=_norm(r.get('shopify_product_id')); vid=_norm(r.get('shopify_variant_id')); hits=[]; dimhits=[]
        for scope,mfs in [('product',byp.get(pid,{}).get('product',[])),('variant',byp.get(pid,{}).get('variants',{}).get(vid,[]))]:
            for m in mfs:
                nk=f"{m.get('namespace','')}.{m.get('key','')}"; val=_norm(m.get('value'))
                if dimname.search(nk):
                    rec={'scope':scope,'name':nk,'type':m.get('type') or '','value':val}; hits.append(rec)
                    if numericdim.search(nk) and val: dimhits.append(rec)
        weight=_norm(r.get('220_weight_kg'))
        status='AUTHORITATIVE_DIMENSIONS_SOURCE_FOUND' if dimhits else ('WEIGHT_ONLY' if weight else 'NO_PACKAGE_SOURCE')
        out.append({'220_sku':_norm(r.get('220_sku')),'220_ean':_norm(r.get('220_ean')),'shopify_product_id':pid,'shopify_variant_id':vid,'220_weight_kg':weight,'package_related_metafields_json':json.dumps(hits,ensure_ascii=False,separators=(',',':')),'authoritative_dimension_metafields_json':json.dumps(dimhits,ensure_ascii=False,separators=(',',':')),'dimension_source_status':status})
    return out

def run():
    master=_master_rows(); shopify=_shopify_products(master)
    tr=title_review(master); pkg=package_sources(master,shopify)
    trc=Counter(x['resolution_status'] for x in tr); pc=Counter(x['dimension_source_status'] for x in pkg)
    summary={'title_review_total':len(tr),'title_review':dict(trc),'package_source_total':len(pkg),'package_dimension_source':dict(pc),'master_writes':0,'shopify_writes':0,'phh_sends':0,'product_xml_publication':'OFF'}
    arts={'title-review-340.csv':_csv(tr,list(tr[0])),'package-dimension-source-673.csv':_csv(pkg,list(pkg[0])),'metadata-summary.json':json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)}
    db=os.getenv('DATABASE_URL')
    if db:
        import psycopg
        with psycopg.connect(db) as c:
            with c.cursor() as cur:
                cur.execute('create table if not exists product_xml_noncategory_audit (id integer primary key default 1, updated_at timestamptz not null default now(), summary jsonb not null, artifacts jsonb not null)')
                cur.execute('insert into product_xml_noncategory_audit(id,summary,artifacts) values(1,%s::jsonb,%s::jsonb) on conflict(id) do update set updated_at=now(),summary=excluded.summary,artifacts=excluded.artifacts',(json.dumps(summary),json.dumps(arts)))
            c.commit()
    return summary
