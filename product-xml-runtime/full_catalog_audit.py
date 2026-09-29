from __future__ import annotations
import csv, io, json, os, time
from collections import Counter, defaultdict
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from app import _master_rows, _shopify_token
from pmp_api_probe import _api_login
from phh_master_audit_v30 import lookup_ean, scan_offers, _offer_eans, _offer_skus

API_VERSION='2026-07'

def _norm(v): return '' if v is None else str(v).strip()
def _csv(rows, fields):
    s=io.StringIO(newline=''); w=csv.DictWriter(s,fieldnames=fields); w.writeheader(); w.writerows(rows)
    return s.getvalue().encode('utf-8-sig')
def _json(o): return json.dumps(o,sort_keys=True,indent=2,ensure_ascii=False).encode('utf-8')

def _shopify_all_variants():
    token=_shopify_token(); shop=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
    url=f'https://{shop}/admin/api/{API_VERSION}/graphql.json'
    query='''query FullCatalog($after:String){productVariants(first:250,after:$after,sortKey:ID){pageInfo{hasNextPage endCursor} nodes{id sku barcode title selectedOptions{name value} product{id title description vendor productType status category{id fullName} featuredMedia{... on MediaImage{image{url width height}}}}}}}'''
    rows=[]; after=None; pages=0
    while True:
        r=requests.post(url,headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},json={'query':query,'variables':{'after':after}},timeout=90)
        r.raise_for_status(); p=r.json()
        if p.get('errors'): raise RuntimeError('Shopify GraphQL errors: '+json.dumps(p['errors'])[:1500])
        conn=((p.get('data') or {}).get('productVariants') or {}); nodes=conn.get('nodes') or []; pages+=1
        for v in nodes:
            product=v.get('product') or {}; cat=product.get('category') or {}; fm=product.get('featuredMedia') or {}; image=fm.get('image') or {}
            rows.append({
                'shopify_product_id':_norm(product.get('id')),'shopify_variant_id':_norm(v.get('id')),
                'shopify_sku':_norm(v.get('sku')),'shopify_barcode':_norm(v.get('barcode')),
                'shopify_title':_norm(product.get('title')),'variant_title':_norm(v.get('title')),
                'vendor':_norm(product.get('vendor')),'product_type':_norm(product.get('productType')),
                'shopify_status':_norm(product.get('status')),'shopify_category_id':_norm(cat.get('id')),
                'shopify_category_name':_norm(cat.get('fullName')),'description_present':'YES' if _norm(product.get('description')) else 'NO',
                'featured_image_url':_norm(image.get('url')),'featured_image_width':image.get('width') or '',
                'featured_image_height':image.get('height') or '',
                'selected_options_json':json.dumps(v.get('selectedOptions') or [],ensure_ascii=False,separators=(',',':'))
            })
        pi=conn.get('pageInfo') or {}
        if not pi.get('hasNextPage'): break
        nxt=pi.get('endCursor')
        if not nxt or nxt==after: raise RuntimeError('Shopify pagination cursor did not advance')
        after=nxt
        if pages>10000: raise RuntimeError('Shopify pagination safety limit exceeded')
    return rows,pages

def run():
    started=time.time(); master=_master_rows(); variants,pages=_shopify_all_variants()
    master_by_vid=defaultdict(list); master_by_sku=defaultdict(list); master_by_ean=defaultdict(list)
    for i,m in enumerate(master,2):
        vid=_norm(m.get('shopify_variant_id')); sku=_norm(m.get('220_sku') or m.get('shopify_sku')); ean=_norm(m.get('220_ean') or m.get('shopify_barcode'))
        if vid: master_by_vid[vid].append((i,m))
        if sku: master_by_sku[sku].append((i,m))
        if ean: master_by_ean[ean].append((i,m))
    shop_skus=Counter(_norm(v['shopify_sku']) for v in variants if _norm(v['shopify_sku']))
    shop_eans=Counter(_norm(v['shopify_barcode']) for v in variants if _norm(v['shopify_barcode']))
    rec=[]; prelim=[]; ready=[]; exc=[]; matched_master_rows=set(); counts=Counter()
    for v in variants:
        vid=v['shopify_variant_id']; sku=v['shopify_sku']; ean=v['shopify_barcode']; reasons=[]; candidates=[]
        if vid and master_by_vid.get(vid): candidates=master_by_vid[vid]; match_basis='VARIANT_ID'
        else:
            s=master_by_sku.get(sku,[]) if sku else []; b=master_by_ean.get(ean,[]) if ean else []
            unique={(r,m.get('shopify_variant_id','')):m for r,m in s+b}
            candidates=[(r,m) for (r,_),m in unique.items()]
            match_basis='SKU_EAN_FALLBACK'
        if shop_skus.get(sku,0)>1 and sku: reasons.append('DUPLICATE_SHOPIFY_SKU')
        if shop_eans.get(ean,0)>1 and ean: reasons.append('DUPLICATE_SHOPIFY_EAN')
        if not sku: reasons.append('MISSING_SHOPIFY_SKU')
        if not ean: reasons.append('MISSING_SHOPIFY_EAN')
        if len(candidates)==0: status='NEW'
        elif len(candidates)>1: status='AMBIGUOUS'
        else:
            status='MATCHED'; matched_master_rows.add(candidates[0][0])
            m=candidates[0][1]
            if sku and _norm(m.get('220_sku') or m.get('shopify_sku')) not in ('',sku): reasons.append('SKU_MISMATCH')
            if ean and _norm(m.get('220_ean') or m.get('shopify_barcode')) not in ('',ean): reasons.append('EAN_MISMATCH')
        if reasons and status=='MATCHED': status='DUPLICATE' if any(x.startswith('DUPLICATE_') for x in reasons) else 'AMBIGUOUS'
        counts[status]+=1
        row={**v,'reconciliation_status':status,'match_basis':match_basis,'master_row':candidates[0][0] if len(candidates)==1 else '','reasons':'|'.join(reasons)}
        rec.append(row)
        candidate_blockers=list(reasons)
        # NEW is a valid Product XML discovery state: PHH existence decides CREATE vs SKIP.\n        # Only ambiguous/duplicate reconciliation is unsafe and must stop before PHH identity gating.\n        if status not in ('MATCHED','NEW'): candidate_blockers.append('RECONCILIATION_'+status)
        if v['shopify_status']!='ACTIVE': candidate_blockers.append('SHOPIFY_NOT_ACTIVE')
        if not v['shopify_title']: candidate_blockers.append('MISSING_TITLE')
        if v['description_present']!='YES': candidate_blockers.append('MISSING_DESCRIPTION')
        if not v['featured_image_url']: candidate_blockers.append('MISSING_FEATURED_IMAGE')
        if not candidate_blockers: prelim.append(row)
        else: exc.append({**row,'candidate_blockers':'|'.join(dict.fromkeys(candidate_blockers))})
    # Identity/existence gate MUST run before category/attributes and before FHM GTIN handling.
    # Existing 220 cards are never CREATE candidates. New FHM is parked until Latvian GTINs are supplied.
    lr=_api_login('v3'); lr.raise_for_status(); phh_token=lr.json()['token']
    offers=scan_offers(phh_token)
    by_ean=defaultdict(list); by_sku=defaultdict(list)
    for o in offers:
        for x in _offer_eans(o): by_ean[x].append(o)
        for x in _offer_skus(o): by_sku[x].append(o)
    identity_counts=Counter()
    # Pilot mode: prioritize clean NEW non-FHM rows and bound slow PHH barcode checks.
    # Unchecked rows are explicitly deferred, never assumed absent from 220.
    pilot_lookup_limit=max(1,int(os.getenv('FULL_CATALOG_IDENTITY_LOOKUP_LIMIT','200')))
    lookup_priority=sorted(
        (row for row in prelim
         if _norm(row.get('shopify_barcode'))
         and not by_ean.get(_norm(row.get('shopify_barcode')))
         and not by_sku.get(_norm(row.get('shopify_sku')))),
        key=lambda row: (
            0 if row.get('reconciliation_status')=='NEW' and _norm(row.get('vendor')).casefold()!='fhm' else 1,
            _norm(row.get('shopify_product_id')),
            _norm(row.get('shopify_sku'))
        )
    )
    lookup_eans=[]; seen_lookup_eans=set()
    for row in lookup_priority:
        ean=_norm(row.get('shopify_barcode'))
        if ean in seen_lookup_eans: continue
        seen_lookup_eans.add(ean); lookup_eans.append(ean)
        if len(lookup_eans)>=pilot_lookup_limit: break
    barcode_lookup={}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures={pool.submit(lookup_ean,phh_token,ean):ean for ean in lookup_eans}
        for future in as_completed(futures):
            ean=futures[future]
            try: barcode_lookup[ean]=future.result()
            except Exception as e: barcode_lookup[ean]={'exists_220':False,'http':0,'error':f'{type(e).__name__}: {e}','items':[]}
    for row in prelim:
        sku=_norm(row.get('shopify_sku')); ean=_norm(row.get('shopify_barcode'))
        offer_matches=list(by_ean.get(ean,[])) if ean else []
        if not offer_matches and sku: offer_matches=list(by_sku.get(sku,[]))
        exists=bool(offer_matches); lookup_http=''; identity_basis='SELLER_OFFER' if exists else ''
        if not exists and ean:
            if ean not in barcode_lookup:
                identity_counts['IDENTITY_DEFERRED']+=1
                exc.append({**row,'identity_status':'IDENTITY_DEFERRED','identity_basis':'PILOT_LIMIT','phh_lookup_http':'','candidate_blockers':'IDENTITY_DEFERRED'})
                continue
            q=barcode_lookup[ean]; lookup_http=q.get('http','')
            if q.get('http') not in (200,404):
                identity_counts['IDENTITY_EXCEPTION']+=1
                exc.append({**row,'identity_status':'IDENTITY_EXCEPTION','identity_basis':'BARCODE_LOOKUP','phh_lookup_http':lookup_http,'candidate_blockers':'IDENTITY_EXCEPTION'})
                continue
            exists=bool(q.get('exists_220')); identity_basis='BARCODE_LOOKUP' if exists else 'BARCODE_ABSENT'
        if exists:
            identity_counts['SKIP_EXISTING_220']+=1
            exc.append({**row,'identity_status':'SKIP_EXISTING_220','identity_basis':identity_basis,'phh_lookup_http':lookup_http,'candidate_blockers':'SKIP_EXISTING_220'})
        elif _norm(row.get('vendor')).casefold()=='fhm':
            identity_counts['FHM_GTIN_PENDING']+=1
            exc.append({**row,'identity_status':'FHM_GTIN_PENDING','identity_basis':identity_basis or 'NOT_FOUND_220','phh_lookup_http':lookup_http,'candidate_blockers':'FHM_GTIN_PENDING'})
        else:
            identity_counts['CREATE_CANDIDATE']+=1
            ready.append({**row,'identity_status':'CREATE_CANDIDATE','identity_basis':identity_basis or 'NOT_FOUND_220','phh_lookup_http':lookup_http})
    for i,m in enumerate(master,2):
        if i in matched_master_rows: continue
        sku=_norm(m.get('220_sku') or m.get('shopify_sku')); ean=_norm(m.get('220_ean') or m.get('shopify_barcode')); vid=_norm(m.get('shopify_variant_id'))
        exc.append({'shopify_product_id':_norm(m.get('shopify_product_id')),'shopify_variant_id':vid,'shopify_sku':sku,'shopify_barcode':ean,'shopify_title':_norm(m.get('shopify_title')),'variant_title':_norm(m.get('variant_title')),'vendor':_norm(m.get('vendor')),'product_type':_norm(m.get('product_type')),'shopify_status':_norm(m.get('shopify_status')),'shopify_category_id':'','shopify_category_name':'','description_present':'','featured_image_url':'','featured_image_width':'','featured_image_height':'','selected_options_json':'','reconciliation_status':'MISSING_IN_SHOPIFY','match_basis':'','master_row':i,'reasons':'','candidate_blockers':'MISSING_IN_SHOPIFY'})
        counts['MISSING_IN_SHOPIFY']+=1
    rec_fields=list(rec[0]) if rec else ['shopify_variant_id','shopify_sku','shopify_barcode','reconciliation_status']
    ready_fields=list(ready[0]) if ready else rec_fields+['identity_status','identity_basis','phh_lookup_http']
    exc_fields=[]
    for x in exc:
        for k in x:
            if k not in exc_fields: exc_fields.append(k)
    # Read-only PHH taxonomy probe for the first Product XML pilot.
    pilot_category_probe={'sku':'80673','ean':'0021563806731','status':'NOT_RUN','candidates':[]}
    try:
        import current_product_category_audit as _cat
        _ts,_cats,_attrs=_cat._latest_taxonomy(db)
        _required=defaultdict(list)
        for _a in _attrs:
            if str(_a.get('required')).casefold() in {'true','1','yes'}:
                _required[str(_a.get('category_id'))].append({k:_a.get(k,'') for k in ('field_id','title_en','title_lv','title_ru','required')})
        _terms=('paracord','utility cord','utility cords','rope','cord')
        _hits=[]
        for _x in _cats:
            if str(_x.get('allow_add_products')).casefold() not in {'true','1','yes'}: continue
            _txt=' '.join(str(_x.get(k,'') or '') for k in ('title_en','title_lv','title_lt','title_ee','title_fi','title_ru')).casefold()
            _matched=[t for t in _terms if t in _txt]
            if not _matched: continue
            _cid=str(_x.get('category_id'))
            _hits.append({'category_id':_cid,'parent_id':str(_x.get('parent_id') or ''),'title_en':_x.get('title_en',''),'title_lv':_x.get('title_lv',''),'title_ru':_x.get('title_ru',''),'matched_terms':_matched,'required_fields':_required.get(_cid,[])})
        pilot_category_probe={'sku':'80673','ean':'0021563806731','status':'PASS','query_terms':list(_terms),'candidate_count':len(_hits),'candidates':_hits[:30]}
    except Exception as _e:
        pilot_category_probe={'sku':'80673','ean':'0021563806731','status':'ERROR','error':f'{type(_e).__name__}: {_e}','candidates':[]}
    summary={'status':'PASS','read_only':True,'shopify_pages':pages,'shopify_variants':len(variants),'master_rows':len(master),'matched':counts['MATCHED'],'new':counts['NEW'],'ambiguous':counts['AMBIGUOUS'],'duplicate':counts['DUPLICATE'],'missing_in_shopify':counts['MISSING_IN_SHOPIFY'],'preliminary_ready_candidates':len(prelim),'identity_lookup_limit':pilot_lookup_limit,'unique_barcode_lookups':len(lookup_eans),'create_candidates_after_identity':len(ready),'top_create_candidates':[{k:row.get(k,'') for k in ('shopify_sku','shopify_barcode','vendor','shopify_category_id','shopify_category_name','product_type','shopify_title')} for row in ready[:10]],'pilot_80673_category_probe':pilot_category_probe,'identity_status_counts':dict(identity_counts),'exceptions':len(exc),'elapsed_seconds':round(time.time()-started,3),'note':'Preliminary ready means identity + active + title + description + featured image only; PHH category/attributes/image-count/package gates are evaluated downstream.'}
    return {
        'full-catalog-summary.json':_json(summary),
        'full-catalog-reconciliation.csv':_csv(rec,rec_fields),
        'full-catalog-ready-candidates.csv':_csv(ready,ready_fields),
        'full-catalog-exceptions.csv':_csv(exc,exc_fields)
    },summary
