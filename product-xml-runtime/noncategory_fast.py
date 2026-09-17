from __future__ import annotations
import csv, hashlib, io, json, math, os, re
from collections import Counter, deque
from PIL import Image
import requests
from app import _master_rows,_shopify_products,_shopify_token
from noncategory_audit import _norm,_ean_ok,_csv,ALLOWED_MIME

AUDIT_VERSION='external-gates-v3-2026-09-17'
SHOPIFY_API='2026-07'


def _jhash(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


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
    return sorted(sizes,reverse=True)


def _pixel_audit(url):
    out={'url':url,'http_status':'','content_type':'','width':'','height':'','technical_ok':False,'image_sha256':'','bytes':0,'background_status':'BACKGROUND_REVIEW','background_reason':'DOWNLOAD_FAILED','metrics_json':''}
    try:
        r=requests.get(url,timeout=(8,40),allow_redirects=True,headers={'User-Agent':'OUTfish-PHH-background-audit/3.0'})
        out['http_status']=r.status_code; out['content_type']=(r.headers.get('Content-Type') or '').split(';')[0].lower(); out['bytes']=len(r.content or b'')
        if r.status_code!=200:
            out['background_reason']='HTTP_DOWNLOAD_FAILED'; return out
        if r.history:
            out['background_reason']='REDIRECT_NOT_DIRECT_IMAGE'; return out
        if out['content_type'] not in ALLOWED_MIME:
            out['background_reason']='UNSUPPORTED_MIME'; return out
        out['image_sha256']=hashlib.sha256(r.content).hexdigest()
        im=Image.open(io.BytesIO(r.content)).convert('RGB'); out['width'],out['height']=im.size
        if im.width<1000 or im.height<1000:
            out['background_reason']='TECHNICAL_DIMENSIONS_FAIL'; return out
        out['technical_ok']=True
        im.thumbnail((360,360)); w,h=im.size; pix=list(im.getdata())
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
        mask=bytearray(w*h); outer=0; outer_total=0; fg=0
        for y in range(h):
            for x in range(w):
                p=pix[y*w+x]; isfg=(sum(p)/3<220 or ch(p)>28); idx=y*w+x
                if isfg: mask[idx]=1; fg+=1
                if x<int(w*.16) or x>=int(w*.84) or y<int(h*.16) or y>=int(h*.84): outer_total+=1; outer+=int(isfg)
        comps=_components(mask,w,h); main=comps[0] if comps else 0; secondary=sum(comps[1:]) if len(comps)>1 else 0
        dominance=main/max(1,fg); secondary_img=secondary/max(1,w*h); outer_ratio=outer/max(1,outer_total)
        significant=sum(s>=max(20,int(w*h*.0004)) for s in comps)
        metrics={'white_border_ratio':round(white,4),'neutral_border_ratio':round(neutral,4),'border_mean':round(mean,2),'border_sd':round(sd,2),'corner_spread':round(spread,2),'outer_foreground_ratio':round(outer_ratio,4),'foreground_dominance':round(dominance,4),'secondary_foreground_image_ratio':round(secondary_img,4),'significant_foreground_components':significant}
        out['metrics_json']=json.dumps(metrics,separators=(',',':'))
        # Conservative PASS: deliberately narrow. Possible overlays/detached graphics go to REVIEW.
        if white>=.96 and neutral>=.99 and mean>=244 and sd<=15 and spread<=18 and outer_ratio<=.10 and dominance>=.95 and secondary_img<=.005 and significant<=2:
            out['background_status']='BACKGROUND_PASS'; out['background_reason']='HIGH_CONFIDENCE_NEUTRAL_BACKGROUND_NO_DETACHED_GRAPHICS'
        elif neutral<.35 or mean<170 or spread>130 or outer_ratio>.72:
            out['background_status']='BACKGROUND_FAIL'; out['background_reason']='CLEAR_NON_NEUTRAL_OR_GRAPHIC_BACKGROUND'
        else:
            out['background_status']='BACKGROUND_REVIEW'; out['background_reason']='PIXEL_ANALYSIS_UNCERTAIN_OR_POSSIBLE_WATERMARK_LOGO_OVERLAY'
        return out
    except Exception as e:
        out['background_status']='BACKGROUND_REVIEW'; out['background_reason']=f'DECODE_OR_DOWNLOAD_ERROR:{type(e).__name__}:{str(e)[:120]}'; return out


def _gql(domain,token,query,variables):
    rr=requests.post(f'https://{domain}/admin/api/{SHOPIFY_API}/graphql.json',headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},json={'query':query,'variables':variables},timeout=90)
    rr.raise_for_status(); payload=rr.json()
    if payload.get('errors'): raise RuntimeError('Shopify GraphQL: '+json.dumps(payload['errors'])[:1600])
    return payload.get('data') or {}


def _all_resource_metafields(domain,token,resource_id,kind):
    frag='Product' if kind=='product' else 'ProductVariant'
    q=f'''query M($id:ID!,$after:String){{node(id:$id){{... on {frag}{{id metafields(first:100,after:$after){{nodes{{namespace key type value}} pageInfo{{hasNextPage endCursor}}}}}}}}}}'''
    out=[]; after=None
    while True:
        d=_gql(domain,token,q,{'id':resource_id,'after':after}); n=d.get('node') or {}; conn=n.get('metafields') or {}; out.extend(conn.get('nodes') or [])
        pi=conn.get('pageInfo') or {}
        if not pi.get('hasNextPage'): break
        after=pi.get('endCursor')
        if not after: raise RuntimeError(f'metafield pagination cursor missing {resource_id}')
    return out


def _all_definitions(domain,token,owner_type):
    q='''query D($owner:MetafieldOwnerType!,$after:String){metafieldDefinitions(first:100,ownerType:$owner,after:$after){nodes{id namespace key name description ownerType type{name category} metafieldsCount} pageInfo{hasNextPage endCursor}}}'''
    out=[]; after=None
    while True:
        d=_gql(domain,token,q,{'owner':owner_type,'after':after}); conn=d.get('metafieldDefinitions') or {}; out.extend(conn.get('nodes') or [])
        pi=conn.get('pageInfo') or {}
        if not pi.get('hasNextPage'): break
        after=pi.get('endCursor')
    return out


def _parse_measure(m,kind):
    typ=_norm(m.get('type')).lower(); raw=_norm(m.get('value'))
    if not raw: return None
    try:
        j=json.loads(raw)
        if isinstance(j,dict) and j.get('value') not in (None,'') and j.get('unit'):
            return {'value':j.get('value'),'unit':j.get('unit')}
    except Exception: pass
    units=r'(mm|cm|m|in|inch|inches|ft)' if kind=='dim' else r'(g|kg|lb|lbs|oz)'
    mt=re.fullmatch(r'\s*(-?\d+(?:[.,]\d+)?)\s*'+units+r'\s*',raw,re.I)
    if mt: return {'value':mt.group(1).replace(',','.'),'unit':mt.group(2)}
    if typ.startswith(('dimension','weight')): return {'value':raw,'unit':''}
    return None


def _package_scan(master,shopify):
    safe=[r for r in master if _norm(r.get('shopify_product_id')) in shopify and _norm(r.get('shopify_variant_id')) in shopify.get(_norm(r.get('shopify_product_id')),{ }).get('variants_by_id',{})]
    domain=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip(); token=_shopify_token()
    pids=sorted({_norm(r.get('shopify_product_id')) for r in safe}); vids=sorted({_norm(r.get('shopify_variant_id')) for r in safe})
    definitions={'PRODUCT':_all_definitions(domain,token,'PRODUCT'),'PRODUCTVARIANT':_all_definitions(domain,token,'PRODUCTVARIANT')}
    defs_by={(d.get('ownerType'),d.get('namespace'),d.get('key')):d for xs in definitions.values() for d in xs}
    pm={}; vm={}
    for i,pid in enumerate(pids,1):
        pm[pid]=_all_resource_metafields(domain,token,pid,'product')
        if i%50==0: print('METAFIELD_PRODUCT_PROGRESS '+json.dumps({'done':i,'total':len(pids)}),flush=True)
    for i,vid in enumerate(vids,1):
        vm[vid]=_all_resource_metafields(domain,token,vid,'variant')
        if i%100==0: print('METAFIELD_VARIANT_PROGRESS '+json.dumps({'done':i,'total':len(vids)}),flush=True)
    pkgctx=re.compile(r'(package|packaged|shipping|shipment|parcel|box|carton|fulfillment)',re.I)
    dimword=re.compile(r'(length|width|height|depth)',re.I); wtword=re.compile(r'weight',re.I)
    rows=[]; candidates=[]
    for r in safe:
        pid=_norm(r.get('shopify_product_id')); vid=_norm(r.get('shopify_variant_id')); accepted=[]; has_dims=set(); has_pkg_weight=False
        for scope,rid,mfs,otype in [('product',pid,pm.get(pid,[]),'PRODUCT'),('variant',vid,vm.get(vid,[]),'PRODUCT_VARIANT')]:
            for m in mfs:
                ns=_norm(m.get('namespace')); key=_norm(m.get('key')); nk=f'{ns}.{key}'; low=nk.casefold(); definition=defs_by.get((otype,ns,key)) or defs_by.get(('PRODUCTVARIANT' if otype=='PRODUCT_VARIANT' else otype,ns,key)) or {}
                dtext=' '.join([nk,_norm(definition.get('name')),_norm(definition.get('description'))])
                looks=bool(pkgctx.search(dtext) or dimword.search(dtext) or wtword.search(dtext))
                if not looks: continue
                kind='weight' if wtword.search(dtext) else ('dim' if dimword.search(dtext) else 'other')
                measure=_parse_measure(m,'weight' if kind=='weight' else 'dim') if kind!='other' else None
                package_proof=bool(pkgctx.search(dtext))
                accepted_as=''; reason=''
                if kind=='dim' and package_proof and measure:
                    accepted_as='PACKAGE_DIMENSION'; reason='PACKAGE_CONTEXT_IN_KEY_OR_DEFINITION_AND_PARSEABLE_MEASURE'
                    for w in ('length','width','height','depth'):
                        if re.search(w,dtext,re.I): has_dims.add('height' if w=='depth' else w)
                elif kind=='weight' and package_proof and measure:
                    accepted_as='PACKAGED_WEIGHT'; reason='PACKAGE_CONTEXT_IN_KEY_OR_DEFINITION_AND_PARSEABLE_MEASURE'; has_pkg_weight=True
                elif not package_proof: reason='REJECTED_NO_PACKAGE_OR_SHIPPING_SEMANTIC_PROOF'
                elif not measure: reason='REJECTED_AMBIGUOUS_OR_UNPARSEABLE_VALUE'
                else: reason='REJECTED_NOT_PACKAGE_DIMENSION_OR_WEIGHT'
                rec={'220_sku':_norm(r.get('220_sku')),'220_ean':_norm(r.get('220_ean')),'scope':scope,'source_id':rid,'namespace':ns,'key':key,'raw_value':_norm(m.get('value')),'type':_norm(m.get('type')),'unit':(measure or {}).get('unit',''),'definition_name':_norm(definition.get('name')),'definition_description':_norm(definition.get('description')),'accepted':'YES' if accepted_as else 'NO','accepted_as':accepted_as,'reason':reason}
                candidates.append(rec)
                if accepted_as: accepted.append(rec)
        lwh_complete={'length','width','height'}.issubset(has_dims)
        master_weight=bool(_norm(r.get('220_weight_kg')))
        status='AUTHORITATIVE_LWH_SOURCE' if lwh_complete else ('WEIGHT_ONLY' if master_weight or has_pkg_weight else 'NO_PACKAGE_SOURCE')
        rows.append({'220_sku':_norm(r.get('220_sku')),'220_ean':_norm(r.get('220_ean')),'shopify_product_id':pid,'shopify_variant_id':vid,'source_status':status,'220_weight_kg':_norm(r.get('220_weight_kg')),'accepted_package_metafields_json':json.dumps(accepted,ensure_ascii=False,separators=(',',':'))})
    meta={'product_resources_scanned':len(pm),'variant_resources_scanned':len(vm),'product_definitions':len(definitions['PRODUCT']),'variant_definitions':len(definitions['PRODUCTVARIANT']),'complete':len(rows)==673 and len(pm)==len(pids) and len(vm)==len(vids)}
    return rows,candidates,definitions,meta


def _restore_title29(db):
    if not db: return False,[],'DATABASE_URL_MISSING'
    try:
        import psycopg
        with psycopg.connect(db) as c:
            with c.cursor() as cur:
                cur.execute("select to_regclass('public.product_xml_noncategory_audit')")
                if not cur.fetchone()[0]: return False,[],'SOURCE_TABLE_ABSENT'
                cur.execute('select artifacts from product_xml_noncategory_audit where id=1')
                z=cur.fetchone()
                if not z: return False,[],'SOURCE_ROW_ABSENT'
                arts=z[0] if isinstance(z[0],dict) else json.loads(z[0])
        raw=arts.get('title-review-340.csv') or arts.get('title_review_340.csv') or ''
        if not raw: return False,[],'CONFIRMED_29_ARTIFACT_ABSENT'
        rs=list(csv.DictReader(io.StringIO(raw)))
        got=[r for r in rs if _norm(r.get('resolution_status'))=='AUTO_RESOLVED']
        if len(got)!=29: return False,[],f'ARTIFACT_AUTO_RESOLVED_COUNT_{len(got)}_NOT_29'
        out=[{'220_sku':_norm(r.get('220_sku')),'220_ean':_norm(r.get('220_ean')),'candidate':_norm(r.get('resolved_title_candidate') or r.get('220_title_candidate')),'authoritative_write':'NO'} for r in got]
        if len({(x['220_sku'],x['220_ean']) for x in out})!=29 or any(not x['candidate'] for x in out): return False,[],'ARTIFACT_29_NOT_EXACT_UNIQUE_COMPLETE'
        return True,out,'PERSISTED_CONFIRMED_ARTIFACT'
    except Exception as e: return False,[],f'{type(e).__name__}:{str(e)[:180]}'


def _persist(summary,arts):
    db=os.getenv('DATABASE_URL')
    if not db: raise RuntimeError('DATABASE_URL missing')
    import psycopg
    serial={k:(v.decode('utf-8',errors='replace') if isinstance(v,(bytes,bytearray)) else str(v)) for k,v in arts.items()}
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''create table if not exists product_xml_external_gate_audit (
                audit_version text primary key, created_at timestamptz not null default now(), completed_at timestamptz not null default now(),
                status text not null, dataset_hash text not null, summary jsonb not null, artifacts jsonb not null)''')
            cur.execute('''insert into product_xml_external_gate_audit(audit_version,status,dataset_hash,summary,artifacts)
                values(%s,%s,%s,%s::jsonb,%s::jsonb)
                on conflict(audit_version) do update set completed_at=now(),status=excluded.status,dataset_hash=excluded.dataset_hash,summary=excluded.summary,artifacts=excluded.artifacts''',
                (AUDIT_VERSION,'PASS',summary['dataset_hash'],json.dumps(summary),json.dumps(serial)))
        c.commit()


def load_persisted_complete():
    db=os.getenv('DATABASE_URL')
    if not db: return None,None
    try:
        import psycopg
        with psycopg.connect(db) as c:
            with c.cursor() as cur:
                cur.execute("select to_regclass('public.product_xml_external_gate_audit')")
                if not cur.fetchone()[0]: return None,None
                cur.execute('select summary,artifacts from product_xml_external_gate_audit where audit_version=%s and status=%s',(AUDIT_VERSION,'PASS'))
                z=cur.fetchone()
                if not z: return None,None
        summary=z[0] if isinstance(z[0],dict) else json.loads(z[0]); raw=z[1] if isinstance(z[1],dict) else json.loads(z[1])
        arts={k:v.encode('utf-8') for k,v in raw.items()}
        if summary.get('safe_mappings')==673 and summary.get('UNSTRUCTURED_METAFIELD_SCAN_COMPLETE')=='YES' and sum(summary.get('background',{}).values())==673:
            return summary,arts
    except Exception as e:
        print('PERSISTED_AUDIT_READ_FAILED '+type(e).__name__+' '+str(e)[:300],flush=True)
    return None,None


def run_noncategory_fast(force=False):
    if not force:
        s,a=load_persisted_complete()
        if s:
            s=dict(s); s['restart_recovery']='PASS'; s['audit_execution']='RECOVERED_WITHOUT_RERUN'
            return s,a
    master=_master_rows(); shopify=_shopify_products(master); safe=[]
    for r in master:
        pid=_norm(r.get('shopify_product_id')); vid=_norm(r.get('shopify_variant_id'))
        if pid and vid and pid in shopify and vid in shopify[pid].get('variants_by_id',{}): safe.append(r)
    if len(safe)!=673: raise RuntimeError(f'expected 673 safe mappings, got {len(safe)}')
    cache={}; bg=[]
    for n,r in enumerate(safe,1):
        p=shopify[_norm(r.get('shopify_product_id'))]; chosen=None; attempted=None
        for im in p.get('images') or []:
            u=_norm(im.get('url'))
            if not (u.lower().startswith('https://') and _norm(im.get('mime_type')).lower() in ALLOWED_MIME and (im.get('width') or 0)>=1000 and (im.get('height') or 0)>=1000): continue
            if u not in cache: cache[u]=_pixel_audit(u)
            attempted=cache[u]
            if attempted['technical_ok']: chosen=attempted; break
            # First technically-eligible metadata image that fails server download stays REVIEW; do not silently skip to create a PASS.
            chosen=attempted; break
        if not chosen: chosen={'url':'','http_status':'','content_type':'','width':'','height':'','image_sha256':'','bytes':0,'background_status':'BACKGROUND_REVIEW','background_reason':'NO_TECHNICALLY_USABLE_IMAGE_AVAILABLE','metrics_json':''}
        bg.append({'220_sku':_norm(r.get('220_sku')),'220_ean':_norm(r.get('220_ean')),'first_technically_usable_image':chosen['url'],'image_sha256':chosen.get('image_sha256',''),'downloaded_bytes':chosen.get('bytes',0),'background_status':chosen['background_status'],'background_reason':chosen['background_reason'],'http_status':chosen['http_status'],'content_type':chosen['content_type'],'width':chosen['width'],'height':chosen['height'],'metrics_json':chosen['metrics_json']})
        if n%100==0: print('PIXEL_AUDIT_PROGRESS '+json.dumps({'rows_done':n,'unique_images_attempted':len(cache),'total':len(safe)}),flush=True)
    pkg,candidates,definitions,mfmeta=_package_scan(master,shopify)
    bgby={(x['220_sku'],x['220_ean']):x for x in bg}; pkgby={(x['220_sku'],x['220_ean']):x for x in pkg}
    sku_counts=Counter(_norm(r.get('220_sku')) for r in safe); cand=[]
    for r in safe:
        sku=_norm(r.get('220_sku')); ean=_norm(r.get('220_ean')); p=shopify[_norm(r.get('shopify_product_id'))]
        usable=[im for im in p.get('images') or [] if _norm(im.get('url')).lower().startswith('https://') and _norm(im.get('mime_type')).lower() in ALLOWED_MIME and (im.get('width') or 0)>=1000 and (im.get('height') or 0)>=1000]
        title_ok=bool(_norm(r.get('220_title'))); desc_ok=bool(_norm(r.get('220_description'))); group_ok=_norm(r.get('220_grouping_status'))=='PASS'; ean_ok=_ean_ok(ean); supplier_ok=bool(sku) and sku_counts[sku]==1; img_ok=len(usable)>=2; bg_ok=bgby[(sku,ean)]['background_status']=='BACKGROUND_PASS'
        # Package L/W/H requirement/exemptions remain category-dependent. Scan completion itself is the pre-category package gate.
        package_precategory_ok=mfmeta['complete']
        ok=title_ok and desc_ok and group_ok and ean_ok and supplier_ok and img_ok and bg_ok and package_precategory_ok
        cand.append({'220_sku':sku,'220_ean':ean,'title_pass':'YES' if title_ok else 'NO','description_pass':'YES' if desc_ok else 'NO','grouping_pass':'YES' if group_ok else 'NO','ean_pass':'YES' if ean_ok else 'NO','supplier_code_pass':'YES' if supplier_ok else 'NO','technical_images_ge2':'YES' if img_ok else 'NO','background_pass':'YES' if bg_ok else 'NO','package_source_status':pkgby[(sku,ean)]['source_status'],'precategory_package_gate':'YES' if package_precategory_ok else 'NO','CATEGORY_PENDING_PILOT_CANDIDATE':'YES' if ok else 'NO'})
    bc=Counter(x['background_status'] for x in bg); pc=Counter(x['source_status'] for x in pkg); blockers=Counter()
    for x in cand:
        for k in ('title_pass','description_pass','grouping_pass','ean_pass','supplier_code_pass','technical_images_ge2','background_pass','precategory_package_gate'):
            if x[k]!='YES': blockers[k]+=1
    restored,title29,restore_reason=_restore_title29(os.getenv('DATABASE_URL'))
    image_downloaded=sum(bool(x['image_sha256']) for x in bg); image_failed=673-image_downloaded
    artifacts={
      'background-audit-673.csv':_csv(bg),
      'metafield-candidates.csv':_csv(candidates) if candidates else b'',
      'package-source-673.csv':_csv(pkg),
      'metafield-definitions.json':json.dumps(definitions,ensure_ascii=False,indent=2).encode(),
      'final-noncategory-gate-matrix.csv':_csv(cand),
      'category-pending-pilot-candidate.csv':_csv([x for x in cand if x['CATEGORY_PENDING_PILOT_CANDIDATE']=='YES']),
      'title-auto-resolved-29-proposal.csv':_csv(title29) if restored else b'',
    }
    hashes={k:hashlib.sha256(v).hexdigest() for k,v in artifacts.items()}
    summary={'audit_version':AUDIT_VERSION,'safe_mappings':len(safe),'shopify_mappings_scanned':'673/673','UNSTRUCTURED_METAFIELD_SCAN_COMPLETE':'YES' if mfmeta['complete'] else 'NO','metafield_scan_meta':mfmeta,'package_source':dict(pc),'images_pixel_downloaded':image_downloaded,'images_pixel_failed':image_failed,'unique_images_attempted':len(cache),'background':dict(bc),'category_pending_pilot_candidate':sum(x['CATEGORY_PENDING_PILOT_CANDIDATE']=='YES' for x in cand),'title_29_identity_set_restored':'YES' if restored else 'NO','title_29_restore_reason':restore_reason,'remaining_noncategory_blockers':dict(blockers),'dataset_hashes':hashes,'restart_recovery':'PENDING_RESTART_CHECK','audit_execution':'EXECUTED','master_writes':0,'shopify_writes':0,'phh_sends':0,'product_xml_publication':'OFF','stock_price':'UNTOUCHED','marketplace_cards':'UNTOUCHED'}
    summary['dataset_hash']=_jhash({'hashes':hashes,'summary_core':{k:v for k,v in summary.items() if k not in ('dataset_hashes','dataset_hash')}})
    artifacts['noncategory-summary.json']=json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True).encode()
    _persist(summary,artifacts)
    return summary,artifacts
