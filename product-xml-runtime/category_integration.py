from __future__ import annotations

import csv, hashlib, io, json, re
from dataclasses import dataclass
from typing import Any, Iterable

CANONICAL_MATCH='LEGACY_PRODUCTION_IDENTITY_CANONICAL'
BLOCKED_DUP_MATCH='DUPLICATE_MASTER_220_SKU_CONFLICT'
CATEGORY_WRITE_FIELDS=('220_category_id','220_category_name','220_properties_json')
PROTECTED_FIELDS={
    'shopify_product_id','shopify_variant_id','shopify_sku','shopify_barcode','220_sku','220_ean',
    'shopify_title','220_title','variant_title','vendor','product_type','shopify_status','shopify_price_reference',
    'shopify_main_image_url','220_main_image_url','220_main_image_status','220_status','220_synced',
    '220_product_xml_enabled','220_stock_feed_enabled','220_price_before_discount','220_price_after_discount',
    'price_override_reason','match_status','validation_status','shopify_updated_at','last_sync','notes',
    '220_description','220_grouping_status','220_selected_options_json','220_title_candidate',
    '220_title_candidate_status','220_weight_kg'
}
EXTERNAL_RUNTIME_REQUIRED_UNSTRUCTURED_METAFIELDS='EXTERNAL_RUNTIME_REQUIRED_UNSTRUCTURED_METAFIELDS'
EXTERNAL_RUNTIME_REQUIRED_BACKGROUND_AUDIT='EXTERNAL_RUNTIME_REQUIRED_BACKGROUND_AUDIT'

class CategoryImportError(ValueError):
    pass


def _norm(v: Any) -> str:
    return re.sub(r'\s+', ' ', str(v or '').strip())


def _key(v: Any) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', _norm(v).casefold()).strip()


def _bool(v: Any) -> bool:
    s=_norm(v).casefold()
    if s in {'1','true','yes','y','required','mandatory','obligāts','obligata','obligāti'}: return True
    if s in {'0','false','no','n','optional','nav obligāts','not required',''}: return False
    raise CategoryImportError(f'cannot parse boolean: {v!r}')


def _json(v: Any, default):
    if isinstance(v, (dict,list)): return v
    s=_norm(v)
    if not s: return default
    try: return json.loads(s)
    except json.JSONDecodeError as e: raise CategoryImportError(f'invalid JSON: {s[:200]}') from e


def _split_values(v: Any) -> list[str]:
    if isinstance(v,list): xs=v
    else:
        s=_norm(v)
        if not s: return []
        try:
            j=json.loads(s)
            xs=j if isinstance(j,list) else [s]
        except Exception:
            xs=re.split(r'\s*[|;]\s*',s)
    out=[]; seen=set()
    for x in xs:
        z=_norm(x)
        if z and z.casefold() not in seen:
            seen.add(z.casefold()); out.append(z)
    return out

@dataclass(frozen=True)
class PropertyRule:
    key: str
    name: str
    required: bool
    allowed_values: tuple[str,...]
    value_type: str

@dataclass(frozen=True)
class CategoryRule:
    category_id: str
    category_name: str
    properties: tuple[PropertyRule,...]
    package_dimensions_required: bool | None
    fashion_package_exempt: bool | None
    source_row: int


def ingest_categories(rows: Iterable[dict[str,Any]]) -> dict[str,Any]:
    """Normalize an authoritative PHH Categories fields and values export.

    Accepted aliases are deliberately structural, not taxonomy-specific. No production IDs/names are fabricated.
    Rows may repeat a category for multiple properties; conflicts are rejected.
    """
    aliases={
      'category_id':['category_id','category id','category-id','kategorijas id'],
      'category_name':['category_name','category name','category-name','kategorija','kategorijas nosaukums'],
      'property_key':['property_key','field_key','attribute_key','property id','field id','attribute id'],
      'property_name':['property_name','field_name','attribute_name','property','field','attribute'],
      'required':['required','mandatory','is_required','obligāts','obligats'],
      'allowed_values':['allowed_values','values','allowed values','attribute values','field values'],
      'value_type':['value_type','type','field_type','attribute_type'],
      'package_dimensions_required':['package_dimensions_required','package dimensions required','dimensions_required'],
      'fashion_package_exempt':['fashion_package_exempt','package_dimension_exempt','dimensions_exempt','fashion exemption'],
    }
    def pick(row,name):
        km={_key(k):v for k,v in row.items()}
        for a in aliases[name]:
            if _key(a) in km: return km[_key(a)]
        return ''
    cats={}; conflicts=[]; dup_rows=[]
    for i,row in enumerate(rows, start=2):
        cid=_norm(pick(row,'category_id')); cname=_norm(pick(row,'category_name'))
        if not cid or not cname: raise CategoryImportError(f'row {i}: category ID/name required')
        pd=_norm(pick(row,'package_dimensions_required')); fe=_norm(pick(row,'fashion_package_exempt'))
        dims=None if pd=='' else _bool(pd); exempt=None if fe=='' else _bool(fe)
        pkey=_norm(pick(row,'property_key')); pname=_norm(pick(row,'property_name'))
        prop=None
        if pkey or pname:
            pkey=pkey or _key(pname).replace(' ','_')
            pname=pname or pkey
            prop=PropertyRule(pkey,pname,_bool(pick(row,'required')),_split_values(pick(row,'allowed_values')) and tuple(_split_values(pick(row,'allowed_values'))) or tuple(),_norm(pick(row,'value_type')) or 'string')
        if cid not in cats:
            cats[cid]={'name':cname,'dims':dims,'exempt':exempt,'props':{},'first':i}
        c=cats[cid]
        if c['name']!=cname: conflicts.append({'category_id':cid,'field':'category_name','a':c['name'],'b':cname,'row':i})
        for field,val in [('dims',dims),('exempt',exempt)]:
            if val is not None and c[field] is not None and val!=c[field]: conflicts.append({'category_id':cid,'field':field,'a':c[field],'b':val,'row':i})
            elif c[field] is None: c[field]=val
        if prop:
            old=c['props'].get(prop.key)
            if old and old!=prop: conflicts.append({'category_id':cid,'field':f'property:{prop.key}','a':old.__dict__,'b':prop.__dict__,'row':i})
            elif old: dup_rows.append({'category_id':cid,'property_key':prop.key,'row':i})
            else: c['props'][prop.key]=prop
    if conflicts: raise CategoryImportError('category conflicts: '+json.dumps(conflicts[:20],ensure_ascii=False))
    normalized=[]
    for cid,c in sorted(cats.items(),key=lambda x:x[0]):
        normalized.append({
          'category_id':cid,'category_name':c['name'],
          'properties':[{'key':p.key,'name':p.name,'required':p.required,'allowed_values':list(p.allowed_values),'value_type':p.value_type} for p in sorted(c['props'].values(),key=lambda p:p.key)],
          'package_rules':{'dimensions_required':c['dims'],'fashion_package_exempt':c['exempt']},
          'source_row':c['first']
        })
    payload={'schema_version':'phh-category-normalized-v1','categories':normalized,'validation':{'category_count':len(normalized),'duplicate_identical_rows':len(dup_rows),'conflicts':0}}
    payload['schema_hash']=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    return payload


def ingest_csv_bytes(data: bytes) -> dict[str,Any]:
    text=data.decode('utf-8-sig')
    return ingest_categories(csv.DictReader(io.StringIO(text)))


def _tokens(*vals: Any) -> set[str]:
    stop={'and','or','the','for','with','of','a','an','in','on','to'}
    return {x for x in re.findall(r'[a-z0-9]+',' '.join(_norm(v).casefold() for v in vals)) if len(x)>1 and x not in stop}


def map_clusters(source_rows: Iterable[dict[str,Any]], normalized_schema: dict[str,Any]) -> list[dict[str,Any]]:
    """Generate deterministic candidate categories from authoritative category names only.

    It never invents IDs. Candidate ranking is lexical evidence; ties/weak scores remain ambiguous/unmapped.
    """
    cats=normalized_schema.get('categories') or []
    catvec=[(c,_tokens(c.get('category_name'))) for c in cats]
    out=[]
    for row in source_rows:
        cluster=_norm(row.get('cluster') or row.get('source_cluster') or row.get('cohort') or row.get('cohort_key') or row.get('product_type'))
        evidence=_tokens(cluster,row.get('product_type'),row.get('title_semantic_cohort'),row.get('title_reference'),row.get('vendor'))
        scored=[]
        for c,ct in catvec:
            if not evidence or not ct: score=0.0
            else:
                inter=len(evidence & ct); union=len(evidence | ct); score=inter/union if union else 0.0
                if _key(c['category_name'])==_key(cluster) and cluster: score=1.0
            if score>0: scored.append((round(score,6),c))
        scored.sort(key=lambda x:(-x[0],x[1]['category_id']))
        top=scored[:5]
        if not top: status='UNMAPPED'
        elif top[0][0] < 0.20: status='UNMAPPED'
        elif len(top)>1 and abs(top[0][0]-top[1][0])<0.08: status='AMBIGUOUS'
        else: status='CANDIDATE'
        out.append({
          'source_key':cluster,
          'status':status,
          'candidates':[{'category_id':c['category_id'],'category_name':c['category_name'],'score':s,'required_properties':[p['key'] for p in c.get('properties',[]) if p.get('required')],'package_rules':c.get('package_rules',{})} for s,c in top],
          'authoritative_category_write':'NO'
        })
    return out


def build_category_write_plan(master_rows: list[dict[str,Any]], assignments: Iterable[dict[str,Any]]) -> dict[str,Any]:
    """Build a disabled dry-run plan targeting canonical identity 220_sku+220_ean only."""
    idx={}
    for pos,row in enumerate(master_rows,start=2):
        ident=(_norm(row.get('220_sku')),_norm(row.get('220_ean')))
        if not all(ident): continue
        idx.setdefault(ident,[]).append((pos,row))
    plan=[]; rollback=[]; errors=[]
    for a in assignments:
        ident=(_norm(a.get('220_sku')),_norm(a.get('220_ean')))
        matches=idx.get(ident,[])
        if not matches: errors.append({'identity':ident,'error':'MISSING_IDENTITY'}); continue
        canon=[x for x in matches if _norm(x[1].get('match_status'))==CANONICAL_MATCH and _norm(x[1].get('220_status'))=='ACTIVE_220']
        if len(matches)>1:
            if len(canon)!=1: errors.append({'identity':ident,'error':'AMBIGUOUS_DUPLICATE_NO_EXACT_CANONICAL'}); continue
            target=canon[0]
            for rn,r in matches:
                if rn!=target[0] and _norm(r.get('match_status'))!=BLOCKED_DUP_MATCH: errors.append({'identity':ident,'error':'DUPLICATE_NOT_EXPLICITLY_BLOCKED','row':rn})
        else: target=matches[0]
        rn,row=target
        if _norm(row.get('match_status'))==BLOCKED_DUP_MATCH or _norm(row.get('220_status'))=='BLOCKED': errors.append({'identity':ident,'error':'BLOCKED_ROW'}); continue
        changes={
          '220_category_id':_norm(a.get('220_category_id')),
          '220_category_name':_norm(a.get('220_category_name')),
          '220_properties_json':json.dumps(a.get('220_properties') or {},sort_keys=True,separators=(',',':'),ensure_ascii=False)
        }
        if not changes['220_category_id'] or not changes['220_category_name']: errors.append({'identity':ident,'error':'EMPTY_CATEGORY'}); continue
        for f,v in changes.items():
            before=_norm(row.get(f))
            if before and before!=v: errors.append({'identity':ident,'error':'PROTECTED_CATEGORY_OVERWRITE','field':f,'before':before,'after':v}); continue
            if before!=v:
                plan.append({'row':rn,'220_sku':ident[0],'220_ean':ident[1],'field':f,'before':before,'after':v})
                rollback.append({'row':rn,'220_sku':ident[0],'220_ean':ident[1],'field':f,'before':before})
    payload={'enabled':False,'write_count':len(plan),'errors':errors,'plan':plan,'rollback':rollback}
    payload['dry_run_hash']=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    return payload


def verify_category_readback(before_rows: list[dict[str,Any]], after_rows: list[dict[str,Any]], plan: dict[str,Any]) -> dict[str,Any]:
    """Verification routine for future writer: exact readback + protected/identity/row-count checks."""
    failures=[]
    if len(before_rows)!=len(after_rows): failures.append('ROW_COUNT_CHANGED')
    for ch in plan.get('plan',[]):
        rn=ch['row']; got=_norm(after_rows[rn-2].get(ch['field'])) if 0 <= rn-2 < len(after_rows) else '__MISSING_ROW__'
        if got!=ch['after']: failures.append({'row':rn,'field':ch['field'],'expected':ch['after'],'got':got})
    for i,(b,a) in enumerate(zip(before_rows,after_rows),start=2):
        if (_norm(b.get('220_sku')),_norm(b.get('220_ean')))!=(_norm(a.get('220_sku')),_norm(a.get('220_ean'))): failures.append({'row':i,'error':'IDENTITY_CHANGED'})
        for f in PROTECTED_FIELDS:
            if _norm(b.get(f))!=_norm(a.get(f)): failures.append({'row':i,'error':'PROTECTED_FIELD_CHANGED','field':f})
    return {'status':'PASS' if not failures else 'FAIL','failure_count':len(failures),'failures':failures}


def category_aware_readiness(identity: dict[str,Any], category: dict[str,Any] | None, *, background_status: str | None, external_metafield_gate_resolved: bool=False) -> dict[str,Any]:
    """Category-specific readiness kept separate from external image/metafield gates."""
    cat_pass=bool(category and category.get('category_id') and category.get('category_name'))
    props=identity.get('properties') or {}
    req=[p for p in (category or {}).get('properties',[]) if p.get('required')]
    prop_fail=[]
    for p in req:
        v=props.get(p['key'])
        if v in (None,'',[]): prop_fail.append(p['key']); continue
        allowed=p.get('allowed_values') or []
        if allowed and _norm(v) not in {_norm(x) for x in allowed}: prop_fail.append(p['key'])
    props_pass=cat_pass and not prop_fail
    pkg=(category or {}).get('package_rules') or {}
    exempt=pkg.get('fashion_package_exempt') is True
    dims_required=(pkg.get('dimensions_required') is True) and not exempt
    dims_available=all(identity.get(x) not in (None,'') for x in ('package_length','package_width','package_height'))
    dimension_pass=(not dims_required) or dims_available
    external={
      'background_gate': background_status or EXTERNAL_RUNTIME_REQUIRED_BACKGROUND_AUDIT,
      'unstructured_metafield_gate': 'PASS' if external_metafield_gate_resolved else EXTERNAL_RUNTIME_REQUIRED_UNSTRUCTURED_METAFIELDS
    }
    base_noncategory=all(bool(identity.get(k)) for k in ('title_pass','description_pass','grouping_pass','ean_pass','supplier_code_pass','images_ge2_pass'))
    category_ready=cat_pass and props_pass and dimension_pass
    first_pilot=base_noncategory and category_ready and background_status=='BACKGROUND_PASS' and external_metafield_gate_resolved
    return {
      'category_pass':cat_pass,'required_properties_pass':props_pass,'missing_or_invalid_properties':prop_fail,
      'fashion_package_exempt':exempt,'package_dimensions_required':dims_required,'package_dimensions_pass':dimension_pass,
      'external_gates':external,'first_pilot_candidate':first_pilot,'ready_product_xml':False
    }
