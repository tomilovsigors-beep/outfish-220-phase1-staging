from __future__ import annotations

import csv, io, json, re, hashlib, unicodedata
from collections import Counter, defaultdict

LANG_FIELDS=('title_en','title_lv','title_lt','title_ee','title_fi','title_ru','title_pl')
LEGACY_MISSING={157,3551,8384,17867,3221,3203,21879}
LEGACY_NONADDABLE={20868}
STOP={'and','or','the','for','with','of','a','an','in','on','to','by','from','product','products','item','items','fhm','un','ar','no','uz','ir','bei','su','ja','ning','tai','seka'}


def _norm(v):
    return re.sub(r'\s+',' ',str(v or '').strip())


def _fold(v):
    s=unicodedata.normalize('NFKD',_norm(v).casefold())
    return ''.join(ch for ch in s if not unicodedata.combining(ch))


def _words(v):
    return [x for x in re.findall(r'[^\W_]+',_fold(v),flags=re.UNICODE) if len(x)>1 and x not in STOP]


def _tokens(*vals):
    out=set()
    for v in vals: out.update(_words(v))
    return out


def _key(v):
    return ' '.join(_words(v))


def _csv_bytes(rows,fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    return s.getvalue().encode('utf-8-sig')


def _latest_taxonomy(db):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select summary,artifacts from phh_category_export_snapshots order by created_at desc limit 1')
            row=cur.fetchone()
            if not row: raise RuntimeError('no PHH category export snapshot')
    summary,arts=row
    if not isinstance(summary,dict): summary=json.loads(summary)
    if not isinstance(arts,dict): arts=json.loads(arts)
    cats=list(csv.DictReader(io.StringIO(arts['pmp-categories.csv'])))
    attrs=list(csv.DictReader(io.StringIO(arts['pmp-category-attributes.csv'])))
    return summary,cats,attrs


def _canonical_identities(master_rows):
    by=defaultdict(list)
    for rn,r in enumerate(master_rows,start=2):
        sku=_norm(r.get('220_sku')); ean=_norm(r.get('220_ean'))
        if sku and ean: by[(sku,ean)].append((rn,r))
    fields=['shopify_product_id','shopify_variant_id','shopify_sku','shopify_barcode','shopify_title','220_title','variant_title','vendor','product_type','shopify_status','220_status','match_status','validation_status','notes','220_selected_options_json','220_title_candidate','220_title_candidate_status','220_weight_kg','220_category_id','220_category_name','220_properties_json']
    out=[]
    for (sku,ean),rows in sorted(by.items()):
        x={'220_sku':sku,'220_ean':ean,'master_rows':','.join(str(rn) for rn,_ in rows),'duplicate_row_count':len(rows)}; conflicts=[]
        for f in fields:
            vals=[]
            for _,r in rows:
                v=_norm(r.get(f))
                if v and v not in vals: vals.append(v)
            x[f]=vals[0] if vals else ''
            if len(vals)>1: conflicts.append(f)
        x['duplicate_conflict_fields']='|'.join(conflicts); out.append(x)
    return out


def _shopify_enrich(i,shopify):
    pid=_norm(i.get('shopify_product_id')); vid=_norm(i.get('shopify_variant_id'))
    p=shopify.get(pid) if pid else None
    v=(p.get('variants_by_id') or {}).get(vid) if p and vid else None
    return p or {},v or {}


def _selected_options(i,v):
    out={}; raw=_norm(i.get('220_selected_options_json'))
    if raw:
        try:
            j=json.loads(raw)
            if isinstance(j,dict):
                for k,val in j.items(): out[_key(k)]=_norm(val)
            elif isinstance(j,list):
                for x in j:
                    if isinstance(x,dict) and x.get('name'): out[_key(x.get('name'))]=_norm(x.get('value'))
        except Exception: pass
    for x in v.get('selected_options') or []:
        if isinstance(x,dict) and x.get('name'): out.setdefault(_key(x.get('name')),_norm(x.get('value')))
    return out


def _group_key(i):
    pid=_norm(i.get('shopify_product_id'))
    if pid: return 'SHOPIFY_PRODUCT:'+pid
    title=_norm(i.get('220_title')) or _norm(i.get('220_title_candidate')) or _norm(i.get('shopify_title'))
    if title: return 'TITLE:'+_key(title)
    sku=_norm(i.get('220_sku'))
    family=re.sub(r'[-_](?:xs|s|m|l|xl|xxl|2xl|3xl|4xl|5xl|6xl|\d{1,3})$','',sku,flags=re.I)
    return 'SKU_FAMILY:'+family


def _is_guarded_category(c):
    en=_key(c.get('title_en'))
    return en.startswith('other ') or 'damaged packaging' in en or 'installation service' in en


def _category_index(cats,attrs):
    by_id={str(c.get('category_id')):c for c in cats}; required=defaultdict(list)
    for a in attrs:
        if str(a.get('required')).casefold() in {'true','1','yes'}: required[str(a.get('category_id'))].append(a)
    addable=[]
    for c in cats:
        if str(c.get('allow_add_products')).casefold() not in {'true','1','yes'}: continue
        leaf_titles=[_norm(c.get(f)) for f in LANG_FIELDS if _norm(c.get(f))]
        leaf_by_lang=[(_key(x),_tokens(x)) for x in leaf_titles if _key(x)]
        cur=_norm(c.get('parent_id')); seen=set(); ancestor_tokens=set(); path_ids=[]
        while cur and cur not in seen and len(path_ids)<20:
            seen.add(cur); path_ids.append(cur); pc=by_id.get(cur)
            if not pc: break
            ancestor_tokens|=_tokens(*[_norm(pc.get(f)) for f in LANG_FIELDS]); cur=_norm(pc.get('parent_id'))
        c2=dict(c); c2['_leaf_by_lang']=leaf_by_lang; c2['_leaf_keys']={k for k,_ in leaf_by_lang}; c2['_path_ids']=path_ids; c2['_guarded']=_is_guarded_category(c)
        addable.append((c2,ancestor_tokens))
    return by_id,required,addable


def _best_leaf_overlap(source_tokens,leaf_by_lang):
    best=(0.0,0,set())
    for _,lt in leaf_by_lang:
        if not lt: continue
        inter=source_tokens & lt; cov=len(inter)/len(lt)
        if (cov,len(inter))>(best[0],best[1]): best=(cov,len(inter),inter)
    return best


def _candidate_scores(i,p,group_members,addable):
    titles=[i.get('220_title'),i.get('220_title_candidate'),i.get('shopify_title'),p.get('title')]
    title_tokens=_tokens(*titles); product_type=_norm(p.get('product_type')) or _norm(i.get('product_type')); type_tokens=_tokens(product_type)
    shopcat=_norm(p.get('category_name')); shop_tokens=_tokens(shopcat); variant_tokens=_tokens(i.get('variant_title')); vendor_tokens=_tokens(i.get('vendor'))
    base=title_tokens|type_tokens|shop_tokens|variant_tokens|vendor_tokens
    title_keys={_key(x) for x in titles if _key(x)}; type_key=_key(product_type); shop_key=_key(shopcat); legacy=_norm(i.get('220_category_id'))
    group_tokens=set()
    for g in group_members: group_tokens|=_tokens(g.get('220_title'),g.get('220_title_candidate'),g.get('shopify_title'),g.get('product_type'))
    scored=[]
    for c,anc in addable:
        leaf_by_lang=c['_leaf_by_lang']; leaf_keys=c['_leaf_keys']; cid=str(c.get('category_id'))
        title_cov,title_n,_=_best_leaf_overlap(title_tokens,leaf_by_lang); type_cov,type_n,_=_best_leaf_overlap(type_tokens,leaf_by_lang); group_cov,group_n,_=_best_leaf_overlap(group_tokens,leaf_by_lang)
        shop_all=shop_tokens|set(); shop_cov,shop_n,_=_best_leaf_overlap(shop_all,leaf_by_lang)
        exact_type=bool(type_key and type_key in leaf_keys); exact_shop=bool(shop_key and shop_key in leaf_keys); exact_title=bool(title_keys&leaf_keys); legacy_exact=bool(legacy and cid==legacy)
        ancestor_cov=len(base&anc)/max(1,len(base)) if anc else 0.0
        if not (title_n or type_n or shop_n or exact_type or exact_shop or exact_title or legacy_exact): continue
        score=0.40*title_cov+0.22*type_cov+0.18*shop_cov+0.12*group_cov+0.03*ancestor_cov
        strong=[]
        if exact_type: score=max(score,0.93); strong.append('exact_product_type')
        if exact_shop: score=max(score,0.93); strong.append('exact_shopify_category')
        if exact_title: score=max(score,0.91); strong.append('exact_title_category')
        if legacy_exact: score=max(score,0.95); strong.append('existing_master_category_hint')
        if title_cov>=0.80 and title_n>=2: strong.append('high_title_leaf_overlap')
        if type_cov>=0.80 and type_n>=1 and len(type_tokens)>=1: strong.append('high_product_type_overlap')
        if shop_cov>=0.80 and shop_n>=1 and len(shop_tokens)>=1: strong.append('high_shopify_category_overlap')
        if c['_guarded'] and not (exact_type or exact_shop or legacy_exact):
            score*=0.35; strong=[x for x in strong if x in {'existing_master_category_hint'}]
        score=min(0.99,round(score+min(0.04,0.01*max(0,len(strong)-1)),6))
        evidence={'title_coverage':round(title_cov,4),'title_overlap':title_n,'product_type_coverage':round(type_cov,4),'shopify_category_coverage':round(shop_cov,4),'group_coverage':round(group_cov,4),'ancestor_coverage':round(ancestor_cov,4),'strong_signals':strong,'guarded_category':c['_guarded'],'path_ids':c['_path_ids']}
        scored.append((score,c,evidence))
    scored.sort(key=lambda x:(-x[0],str(x[1].get('category_id')))); return scored[:5]


def _group_consensus(raw_by_identity,groups):
    out={}
    for gkey,members in groups.items():
        votes=Counter(); score_sum=defaultdict(float)
        for i in members:
            top=(raw_by_identity.get((i['220_sku'],i['220_ean'])) or [])[:1]
            if not top: continue
            s,c,_=top[0]; cid=str(c.get('category_id')); votes[cid]+=1; score_sum[cid]+=s
        if not votes: continue
        cid,count=votes.most_common(1)[0]; size=len(members); ratio=count/max(1,size); avg=score_sum[cid]/count
        if size>=2 and count>=2 and ratio>=0.75 and avg>=0.50: out[gkey]={'category_id':cid,'count':count,'size':size,'ratio':ratio,'avg_score':avg}
    return out


FIELD_SEMANTICS={
 'brand':['prekes zenklas','prekes zenklo','zimols','kaubamark','merkki','brend','brand'],
 'color':['spalva','krasa','varv','vari','cvet','color','colour'],
 'size':['dydis','izmers','suurus','koko','razmer','size'],
 'weight':['svoris','svars','kaal','paino','ves','weight'],
 'model':['modelis','model','mudel','malli','model'],
 'material':['medziaga','materials','materjal','materiaali','material'],
}


def _semantic_field(a):
    text=_key(' '.join(_norm(a.get(k)) for k in ('title_lt','title_lv','title_ee','title_fi','title_ru')))
    for sem,keys in FIELD_SEMANTICS.items():
        if any(_key(k) in text for k in keys): return sem
    return ''


def _attribute_source(a,i,p,v,opts):
    sem=_semantic_field(a)
    if sem=='brand':
        val=_norm(i.get('vendor')) or _norm(p.get('vendor')); return ('SOURCE_IDENTIFIED','master.vendor/shopify.vendor',val) if val else ('MISSING_SOURCE','','')
    if sem=='weight':
        val=_norm(i.get('220_weight_kg'))
        if val: return ('SOURCE_IDENTIFIED_UNIT_UNPROVEN','master.220_weight_kg',val)
        w=v.get('weight') or {}; val=_norm(w.get('value')); return ('SOURCE_IDENTIFIED_UNIT_UNPROVEN','shopify.variant.weight',val) if val else ('MISSING_SOURCE','','')
    if sem in {'color','size'}:
        keys={'color':['color','colour','spalva','krasa','varv','vari'],'size':['size','dydis','izmers','suurus','koko']}[sem]
        for k,val in opts.items():
            if any(_key(x) in k for x in keys) and _norm(val): return ('SOURCE_IDENTIFIED_ALLOWED_VALUES_UNPROVEN','selected_options',_norm(val))
        return ('MISSING_SOURCE','','')
    if sem=='material': return ('POTENTIAL_UNSTRUCTURED_SOURCE','title/description','')
    return ('MISSING_SOURCE','','')


def _persist(db,summary,artifacts):
    import psycopg
    payload={k:v.decode('utf-8-sig') for k,v in artifacts.items()}
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''create table if not exists phh_product_mapping_snapshots(id bigserial primary key,created_at timestamptz not null default now(),dataset_hash text not null,summary jsonb not null,artifacts jsonb not null)''')
            cur.execute('insert into phh_product_mapping_snapshots(dataset_hash,summary,artifacts) values(%s,%s::jsonb,%s::jsonb)',(summary['dataset_hash'],json.dumps(summary,ensure_ascii=False),json.dumps(payload,ensure_ascii=False)))
        c.commit()


def load_latest_artifact(db,name):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select artifacts->>%s from phh_product_mapping_snapshots order by created_at desc limit 1',(name,)); row=cur.fetchone()
            return None if not row or row[0] is None else row[0].encode('utf-8-sig')


def run_audit(master_rows,shopify,db):
    tax_summary,cats,attrs=_latest_taxonomy(db); by_cat,required,addable=_category_index(cats,attrs); ids=_canonical_identities(master_rows)
    if len(ids)!=1671: raise RuntimeError(f'canonical identity universe changed: {len(ids)} != 1671')
    groups=defaultdict(list)
    for i in ids: groups[_group_key(i)].append(i)
    raw={}; enriched={}
    for i in ids:
        p,v=_shopify_enrich(i,shopify); enriched[(i['220_sku'],i['220_ean'])]=(p,v); raw[(i['220_sku'],i['220_ean'])]=_candidate_scores(i,p,groups[_group_key(i)],addable)
    consensus=_group_consensus(raw,groups)
    mapping=[]; gaps=[]; exceptions=[]; status_counts=Counter(); selected_counts=Counter(); score_buckets=Counter(); input_coverage=Counter(); group_status=Counter()
    for i in ids:
        k=(i['220_sku'],i['220_ean']); p,v=enriched[k]; opts=_selected_options(i,v); gkey=_group_key(i); scored=raw[k]; legacy=_norm(i.get('220_category_id'))
        if _norm(i.get('shopify_product_id')): input_coverage['shopify_product_link']+=1
        if _norm(i.get('220_title')): input_coverage['authoritative_220_title']+=1
        if _norm(p.get('product_type')) or _norm(i.get('product_type')): input_coverage['product_type']+=1
        if _norm(p.get('category_name')): input_coverage['shopify_category_hint']+=1
        if legacy: input_coverage['existing_220_category_hint']+=1
        top=scored[0] if scored else None; second=scored[1] if len(scored)>1 else None; selected=top[1] if top else None
        confidence=top[0] if top else 0.0; margin=(confidence-second[0]) if top and second else confidence
        status='NO_MATCH'; reason='no safe live PHH category candidate'; strong=list(top[2].get('strong_signals') or []) if top else []
        gc=consensus.get(gkey)
        if top and gc and str(top[1].get('category_id'))==gc['category_id']:
            strong.append('group_consensus'); confidence=min(0.99,round(confidence+0.04,6))
        legacy_int=int(legacy) if legacy.isdigit() else None
        if legacy_int in LEGACY_MISSING or legacy_int in LEGACY_NONADDABLE:
            status='BLOCKED_TAXONOMY'; reason='existing Master category hint is missing or non-addable in live PHH taxonomy'; selected=by_cat.get(legacy)
        elif top:
            independent_exact=any(x in strong for x in ('exact_product_type','exact_shopify_category','exact_title_category','existing_master_category_hint'))
            corroborated=independent_exact and len(set(strong))>=2 and confidence>=0.88 and margin>=0.10
            if corroborated: status='AUTO'; reason='high-confidence category with independent exact evidence plus corroboration'
            else: status='REVIEW'; reason='candidate category requires review; evidence is not sufficiently independent/corroborated'
        req=required.get(str(selected.get('category_id'))) if selected else []; req=req or []; missing=[]; identified=[]
        for a in req:
            src_status,src,val=_attribute_source(a,i,p,v,opts)
            gaps.append({'220_sku':i['220_sku'],'220_ean':i['220_ean'],'group_key':gkey,'category_id':str(selected.get('category_id')) if selected else '','category_name':(_norm(selected.get('title_en')) or _norm(selected.get('title_lv'))) if selected else '','field_id':_norm(a.get('field_id')),'required':'TRUE','field_title_lt':_norm(a.get('title_lt')),'field_title_lv':_norm(a.get('title_lv')),'field_title_ee':_norm(a.get('title_ee')),'field_title_fi':_norm(a.get('title_fi')),'field_title_ru':_norm(a.get('title_ru')),'semantic':_semantic_field(a),'source_status':src_status,'source':src,'source_value':val})
            (identified if src_status.startswith('SOURCE_IDENTIFIED') else missing).append(str(a.get('field_id')))
        if status=='AUTO' and missing: status='BLOCKED_ATTRIBUTES'; reason='category is high-confidence but required PHH fields lack a safe structured source'
        if i.get('duplicate_conflict_fields'): status='REVIEW'; reason='Master duplicate identity rows contain conflicting classification evidence'
        candidates=[]
        for s,c,ev in scored:
            ev=dict(ev)
            if gc and str(c.get('category_id'))==gc['category_id']: ev['group_consensus']=gc
            candidates.append({'category_id':str(c.get('category_id')),'category_name':_norm(c.get('title_en')) or _norm(c.get('title_lv')),'score':s,'evidence':ev})
        row={'220_sku':i['220_sku'],'220_ean':i['220_ean'],'group_key':gkey,'group_size':len(groups[gkey]),'brand':_norm(i.get('vendor')) or _norm(p.get('vendor')),'product_type':_norm(p.get('product_type')) or _norm(i.get('product_type')),'master_220_title':_norm(i.get('220_title')),'master_title_candidate':_norm(i.get('220_title_candidate')),'shopify_title':_norm(i.get('shopify_title')) or _norm(p.get('title')),'variant_title':_norm(i.get('variant_title')) or _norm(v.get('title')),'shopify_category_hint':_norm(p.get('category_name')),'existing_220_category_hint':legacy,'selected_category_id':str(selected.get('category_id')) if selected else '','selected_category_name':(_norm(selected.get('title_en')) or _norm(selected.get('title_lv'))) if selected else '','confidence':f'{confidence:.6f}','margin_to_second':f'{margin:.6f}','status':status,'reason':reason,'strong_signals':'|'.join(sorted(set(strong))),'required_field_ids':'|'.join(str(a.get('field_id')) for a in req),'source_identified_field_ids':'|'.join(identified),'missing_required_field_ids':'|'.join(missing),'candidate_json':json.dumps(candidates,ensure_ascii=False,sort_keys=True,separators=(',',':')),'master_rows':i.get('master_rows'),'duplicate_conflict_fields':i.get('duplicate_conflict_fields'),'phh_write':'NO','master_write':'NO','shopify_write':'NO'}
        mapping.append(row); status_counts[status]+=1
        if selected: selected_counts[str(selected.get('category_id'))+' '+(_norm(selected.get('title_en')) or _norm(selected.get('title_lv')))]+=1
        score_buckets['0']+=confidence==0; score_buckets['0-0.49']+=0<confidence<0.5; score_buckets['0.50-0.77']+=0.5<=confidence<0.78; score_buckets['0.78-0.87']+=0.78<=confidence<0.88; score_buckets['0.88+']+=confidence>=0.88
        if status!='AUTO': exceptions.append(row)
    for gkey,members in groups.items():
        sts=Counter(next(r['status'] for r in mapping if r['220_sku']==i['220_sku'] and r['220_ean']==i['220_ean']) for i in members); group_status[sts.most_common(1)[0][0]]+=1
    map_fields=['220_sku','220_ean','group_key','group_size','brand','product_type','master_220_title','master_title_candidate','shopify_title','variant_title','shopify_category_hint','existing_220_category_hint','selected_category_id','selected_category_name','confidence','margin_to_second','status','reason','strong_signals','required_field_ids','source_identified_field_ids','missing_required_field_ids','candidate_json','master_rows','duplicate_conflict_fields','phh_write','master_write','shopify_write']
    gap_fields=['220_sku','220_ean','group_key','category_id','category_name','field_id','required','field_title_lt','field_title_lv','field_title_ee','field_title_fi','field_title_ru','semantic','source_status','source','source_value']
    mapping_bytes=_csv_bytes(mapping,map_fields); dataset_hash=hashlib.sha256(mapping_bytes).hexdigest()
    high_review=sorted((r for r in mapping if r['status']=='REVIEW'),key=lambda r:(-float(r['confidence']),r['220_sku']))[:20]
    no_match=[{'220_sku':r['220_sku'],'220_ean':r['220_ean'],'group_key':r['group_key'],'title':r['master_220_title'] or r['master_title_candidate'] or r['shopify_title'],'product_type':r['product_type']} for r in mapping if r['status']=='NO_MATCH'][:20]
    summary={'status':'PASS','mapping_version':'group-consensus-v3','identity_count':len(ids),'group_count':len(groups),'group_consensus_count':len(consensus),'group_status_counts':dict(group_status),'addable_live_categories':len(addable),'taxonomy_categories_fetched':tax_summary.get('categories_fetched'),'mapping_status_counts':dict(status_counts),'input_coverage':dict(input_coverage),'score_buckets':dict(score_buckets),'top_selected_categories':dict(selected_counts.most_common(20)),'high_confidence_review_sample':[{k:r[k] for k in ('220_sku','group_key','master_220_title','master_title_candidate','shopify_title','product_type','shopify_category_hint','selected_category_id','selected_category_name','confidence','margin_to_second','strong_signals')} for r in high_review],'no_match_sample':no_match,'exception_count':len(exceptions),'attribute_gap_rows':len(gaps),'dataset_hash':dataset_hash,'legacy_missing_category_ids':sorted(LEGACY_MISSING),'legacy_nonaddable_category_ids':sorted(LEGACY_NONADDABLE),'phh_writes':0,'master_writes':0,'shopify_writes':0,'product_xml_publication':'OFF','classification_method':'deterministic token-safe multilingual Master + exact Shopify evidence + group consensus + live PHH taxonomy; guarded generic/special categories; no identity fuzzy matching'}
    artifacts={'current-product-category-mapping.csv':mapping_bytes,'current-product-category-exceptions.csv':_csv_bytes(exceptions,map_fields),'current-product-attribute-gap.csv':_csv_bytes(gaps,gap_fields),'current-product-category-summary.json':json.dumps(summary,ensure_ascii=False,sort_keys=True,indent=2).encode()}
    _persist(db,summary,artifacts); return summary,artifacts
