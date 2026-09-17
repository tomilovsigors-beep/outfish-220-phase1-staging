from __future__ import annotations

import json, os
from collections import Counter

PRIORITY = {'9050','19576','17977'}


def _latest_raw_categories(db):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute("select created_at, summary, artifacts->>'pmp-categories-full.json' from phh_category_export_snapshots order by created_at desc limit 1")
            row=cur.fetchone()
    if not row or not row[2]:
        return None,None,None
    raw=json.loads(row[2])
    return row[0].isoformat(), row[1] if isinstance(row[1],dict) else json.loads(row[1]), raw


def _priority_raw_report(raw):
    out={}
    cats=(raw or {}).get('category_list') or []
    for c in cats:
        cid=str(c.get('category_id'))
        if cid not in PRIORITY: continue
        attrs=c.get('attributes') or []
        attr_rows=[]
        all_keys=set()
        for a in attrs:
            if not isinstance(a,dict): continue
            all_keys.update(a.keys())
            # keep official response data; no inferred semantics
            attr_rows.append({k:a.get(k) for k in sorted(a.keys())})
        out[cid]={
            'category_title_en':c.get('title_en'),
            'allow_add_products':c.get('allow_add_products'),
            'attribute_count':len(attrs),
            'attribute_keys':sorted(all_keys),
            'attributes':attr_rows,
        }
    return out


def run(db=None):
    db=db or os.getenv('DATABASE_URL')
    from pmp_api_probe import discover
    result=discover()
    oa=result.get('openapi_summary') or {}
    semantic_paths=oa.get('semantic_paths') or []
    semantic_schemas=oa.get('semantic_schemas') or {}
    created, export_summary, raw=_latest_raw_categories(db)
    priority=_priority_raw_report(raw)

    get_paths=[]
    non_get=[]
    for p in semantic_paths:
        for op in p.get('operations') or []:
            rec={'path':p.get('path'),'method':op.get('method'),'summary':op.get('summary'),'operationId':op.get('operationId'),'parameters':op.get('parameters'),'responses':op.get('responses')}
            (get_paths if op.get('method')=='GET' else non_get).append(rec)

    schema_hits={}
    for name,schema in semantic_schemas.items():
        text=json.dumps(schema,ensure_ascii=False).lower()
        flags=[w for w in ('type','unit','value','values','enum','option','dictionary','field','attribute') if w in text]
        schema_hits[name]=flags

    raw_key_counts=Counter()
    for info in priority.values():
        for a in info.get('attributes') or []:
            raw_key_counts.update(a.keys())

    summary={
        'status':result.get('status'),
        'docs':result.get('docs'),
        'openapi':{
            'parsed':oa.get('embedded_spec_parsed'),
            'title':oa.get('title'),
            'version':oa.get('version'),
            'path_count':oa.get('path_count'),
            'semantic_get_path_count':len(get_paths),
            'semantic_non_get_path_count':len(non_get),
            'semantic_schema_count':len(semantic_schemas),
        },
        'credentials_probe':{
            'docs_access_ok':(result.get('docs') or {}).get('status')==200,
            'api_login':oa.get('api_login_probe'),
            'categories_probe':{k:v for k,v in (oa.get('categories_probe') or {}).items() if k!='json_shape'},
        },
        'full_category_export':oa.get('full_category_export'),
        'latest_category_snapshot':created,
        'latest_export_summary':export_summary,
        'priority_category_raw_attribute_keys':{cid:info.get('attribute_keys') for cid,info in priority.items()},
        'priority_raw_key_counts':dict(raw_key_counts),
        'safety':{'Master_writes':0,'PHH_marketplace_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','stock_changes':0,'price_changes':0,'auth_login_POST_only':True,'marketplace_mutation_requests':0},
    }
    payload={'summary':summary,'semantic_get_paths':get_paths,'semantic_non_get_paths':non_get,'semantic_schemas':semantic_schemas,'semantic_schema_keyword_hits':schema_hits,'priority_categories_raw':priority}

    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('''create table if not exists phh_attribute_authority_v11b_snapshots(id bigserial primary key, created_at timestamptz not null default now(), summary jsonb not null, payload jsonb not null)''')
            cur.execute('insert into phh_attribute_authority_v11b_snapshots(summary,payload) values(%s::jsonb,%s::jsonb)',(json.dumps(summary,ensure_ascii=False),json.dumps(payload,ensure_ascii=False)))
        c.commit()
    print('PMP_ATTRIBUTE_AUTHORITY_V11B_RESULT '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
    return summary,payload
