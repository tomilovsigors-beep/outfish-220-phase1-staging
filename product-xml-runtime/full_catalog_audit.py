from __future__ import annotations
import csv, io, json, os, time, re
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
    query='''query FullCatalog($after:String){productVariants(first:250,after:$after,sortKey:ID){pageInfo{hasNextPage endCursor} nodes{id sku barcode price inventoryQuantity title selectedOptions{name value} product{id title description vendor productType status category{id fullName} featuredMedia{... on MediaImage{image{url width height}}}}}}}'''
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
                'shopify_sku':_norm(v.get('sku')),'shopify_barcode':_norm(v.get('barcode')),'shopify_price_live':_norm(v.get('price')),'shopify_stock_live':v.get('inventoryQuantity'),
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

def _phh_description(title,body):
    title=_norm(title); body=_norm(body)
    if not title or not body: return ''
    # Remove one existing leading H2; PHH requires its own exact title H2 prefix.
    body=re.sub(r'^\\s*<h2\\b[^>]*>.*?</h2>\\s*','',body,count=1,flags=re.I|re.S)
    return '<h2>'+title+'</h2><p><br></p>'+body

def _shopify_translation_index():
    token=_shopify_token(); shop=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
    url=f'https://{shop}/admin/api/{API_VERSION}/graphql.json'
    query='''query TranslationCoverage($after:String){translatableResources(resourceType:PRODUCT,first:250,after:$after){pageInfo{hasNextPage endCursor} nodes{resourceId lt:translations(locale:"lt"){key value outdated} lv:translations(locale:"lv"){key value outdated} et:translations(locale:"et"){key value outdated} ru:translations(locale:"ru"){key value outdated} fi:translations(locale:"fi"){key value outdated}}}}'''
    out={}; after=None; pages=0; source_counts=Counter(); status='PASS'; error=''
    while True:
        r=requests.post(url,headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},
                        json={'query':query,'variables':{'after':after}},timeout=90)
        r.raise_for_status(); p=r.json()
        if p.get('errors'):
            error=json.dumps(p['errors'],ensure_ascii=False)[:1000]
            status='BLOCKED_SOURCE_SCOPE' if 'read_translations' in error else 'ERROR'
            break
        conn=((p.get('data') or {}).get('translatableResources') or {})
        for node in conn.get('nodes') or []:
            locout={}
            for api_loc,out_loc in (('lt','lt'),('lv','lv'),('et','ee'),('ru','ru'),('fi','fi')):
                arr=node.get(api_loc) or []
                title=next((x for x in arr if x.get('key')=='title' and _norm(x.get('value')) and not x.get('outdated')),None)
                body=next((x for x in arr if x.get('key')=='body_html' and _norm(x.get('value')) and not x.get('outdated')),None)
                if title and body:
                    locout[out_loc]={'title':_norm(title.get('value')),
                                     'description_html':_phh_description(title.get('value'),body.get('value')),
                                     'source':'SHOPIFY_TRANSLATION_FRESH'}
                    source_counts['FRESH_'+out_loc.upper()]+=1
            out[_norm(node.get('resourceId'))]=locout
        pages+=1
        pi=conn.get('pageInfo') or {}
        if not pi.get('hasNextPage'): break
        after=pi.get('endCursor')
        if not after or pages>50: break
    return out,pages,dict(source_counts),status,error

def run():
    started=time.time(); master=_master_rows(); variants,pages=_shopify_all_variants(); translations,translation_pages,translation_source_counts,translation_status,translation_error=_shopify_translation_index()
    master_by_vid=defaultdict(list); master_by_sku=defaultdict(list); master_by_ean=defaultdict(list)
    for i,m in enumerate(master,2):
        vid=_norm(m.get('shopify_variant_id')); sku=_norm(m.get('220_sku') or m.get('shopify_sku')); ean=_norm(m.get('220_ean') or m.get('shopify_barcode'))
        if vid: master_by_vid[vid].append((i,m))
        if sku: master_by_sku[sku].append((i,m))
        if ean: master_by_ean[ean].append((i,m))
    from master_variant_identity import gtin13 as canonical_gtin13
    shop_skus=Counter(_norm(v['shopify_sku']) for v in variants if _norm(v['shopify_sku']))
    shop_eans=Counter(canonical_gtin13(v['shopify_barcode']) for v in variants if canonical_gtin13(v['shopify_barcode']))
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
        if canonical_gtin13(ean) and shop_eans.get(canonical_gtin13(ean),0)>1: reasons.append('DUPLICATE_SHOPIFY_EAN')
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
        if _norm(v.get('vendor')).casefold()=='fhm': candidate_blockers.append('EXCLUDED_FHM')
        # NEW is a valid Product XML discovery state: PHH existence decides CREATE vs SKIP.\n        # Only ambiguous/duplicate reconciliation is unsafe and must stop before PHH identity gating.\n        if status not in ('MATCHED','NEW'): candidate_blockers.append('RECONCILIATION_'+status)
        if v['shopify_status']!='ACTIVE': candidate_blockers.append('SHOPIFY_NOT_ACTIVE')
        if not v['shopify_title']: candidate_blockers.append('MISSING_TITLE')
        if v['description_present']!='YES': candidate_blockers.append('MISSING_DESCRIPTION')
        if not v['featured_image_url']: candidate_blockers.append('MISSING_FEATURED_IMAGE')
        if not candidate_blockers: prelim.append(row)
        else: exc.append({**row,'candidate_blockers':'|'.join(dict.fromkeys(candidate_blockers))})
    # FHM is excluded before any PHH identity lookup. Existing 220 cards are never CREATE candidates.
    # PHH outage must not prevent catalog-wide read-only readiness counts.
    # All identity-unverified variants remain BLOCKED, never CREATE.
    phh_token=None; phh_access_error=''
    try:
        lr=_api_login('v3'); lr.raise_for_status(); phh_token=lr.json()['token']
        offers=scan_offers(phh_token)
    except Exception as e:
        offers=[]; phh_token=None; phh_access_error=f'{type(e).__name__}: {str(e)[:300]}'
    by_ean=defaultdict(list); by_sku=defaultdict(list)
    for o in offers:
        for x in _offer_eans(o): by_ean[x].append(o)
        for x in _offer_skus(o): by_sku[x].append(o)
    identity_counts=Counter()
    # Pilot mode: prioritize clean NEW non-FHM rows and bound slow PHH barcode checks.
    # Unchecked rows are explicitly deferred, never assumed absent from 220.
    pilot_lookup_limit=max(0,int(os.getenv('FULL_CATALOG_IDENTITY_LOOKUP_LIMIT','0')))
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
    for row in (lookup_priority if phh_token and pilot_lookup_limit > 0 else []):
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
            # HTTP 404 on one endpoint does not prove absence in the PHH catalogue.
            if q.get('http') != 200:
                identity_counts['IDENTITY_EXCEPTION']+=1
                exc.append({**row,'identity_status':'IDENTITY_EXCEPTION','identity_basis':'BARCODE_LOOKUP','phh_lookup_http':lookup_http,'candidate_blockers':'IDENTITY_EXCEPTION'})
                continue
            exists=bool(q.get('exists_220')); identity_basis='BARCODE_LOOKUP' if exists else 'BARCODE_ABSENT'
        if exists:
            identity_counts['SKIP_EXISTING_220']+=1
            exc.append({**row,'identity_status':'SKIP_EXISTING_220','identity_basis':identity_basis,'phh_lookup_http':lookup_http,'candidate_blockers':'SKIP_EXISTING_220'})
        else:
            # A negative barcode query alone cannot exhaustively prove absence.
            identity_counts['IDENTITY_UNRESOLVED']+=1
            exc.append({**row,'identity_status':'IDENTITY_UNRESOLVED',
                        'identity_basis':identity_basis or 'NO_POSITIVE_MATCH',
                        'phh_lookup_http':lookup_http,
                        'candidate_blockers':'PHH_ABSENCE_NOT_EXHAUSTIVELY_VERIFIED'})
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
    pilot_category_probe={'sku':'36134-010','ean':'0021563361346','status':'NOT_RUN','candidates':[]}
    try:
        import current_product_category_audit as _cat
        _ts,_cats,_attrs=_cat._latest_taxonomy(os.getenv('DATABASE_URL',''))
        _required=defaultdict(list)
        for _a in _attrs:
            if str(_a.get('required')).casefold() in {'true','1','yes'}:
                _required[str(_a.get('category_id'))].append({k:_a.get(k,'') for k in ('field_id','title_en','title_lv','title_ru','required')})
        _terms=('fabric refresher','fabric freshener','odor eliminator','odour eliminator','odor remover','odour remover','textile care','clothing care','smaku','kvap','tekstil','audum')
        _hits=[]
        for _x in _cats:
            if str(_x.get('allow_add_products')).casefold() not in {'true','1','yes'}: continue
            _txt=' '.join(str(_x.get(k,'') or '') for k in ('title_en','title_lv','title_lt','title_ee','title_fi','title_ru')).casefold()
            _matched=[t for t in _terms if t in _txt]
            if not _matched: continue
            _cid=str(_x.get('category_id'))
            _hits.append({'category_id':_cid,'parent_id':str(_x.get('parent_id') or ''),'title_en':_x.get('title_en',''),'title_lv':_x.get('title_lv',''),'title_ru':_x.get('title_ru',''),'matched_terms':_matched,'required_fields':_required.get(_cid,[])})
        pilot_category_probe={'sku':'36134-010','ean':'0021563361346','status':'PASS','query_terms':list(_terms),'candidate_count':len(_hits),'candidates':_hits[:30]}
    except Exception as _e:
        pilot_category_probe={'sku':'36134-010','ean':'0021563361346','status':'ERROR','error':f'{type(_e).__name__}: {_e}','candidates':[]}
    # Safe identity resolver: do not infer locale evidence from fuzzy matches.
    from master_variant_identity import master_variant_index, resolve_variant, selftest as master_identity_selftest
    master_identity_selftest()
    master_identity_index=master_variant_index(master)
    master_identity_counts=Counter()
    # Single catalog-wide state reducer. Evidence is fail-closed: no published
    # counts are inferred from HTTP 404, unverified categories or Master snapshots.
    from catalog_pipeline_gate import audit as pipeline_audit
    # Reuse the pre-existing audited v4 category mappings, matched by exact
    # variant SKU + checked EAN. V10 predominantly concerns excluded FHM:
    # no broad propagation of category IDs by similar product names.
    from catalog_pipeline_gate import _ean13
    category_by_identity={}
    attribute_by_identity={}
    category_artifact_status='UNAVAILABLE'
    try:
        from current_product_category_audit_v4 import load_latest_artifact as load_v4
        payload=load_v4(os.getenv('DATABASE_URL'),'current-product-category-mapping.csv')
        if payload:
            category_artifact_status='LOADED'
            for oldrow in csv.DictReader(io.StringIO(payload.decode('utf-8-sig'))):
                key=(_norm(oldrow.get('220_sku')),_ean13(oldrow.get('220_ean')))
                if not key[0] or not key[1]: continue
                if key in category_by_identity:
                    category_by_identity[key]=None # Ambiguous: never accept either
                else: category_by_identity[key]=oldrow
    except Exception as e:
        category_artifact_status='ERROR_'+type(e).__name__
    try:
        from current_product_attribute_audit_v11 import load_latest_artifact as load_v11
        payload=load_v11(os.getenv('DATABASE_URL'),'v11-product-attribute-readiness.csv')
        if payload:
            for ar in csv.DictReader(io.StringIO(payload.decode('utf-8-sig'))):
                key=(_norm(ar.get('220_sku')),_ean13(ar.get('220_ean')))
                if key[0] and key[1] and key not in attribute_by_identity: attribute_by_identity[key]=ar
    except Exception:
        pass
    category_evidence_counts=Counter()
    attribute_evidence_counts=Counter()
    category_backlog=defaultdict(lambda:Counter())

    # Catalog-wide category rules from the latest authoritative PHH taxonomy.
    exact_category_rules={}
    exact_category_rule_rows=[]
    taxonomy_addable={}
    taxonomy_required=defaultdict(list)
    taxonomy_status='UNAVAILABLE'
    try:
        import current_product_category_audit as _tax
        _tax_summary,_tax_cats,_tax_attrs=_tax._latest_taxonomy(os.getenv('DATABASE_URL',''))
        taxonomy_status='LOADED'
        taxonomy_addable={str(c.get('category_id')):c for c in _tax_cats
                          if str(c.get('allow_add_products')).casefold() in {'true','1','yes'}}
        for a in _tax_attrs:
            if str(a.get('required')).casefold() in {'true','1','yes'}:
                taxonomy_required[str(a.get('category_id'))].append(a)
        from catalog_category_rules import build_rules as build_category_rules
        exact_category_rules,exact_category_rule_rows=build_category_rules(variants,_tax_cats)
    except Exception as e:
        taxonomy_status='ERROR_'+type(e).__name__
    category_rule_counts=Counter(r.get('status') for r in exact_category_rule_rows)
    category_rule_variant_coverage=sum(int(r.get('variant_count') or 0)
                                       for r in exact_category_rule_rows
                                       if r.get('status')=='AUTO_EXACT_LEAF')
    # Authenticated seller-UI observations (2026-10-07, read-only).
    # API taxonomy lists required product fields for these categories, while the
    # selected category form renders none of those field IDs, including hidden DOM.
    # Never resolve this by guessing: keep CREATE blocked under PHH_CONTRACT_CONFLICT.
    ui_contract_conflicts={
        '433':{'ui_category':'Teltis','api_required_count':17,'ui_required_product_feature_count':0,
               'evidence':'AUTHENTICATED_SELLER_UI_READONLY'},
        '434':{'ui_category':'Guļammaisi','api_required_count':13,'ui_required_product_feature_count':0,
               'evidence':'AUTHENTICATED_SELLER_UI_DOM_READONLY'},
        '20523':{'ui_category':'Zeķubikses','api_required_count':9,'ui_required_product_feature_count':0,
                 'evidence':'AUTHENTICATED_SELLER_UI_READONLY'},
        '11810':{'ui_category':'Šūpuļtīkli','api_required_count':9,'ui_required_product_feature_count':0,
                 'evidence':'AUTHENTICATED_SELLER_UI_DOM_READONLY'}
    }
    contract_conflict_counts=Counter()
    content_image_index={}
    content_image_summary={'status':'UNAVAILABLE'}
    try:
        from content_image_evidence import load_latest as load_content_images
        content_image_index,content_image_summary=load_content_images(os.getenv('DATABASE_URL',''))
    except Exception as e:
        content_image_summary={'status':'ERROR_'+type(e).__name__,'writes':0}
    content_image_live=Counter()
    readiness_funnel=Counter()
    readiness_by_category=defaultdict(Counter)
    near_ready_rows=[]
    all_existing_rows=[]
    pipeline_inputs=[]
    translation_queue=[]
    for v in variants:
        sku=_norm(v.get('shopify_sku')); barcode=_norm(v.get('shopify_barcode'))
        vid=_norm(v.get('shopify_variant_id'))
        m,master_basis=resolve_variant(v,master_identity_index)
        master_identity_counts[master_basis]+=1
        m=m or {}
        identity=(sku,_ean13(barcode))
        mapped=category_by_identity.get(identity)
        evidence_status=(_norm(mapped.get('status')) if mapped else 'UNMAPPED')
        mapped_cid=(_norm(mapped.get('selected_category_id')) if mapped else '')
        is_fhm=_norm(v.get('vendor')).casefold()=='fhm'
        shop_cat_key=(_norm(v.get('shopify_category_id')),_norm(v.get('shopify_category_name')))
        rule=exact_category_rules.get(shop_cat_key) or {}
        rule_cid=_norm(rule.get('phh_category_id'))
        cid=''
        category_confirmed=False
        category_basis=''
        if not is_fhm and evidence_status in ('AUTO','BLOCKED_ATTRIBUTES') and mapped_cid in taxonomy_addable:
            cid=mapped_cid; category_confirmed=True; category_basis='V4_EXACT_IDENTITY+LIVE_TAXONOMY'
        elif not is_fhm and rule.get('status')=='AUTO_EXACT_LEAF' and rule_cid in taxonomy_addable:
            cid=rule_cid; category_confirmed=True; category_basis='EXACT_SHOPIFY_TO_PHH_LEAF'
        if not is_fhm:
            category_backlog[shop_cat_key]['variants']+=1
            if category_confirmed:
                category_backlog[shop_cat_key]['mapped_v4']+=1
                category_evidence_counts[category_basis]+=1
            else:
                category_evidence_counts['CATEGORY_NOT_VERIFIED']+=1
            old_attr=attribute_by_identity.get(identity)
            if old_attr:
                attribute_evidence_counts[_norm(old_attr.get('attribute_readiness')) or 'UNKNOWN']+=1
        else:
            category_evidence_counts['FHM_IGNORED']+=1
        # Positive seller offer identity must agree on BOTH SKU and barcode;
        # one-sided matches are collisions requiring review, never CREATE.
        possible_offers={id(x):x for x in (by_ean.get(barcode,[]) if barcode else []) +
                         (by_sku.get(sku,[]) if sku else [])}
        consistent=[]
        for offer in possible_offers.values():
            eans=_offer_eans(offer)
            skus=_offer_skus(offer)
            matched_ean=barcode in eans or (len(barcode)==12 and "0"+barcode in eans) or (
                len(barcode)==13 and barcode.startswith("0") and barcode[1:] in eans)
            if matched_ean and sku in skus:
                consistent.append(offer)
        known_68150=(sku=='68150' and barcode in ('0021563681505','021563681505'))
        matched_offer=consistent[0] if consistent else None
        if consistent or known_68150:
            phh_identity={'status':'EXISTING','identity_verified':True}
            if matched_offer:
                mod=matched_offer.get('modification') or {}
                phh_identity.update({
                    'offer_id':_norm(matched_offer.get('id')),
                    'offer_status':_norm(matched_offer.get('status')),
                    'offer_amount':matched_offer.get('amount'),
                    'offer_price':matched_offer.get('sell_price'),
                    'modification_id':_norm(mod.get('id')),
                    'pigu_external_id':_norm(mod.get('pigu_external_id'))
                })
            if known_68150: phh_identity['product_id']='270344850'
        elif possible_offers:
            phh_identity={'status':'CONFLICT','identity_verified':False}
        else:
            phh_identity={'status':'UNKNOWN'}
        if not is_fhm and phh_identity.get('status')=='EXISTING' and _norm(phh_identity.get('pigu_external_id')):
            all_existing_rows.append({
                'shopify_variant_id':vid,'sku':sku,'ean':_ean13(barcode),
                'vendor':_norm(v.get('vendor')),
                'shopify_category_name':_norm(v.get('shopify_category_name')),
                'phh_category_id':cid,
                'offer_id':phh_identity.get('offer_id',''),
                'offer_status':phh_identity.get('offer_status',''),
                'offer_amount':phh_identity.get('offer_amount',''),
                'offer_price':phh_identity.get('offer_price',''),
                'modification_id':phh_identity.get('modification_id',''),
                'pigu_external_id':phh_identity.get('pigu_external_id',''),
                'shopify_stock_live':v.get('shopify_stock_live'),
                'shopify_price_live':v.get('shopify_price_live'),
                'master_basis':master_basis
            })
        locs={}
        product_translations=translations.get(_norm(v.get('shopify_product_id'))) or {}
        missing_translation_targets=[]
        for loc in ('lt','lv','ee','ru','fi'):
            master_title=_norm(m.get('220_title_'+loc))
            master_body=_norm(m.get('220_description_'+loc+'_html'))
            if master_title and master_body:
                locs[loc]={'title':master_title,
                           'description_html':_phh_description(master_title,master_body),
                           'supplier_code':sku,'source':'MASTER'}
            elif product_translations.get(loc):
                locs[loc]={**product_translations[loc],'supplier_code':sku}
            else:
                locs[loc]={'title':'','description_html':'','supplier_code':sku,'source':'MISSING'}
                missing_translation_targets.append(loc)
        if missing_translation_targets and not is_fhm:
            translation_queue.append({
                'shopify_product_id':_norm(v.get('shopify_product_id')),
                'shopify_variant_id':vid,'sku':sku,'vendor':_norm(v.get('vendor')),
                'shopify_title':_norm(v.get('shopify_title')),
                'targets':'|'.join(missing_translation_targets),
                'preferred_source':'lv' if product_translations.get('lv') else ('ru' if product_translations.get('ru') else 'SHOPIFY_BASE'),
                'writes':'0'})
        image_ev=content_image_index.get((sku,_ean13(barcode))) if _ean13(barcode) else None
        if image_ev and (not image_ev.get('variant_id') or image_ev.get('variant_id')==vid):
            content_image_live['EXACT_CONTENT_IMAGE_IDENTITY']+=1
            if image_ev.get('two_images_verified'): content_image_live['TWO_IMAGES_600_DIRECT']+=1
            if image_ev.get('main_neutral_verified'): content_image_live['MAIN_NEUTRAL_VERIFIED']+=1

        # Progressive preparation funnel. This never authorizes PHH CREATE.
        base_ok=False
        if not is_fhm:
            try: price_ok=float(v.get('shopify_price_live'))>=10
            except (TypeError,ValueError): price_ok=False
            gtin=_ean13(barcode)
            base_ok=bool(sku and gtin and shop_skus.get(sku)==1 and shop_eans.get(gtin)==1
                         and v.get('shopify_status')=='ACTIVE' and price_ok)
            if base_ok:
                readiness_funnel['IDENTITY_PRICE_ACTIVE']+=1
                readiness_by_category[_norm(v.get('shopify_category_name'))]['IDENTITY_PRICE_ACTIVE']+=1
            if base_ok and category_confirmed:
                readiness_funnel['+CATEGORY']+=1
                readiness_by_category[_norm(v.get('shopify_category_name'))]['+CATEGORY']+=1
            if base_ok and category_confirmed and image_ev and image_ev.get('two_images_verified'):
                readiness_funnel['+TWO_IMAGES_600_DIRECT']+=1
                readiness_by_category[_norm(v.get('shopify_category_name'))]['+TWO_IMAGES_600_DIRECT']+=1
                near_ready_rows.append({
                    'shopify_variant_id':vid,'sku':sku,'ean':_ean13(barcode),
                    'vendor':_norm(v.get('vendor')),
                    'shopify_category_name':_norm(v.get('shopify_category_name')),
                    'phh_category_id':cid,'category_basis':category_basis,
                    'contract_conflict':'YES' if cid in ui_contract_conflicts else 'NO',
                    'two_images_600_direct':'YES',
                    'main_neutral_verified':'YES' if image_ev.get('main_neutral_verified') else 'NO',
                    'master_basis':master_basis,'price_eur':_norm(v.get('shopify_price_live')),
                    'phh_identity_state':phh_identity.get('status',''),
                    'offer_id':phh_identity.get('offer_id',''),
                    'offer_status':phh_identity.get('offer_status',''),
                    'offer_amount':phh_identity.get('offer_amount',''),
                    'offer_price':phh_identity.get('offer_price',''),
                    'modification_id':phh_identity.get('modification_id',''),
                    'pigu_external_id':phh_identity.get('pigu_external_id',''),
                    'shopify_stock_live':v.get('shopify_stock_live'),
                    'future_stock_rule_result':0 if (v.get('shopify_stock_live') or 0)==0 else max(3,(v.get('shopify_stock_live') or 0)),
                    'price_match_diagnostic':'YES' if str(phh_identity.get('offer_price',''))==str(v.get('shopify_price_live','')) else 'NO',
                    'phh_create_authorized':'NO'})
            if base_ok and category_confirmed and image_ev and image_ev.get('two_images_verified') and image_ev.get('main_neutral_verified'):
                readiness_funnel['+MAIN_NEUTRAL']+=1
                readiness_by_category[_norm(v.get('shopify_category_name'))]['+MAIN_NEUTRAL']+=1
        images=[]
        image_url=_norm(m.get('220_main_image_url') or v.get('featured_image_url'))
        if image_url:
            images.append({'url':image_url,'width':int(v.get('featured_image_width') or 0),
                           'height':int(v.get('featured_image_height') or 0)})
        if cid in ui_contract_conflicts and not is_fhm:
            contract_conflict_counts[cid]+=1
        pipeline_inputs.append({
            'sku':sku,'barcode':barcode,'vendor':v.get('vendor'),
            'sku_unique':bool(sku and shop_skus[sku]==1),
            'ean_unique':bool(_ean13(barcode) and shop_eans[_ean13(barcode)]==1),
            'shopify_active':v.get('shopify_status')=='ACTIVE',
            'price_eur':v.get('shopify_price_live'),
            'phh_identity':phh_identity,
            'category_id':cid,
            'category_confirmed':category_confirmed,
            'category_basis':category_basis,
            'category_contract_conflict':cid in ui_contract_conflicts,
            'required_attributes':[] if cid in ui_contract_conflicts else
                [{'field_id':_norm(a.get('field_id')),
                  'value':'',
                  'dictionary_required':True,
                  'dictionary_confirmed':False}
                 for a in taxonomy_required.get(cid,[])],
            'required_attributes_complete':False,
            'locales':locs,'images':images,
            'main_image_neutral_verified':_norm(m.get('220_image_rule_status'))=='PASS',
            'package_verified':False,'manufacturer_verified':False,
            'content_approved':_norm(m.get('220_content_ready_status'))=='APPROVED'
        })
    pipeline_report=pipeline_audit(pipeline_inputs)
    pipeline_rows=pipeline_report.pop('rows')
    summary={'status':'PASS' if not phh_access_error else 'DEGRADED_PHH_UNAVAILABLE','phh_access_error':phh_access_error,'read_only':True,'shopify_pages':pages,'shopify_variants':len(variants),'master_rows':len(master),'matched':counts['MATCHED'],'new':counts['NEW'],'ambiguous':counts['AMBIGUOUS'],'duplicate':counts['DUPLICATE'],'missing_in_shopify':counts['MISSING_IN_SHOPIFY'],'preliminary_ready_candidates':len(prelim),'identity_lookup_limit':pilot_lookup_limit,'unique_barcode_lookups':len(lookup_eans),'create_candidates_after_identity':len(ready),'identity_negative_evidence_policy':'FAIL_CLOSED','top_create_candidates':[{k:row.get(k,'') for k in ('shopify_sku','shopify_barcode','vendor','shopify_category_id','shopify_category_name','product_type','shopify_title')} for row in ready[:10]],'pilot_36134_category_probe':pilot_category_probe,'excluded_fhm':sum('EXCLUDED_FHM' in x.get('candidate_blockers','') for x in exc),'identity_status_counts':dict(identity_counts),'exceptions':len(exc),'elapsed_seconds':round(time.time()-started,3),'note':'Preliminary ready means identity + active + title + description + featured image only; PHH category/attributes/image-count/package gates are evaluated downstream.'}
    # Separate source-data gaps from PHH identity safety blockers.
    # Values are live Shopify variants + exact current Master identity snapshots;
    # no write to Master, Shopify, price or stock is ever performed.
    barcode_profile=Counter()
    price_profile=Counter()
    locale_profile=Counter()
    for v in variants:
        if _norm(v.get('vendor')).casefold()=='fhm': continue
        raw=_norm(v.get('shopify_barcode'))
        if not raw:
            barcode_profile['MISSING']+=1
        elif _ean13(raw):
            barcode_profile['VALID_UPC12' if len(raw)==12 else 'VALID_EAN13']+=1
        elif raw.isdigit() and len(raw)==8:
            barcode_profile['UNVERIFIED_EAN8']+=1
        elif raw.isdigit() and len(raw) in (12,13):
            barcode_profile['INVALID_CHECK_DIGIT']+=1
        else:
            barcode_profile['OTHER_UNSUPPORTED']+=1
        if not _norm(v.get('shopify_sku')):
            barcode_profile['MISSING_SKU']+=1
        try:
            price=float(v.get('shopify_price_live'))
            price_profile['BELOW_10_EUR' if price<10 else 'AT_LEAST_10_EUR']+=1
        except (TypeError,ValueError):
            price_profile['UNKNOWN']+=1
        master,basis=resolve_variant(v,master_identity_index)
        master=master or {}
        for loc in ('lt','lv','ee','ru','fi'):
            if (_norm(master.get('220_title_'+loc)) and
                    _norm(master.get('220_description_'+loc+'_html')) and
                    _norm(master.get('220_supplier_code_'+loc))==_norm(v.get('shopify_sku'))):
                locale_profile['MASTER_PRESENT_'+loc.upper()]+=1
        if basis in ('EXACT_VARIANT_ID','EXACT_SKU_GTIN'):
            locale_profile['SAFE_MASTER_IDENTITY_LINK']+=1
    # Recovery analysis for missing/invalid live Shopify GTIN.
    # These are candidates for evidence review only; they never satisfy the
    # publication EAN gate automatically.
    master_gtin_recovery=Counter()
    master_gtin_examples=[]
    for v in variants:
        if _norm(v.get('vendor')).casefold()=='fhm': continue
        live_ean=_ean13(_norm(v.get('shopify_barcode')))
        if live_ean: continue
        sku=_norm(v.get('shopify_sku'))
        if not sku:
            master_gtin_recovery['NO_SKU']+=1
            continue
        candidates=master_by_sku.get(sku,[])
        valid=[]
        for rowno,row in candidates:
            candidate=_ean13(_norm(row.get('220_ean')))
            if candidate:
                valid.append((rowno,row,candidate))
        unique_eans=sorted({x[2] for x in valid})
        if len(unique_eans)==1 and valid:
            master_gtin_recovery['UNIQUE_MASTER_220_EAN_CANDIDATE']+=1
            if len(master_gtin_examples)<25:
                master_gtin_examples.append({'sku':sku,'candidate_ean':unique_eans[0],
                    'master_rows':'|'.join(str(x[0]) for x in valid),
                    'live_barcode':_norm(v.get('shopify_barcode')),
                    'status':'REVIEW_REQUIRED'})
        elif len(unique_eans)>1:
            master_gtin_recovery['CONFLICTING_MASTER_EANS']+=1
        elif candidates:
            master_gtin_recovery['MASTER_MATCH_NO_VALID_220_EAN']+=1
        else:
            master_gtin_recovery['NO_MASTER_SKU_MATCH']+=1
    summary['master_identity_resolution']={'counts':dict(master_identity_counts),
        'exact_links':master_identity_counts['EXACT_VARIANT_ID']+master_identity_counts['EXACT_SKU_GTIN'],
        'gtin_recovery_review_counts':dict(master_gtin_recovery),
        'gtin_recovery_examples':master_gtin_examples,
        'gtin_recovery_authorizes_publish':False,
        'writes':0,'basis':'exact_shopify_variant_id_or_unique_sku_gtin'}
    summary['source_quality']={'barcode_non_fhm':dict(barcode_profile),
              'live_price_non_fhm':dict(price_profile),
              'locale_coverage_non_fhm':dict(locale_profile)}
    # PHH's own auto-check is the authoritative completeness signal for existing cards.
    # Run dynamically for the current near-ready EXISTING cohort; GET-only, fail-closed.
    autocheck_status='UNAVAILABLE'
    autocheck_http_counts={}
    autocheck_error_kinds={}
    autocheck_by_id={}
    existing_ids=[_norm(r.get('pigu_external_id')) for r in all_existing_rows
                  if _norm(r.get('pigu_external_id'))]
    try:
        from pmp_autocheck_batch import run as run_autocheck_batch
        ac=run_autocheck_batch(existing_ids)
        autocheck_status=ac.get('status','UNKNOWN')
        autocheck_http_counts=ac.get('http_counts') or {}
        autocheck_error_kinds=ac.get('error_kinds') or {}
        autocheck_by_id={_norm(x.get('pigu_external_id')):x for x in (ac.get('items') or [])}
    except Exception as e:
        autocheck_status='ERROR_'+type(e).__name__
    all_existing_autocheck_rows=[]
    all_existing_summary=Counter()
    for er in all_existing_rows:
        pid=_norm(er.get('pigu_external_id'))
        item=autocheck_by_id.get(pid)
        if item and item.get('http')==200 and int(item.get('error_count') or 0)==0:
            state='VERIFIED_EXISTING'
            codes=[]
        elif item and item.get('http')==200:
            state='EXISTING_WITH_ERRORS'
            codes=[]
            def collect_existing_codes(obj):
                if isinstance(obj,dict):
                    if obj.get('code'): codes.append(_norm(obj.get('code')))
                    for vv in obj.values(): collect_existing_codes(vv)
                elif isinstance(obj,list):
                    for vv in obj: collect_existing_codes(vv)
            collect_existing_codes(item.get('errors') or [])
        else:
            state='AUTOCHECK_UNAVAILABLE'
            codes=[]
        all_existing_summary[state]+=1
        all_existing_autocheck_rows.append({
            **er,'phh_autocheck_state':state,
            'phh_autocheck_errors':'|'.join(sorted(set(codes))),
            'writes':'0'
        })
    remediation_rows=[]
    stock_sync_rows=[]
    for r in near_ready_rows:
        pid=_norm(r.get('pigu_external_id'))
        item=autocheck_by_id.get(pid)
        if r.get('phh_identity_state')=='EXISTING':
            if item and item.get('http')==200 and int(item.get('error_count') or 0)==0:
                r['phh_autocheck_state']='VERIFIED_EXISTING'
                r['phh_autocheck_errors']=''
            elif item and item.get('http')==200:
                r['phh_autocheck_state']='EXISTING_WITH_ERRORS'
                codes=Counter(); locales=Counter()
                def walk_errors(obj,locale=''):
                    if isinstance(obj,dict):
                        code=_norm(obj.get('code'))
                        if code:
                            codes[code]+=1
                            if locale: locales[locale]+=1
                        for k,v in obj.items():
                            if k in ('lt','lv','ee','fi','ru'):
                                walk_errors(v,k)
                            elif k not in ('code','updated_at','validator_id','words'):
                                walk_errors(v,locale)
                    elif isinstance(obj,list):
                        for v in obj: walk_errors(v,locale)
                walk_errors(item.get('errors') or [])
                code_list=sorted(codes)
                r['phh_autocheck_errors']='|'.join(code_list) or 'UNKNOWN'
                if 'empty_title' in codes or 'empty_description' in codes:
                    action='FIX_MISSING_LOCALIZED_CONTENT'
                elif 'mandatory_fields_missing' in codes:
                    action='PHH_CONTRACT_REVIEW'
                elif 'manufacturer_representative_info_missing' in codes:
                    action='NEEDS_VERIFIED_MANUFACTURER_REPRESENTATIVE_DATA'
                elif 'risky_words_found' in codes:
                    action='CONTENT_RISK_REVIEW'
                else:
                    action='REVIEW_PHH_AUTOCHECK'
                if len(code_list)>1:
                    action='MULTI_ISSUE_REVIEW'
                remediation_rows.append({
                    'sku':r.get('sku'),'pigu_external_id':pid,'offer_id':r.get('offer_id'),
                    'phh_category_id':r.get('phh_category_id'),
                    'error_codes':'|'.join(code_list),
                    'locales':'|'.join(sorted(locales)),
                    'recommended_action':action,
                    'source':'PHH_AUTO_CHECK_GET',
                    'writes':'0'
                })
            else:
                r['phh_autocheck_state']='AUTOCHECK_UNAVAILABLE'
                r['phh_autocheck_errors']=''
        else:
            r['phh_autocheck_state']='NOT_APPLICABLE'
            r['phh_autocheck_errors']=''

        if r.get('phh_identity_state')=='EXISTING':
            try:
                shop_stock=int(float(r.get('shopify_stock_live')))
                phh_stock=int(float(r.get('offer_amount')))
                rule_target=0 if shop_stock==0 else max(3,shop_stock)
                r['future_stock_rule_result']=rule_target
                r['stock_match_rule_diagnostic']='YES' if phh_stock==rule_target else 'NO'
                if phh_stock!=rule_target:
                    source_state='BLOCKED_NEGATIVE_SHOPIFY_STOCK' if shop_stock<0 else 'REVIEW_REQUIRED'
                    stock_sync_rows.append({
                        'sku':r.get('sku'),'offer_id':r.get('offer_id'),
                        'pigu_external_id':pid,'offer_status':r.get('offer_status'),
                        'shopify_stock_live':shop_stock,'phh_stock_live':phh_stock,
                        'rule_target':rule_target,'delta':rule_target-phh_stock,
                        'source_state':source_state,'sync_authorized':'NO'
                    })
            except Exception:
                r['stock_match_rule_diagnostic']='UNKNOWN'
        near_ready_identity_counts=Counter(r.get('phh_identity_state') for r in near_ready_rows)
    near_ready_category_counts=Counter(r.get('phh_category_id') for r in near_ready_rows)
    near_ready_offer_status_counts=Counter(_norm(r.get('offer_status')).upper() or 'NO_OFFER' for r in near_ready_rows if r.get('phh_identity_state')=='EXISTING')
    existing_offer_health=Counter()
    for r in near_ready_rows:
        if r.get('phh_identity_state')!='EXISTING': continue
        st=_norm(r.get('offer_status')).upper()
        if st=='ACTIVE': existing_offer_health['ACTIVE']+=1
        elif st: existing_offer_health['NON_ACTIVE']+=1
        else: existing_offer_health['STATUS_UNKNOWN']+=1
        try:
            amt=int(float(r.get('offer_amount')))
            existing_offer_health['STOCK_POSITIVE' if amt>0 else 'STOCK_ZERO']+=1
        except Exception:
            existing_offer_health['STOCK_UNKNOWN']+=1
        try:
            price_ok=abs(float(r.get('offer_price'))-float(r.get('price_eur')))<0.005
        except Exception:
            price_ok=False
        r['price_match_diagnostic']='YES' if price_ok else 'NO'
        if price_ok: existing_offer_health['PRICE_MATCH_SHOPIFY']+=1
        else: existing_offer_health['PRICE_DIFFERS_OR_FORMAT_UNKNOWN']+=1
    summary['existing_catalog_autocheck']={
        'checked_candidates':len(all_existing_rows),
        'status_counts':dict(all_existing_summary),
        'http_counts':autocheck_http_counts,
        'error_kinds':autocheck_error_kinds,
        'read_only':True,
        'writes':0}
    summary['near_ready_summary']={
        'variants':len(near_ready_rows),
        'identity_counts':dict(near_ready_identity_counts),
        'phh_category_counts':dict(near_ready_category_counts),
        'offer_status_counts':dict(near_ready_offer_status_counts),
        'existing_offer_health':dict(existing_offer_health),
        'autocheck_summary':{
            'status':autocheck_status,
            'checked':sum(1 for r in near_ready_rows if r.get('phh_autocheck_state') in ('VERIFIED_EXISTING','EXISTING_WITH_ERRORS')),
            'verified_existing':sum(1 for r in near_ready_rows if r.get('phh_autocheck_state')=='VERIFIED_EXISTING'),
            'existing_with_errors':sum(1 for r in near_ready_rows if r.get('phh_autocheck_state')=='EXISTING_WITH_ERRORS'),
            'http_counts':autocheck_http_counts,
            'error_kinds':autocheck_error_kinds,
            'writes':0
        },
        'stock_sync_review':{
            'rows':len(stock_sync_rows),
            'negative_shopify_stock_blocked':sum(1 for r in stock_sync_rows if r.get('source_state')=='BLOCKED_NEGATIVE_SHOPIFY_STOCK'),
            'sync_authorized':False,
            'writes':0
        },
        'create_candidates_before_absence_proof':sum(1 for r in near_ready_rows if r.get('phh_identity_state')!='EXISTING'),
        'existing_not_create_candidates':near_ready_identity_counts.get('EXISTING',0),
        'all_categories_ui_api_contract_checked':all(str(k) in ui_contract_conflicts for k in near_ready_category_counts),
        'create_authorized':False,
        'writes':0}
    funnel_categories=[{'shopify_category_name':k,**dict(v)}
                       for k,v in readiness_by_category.items() if v.get('IDENTITY_PRICE_ACTIVE')]
    funnel_categories.sort(key=lambda x:(-x.get('+TWO_IMAGES_600_DIRECT',0),-x.get('+CATEGORY',0),-x.get('IDENTITY_PRICE_ACTIVE',0),x['shopify_category_name']))
    summary['readiness_funnel']={
        'counts':dict(readiness_funnel),
        'top_categories':funnel_categories[:30],
        'create_authorized':False,
        'phh_identity_absence_required_later':True,
        'writes':0}
    summary['image_evidence']={
        'snapshot':content_image_summary,
        'live_exact_identity_counts':dict(content_image_live),
        'pipeline_image_gate_unchanged':True,
        'reason':'Stored direct/dimension evidence is reused for diagnostics; PHH no-query URLs and neutral main background remain fail-closed.',
        'writes':0}
    summary['translation_evidence']={
        'status':translation_status,
        'source_error_class':'READ_TRANSLATIONS_SCOPE_MISSING' if translation_status=='BLOCKED_SOURCE_SCOPE' else ('OTHER' if translation_error else ''),
        'shopify_translation_pages':translation_pages,
        'fresh_title_body_by_locale':translation_source_counts,
        'translation_queue_variants':len(translation_queue),
        'master_or_shopify_sources_only':True,
        'translation_writes':0}
    summary['phh_contract_conflicts']={
        'categories':ui_contract_conflicts,
        'variant_counts':dict(contract_conflict_counts),
        'create_authorized':False,
        'resolution_required':True,
        'writes':0}
    summary['category_evidence']={
        'source':'v4_exact_identity_plus_unique_exact_shopify_phh_leaf',
        'artifact_status':category_artifact_status,
        'taxonomy_status':taxonomy_status,
        'non_fhm_status_counts':dict(category_evidence_counts),
        'exact_leaf_rule_status_counts':dict(category_rule_counts),
        'exact_leaf_rule_variant_coverage':category_rule_variant_coverage,
        'existing_v11_attribute_status_counts':dict(attribute_evidence_counts),
        'category_groups_non_fhm':len(category_backlog),
        'values_dictionary_confirmed_for_import':False,
        'category_assignments_authorize_create':False}
    backlog_rows=[{'shopify_category_id':cat[0],'shopify_category_name':cat[1],
                   'variants':c['variants'],'mapped_v4':c['mapped_v4'],
                   'unmapped_or_review':c['variants']-c['mapped_v4']}
                  for cat,c in category_backlog.items()]
    backlog_rows.sort(key=lambda r:(-r['unmapped_or_review'],-r['variants'],r['shopify_category_name']))
    summary['publication_pipeline']=pipeline_report
    return {
        'full-catalog-summary.json':_json(summary),
        'full-catalog-reconciliation.csv':_csv(rec,rec_fields),
        'catalog-pipeline-state.json':_json({'summary':pipeline_report,'rows':pipeline_rows}),
        'catalog-category-backlog.csv':_csv(backlog_rows,['shopify_category_id','shopify_category_name','variants','mapped_v4','unmapped_or_review']),
        'catalog-category-rules.csv':_csv(exact_category_rule_rows,['shopify_category_id','shopify_category_name','shopify_terminal','variant_count','vendor_count','phh_category_id','phh_category_title','status','confidence','basis','phh_write']),
        'catalog-translation-queue.csv':_csv(translation_queue,['shopify_product_id','shopify_variant_id','sku','vendor','shopify_title','targets','preferred_source','writes']),
        'near-ready-cohort.csv':_csv(near_ready_rows,['shopify_variant_id','sku','ean','vendor','shopify_category_name','phh_category_id','category_basis','contract_conflict','two_images_600_direct','main_neutral_verified','master_basis','price_eur','phh_identity_state','offer_id','offer_status','offer_amount','offer_price','modification_id','pigu_external_id','shopify_stock_live','future_stock_rule_result','price_match_diagnostic','stock_match_rule_diagnostic','phh_autocheck_state','phh_autocheck_errors','phh_create_authorized']),
        'all-existing-autocheck.csv':_csv(all_existing_autocheck_rows,['shopify_variant_id','sku','ean','vendor','shopify_category_name','phh_category_id','offer_id','offer_status','offer_amount','offer_price','modification_id','pigu_external_id','shopify_stock_live','shopify_price_live','master_basis','phh_autocheck_state','phh_autocheck_errors','writes']),
        'existing-autocheck-remediation.csv':_csv(remediation_rows,['sku','pigu_external_id','offer_id','phh_category_id','error_codes','locales','recommended_action','source','writes']),
        'stock-sync-review.csv':_csv(stock_sync_rows,['sku','offer_id','pigu_external_id','offer_status','shopify_stock_live','phh_stock_live','rule_target','delta','source_state','sync_authorized']),
        'full-catalog-ready-candidates.csv':_csv(ready,ready_fields),
        'full-catalog-exceptions.csv':_csv(exc,exc_fields)
    },summary
