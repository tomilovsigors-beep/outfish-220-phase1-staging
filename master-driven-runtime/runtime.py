from __future__ import annotations
import csv, hashlib, io, json, os, threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import requests
import psycopg
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
from compact_loader import load_locked_mapped

ROOT=Path(__file__).resolve().parent
SNAP=Path(os.getenv('SNAPSHOT_DIR', str(ROOT/'runtime_snapshots'))); SNAP.mkdir(parents=True,exist_ok=True)
EXPLICIT_BLOCKERS={'000055-0004-OneSize','000055-0008-OneSize'}
SPECIAL_CANONICAL_BLOCKED={'NH21MSD08L','NH21MSD08R'}
EXPECTED_TOTAL=1671; EXPECTED_SHOPIFY=970; EXPECTED_EXTERNAL=701
STORE='gid://shopify/Location/84891861330'; LOWA='gid://shopify/Location/107817075026'; FJORD='gid://shopify/Location/107805770066'
KNOWN_LOCATIONS=[STORE,LOWA,FJORD]
KNOWN_LOCATION_NAMES={STORE:'Cēsu iela 18, Veikals',LOWA:'Cēcu 18, (Lowa)',FJORD:'Cēsu 18, (Fjord Nansen)'}
ARTIFACT_NAMES=['audit.json','snapshot-manifest.json','runtime-dataset.csv','blockers.csv','shopify_inventory_snapshot.csv','production-equivalent-comparison.csv','220-stock-master-driven.xml']
_state_lock=threading.RLock(); _state={'service_status':'starting','stale':False,'last_attempt':None,'last_successful_refresh':None,'error':None,'validated_dir':None,'audit':{},'manifest':{},'persistence_status':'not_initialized','recovered_from_durable':False}

def now(): return datetime.now(timezone.utc).isoformat()
def norm(v): return '' if v is None else str(v).strip()
def dec(v):
    s=norm(v)
    if not s:return None
    try:return Decimal(s)
    except InvalidOperation:return None
def exact_int(v):
    s=norm(v)
    if not s:return None
    try:
        d=Decimal(s)
        if d!=d.to_integral_value():return None
        return int(d)
    except InvalidOperation:return None
def sha(b): return hashlib.sha256(b).hexdigest()
def csv_bytes(rows,fields):
    s=io.StringIO(newline=''); w=csv.DictWriter(s,fieldnames=fields); w.writeheader(); w.writerows(rows); return s.getvalue().encode()
def canon_hash(rows,fields): return sha(('\n'.join('|'.join(norm(r.get(k)) for k in fields) for r in rows)).encode())
def deployed_commit(): return os.getenv('RENDER_GIT_COMMIT') or os.getenv('RENDER_GIT_COMMIT_SHA') or os.getenv('DEPLOY_COMMIT_SHA') or 'unknown'
def audit_database_url():
    url=os.getenv('AUDIT_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('AUDIT_DATABASE_URL missing')
    return url

def fetch_sheet_csv(sheet_id,gid,url_env):
    url=os.getenv(url_env) or f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}'
    sa=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if sa:
        creds=service_account.Credentials.from_service_account_info(json.loads(sa),scopes=['https://www.googleapis.com/auth/drive.readonly','https://www.googleapis.com/auth/spreadsheets.readonly'])
        r=AuthorizedSession(creds).get(url,timeout=45)
    else: r=requests.get(url,timeout=45,allow_redirects=True)
    r.raise_for_status()
    if 'text/html' in r.headers.get('content-type','') and b'<html' in r.content[:500].lower(): raise RuntimeError('Google sheet fetch returned HTML/login page')
    return r.content,list(csv.DictReader(io.StringIO(r.content.decode('utf-8-sig'))))

def fetch_master():
    return fetch_sheet_csv(os.getenv('MASTER_SHEET_ID','1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I'),os.getenv('MASTER_GID','837961277'),'MASTER_CSV_URL')
def fetch_sia():
    raw,rows=fetch_sheet_csv(os.getenv('SIA_SHEET_ID','1qWs8saSIlq1TLhcg-BLZ1dWHRojDQWRX2BlEpZTkDjE'),os.getenv('SIA_GID','0'),'SIA_CSV_URL')
    return raw,[{str(k).strip():v for k,v in r.items()} for r in rows]

def canonical_master(rows):
    out={}
    for r in rows:
        sku=norm(r.get('220_sku')); st=norm(r.get('220_status'))
        if not sku: continue
        if st=='ACTIVE_220' or sku in SPECIAL_CANONICAL_BLOCKED:
            if sku in out: raise RuntimeError(f'duplicate canonical Master SKU {sku}')
            out[sku]=r
    if len(out)!=EXPECTED_TOTAL: raise RuntimeError(f'Master canonical identities {len(out)}/{EXPECTED_TOTAL}')
    return out

def gql(ids):
    token=os.getenv('SHOPIFY_ACCESS_TOKEN'); shop=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com')
    if not token: raise RuntimeError('SHOPIFY_ACCESS_TOKEN missing')
    q='''query RuntimeInventory($ids:[ID!]!){nodes(ids:$ids){... on ProductVariant{id inventoryItem{id inventoryLevels(first:50){nodes{location{id name} quantities(names:["on_hand"]){name quantity updatedAt}} pageInfo{hasNextPage}}}}}}'''
    r=requests.post(f'https://{shop}/admin/api/2026-07/graphql.json',headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},json={'query':q,'variables':{'ids':ids}},timeout=60); r.raise_for_status(); j=r.json()
    if j.get('errors'): raise RuntimeError('Shopify GraphQL errors: '+json.dumps(j['errors'])[:1000])
    return j['data']['nodes']

def fetch_shopify(mapped):
    pairs=sorted(mapped.items()); gids=[g for _,g in pairs]
    if len(gids)!=EXPECTED_SHOPIFY or len(set(gids))!=EXPECTED_SHOPIFY: raise RuntimeError('locked Shopify GID count/uniqueness gate failed')
    out={}; snap=[]; unknown=set(); fetched_at=now()
    for i in range(0,len(gids),50):
        batch=gids[i:i+50]; nodes=gql(batch)
        if len(nodes)!=len(batch): raise RuntimeError(f'Shopify batch incomplete {i//50+1}')
        for gid,node in zip(batch,nodes):
            if not node or node.get('id')!=gid: raise RuntimeError(f'Shopify locked GID missing/mismatch {gid}')
            inv_item=(node.get('inventoryItem') or {}); inv_gid=inv_item.get('id',''); inv=inv_item.get('inventoryLevels') or {}
            if (inv.get('pageInfo') or {}).get('hasNextPage'): raise RuntimeError(f'inventoryLevels pagination incomplete {gid}')
            levels={STORE:None,LOWA:None,FJORD:None}; seen=set(); extras=[]
            for lv in inv.get('nodes') or []:
                loc=lv.get('location') or {}; lid=loc.get('id') or ''; name=loc.get('name') or ''
                qv=next((x for x in (lv.get('quantities') or []) if x.get('name')=='on_hand'),None); raw=None if qv is None else qv.get('quantity')
                if lid in levels:
                    levels[lid]=raw; seen.add(lid)
                else:
                    extras.append((lid,name,raw))
                    if raw not in (None,0): unknown.add(f'{lid}|{name}|{raw}')
            for lid in KNOWN_LOCATIONS:
                raw=levels[lid]
                snap.append({'variant_gid':gid,'inventory_item_gid':inv_gid,'location_gid':lid,'location_name':KNOWN_LOCATION_NAMES[lid],'raw_on_hand':'' if raw is None else raw,'source_fetch_status':'OK' if lid in seen else 'LOCATION_LEVEL_MISSING','fetched_at':fetched_at})
            for lid,name,raw in extras:
                snap.append({'variant_gid':gid,'inventory_item_gid':inv_gid,'location_gid':lid,'location_name':name,'raw_on_hand':'' if raw is None else raw,'source_fetch_status':'UNKNOWN_LOCATION','fetched_at':fetched_at})
            out[gid]=levels
    return out,snap,sorted(unknown)

def shopify_semantic_hash(snap):
    fields=['variant_gid','inventory_item_gid','location_gid','raw_on_hand']
    rows=sorted(snap,key=lambda r:tuple(norm(r.get(k)) for k in fields))
    return canon_hash(rows,fields)

def price_errors(m):
    e=[]; b=dec(m.get('220_price_before_discount')); a=dec(m.get('220_price_after_discount'))
    if b is None or b<=0:e.append('PRICE_BEFORE_INVALID')
    if norm(m.get('220_price_after_discount')):
        if a is None or a<=0:e.append('PRICE_AFTER_NONPOSITIVE')
        elif b is not None and a>b:e.append('PRICE_AFTER_GT_BEFORE')
    return e

def route_shop(levels,zero_hours):
    vals=[]
    for x in (levels.get(STORE),levels.get(LOWA),levels.get(FJORD)):
        if x is None: vals.append(0)
        elif isinstance(x,int): vals.append(max(0,x))
        else:return None,None,None,['SHOPIFY_ON_HAND_INVALID']
    store,lowa,fjord=vals
    if store>0:return 'STORE',max(store,3),'24',[]
    if lowa>0 and fjord>0:return None,None,None,['BLOCKED_LOCATION_CONFLICT_LOWA_FJORD']
    if lowa>0:return 'LOWA',max(lowa,3),'48',[]
    if fjord>0:return 'FJORD',max(fjord,3),'72',[]
    if not norm(zero_hours):return 'ZERO',0,None,['BLOCKED_ZERO_STOCK_COLLECTIONHOURS_UNDEFINED']
    return 'ZERO',0,norm(zero_hours),[]

def build_xml(ok):
    from xml.etree.ElementTree import Element,SubElement,tostring
    root=Element('products',source='MASTER_DRIVEN_STAGING',shopify_price_used='false',old_production_dependency='false')
    for r in ok:
        e=SubElement(root,'product')
        for k,v in [('gtin',r['220_ean']),('sku',r['220_sku']),('stock',r['calculated_stock']),('price_before_discount',r['220_price_before_discount']),('price_after_discount',r['220_price_after_discount']),('collectionhours',r['calculated_collectionhours'])]: SubElement(e,k).text=norm(v)
    return b'<?xml version="1.0" encoding="utf-8"?>\n'+tostring(root,encoding='utf-8')

def _ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS runtime_snapshots(refresh_id text PRIMARY KEY,created_at timestamptz NOT NULL,deployed_commit text NOT NULL,master_sha256 text NOT NULL,sia_sha256 text NOT NULL,shopify_semantic_sha256 text NOT NULL,shopify_raw_audit_sha256 text NOT NULL,runtime_dataset_sha256 text NOT NULL,xml_sha256 text NOT NULL,runtime_ok integer NOT NULL,blocked integer NOT NULL,xml_rows integer NOT NULL,manifest jsonb NOT NULL,audit jsonb NOT NULL,published boolean NOT NULL DEFAULT false)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS runtime_snapshot_artifacts(refresh_id text NOT NULL REFERENCES runtime_snapshots(refresh_id) ON DELETE CASCADE,name text NOT NULL,sha256 text NOT NULL,content bytea NOT NULL,PRIMARY KEY(refresh_id,name))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS runtime_current(singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),refresh_id text NOT NULL REFERENCES runtime_snapshots(refresh_id))''')

def _load_snapshot_from_db(refresh_id=None):
    with psycopg.connect(audit_database_url(),connect_timeout=10) as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            if refresh_id is None:
                cur.execute('SELECT refresh_id FROM runtime_current WHERE singleton=true'); row=cur.fetchone()
                if not row:return None
                refresh_id=row[0]
            cur.execute('SELECT manifest,audit,published FROM runtime_snapshots WHERE refresh_id=%s',(refresh_id,)); row=cur.fetchone()
            if not row:return None
            manifest,audit,published=row
            cur.execute('SELECT name,sha256,content FROM runtime_snapshot_artifacts WHERE refresh_id=%s ORDER BY name',(refresh_id,)); artifacts={}
            for name,expected,content in cur.fetchall():
                b=bytes(content)
                if sha(b)!=expected: raise RuntimeError(f'durable artifact hash mismatch {refresh_id}/{name}')
                artifacts[name]=b
    return {'refresh_id':refresh_id,'manifest':manifest,'audit':audit,'published':published,'artifacts':artifacts}

def _write_local_cache(snapshot):
    d=SNAP/('durable_'+snapshot['refresh_id']); d.mkdir(parents=True,exist_ok=True)
    for name,b in snapshot['artifacts'].items(): (d/name).write_bytes(b)
    return d

def recover_current_snapshot():
    snap=_load_snapshot_from_db(None)
    if not snap:return None
    if not snap['published']: raise RuntimeError('durable current points to unpublished snapshot')
    for name in ARTIFACT_NAMES:
        if name not in snap['artifacts']: raise RuntimeError(f'durable current missing artifact {name}')
    d=_write_local_cache(snap); audit=dict(snap['audit']); manifest=dict(snap['manifest'])
    with _state_lock:_state.update(service_status='ok',stale=False,last_successful_refresh=(audit.get('source_timestamps') or {}).get('refresh_completed_at'),error=None,validated_dir=str(d),audit=audit,manifest=manifest,persistence_status='verified',recovered_from_durable=True)
    return {'refresh_id':snap['refresh_id'],'dataset_sha256':manifest.get('runtime_dataset_sha256'),'shopify_semantic_sha256':manifest.get('shopify_semantic_sha256')}

def persist_snapshot(artifacts,audit,manifest):
    refresh_id=manifest['refresh_id']
    with psycopg.connect(audit_database_url(),connect_timeout=10) as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO runtime_snapshots(refresh_id,created_at,deployed_commit,master_sha256,sia_sha256,shopify_semantic_sha256,shopify_raw_audit_sha256,runtime_dataset_sha256,xml_sha256,runtime_ok,blocked,xml_rows,manifest,audit,published) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,false) ON CONFLICT(refresh_id) DO NOTHING''',(refresh_id,manifest['timestamp'],manifest['deployed_commit'],manifest['master_sha256'],manifest['sia_sha256'],manifest['shopify_semantic_sha256'],manifest['shopify_raw_audit_sha256'],manifest['runtime_dataset_sha256'],manifest['xml_sha256'],manifest['runtime_ok'],manifest['blocked'],manifest['xml_rows'],json.dumps(manifest),json.dumps(audit)))
            for name,b in artifacts.items(): cur.execute('''INSERT INTO runtime_snapshot_artifacts(refresh_id,name,sha256,content) VALUES(%s,%s,%s,%s) ON CONFLICT(refresh_id,name) DO NOTHING''',(refresh_id,name,sha(b),b))
        conn.commit()
    rb=_load_snapshot_from_db(refresh_id)
    if not rb: raise RuntimeError('durable snapshot read-back missing')
    if dict(rb['manifest'])!=manifest: raise RuntimeError('durable manifest read-back mismatch')
    if set(rb['artifacts'])!=set(artifacts): raise RuntimeError('durable artifact set read-back mismatch')
    for name,b in artifacts.items():
        if rb['artifacts'][name]!=b: raise RuntimeError(f'durable artifact read-back mismatch {name}')
    with psycopg.connect(audit_database_url(),connect_timeout=10) as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('UPDATE runtime_snapshots SET published=true WHERE refresh_id=%s',(refresh_id,))
            cur.execute('''INSERT INTO runtime_current(singleton,refresh_id) VALUES(true,%s) ON CONFLICT(singleton) DO UPDATE SET refresh_id=EXCLUDED.refresh_id''',(refresh_id,))
            cur.execute('SELECT refresh_id FROM runtime_current WHERE singleton=true'); current=cur.fetchone()
            if not current or current[0]!=refresh_id: raise RuntimeError('durable current pointer verification failed')
        conn.commit()
    rb['published']=True; return rb

def refresh():
    started=now(); refresh_id=started.replace(':','').replace('+','_')
    with _state_lock:_state.update(last_attempt=started,service_status='refreshing',error=None,persistence_status='pending')
    try:
        master_raw,mrows=fetch_master(); master=canonical_master(mrows); skus=sorted(master); mapped=load_locked_mapped(skus); external=[s for s in skus if s not in mapped]
        if len(external)!=EXPECTED_EXTERNAL: raise RuntimeError(f'external source assignment {len(external)}/{EXPECTED_EXTERNAL}')
        sia_raw,srows=fetch_sia(); sia={norm(r.get('sku')):r for r in srows if norm(r.get('sku'))}; missing_external=[s for s in external if s not in sia]
        if missing_external: raise RuntimeError(f'SIA external incomplete {len(missing_external)}')
        shop,snap,unknown=fetch_shopify(mapped); rows=[]
        for sku in skus:
            m=master[sku]; sr=sia.get(sku,{}) ; errors=[]
            if sku in EXPLICIT_BLOCKERS: errors.append('EXPLICIT_CURRENT_BLOCKER')
            st=norm(m.get('220_status'))
            if st!='ACTIVE_220': errors.append('LIFECYCLE_'+(st or 'MISSING'))
            errors+=price_errors(m); selected=''; stock=''; hours=''; rs=rl=rf=re=''; zero_hours=norm(sr.get('collectionhours'))
            if sku in mapped:
                gid=mapped[sku]; lv=shop.get(gid)
                if lv is None: errors.append('SHOPIFY_SNAPSHOT_ROW_MISSING')
                else: rs,rl,rf=lv.get(STORE),lv.get(LOWA),lv.get(FJORD); selected,stock,hours,ee=route_shop(lv,zero_hours); errors+=ee
                typ='SHOPIFY_MAPPED'; key=gid
            else:
                typ='LEGACY_EXTERNAL_SOURCE'; key='SIA_FHM:Sheet1:sku='+sku; re=norm(sr.get('stock')); iv=exact_int(re)
                if iv is None: errors.append('EXTERNAL_STOCK_VALUE_MISSING_OR_INVALID')
                else: stock=iv
                hours=zero_hours; selected='SIA_FHM'
                if not hours: errors.append('EXTERNAL_COLLECTIONHOURS_MISSING')
            rows.append({'220_sku':sku,'220_ean':norm(m.get('220_ean')),'inventory_source_type':typ,'inventory_source_key':key,'raw_store_on_hand':'' if rs is None else rs,'raw_lowa_on_hand':'' if rl is None else rl,'raw_fjord_on_hand':'' if rf is None else rf,'raw_external_stock':re,'selected_source_location':selected,'calculated_stock':stock,'calculated_collectionhours':hours,'220_price_before_discount':norm(m.get('220_price_before_discount')),'220_price_after_discount':norm(m.get('220_price_after_discount')),'lifecycle_status':st,'runtime_validation_status':'OK' if not errors else 'BLOCKED','blocker_reason':'|'.join(errors),'shopify_price_used':'false'})
        fields=list(rows[0]); ok=[r for r in rows if r['runtime_validation_status']=='OK']; blocked=[r for r in rows if r['runtime_validation_status']=='BLOCKED']; dataset=csv_bytes(rows,fields); xml=build_xml(ok)
        by_gid={r['inventory_source_key']:r for r in rows if r['inventory_source_type']=='SHOPIFY_MAPPED'}
        for x in snap:
            rr=by_gid.get(x['variant_gid'],{}); x['resulting_selected_stock']=rr.get('calculated_stock',''); x['collectionhours']=rr.get('calculated_collectionhours',''); x['blocker_reason']=rr.get('blocker_reason','')
        sf=list(snap[0]) if snap else ['variant_gid']; shopb=csv_bytes(snap,sf); semantic_sha=shopify_semantic_hash(snap); diffs=[]
        for r in rows:
            sr=sia.get(r['220_sku'],{}); bs=norm(sr.get('stock')); bh=norm(sr.get('collectionhours')); bp=norm(sr.get('(1) price before discount')); ap=norm(sr.get('(2) price after discount')); pd=(norm(r['220_price_before_discount'])!=bp or norm(r['220_price_after_discount'])!=ap)
            diffs.append({'220_sku':r['220_sku'],'stock_diff':str(norm(r['calculated_stock'])!=bs).lower(),'hours_diff':str(norm(r['calculated_collectionhours'])!=bh).lower(),'price_diff':str(pd).lower(),'current_stock':r['calculated_stock'],'baseline_stock':bs,'current_hours':r['calculated_collectionhours'],'baseline_hours':bh,'current_price_before':r['220_price_before_discount'],'baseline_price_before':bp,'current_price_after':r['220_price_after_discount'],'baseline_price_after':ap,'blocker_reason':r['blocker_reason']})
        diffb=csv_bytes(diffs,list(diffs[0])); dh=canon_hash(rows,fields); blockedb=csv_bytes(blocked,fields); completed=now()
        source_ids={'master_sha256':sha(master_raw),'sia_sha256':sha(sia_raw),'shopify_semantic_sha256':semantic_sha,'shopify_raw_audit_sha256':sha(shopb)}
        audit={'service_status':'ok','stale':False,'refresh_id':refresh_id,'source_timestamps':{'refresh_started_at':started,'refresh_completed_at':completed},'source_snapshot_ids':source_ids,'identity_coverage':'1671/1671','source_coverage':'1671/1671','master_identities':1671,'shopify_mapped_expected':970,'shopify_fetched':len(shop),'sia_external_expected':701,'sia_external_fetched':701-len(missing_external),'missing_shopify_gid_count':970-len(shop),'duplicate_shopify_gid_count':970-len(set(mapped.values())),'unknown_shopify_locations':unknown,'location_conflicts':sum('BLOCKED_LOCATION_CONFLICT_LOWA_FJORD' in r['blocker_reason'] for r in rows),'invalid_external_stock':sum('EXTERNAL_STOCK_VALUE_MISSING_OR_INVALID' in r['blocker_reason'] for r in rows),'zero_stock_hours_blockers':sum('BLOCKED_ZERO_STOCK_COLLECTIONHOURS_UNDEFINED' in r['blocker_reason'] for r in rows),'lifecycle_blockers':sum('LIFECYCLE_' in r['blocker_reason'] for r in rows),'price_blockers':sum('PRICE_' in r['blocker_reason'] for r in rows),'runtime_ok':len(ok),'blocked':len(blocked),'generated_xml_rows':len(ok),'dataset_sha256':dh,'xml_sha256':sha(xml),'shopify_price_used':False,'old_production_xml_dependency':False,'silent_fallback':False,'stock_diffs':sum(d['stock_diff']=='true' for d in diffs),'hours_diffs':sum(d['hours_diff']=='true' for d in diffs),'price_diffs':sum(d['price_diff']=='true' for d in diffs),'explicit_excluded_skus':sorted(EXPLICIT_BLOCKERS),'durable_persistence_required':True,'durable_persistence_verified':True,'deployed_commit':deployed_commit()}
        manifest={'refresh_id':refresh_id,'deployed_commit':deployed_commit(),'master_sha256':source_ids['master_sha256'],'sia_sha256':source_ids['sia_sha256'],'shopify_semantic_sha256':semantic_sha,'shopify_raw_audit_sha256':source_ids['shopify_raw_audit_sha256'],'runtime_dataset_sha256':dh,'xml_sha256':sha(xml),'runtime_ok':len(ok),'blocked':len(blocked),'xml_rows':len(ok),'timestamp':completed}
        auditb=json.dumps(audit,indent=2,sort_keys=True).encode(); manifestb=json.dumps(manifest,indent=2,sort_keys=True).encode(); artifacts={'audit.json':auditb,'snapshot-manifest.json':manifestb,'runtime-dataset.csv':dataset,'blockers.csv':blockedb,'shopify_inventory_snapshot.csv':shopb,'production-equivalent-comparison.csv':diffb,'220-stock-master-driven.xml':xml}
        durable=persist_snapshot(artifacts,audit,manifest); d=_write_local_cache(durable)
        with _state_lock:_state.update(service_status='ok',stale=False,last_successful_refresh=completed,error=None,validated_dir=str(d),audit=audit,manifest=manifest,persistence_status='verified',recovered_from_durable=False)
        return audit
    except Exception as ex:
        with _state_lock: prev=_state.get('validated_dir')
        if not prev:
            try: recover_current_snapshot()
            except Exception: pass
        with _state_lock:_state.update(service_status='degraded',stale=bool(_state.get('validated_dir')),error=f'{type(ex).__name__}: {ex}',persistence_status='failed')
        raise

def current_state():
    with _state_lock:return json.loads(json.dumps(_state))
def current_file(name):
    d=current_state().get('validated_dir')
    if not d:return None
    p=Path(d)/name; return p if p.exists() else None
