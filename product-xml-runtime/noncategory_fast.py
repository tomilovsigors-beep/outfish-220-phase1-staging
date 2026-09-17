from __future__ import annotations
import json
from collections import Counter
from app import _master_rows,_shopify_products
from noncategory_audit import _norm,_ean_ok,_pixel_audit,_package_sources,_csv,ALLOWED_MIME,emit_artifacts

def run_noncategory_fast():
    master=_master_rows(); shopify=_shopify_products(master); safe=[]
    for r in master:
        pid=_norm(r.get('shopify_product_id')); vid=_norm(r.get('shopify_variant_id'))
        if pid and vid and pid in shopify and vid in shopify[pid].get('variants_by_id',{}): safe.append(r)
    cache={}; bg=[]
    for n,r in enumerate(safe,1):
        p=shopify[_norm(r.get('shopify_product_id'))]; chosen=None
        for im in p.get('images') or []:
            u=_norm(im.get('url'))
            if not (u.lower().startswith('https://') and _norm(im.get('mime_type')).lower() in ALLOWED_MIME and (im.get('width') or 0)>=1000 and (im.get('height') or 0)>=1000): continue
            if u not in cache: cache[u]=_pixel_audit(u)
            a=cache[u]
            if a['technical_ok']: chosen=a; break
        if not chosen: chosen={'url':'','http_status':'','content_type':'','width':'','height':'','background_status':'BACKGROUND_FAIL','background_reason':'NO_TECHNICALLY_USABLE_IMAGE','metrics_json':''}
        bg.append({'220_sku':_norm(r.get('220_sku')),'220_ean':_norm(r.get('220_ean')),'first_technically_usable_image':chosen['url'],'background_status':chosen['background_status'],'background_reason':chosen['background_reason'],'http_status':chosen['http_status'],'content_type':chosen['content_type'],'width':chosen['width'],'height':chosen['height'],'metrics_json':chosen['metrics_json']})
        if n%100==0: print('NONCATEGORY_FAST_PROGRESS '+json.dumps({'rows_done':n,'unique_images_downloaded':len(cache),'total':len(safe)}),flush=True)
    pkg,mf_complete=_package_sources(master,shopify); bgby={x['220_sku']:x for x in bg}; pkgby={x['220_sku']:x for x in pkg}
    sku_counts=Counter(_norm(r.get('220_sku')) for r in safe); cand=[]
    for r in safe:
        sku=_norm(r.get('220_sku')); p=shopify[_norm(r.get('shopify_product_id'))]
        usable=[im for im in p.get('images') or [] if _norm(im.get('url')).lower().startswith('https://') and _norm(im.get('mime_type')).lower() in ALLOWED_MIME and (im.get('width') or 0)>=1000 and (im.get('height') or 0)>=1000]
        title_ok=bool(_norm(r.get('220_title'))); desc_ok=bool(_norm(r.get('220_description'))); group_ok=_norm(r.get('220_grouping_status'))=='PASS'; ean_ok=_ean_ok(r.get('220_ean')); supplier_ok=bool(sku) and sku_counts[sku]==1; img_ok=len(usable)>=2; bg_ok=bgby[sku]['background_status']=='BACKGROUND_PASS'
        # Missing L/W/H or packaged weight is category-dependent until authoritative PHH category rules arrive.
        # Therefore it is not an independent blocker for CATEGORY_PENDING_PILOT_CANDIDATE.
        package_independent_ok=True
        ok=title_ok and desc_ok and group_ok and ean_ok and supplier_ok and img_ok and bg_ok and package_independent_ok
        cand.append({'220_sku':sku,'220_ean':_norm(r.get('220_ean')),'title_pass':'YES' if title_ok else 'NO','description_pass':'YES' if desc_ok else 'NO','grouping_pass':'YES' if group_ok else 'NO','ean_pass':'YES' if ean_ok else 'NO','supplier_code_pass':'YES' if supplier_ok else 'NO','technical_images_ge2':'YES' if img_ok else 'NO','background_pass':'YES' if bg_ok else 'NO','package_source_status':pkgby[sku]['source_status'],'package_independent_blocker':'NO','CATEGORY_PENDING_PILOT_CANDIDATE':'YES' if ok else 'NO'})
    bc=Counter(x['background_status'] for x in bg); pc=Counter(x['source_status'] for x in pkg); blockers=Counter()
    for x in cand:
        for k in ('title_pass','description_pass','grouping_pass','ean_pass','supplier_code_pass','technical_images_ge2','background_pass'):
            if x[k]!='YES': blockers[k]+=1
    summary={'safe_mappings':len(safe),'unique_images_downloaded':len(cache),'residual_metafield_scan_complete':bool(mf_complete),'package_source':dict(pc),'background':dict(bc),'category_pending_pilot_candidate':sum(x['CATEGORY_PENDING_PILOT_CANDIDATE']=='YES' for x in cand),'remaining_noncategory_blockers':dict(blockers),'package_rule_note':'NO_INDEPENDENT_PACKAGE_BLOCKER_BEFORE_AUTHORITATIVE_PHH_CATEGORY_RULES','master_writes':0,'shopify_writes':0,'phh_sends':0,'product_xml_publication':'OFF'}
    arts={'background-audit-673.csv':_csv(bg),'package-source-673.csv':_csv(pkg),'category-pending-pilot-candidate.csv':_csv(cand),'noncategory-summary.json':json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True).encode()}
    return summary,arts
