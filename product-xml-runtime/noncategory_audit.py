from __future__ import annotations
import base64, csv, gzip, io, json, math, os, re, statistics
from collections import Counter, defaultdict, deque
from PIL import Image, ImageStat
import requests
from app import _master_rows, _shopify_products, _shopify_token

ALLOWED_MIME={'image/jpeg','image/jpg','image/png'}
SIZE_TOKENS={'xs','s','m','l','xl','2xl','3xl','4xl','5xl','6xl','xxl','xxxl','onesize','one-size','sm','lxl'}

def _csv(rows,fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields); w.writeheader(); w.writerows(rows); return s.getvalue().encode('utf-8-sig')

def _norm(v): return re.sub(r'\s+',' ',str(v or '').strip())
def _family(sku):
    p=_norm(sku).split('-')
    return '-'.join(p[:-1]) if len(p)>=3 and p[-1].lower() in SIZE_TOKENS else _norm(sku)

def _ean_ok(v):
    s=re.sub(r'\D','',_norm(v))
    if len(s) not in (8,12,13,14): return False
    try:
        digits=list(map(int,s)); check=digits.pop(); total=sum(d*(3 if (len(digits)-i)%2==1 else 1) for i,d in enumerate(digits))
        return (10-total%10)%10==check
    except Exception: return False

def _image_download_and_visual(url):
    out={'url':url,'http_status':'','content_type':'','width':'','height':'','technical_ok':False,'background_status':'BACKGROUND_FAIL','background_reason':'DOWNLOAD_FAILED','metrics_json':''}
    try:
        r=requests.get(url,timeout=(8,30),allow_redirects=True,headers={'User-Agent':'OUTfish-PHH-background-audit/1.0'})
        out['http_status']=r.status_code; out['content_type']=(r.headers.get('Content-Type') or '').split(';')[0].lower()
        if r.status_code!=200 or len(r.history)!=0 or out['content_type'] not in ALLOWED_MIME:
            out['background_reason']='TECHNICAL_HTTP_MIME_OR_REDIRECT_FAIL'; return out
        im=Image.open(io.BytesIO(r.content)).convert('RGB'); out['width'],out['height']=im.size
        if im.width<1000 or im.height<1000:
            out['background_reason']='TECHNICAL_DIMENSIONS_FAIL'; return out
        out['technical_ok']=True
        # conservative visual audit on bounded copy
        im.thumbnail((420,420))
        w,h=im.size; px=im.load(); band=max(4,int(min(w,h)*0.06))
        border=[]
        for y in range(h):
            for x in range(w):
                if x<band or y<band or x>=w-band or y>=h-band: border.append(px[x,y])
        def chroma(p): return max(p)-min(p)
        white=[p for p in border if sum(p)/3>=238 and chroma(p)<=16]
        neutral=[p for p in border if sum(p)/3>=218 and chroma(p)<=22]
        white_ratio=len(white)/max(1,len(border)); neutral_ratio=len(neutral)/max(1,len(border))
        br=[sum(p)/3 for p in border]; mean_b=sum(br)/len(br); sd_b=(sum((x-mean_b)**2 for x in br)/len(br))**0.5
        # corner consistency catches graphic/photo backgrounds.
        corners=[]
        q=max(8,int(min(w,h)*0.12))
        for ox,oy in ((0,0),(w-q,0),(0,h-q),(w-q,h-q)):
            vals=[px[x,y] for y in range(oy,oy+q) for x in range(ox,ox+q)]
            m=tuple(sum(v[i] for v in vals)/len(vals) for i in range(3)); corners.append(m)
        corner_spread=max(math.dist(a,b) for a in corners for b in corners)
        # High-contrast activity in outer background zone is a conservative watermark/logo/graphic signal.
        outer_non_neutral=0; outer_total=0
        step=max(1,min(w,h)//220)
        for y in range(0,h,step):
            for x in range(0,w,step):
                if x<int(w*.18) or x>int(w*.82) or y<int(h*.18) or y>int(h*.82):
                    p=px[x,y]; outer_total+=1
                    if sum(p)/3<205 or chroma(p)>32: outer_non_neutral+=1
        outer_ratio=outer_non_neutral/max(1,outer_total)
        metrics={'white_border_ratio':round(white_ratio,4),'neutral_border_ratio':round(neutral_ratio,4),'border_brightness_mean':round(mean_b,2),'border_brightness_sd':round(sd_b,2),'corner_spread':round(corner_spread,2),'outer_non_neutral_ratio':round(outer_ratio,4)}
        out['metrics_json']=json.dumps(metrics,separators=(',',':'))
        # PASS only for strongly white/neutral and visually simple outer background. Uncertain => review.
        if white_ratio>=0.88 and neutral_ratio>=0.95 and mean_b>=238 and sd_b<=24 and corner_spread<=28 and outer_ratio<=0.20:
            out['background_status']='BACKGROUND_PASS'; out['background_reason']='HIGH_CONFIDENCE_WHITE_NEUTRAL_UNIFORM_BACKGROUND'
        elif neutral_ratio<0.45 or mean_b<185 or corner_spread>105 or outer_ratio>0.62:
            out['background_status']='BACKGROUND_FAIL'; out['background_reason']='CLEAR_NON_NEUTRAL_OR_GRAPHIC_BACKGROUND'
        else:
            out['background_status']='BACKGROUND_REVIEW'; out['background_reason']='VISUAL_HEURISTIC_UNCERTAIN_OR_POSSIBLE_GRAPHIC_WATERMARK'
        return out
    except Exception as e:
        out['background_reason']=f'{type(e).__name__}: {str(e)[:180]}'; return out

def _metafield_candidates(master,shopify):
    safe=[r for r in master if _norm(r.get('shopify_product_id')) and _norm(r.get('shopify_variant_id')) and _norm(r.get('shopify_product_id')) in shopify and _norm(r.get('shopify_variant_id')) in shopify[_norm(r.get('shopify_product_id'))].get('variants_by_id',{})]
    pids=sorted({_norm(r.get('shopify_product_id')) for r in safe})
    token=_shopify_token(); domain=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
    q='''query DimSources($ids:[ID!]!){nodes(ids:$ids){... on Product{id metafields(first:100){nodes{namespace key type value}} variants(first:100){nodes{id metafields(first:100){nodes{namespace key type value}}}}}}}'''
    byp={}
    for i in range(0,len(pids),30):
        ids=pids[i:i+30]
        rr=requests.post(f'https://{domain}/admin/api/2026-07/graphql.json',headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},json={'query':q,'variables':{'ids':ids}},timeout=90); rr.raise_for_status(); payload=rr.json()
        if payload.get('errors'): raise RuntimeError(json.dumps(payload['errors'])[:1200])
        for node in (payload.get('data') or {}).get('nodes') or []:
            if not node: continue
            byp[node['id']]={'product':(node.get('metafields') or {}).get('nodes') or [],'variants':{v['id']:(v.get('metafields') or {}).get('nodes') or [] for v in (node.get('variants') or {}).get('nodes') or []}}
    dimpat=re.compile(r'(?i)(package|packaged|shipping|parcel|box|length|width|height|depth|dimension|weight)')
    exactdim=re.compile(r'(?i)(length|width|height|depth)')
    out=[]
    for r in safe:
        pid=_norm(r.get('shopify_product_id')); vid=_norm(r.get('shopify_variant_id')); candidates=[]
        for scope,mfs in [('product',byp.get(pid,{}).get('product',[])),('variant',byp.get(pid,{}).get('variants',{}).get(vid,[]))]:
            for m in mfs:
                nk=f"{m.get('namespace','')}.{m.get('key','')}"
                if dimpat.search(nk): candidates.append({'scope':scope,'name':nk,'type':m.get('type') or '','value':m.get('value') or ''})
        has_dims=any(exactdim.search(x['name']) and _norm(x['value']) for x in candidates)
        weight=_norm(r.get('220_weight_kg'))
        out.append({'220_sku':_norm(r.get('220_sku')),'220_ean':_norm(r.get('220_ean')),'shopify_product_id':pid,'shopify_variant_id':vid,'220_weight_kg':weight,'dimension_metafields_json':json.dumps(candidates,ensure_ascii=False,separators=(',',':')),'dimension_source_status':'AUTHORITATIVE_DIMENSIONS_SOURCE_FOUND' if has_dims else ('WEIGHT_ONLY' if weight else 'NO_PACKAGE_SOURCE')})
    return out

def _title_review(master):
    target=[r for r in master if _norm(r.get('220_title_candidate_status'))=='REVIEW_REQUIRED']
    by_title=defaultdict(list)
    for r in target: by_title[_norm(r.get('220_title_candidate')).casefold()].append(r)
    out=[]
    for r in target:
        cand=_norm(r.get('220_title_candidate')); peers=by_title[cand.casefold()]; reasons=[]
        if len(cand)<15: reasons.append('ANOMALOUSLY_SHORT')
        if len(cand)>80: reasons.append('ANOMALOUSLY_LONG')
        letters=[c for c in cand if c.isalpha()]
        if len(letters)>=8 and sum(c.isupper() for c in letters)/len(letters)>.85: reasons.append('EXCESSIVE_ALL_CAPS')
        if len(peers)>1: reasons.append('DUPLICATE_CANDIDATE_TITLE')
        pids={_norm(x.get('shopify_product_id')) for x in peers if _norm(x.get('shopify_product_id'))}
        fams={_family(x.get('220_sku')) for x in peers}
        result='MANUAL_REVIEW_REQUIRED'; resolved=''; method=''
        if reasons==['DUPLICATE_CANDIDATE_TITLE']:
            if len(pids)==1 and len(pids)>0:
                result='AUTO_RESOLVED'; resolved=cand; method='SAME_EXACT_SHOPIFY_PRODUCT_VARIANTS'
            elif len(fams)==1:
                result='AUTO_RESOLVED'; resolved=cand; method='SAME_EXACT_SKU_FAMILY_VARIANTS'
            else:
                result='BLOCKED_NO_SAFE_SOURCE'; method='DUPLICATE_SPANS_MULTIPLE_PRODUCTS_OR_FAMILIES'
        elif 'DUPLICATE_CANDIDATE_TITLE' in reasons and len(fams)>1 and not pids:
            result='BLOCKED_NO_SAFE_SOURCE'; method='MULTIPLE_FLAGS_AND_NO_SAFE_PRODUCT_SOURCE'
        else:
            result='MANUAL_REVIEW_REQUIRED'; method='NO_SAFE_AUTOMATIC_TEXT_TRANSFORMATION'
        out.append({'220_sku':_norm(r.get('220_sku')),'220_ean':_norm(r.get('220_ean')),'220_title_candidate':cand,'review_reasons':'|'.join(reasons),'resolution_status':result,'resolved_title_candidate':resolved,'resolution_method':method,'authoritative_write':'NO'})
    return out

def run_noncategory_audit():
    master=_master_rows(); shopify=_shopify_products(master)
    safe=[]
    for r in master:
        pid=_norm(r.get('shopify_product_id')); vid=_norm(r.get('shopify_variant_id'))
        if pid and vid and pid in shopify and vid in shopify[pid].get('variants_by_id',{}): safe.append(r)
    # background: inspect images in Shopify media order until first technically usable, then audit visual background.
    bg=[]
    for r in safe:
        pid=_norm(r.get('shopify_product_id')); p=shopify[pid]; chosen=None; attempts=[]
        for im in p.get('images') or []:
            u=_norm(im.get('url'))
            if not u: continue
            meta_ok=(u.lower().startswith('https://') and _norm(im.get('mime_type')).lower() in ALLOWED_MIME and (im.get('width') or 0)>=1000 and (im.get('height') or 0)>=1000)
            if not meta_ok: continue
            a=_image_download_and_visual(u); attempts.append(a)
            if a['technical_ok']:
                chosen=a; break
        if not chosen:
            chosen={'url':'','http_status':'','content_type':'','width':'','height':'','technical_ok':False,'background_status':'BACKGROUND_FAIL','background_reason':'NO_TECHNICALLY_USABLE_IMAGE','metrics_json':''}
        bg.append({'220_sku':_norm(r.get('220_sku')),'220_ean':_norm(r.get('220_ean')),'first_technically_usable_image':chosen['url'],'background_status':chosen['background_status'],'background_reason':chosen['background_reason'],'http_status':chosen['http_status'],'content_type':chosen['content_type'],'width':chosen['width'],'height':chosen['height'],'metrics_json':chosen['metrics_json']})
    tr=_title_review(master)
    pkg=_metafield_candidates(master,shopify)
    bgc=Counter(x['background_status'] for x in bg); trc=Counter(x['resolution_status'] for x in tr); pc=Counter(x['dimension_source_status'] for x in pkg)
    bgby={x['220_sku']:x for x in bg}; pkgby={x['220_sku']:x for x in pkg}
    candidate=[]
    for r in safe:
        sku=_norm(r.get('220_sku')); pid=_norm(r.get('shopify_product_id')); p=shopify[pid]
        # technical >=2 from live Shopify media metadata + direct server checks are already known from prior audit; conservatively require >=2 metadata-eligible images here.
        meta_usable=[im for im in p.get('images') or [] if _norm(im.get('url')).lower().startswith('https://') and _norm(im.get('mime_type')).lower() in ALLOWED_MIME and (im.get('width') or 0)>=1000 and (im.get('height') or 0)>=1000]
        title_ok=bool(_norm(r.get('220_title'))); desc_ok=bool(_norm(r.get('220_description'))); group_ok=_norm(r.get('220_grouping_status'))=='PASS'; ean_ok=_ean_ok(r.get('220_ean')); bg_ok=bgby[sku]['background_status']=='BACKGROUND_PASS'; weight_ok=bool(_norm(r.get('220_weight_kg')))
        ok=title_ok and desc_ok and group_ok and ean_ok and len(meta_usable)>=2 and bg_ok and weight_ok
        candidate.append({'220_sku':sku,'220_ean':_norm(r.get('220_ean')),'title_pass':'YES' if title_ok else 'NO','description_pass':'YES' if desc_ok else 'NO','grouping_pass':'YES' if group_ok else 'NO','ean_supplier_pass':'YES' if ean_ok else 'NO','technical_images_ge2':'YES' if len(meta_usable)>=2 else 'NO','background_pass':'YES' if bg_ok else 'NO','weight_available':'YES' if weight_ok else 'NO','dimensions_source_status':pkgby[sku]['dimension_source_status'],'CATEGORY_PENDING_PILOT_CANDIDATE':'YES' if ok else 'NO'})
    summary={'safe_mappings':len(safe),'background':dict(bgc),'title_review_total':len(tr),'title_review':dict(trc),'package_dimension_source':dict(pc),'category_pending_pilot_candidate':sum(x['CATEGORY_PENDING_PILOT_CANDIDATE']=='YES' for x in candidate),'master_writes':0,'shopify_writes':0,'phh_sends':0,'product_xml_publication':'OFF'}
    arts={'background-audit-673.csv':_csv(bg,list(bg[0])),'title-review-340.csv':_csv(tr,list(tr[0])),'package-dimension-source-673.csv':_csv(pkg,list(pkg[0])),'category-pending-pilot-candidate.csv':_csv(candidate,list(candidate[0])),'noncategory-summary.json':json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True).encode()}
    return summary,arts

def emit_artifacts(summary,arts):
    print('NONCATEGORY_AUDIT_RESULT '+json.dumps(summary,sort_keys=True),flush=True)
    for name,data in arts.items():
        enc=base64.b64encode(gzip.compress(data,9)).decode()
        chunks=[enc[i:i+3500] for i in range(0,len(enc),3500)]
        for i,ch in enumerate(chunks): print(f'NONCATEGORY_ARTIFACT {name} {i+1}/{len(chunks)} {ch}',flush=True)
