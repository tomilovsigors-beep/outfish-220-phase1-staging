from __future__ import annotations
import csv, hashlib, io, json, re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ALLOWED_MIME={'image/jpeg','image/jpg','image/png'}

def _sha(b): return hashlib.sha256(b).hexdigest()
def _jb(o): return json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def _cb(rows,fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields); w.writeheader(); w.writerows(rows); return s.getvalue().encode('utf-8-sig')
def _kg(weight):
    if not weight: return None,'missing'
    try: v=float(weight.get('value'))
    except Exception: return None,'missing'
    unit=str(weight.get('unit') or '').upper()
    if v==0: return None,'zero'
    f={'KILOGRAMS':1.0,'GRAMS':.001,'POUNDS':.45359237,'OUNCES':.028349523125}
    return (round(v*f[unit],6),'present') if unit in f else (None,'missing')
def _opt(options,names):
    names={x.lower() for x in names}
    for o in options or []:
        if str(o.get('name') or '').lower() in names: return str(o.get('value') or '').strip()
    return ''
def _audit_one(url):
    out={'direct':False,'redirect_count':None,'http_status':None,'final_url':'','content_type':''}
    if not str(url).lower().startswith('https://'): return out
    try:
        r=requests.get(url,stream=True,allow_redirects=True,timeout=(8,18),headers={'User-Agent':'OUTfish-PHH-content-audit/1.0'})
        out.update(direct=(len(r.history)==0),redirect_count=len(r.history),http_status=r.status_code,final_url=r.url,content_type=(r.headers.get('Content-Type') or '').split(';')[0].lower()); r.close()
    except Exception: pass
    return out
def _audit_urls(urls):
    out={}
    with ThreadPoolExecutor(max_workers=16) as ex:
        fut={ex.submit(_audit_one,u):u for u in sorted(set(urls)) if u}
        for f in as_completed(fut): out[fut[f]]=f.result()
    return out
def _title_qa(master_rows):
    rows=[]; groups=defaultdict(list)
    for r in master_rows:
        sku=str(r.get('220_sku') or '').strip(); ean=str(r.get('220_ean') or '').strip(); t=re.sub(r'\s+',' ',str(r.get('shopify_title') or '').strip())
        if sku and ean and t: rows.append((r,t)); groups[t.casefold()].append(r)
    seo=re.compile(r'(?i)(\bshop now\b|\bbuy now\b|\bbuy online\b|\bfree shipping\b|\bbest price\b|\bon sale\b|\bsale\b|\bofficial store\b|https?://|www\.|\|)')
    out=[]
    for r,t in rows:
        reasons=[]; status='AUTO_APPROVABLE'
        if len(t)<8 or not re.search(r'[A-Za-zÀ-žА-Яа-я]',t): status='INVALID'; reasons=['EMPTY_OR_NONDESCRIPTIVE']
        else:
            if len(t)<15: reasons.append('ANOMALOUSLY_SHORT')
            if len(t)>80: reasons.append('ANOMALOUSLY_LONG')
            if seo.search(t): reasons.append('SEO_OR_PROMO_PATTERN')
            letters=[c for c in t if c.isalpha()]
            if len(letters)>=8 and sum(c.isupper() for c in letters)/len(letters)>.85: reasons.append('EXCESSIVE_ALL_CAPS')
            peers=groups[t.casefold()]
            if len(peers)>1:
                pids={str(x.get('shopify_product_id') or '').strip() for x in peers}
                if len({x for x in pids if x})>1 or '' in pids: reasons.append('DUPLICATE_ACROSS_DISTINCT_OR_UNMAPPED_IDENTITIES')
            if reasons: status='REVIEW_REQUIRED'
        out.append({'220_sku':str(r.get('220_sku') or '').strip(),'220_ean':str(r.get('220_ean') or '').strip(),'shopify_title':t,'220_title_candidate':t,'title_qa_status':status,'title_qa_reasons':'|'.join(reasons) if reasons else 'CLEAN_DETERMINISTIC_SOURCE'})
    return out

def build_snapshot(master_rows,shopify):
    safe=[]
    for r in master_rows:
        pid=str(r.get('shopify_product_id') or '').strip(); vid=str(r.get('shopify_variant_id') or '').strip()
        if pid and vid and pid in shopify and vid in shopify[pid].get('variants_by_id',{}): safe.append(r)
    urls=[im.get('url') for p in shopify.values() for im in p.get('images') or [] if im.get('url')]
    http=_audit_urls(urls)
    content=[]; images=[]; weights=[]; groups=[]; blockers=[]; descpass=0; ic=Counter(); wc=Counter()
    group_members=defaultdict(list)
    for r in safe:
        sku=str(r.get('220_sku') or '').strip(); ean=str(r.get('220_ean') or '').strip(); pid=str(r.get('shopify_product_id') or '').strip(); vid=str(r.get('shopify_variant_id') or '').strip(); p=shopify[pid]; v=p['variants_by_id'][vid]
        desc=str(p.get('description') or '').strip(); descpass+=bool(desc)
        colour=_opt(v.get('selected_options'),{'color','colour','krāsa','krasa','spalva','värv','varv'}); size=_opt(v.get('selected_options'),{'size','izmērs','izmers','dydis','suurus','koko'}); mod=size or str(v.get('title') or '').strip(); group_members[(pid,colour)].append((sku,mod))
        kg,wstat=_kg(v.get('weight')); wc[wstat]+=1; weights.append({'220_sku':sku,'220_ean':ean,'shopify_variant_id':vid,'source_value':(v.get('weight') or {}).get('value',''),'source_unit':(v.get('weight') or {}).get('unit',''),'weight_kg':kg if kg is not None else '','status':wstat.upper()})
        usable=[]
        for idx,im in enumerate(p.get('images') or []):
            u=im.get('url') or ''; m=(im.get('mime_type') or '').lower(); w=im.get('width') or 0; h=im.get('height') or 0; q=http.get(u,{})
            eff=m or q.get('content_type',''); https=u.lower().startswith('https://'); mime=eff in ALLOWED_MIME; dim=isinstance(w,(int,float)) and isinstance(h,(int,float)) and w>=1000 and h>=1000; direct=bool(q.get('direct')) and q.get('http_status')==200; ok=https and mime and dim and direct
            ic['ge1000']+=bool(dim); ic['unsupported_mime']+=not mime; ic['redirect_fail']+=https and not direct; ic['usable']+=ok
            if ok: usable.append(u)
            images.append({'220_sku':sku,'220_ean':ean,'shopify_product_id':pid,'image_index':idx+1,'url':u,'mime':eff,'width':w,'height':h,'https_pass':'YES' if https else 'NO','direct_pass':'YES' if direct else 'NO','redirect_count':q.get('redirect_count'),'http_status':q.get('http_status'),'usable':'YES' if ok else 'NO','background_status':'BACKGROUND_REVIEW_REQUIRED'})
        main_override=str(r.get('220_main_image_url') or '').strip(); source_main=str(p.get('main_image_url') or '').strip(); resolved_main=main_override or source_main; additional=[u for u in usable if u!=resolved_main]
        content.append({'220_sku':sku,'220_ean':ean,'shopify_product_id':pid,'shopify_variant_id':vid,'description':desc,'vendor':p.get('vendor') or '','product_type':p.get('product_type') or '','source_category_id':p.get('category_id') or '','source_category_name':p.get('category_name') or '','selected_options_json':json.dumps(v.get('selected_options') or [],ensure_ascii=False,separators=(',',':')),'colour':colour,'size':size,'modification_title':mod,'weight_kg':kg if kg is not None else '','resolved_main_image':resolved_main,'usable_images_json':json.dumps(usable,ensure_ascii=False,separators=(',',':')),'validated_additional_images_json':json.dumps(additional,ensure_ascii=False,separators=(',',':'))})
        if not desc: blockers.append({'220_sku':sku,'blocker':'MISSING_DESCRIPTION'})
    blocked=set()
    for key,members in group_members.items():
        if len(members)>1:
            mods=[m for _,m in members]
            if any(not m for m in mods) or len(mods)!=len(set(mods)): blocked.update(s for s,_ in members)
    gc=Counter(); usable_products=Counter(); proposal=[]
    content_by={r['220_sku']:r for r in content}
    for r in safe:
        sku=str(r.get('220_sku') or '').strip(); c=content_by[sku]; status='BLOCKED' if sku in blocked else 'PASS'; gc[status]+=1
        groups.append({'220_sku':sku,'220_ean':c['220_ean'],'shopify_product_id':c['shopify_product_id'],'shopify_variant_id':c['shopify_variant_id'],'colour':c['colour'],'size':c['size'],'modification_title':c['modification_title'],'group_key':c['shopify_product_id']+'|'+c['colour'],'status':status})
        n=len(json.loads(c['usable_images_json'])); usable_products['ge2' if n>=2 else 'one' if n==1 else 'zero']+=1
        for field,val,source in [('220_description',c['description'],'SHOPIFY_DESCRIPTION'),('220_selected_options_json',c['selected_options_json'],'SHOPIFY_SELECTED_OPTIONS'),('220_grouping_status',status,'DETERMINISTIC_GROUPING_AUDIT')]:
            if val!='': proposal.append({'220_sku':sku,'220_ean':c['220_ean'],'field':field,'value':val,'source':source,'write_class':'SAFE_TO_BULK_WRITE_AFTER_APPROVAL'})
        if c['validated_additional_images_json']!='[]': proposal.append({'220_sku':sku,'220_ean':c['220_ean'],'field':'220_additional_images','value':c['validated_additional_images_json'],'source':'TECHNICALLY_VALIDATED_SHOPIFY_IMAGES','write_class':'SAFE_TO_BULK_WRITE_AFTER_APPROVAL'})
        if c['weight_kg']!='': proposal.append({'220_sku':sku,'220_ean':c['220_ean'],'field':'220_weight_kg','value':c['weight_kg'],'source':'SHOPIFY_INVENTORY_ITEM_WEIGHT','write_class':'SAFE_TO_BULK_WRITE_AFTER_APPROVAL'})
    titleqa=_title_qa(master_rows); tc=Counter(x['title_qa_status'] for x in titleqa)
    for x in titleqa:
        proposal.extend([{'220_sku':x['220_sku'],'220_ean':x['220_ean'],'field':'220_title_candidate','value':x['220_title_candidate'],'source':'SHOPIFY_TITLE_REFERENCE','write_class':'SAFE_TO_BULK_WRITE_AFTER_APPROVAL'},{'220_sku':x['220_sku'],'220_ean':x['220_ean'],'field':'220_title_candidate_status','value':x['title_qa_status'],'source':'MASS_TITLE_QA','write_class':'SAFE_TO_BULK_WRITE_AFTER_APPROVAL'}])
    summary={'safe_mappings_fetched':len(safe),'descriptions_pass':descpass,'image_assets_total':len(images),'images_ge1000':ic['ge1000'],'unsupported_mime':ic['unsupported_mime'],'redirect_failures':ic['redirect_fail'],'products_ge2_usable_images':usable_products['ge2'],'products_one_usable_image':usable_products['one'],'products_no_usable_image':usable_products['zero'],'weight_present':wc['present'],'weight_zero':wc['zero'],'weight_missing':wc['missing'],'grouping_pass':gc['PASS'],'grouping_blocked':gc['BLOCKED'],'background_review_required':len(safe),'title_auto_approvable':tc['AUTO_APPROVABLE'],'title_review_required':tc['REVIEW_REQUIRED'],'title_invalid':tc['INVALID'],'bulk_write_cells':len(proposal),'bulk_write_identities':len({x['220_sku'] for x in proposal}),'projected_product_xml_ready':0,'projected_note':'PHH category mapping/fields, authoritative 220_title, background review and package dimensions remain blockers.'}
    arts={'content-dataset.csv':_cb(content,list(content[0]) if content else []),'image-audit.csv':_cb(images,list(images[0]) if images else []),'weight-audit.csv':_cb(weights,list(weights[0]) if weights else []),'grouping-audit.csv':_cb(groups,list(groups[0]) if groups else []),'title-qa.csv':_cb(titleqa,list(titleqa[0]) if titleqa else []),'content-blockers.csv':_cb(blockers,['220_sku','blocker']),'master-bulk-write-proposal.csv':_cb(proposal,list(proposal[0]) if proposal else [])}
    hashes={k:_sha(v) for k,v in arts.items()}; summary['source_hashes']=hashes; summary['dataset_hash']=_sha(_jb(hashes)); arts['content-summary.json']=_jb(summary)
    return arts,summary

def persist_snapshot(database_url,arts,summary):
    if not database_url: return False,'DATABASE_URL missing'
    try:
        import psycopg
        with psycopg.connect(database_url) as c:
            with c.cursor() as cur:
                cur.execute('create table if not exists product_xml_content_snapshots (dataset_hash text primary key, created_at timestamptz not null default now(), summary jsonb not null, artifacts jsonb not null)')
                payload={k:v.decode('utf-8',errors='replace') for k,v in arts.items()}
                cur.execute('insert into product_xml_content_snapshots(dataset_hash,summary,artifacts) values(%s,%s::jsonb,%s::jsonb) on conflict(dataset_hash) do update set summary=excluded.summary,artifacts=excluded.artifacts,created_at=now()', (summary['dataset_hash'],json.dumps(summary),json.dumps(payload)))
            c.commit()
        return True,None
    except Exception as e: return False,f'{type(e).__name__}: {e}'
