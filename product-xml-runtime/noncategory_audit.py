from __future__ import annotations
import base64,csv,gzip,io,json,math,os,re
from collections import Counter,deque
from PIL import Image
import requests
from app import _master_rows,_shopify_products,_shopify_token

ALLOWED_MIME={'image/jpeg','image/jpg','image/png'}

def _norm(v): return re.sub(r'\s+',' ',str(v or '').strip())
def _csv(rows):
    if not rows: return b''
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows); return s.getvalue().encode('utf-8-sig')

def _ean_ok(v):
    s=re.sub(r'\D','',_norm(v))
    if len(s) not in (8,12,13,14): return False
    d=list(map(int,s)); check=d.pop(); total=sum(x*(3 if (len(d)-i)%2 else 1) for i,x in enumerate(d))
    return (10-total%10)%10==check

def _components(mask,w,h):
    seen=bytearray(w*h); sizes=[]
    for y in range(h):
        for x in range(w):
            idx=y*w+x
            if not mask[idx] or seen[idx]: continue
            q=deque([idx]); seen[idx]=1; n=0
            while q:
                j=q.popleft(); n+=1; yy,xx=divmod(j,w)
                for nx,ny in ((xx-1,yy),(xx+1,yy),(xx,yy-1),(xx,yy+1)):
                    if 0<=nx<w and 0<=ny<h:
                        k=ny*w+nx
                        if mask[k] and not seen[k]: seen[k]=1; q.append(k)
            sizes.append(n)
    sizes.sort(reverse=True); return sizes

def _pixel_audit(url):
    out={'url':url,'http_status':'','content_type':'','width':'','height':'','technical_ok':False,'background_status':'BACKGROUND_FAIL','background_reason':'DOWNLOAD_FAILED','metrics_json':''}
    try:
        r=requests.get(url,timeout=(8,35),allow_redirects=True,headers={'User-Agent':'OUTfish-PHH-background-audit/2.0'})
        out['http_status']=r.status_code; out['content_type']=(r.headers.get('Content-Type') or '').split(';')[0].lower()
        if r.status_code!=200 or r.history or out['content_type'] not in ALLOWED_MIME:
            out['background_reason']='TECHNICAL_HTTP_MIME_OR_REDIRECT_FAIL'; return out
        im=Image.open(io.BytesIO(r.content)).convert('RGB'); out['width'],out['height']=im.size
        if im.width<1000 or im.height<1000:
            out['background_reason']='TECHNICAL_DIMENSIONS_FAIL'; return out
        out['technical_ok']=True; im.thumbnail((360,360)); w,h=im.size; pix=list(im.getdata())
        band=max(4,int(min(w,h)*.06)); border=[]
        for y in range(h):
            for x in range(w):
                if x<band or y<band or x>=w-band or y>=h-band: border.append(pix[y*w+x])
        def ch(p): return max(p)-min(p)
        br=[sum(p)/3 for p in border]; mean=sum(br)/len(br); sd=(sum((z-mean)**2 for z in br)/len(br))**.5
        white=sum(sum(p)/3>=242 and ch(p)<=14 for p in border)/len(border)
        neutral=sum(sum(p)/3>=222 and ch(p)<=22 for p in border)/len(border)
        q=max(8,int(min(w,h)*.10)); corners=[]
        for ox,oy in ((0,0),(w-q,0),(0,h-q),(w-q,h-q)):
            vals=[pix[y*w+x] for y in range(oy,oy+q) for x in range(ox,ox+q)]
            corners.append(tuple(sum(p[i] for p in vals)/len(vals) for i in range(3)))
        spread=max(math.dist(a,b) for a in corners for b in corners)
        # foreground mask: catches seller logos/watermarks/decorative graphics as detached components.
        mask=bytearray(w*h); outer=0; outer_total=0; fg=0
        for y in range(h):
            for x in range(w):
                p=pix[y*w+x]; isfg=(sum(p)/3<220 or ch(p)>28); idx=y*w+x
                if isfg: mask[idx]=1; fg+=1
                if x<int(w*.16) or x>=int(w*.84) or y<int(h*.16) or y>=int(h*.84):
                    outer_total+=1; outer+=int(isfg)
        comps=_components(mask,w,h); main=comps[0] if comps else 0; secondary=sum(comps[1:]) if len(comps)>1 else 0
        dominance=main/max(1,fg); secondary_img=secondary/max(1,w*h); outer_ratio=outer/max(1,outer_total)
        significant=sum(s>=max(20,int(w*h*.0004)) for s in comps)
        metrics={'white_border_ratio':round(white,4),'neutral_border_ratio':round(neutral,4),'border_mean':round(mean,2),'border_sd':round(sd,2),'corner_spread':round(spread,2),'outer_foreground_ratio':round(outer_ratio,4),'foreground_dominance':round(dominance,4),'secondary_foreground_image_ratio':round(secondary_img,4),'significant_foreground_components':significant}
        out['metrics_json']=json.dumps(metrics,separators=(',',':'))
        if white>=.94 and neutral>=.98 and mean>=242 and sd<=18 and spread<=22 and outer_ratio<=.12 and dominance>=.93 and secondary_img<=.008 and significant<=3:
            out['background_status']='BACKGROUND_PASS'; out['background_reason']='HIGH_CONFIDENCE_NEUTRAL_BACKGROUND_NO_DETACHED_GRAPHICS'
        elif neutral<.40 or mean<180 or spread>120 or outer_ratio>.65:
            out['background_status']='BACKGROUND_FAIL'; out['background_reason']='CLEAR_NON_NEUTRAL_OR_GRAPHIC_BACKGROUND'
        else:
            out['background_status']='BACKGROUND_REVIEW'; out['background_reason']='PIXEL_ANALYSIS_UNCERTAIN_OR_POSSIBLE_WATERMARK_LOGO_GRAPHICS'
        return out
    except Exception as e:
        out['background_reason']=f'{type(e).__name__}: {str(e)[:160]}'; return out

def _structured_value(m,kind):
    typ=_norm(m.get('type')).lower(); val=_norm(m.get('value'))
    if not val: return False
    if typ.startswith(('dimension','weight','number_')): return True
    try:
        j=json.loads(val)
        if isinstance(j,dict) and j.get('value') not in (None,'') and j.get('unit'): return True
    except Exception: pass
    if kind=='dim': return bool(re.fullmatch(r'\d+(?:[.,]\d+)?\s*(?:mm|cm|m|in|inch|inches|ft)',val,re.I))
    return bool(re.fullmatch(r'\d+(?:[.,]\d+)?\s*(?:g|kg|lb|lbs|oz)',val,re.I))

def _package_sources(master,shopify):
    safe=[r for r in master if _norm(r.get('shopify_product_id')) in shopify and _norm(r.get('shopify_variant_id')) in shopify.get(_norm(r.get('shopify_product_id')),{ }).get('variants_by_id',{})]
    pids=sorted({_norm(r.get('shopify_product_id')) for r in safe}); token=_shopify_token(); domain=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
    q='''query P($ids:[ID!]!){nodes(ids:$ids){... on Product{id metafields(first:100){nodes{namespace key type value} pageInfo{hasNextPage}} variants(first:100){nodes{id metafields(first:100){nodes{namespace key type value} pageInfo{hasNextPage}}} pageInfo{hasNextPage}}}}}'''
    byp={}; complete=True; fetched=0
    for i in range(0,len(pids),30):
        rr=requests.post(f'https://{domain}/admin/api/2026-07/graphql.json',headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},json={'query':q,'variables':{'ids':pids[i:i+30]}},timeout=90); rr.raise_for_status(); payload=rr.json()
        if payload.get('errors'): raise RuntimeError(json.dumps(payload['errors'])[:900])
        for n in (payload.get('data') or {}).get('nodes') or []:
            if not n: continue
            fetched+=1; complete &= not (n.get('metafields') or {}).get('pageInfo',{}).get('hasNextPage',False); complete &= not (n.get('variants') or {}).get('pageInfo',{}).get('hasNextPage',False)
            vm={}
            for v in (n.get('variants') or {}).get('nodes') or []:
                complete &= not (v.get('metafields') or {}).get('pageInfo',{}).get('hasNextPage',False); vm[v['id']]=(v.get('metafields') or {}).get('nodes') or []
            byp[n['id']]={'product':(n.get('metafields') or {}).get('nodes') or [],'variants':vm}
    complete &= fetched==len(pids)
    ctx=re.compile(r'(?:^|[._-])(package|packaged|shipping|parcel|box)(?:$|[._-])',re.I); dim=re.compile(r'(length|width|height|depth)',re.I); wt=re.compile(r'weight',re.I)
    out=[]
    for r in safe:
        pid=_norm(r.get('shopify_product_id')); vid=_norm(r.get('shopify_variant_id')); accepted=[]; rejected=[]; has_lwh=False; has_pkg_weight=False
        for scope,mfs in [('product',byp.get(pid,{}).get('product',[])),('variant',byp.get(pid,{}).get('variants',{}).get(vid,[]))]:
            for m in mfs:
                nk=f"{m.get('namespace','')}.{m.get('key','')}"; low=nk.lower()
                if not (dim.search(low) or wt.search(low) or ctx.search(low)): continue
                if dim.search(low) and ctx.search(low) and _structured_value(m,'dim'):
                    accepted.append({'scope':scope,'name':nk,'type':m.get('type'),'value':m.get('value'),'accepted_as':'PACKAGE_DIMENSION'}); has_lwh=True
                elif wt.search(low) and ctx.search(low) and _structured_value(m,'weight'):
                    accepted.append({'scope':scope,'name':nk,'type':m.get('type'),'value':m.get('value'),'accepted_as':'PACKAGED_WEIGHT'}); has_pkg_weight=True
                else:
                    rejected.append({'scope':scope,'name':nk,'type':m.get('type'),'value':m.get('value'),'reason':'NOT_UNAMBIGUOUS_PACKAGE_FIELD'})
        master_weight=bool(_norm(r.get('220_weight_kg')))
        status='AUTHORITATIVE_LWH_SOURCE' if has_lwh else ('WEIGHT_ONLY' if (master_weight or has_pkg_weight) else 'NO_PACKAGE_SOURCE')
        out.append({'220_sku':_norm(r.get('220_sku')),'220_ean':_norm(r.get('220_ean')),'shopify_product_id':pid,'shopify_variant_id':vid,'source_status':status,'220_weight_kg':_norm(r.get('220_weight_kg')),'accepted_live_metafields_json':json.dumps(accepted,ensure_ascii=False,separators=(',',':')),'rejected_dimension_like_metafields_json':json.dumps(rejected,ensure_ascii=False,separators=(',',':'))})
    return out,complete

def run_noncategory_audit():
    master=_master_rows(); shopify=_shopify_products(master); safe=[]
    for r in master:
        pid=_norm(r.get('shopify_product_id')); vid=_norm(r.get('shopify_variant_id'))
        if pid and vid and pid in shopify and vid in shopify[pid].get('variants_by_id',{}): safe.append(r)
    bg=[]
    for n,r in enumerate(safe,1):
        p=shopify[_norm(r.get('shopify_product_id'))]; chosen=None
        for im in p.get('images') or []:
            u=_norm(im.get('url'))
            if not (u.lower().startswith('https://') and _norm(im.get('mime_type')).lower() in ALLOWED_MIME and (im.get('width') or 0)>=1000 and (im.get('height') or 0)>=1000): continue
            a=_pixel_audit(u)
            if a['technical_ok']: chosen=a; break
        if not chosen: chosen={'url':'','http_status':'','content_type':'','width':'','height':'','background_status':'BACKGROUND_FAIL','background_reason':'NO_TECHNICALLY_USABLE_IMAGE','metrics_json':''}
        bg.append({'220_sku':_norm(r.get('220_sku')),'220_ean':_norm(r.get('220_ean')),'first_technically_usable_image':chosen['url'],'background_status':chosen['background_status'],'background_reason':chosen['background_reason'],'http_status':chosen['http_status'],'content_type':chosen['content_type'],'width':chosen['width'],'height':chosen['height'],'metrics_json':chosen['metrics_json']})
        if n%50==0: print('NONCATEGORY_PROGRESS '+json.dumps({'background_done':n,'total':len(safe)}),flush=True)
    pkg,mf_complete=_package_sources(master,shopify); bgby={x['220_sku']:x for x in bg}; pkgby={x['220_sku']:x for x in pkg}
    sku_counts=Counter(_norm(r.get('220_sku')) for r in safe)
    cand=[]
    for r in safe:
        sku=_norm(r.get('220_sku')); p=shopify[_norm(r.get('shopify_product_id'))]
        usable=[im for im in p.get('images') or [] if _norm(im.get('url')).lower().startswith('https://') and _norm(im.get('mime_type')).lower() in ALLOWED_MIME and (im.get('width') or 0)>=1000 and (im.get('height') or 0)>=1000]
        title_ok=bool(_norm(r.get('220_title'))); desc_ok=bool(_norm(r.get('220_description'))); group_ok=_norm(r.get('220_grouping_status'))=='PASS'; ean_ok=_ean_ok(r.get('220_ean')); supplier_ok=bool(sku) and sku_counts[sku]==1; img_ok=len(usable)>=2; bg_ok=bgby[sku]['background_status']=='BACKGROUND_PASS'; package_ok=pkgby[sku]['source_status']!='NO_PACKAGE_SOURCE'
        ok=title_ok and desc_ok and group_ok and ean_ok and supplier_ok and img_ok and bg_ok and package_ok
        cand.append({'220_sku':sku,'220_ean':_norm(r.get('220_ean')),'title_pass':'YES' if title_ok else 'NO','description_pass':'YES' if desc_ok else 'NO','grouping_pass':'YES' if group_ok else 'NO','ean_pass':'YES' if ean_ok else 'NO','supplier_code_pass':'YES' if supplier_ok else 'NO','technical_images_ge2':'YES' if img_ok else 'NO','background_pass':'YES' if bg_ok else 'NO','package_source_status':pkgby[sku]['source_status'],'CATEGORY_PENDING_PILOT_CANDIDATE':'YES' if ok else 'NO'})
    bc=Counter(x['background_status'] for x in bg); pc=Counter(x['source_status'] for x in pkg)
    blockers=Counter()
    for x in cand:
        for k in ('title_pass','description_pass','grouping_pass','ean_pass','supplier_code_pass','technical_images_ge2','background_pass'):
            if x[k]!='YES': blockers[k]+=1
        if x['package_source_status']=='NO_PACKAGE_SOURCE': blockers['package_source_no_independent_evidence']+=1
    summary={'safe_mappings':len(safe),'residual_metafield_scan_complete':bool(mf_complete),'package_source':dict(pc),'background':dict(bc),'category_pending_pilot_candidate':sum(x['CATEGORY_PENDING_PILOT_CANDIDATE']=='YES' for x in cand),'remaining_noncategory_blockers':dict(blockers),'master_writes':0,'shopify_writes':0,'phh_sends':0,'product_xml_publication':'OFF'}
    arts={'background-audit-673.csv':_csv(bg),'package-source-673.csv':_csv(pkg),'category-pending-pilot-candidate.csv':_csv(cand),'noncategory-summary.json':json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True).encode()}
    return summary,arts

def emit_artifacts(summary,arts):
    print('NONCATEGORY_AUDIT_RESULT '+json.dumps(summary,sort_keys=True),flush=True)
    for name,data in arts.items():
        enc=base64.b64encode(gzip.compress(data,9)).decode(); chunks=[enc[i:i+3500] for i in range(0,len(enc),3500)]
        for i,ch in enumerate(chunks): print(f'NONCATEGORY_ARTIFACT {name} {i+1}/{len(chunks)} {ch}',flush=True)
