from __future__ import annotations
import base64, csv, gzip, hashlib, io, json, os, threading, time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'config'
SNAP=Path(os.getenv('SNAPSHOT_DIR', str(ROOT/'runtime_snapshots')))
SNAP.mkdir(parents=True, exist_ok=True)
LOCKED_MAP=CONFIG/'inventory_source_map.csv'
LOCKED_MAP_B64=CONFIG/'inventory_source_map.csv.gz.b64'
BASELINE=CONFIG/'production_equivalent_baseline.csv'
BASELINE_B64=CONFIG/'production_equivalent_baseline.csv.gz.b64'
EXPLICIT_BLOCKERS={'000055-0004-OneSize','000055-0008-OneSize'}
EXPECTED_TOTAL=1671; EXPECTED_SHOPIFY=970; EXPECTED_EXTERNAL=701
STORE='gid://shopify/Location/84891861330'
LOWA='gid://shopify/Location/107817075026'
FJORD='gid://shopify/Location/107805770066'
KNOWN_LOCATIONS={STORE:'STORE',LOWA:'LOWA',FJORD:'FJORD'}

_state_lock=threading.RLock()
_state={'service_status':'starting','stale':False,'last_attempt':None,'last_successful_refresh':None,'error':None,'validated_dir':None,'audit':{}}

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

def sha256_bytes(b:bytes): return hashlib.sha256(b).hexdigest()
def canonical_hash(rows,fields):
    b='\n'.join('|'.join(norm(r.get(k)) for k in fields) for r in rows).encode()
    return sha256_bytes(b)

def read_csv(path, b64_path=None):
    if Path(path).exists():
        with open(path,encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
    if b64_path and Path(b64_path).exists():
        raw=gzip.decompress(base64.b64decode(Path(b64_path).read_bytes())).decode('utf-8')
        return list(csv.DictReader(io.StringIO(raw)))
    raise FileNotFoundError(path)

def csv_bytes(rows,fields):
    s=io.StringIO(newline=''); w=csv.DictWriter(s,fieldnames=fields); w.writeheader(); w.writerows(rows); return s.getvalue().encode()

def fetch_sheet_csv(sheet_id,gid):
    direct=os.getenv('MASTER_CSV_URL' if sheet_id==os.getenv('MASTER_SHEET_ID','1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I') else 'SIA_CSV_URL')
    url=direct or f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}'
    sa=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if sa:
        info=json.loads(sa)
        creds=service_account.Credentials.from_service_account_info(info,scopes=['https://www.googleapis.com/auth/drive.readonly','https://www.googleapis.com/auth/spreadsheets.readonly'])
        sess=AuthorizedSession(creds); r=sess.get(url,timeout=45)
    else:
        r=requests.get(url,timeout=45,allow_redirects=True)
    r.raise_for_status()
    if 'text/html' in r.headers.get('content-type','') and b'<html' in r.content[:500].lower(): raise RuntimeError('Google sheet fetch returned HTML/login page')
    return r.content, list(csv.DictReader(io.StringIO(r.content.decode('utf-8-sig'))))

def fetch_master():
    sid=os.getenv('MASTER_SHEET_ID','1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I'); gid=os.getenv('MASTER_GID','837961277')
    raw,rows=fetch_sheet_csv(sid,gid)
    active={norm(r.get('220_sku')):r for r in rows if norm(r.get('220_sku'))}
    return raw,active

def fetch_sia():
    sid=os.getenv('SIA_SHEET_ID','1qWs8saSIlq1TLhcg-BLZ1dWHRojDQWRX2BlEpZTkDjE'); gid=os.getenv('SIA_GID','0')
    raw,rows=fetch_sheet_csv(sid,gid)
    by={norm(r.get('sku')):r for r in rows if norm(r.get('sku'))}
    return raw,by

def gql(ids):
    shop=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com'); token=os.getenv('SHOPIFY_ACCESS_TOKEN')
    if not token: raise RuntimeError('SHOPIFY_ACCESS_TOKEN missing')
    q='''query RuntimeInventory($ids:[ID!]!){nodes(ids:$ids){... on ProductVariant{id inventoryItem{id inventoryLevels(first:50){nodes{location{id name} quantities(names:["on_hand"]){name quantity updatedAt}} pageInfo{hasNextPage}}}}}}'''
    r=requests.post(f'https://{shop}/admin/api/2026-07/graphql.json',headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},json={'query':q,'variables':{'ids':ids}},timeout=60)
    r.raise_for_status(); j=r.json()
    if j.get('errors'): raise RuntimeError('Shopify GraphQL errors: '+json.dumps(j['errors'])[:1000])
    return j['data']['nodes']

def fetch_shopify(source_rows):
    gids=[r['inventory_source_key'] for r in source_rows if r['inventory_source_type']=='SHOPIFY_MAPPED']
    if len(gids)!=EXPECTED_SHOPIFY or len(set(gids))!=EXPECTED_SHOPIFY: raise RuntimeError(f'locked Shopify GID gate failed: {len(gids)}/{len(set(gids))}')
    out={}; unknown=set(); snapshot=[]
    for i in range(0,len(gids),50):
        batch=gids[i:i+50]
        nodes=gql(batch)
        if len(nodes)!=len(batch): raise RuntimeError(f'Shopify batch incomplete {i//50+1}: {len(nodes)}/{len(batch)}')
        for gid,node in zip(batch,nodes):
            if not node or node.get('id')!=gid: raise RuntimeError(f'Shopify locked GID missing/mismatch: {gid}')
            levels={STORE:None,LOWA:None,FJORD:None}
            inv=(node.get('inventoryItem') or {}).get('inventoryLevels') or {}
            if (inv.get('pageInfo') or {}).get('hasNextPage'): raise RuntimeError(f'inventoryLevels pagination not exhausted for {gid}')
            for lv in inv.get('nodes') or []:
                loc=lv.get('location') or {}; lid=loc.get('id'); name=loc.get('name')
                qs=lv.get('quantities') or []; qv=next((x for x in qs if x.get('name')=='on_hand'),None)
                raw=None if qv is None else qv.get('quantity')
                if lid in levels: levels[lid]=raw
                elif raw not in (None,0): unknown.add(f'{lid}|{name}|{raw}')
                snapshot.append({'variant_gid':gid,'inventory_item_gid':(node.get('inventoryItem') or {}).get('id',''),'location_gid':lid or '','location_name':name or '','raw_on_hand':'' if raw is None else raw,'source_fetch_status':'OK','fetched_at':now()})
            out[gid]=levels
    return out,snapshot,sorted(unknown)

def price_errors(m):
    e=[]; b=dec(m.get('220_price_before_discount')); a=dec(m.get('220_price_after_discount'))
    if b is None or b<=0:e.append('PRICE_BEFORE_INVALID')
    if norm(m.get('220_price_after_discount')):
        if a is None or a<=0:e.append('PRICE_AFTER_NONPOSITIVE')
        elif b is not None and a>b:e.append('PRICE_AFTER_GT_BEFORE')
    return e

def route_shop(levels,zero_hours):
    raw=[levels.get(STORE),levels.get(LOWA),levels.get(FJORD)]
    vals=[]
    for x in raw:
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

def build_xml(okrows):
    from xml.etree.ElementTree import Element,SubElement,tostring
    root=Element('products',source='MASTER_DRIVEN_STAGING',shopify_price_used='false',old_production_dependency='false')
    for r in okrows:
        e=SubElement(root,'product')
        vals=[('gtin',r['220_ean']),('sku',r['220_sku']),('stock',r['calculated_stock']),('price_before_discount',r['220_price_before_discount']),('price_after_discount',r['220_price_after_discount']),('collectionhours',r['calculated_collectionhours'])]
        for k,v in vals: SubElement(e,k).text=norm(v)
    return b'<?xml version="1.0" encoding="utf-8"?>\n'+tostring(root,encoding='utf-8')

def refresh():
    attempt=now()
    with _state_lock: _state.update(last_attempt=attempt,service_status='refreshing',error=None)
    run=SNAP/attempt.replace(':','').replace('+','_'); run.mkdir(parents=True,exist_ok=True)
    try:
        source=read_csv(LOCKED_MAP,LOCKED_MAP_B64)
        if len(source)!=EXPECTED_TOTAL: raise RuntimeError(f'source map {len(source)}/{EXPECTED_TOTAL}')
        master_raw,master=fetch_master(); sia_raw,sia=fetch_sia()
        shop_source=[r for r in source if r['inventory_source_type']=='SHOPIFY_MAPPED']; ext_source=[r for r in source if r['inventory_source_type']=='LEGACY_EXTERNAL_SOURCE']
        if len(shop_source)!=EXPECTED_SHOPIFY or len(ext_source)!=EXPECTED_EXTERNAL: raise RuntimeError('source assignment gate failed')
        missing_master=[r['220_sku'] for r in source if r['220_sku'] not in master]
        if missing_master: raise RuntimeError(f'Master incomplete: {len(missing_master)} locked identities missing')
        missing_sia=[r['220_sku'] for r in ext_source if r['220_sku'] not in sia]
        if missing_sia: raise RuntimeError(f'SIA incomplete external identities: {len(missing_sia)}')
        shop,snaprows,unknown=fetch_shopify(source)
        rows=[]
        for s in source:
            sku=s['220_sku']; m=master[sku]; sr=sia.get(sku,{})
            e=[]
            if sku in EXPLICIT_BLOCKERS:e.append('EXPLICIT_CURRENT_BLOCKER')
            status=norm(m.get('220_status'))
            if status!='ACTIVE_220':e.append('LIFECYCLE_'+(status or 'MISSING'))
            if norm(m.get('220_ean'))!=norm(s['220_ean']):e.append('MASTER_IDENTITY_EAN_MISMATCH')
            e+=price_errors(m)
            selected=''; stock=''; hours=''; raw_store=raw_lowa=raw_fjord=raw_ext=''
            zero_hours=norm(sr.get('collectionhours'))
            if s['inventory_source_type']=='SHOPIFY_MAPPED':
                lv=shop.get(s['inventory_source_key'])
                if lv is None:e.append('SHOPIFY_SNAPSHOT_ROW_MISSING')
                else:
                    raw_store=lv.get(STORE); raw_lowa=lv.get(LOWA); raw_fjord=lv.get(FJORD)
                    selected,stock,hours,ee=route_shop(lv,zero_hours); e+=ee
            else:
                raw_ext=norm(sr.get('stock')); iv=exact_int(raw_ext)
                if iv is None:e.append('EXTERNAL_STOCK_VALUE_MISSING_OR_INVALID')
                else: stock=iv
                hours=zero_hours; selected='SIA_FHM'
                if not hours:e.append('EXTERNAL_COLLECTIONHOURS_MISSING')
            rows.append({'220_sku':sku,'220_ean':norm(m.get('220_ean')),'inventory_source_type':s['inventory_source_type'],'inventory_source_key':s['inventory_source_key'],'raw_store_on_hand':'' if raw_store is None else raw_store,'raw_lowa_on_hand':'' if raw_lowa is None else raw_lowa,'raw_fjord_on_hand':'' if raw_fjord is None else raw_fjord,'raw_external_stock':raw_ext,'selected_source_location':selected,'calculated_stock':stock,'calculated_collectionhours':hours,'220_price_before_discount':norm(m.get('220_price_before_discount')),'220_price_after_discount':norm(m.get('220_price_after_discount')),'lifecycle_status':status,'runtime_validation_status':'OK' if not e else 'BLOCKED','blocker_reason':'|'.join(e),'shopify_price_used':'false'})
        fields=list(rows[0]); ok=[r for r in rows if r['runtime_validation_status']=='OK']; blockers=[r for r in rows if r['runtime_validation_status']=='BLOCKED']
        dataset=csv_bytes(rows,fields); blocker_b=csv_bytes(blockers,fields); xml=build_xml(ok)
        dh=canonical_hash(rows,fields)
        by_gid={r['inventory_source_key']:r for r in rows if r['inventory_source_type']=='SHOPIFY_MAPPED'}
        for x in snaprows:
            rr=by_gid.get(x['variant_gid'],{}); x['resulting_selected_stock']=rr.get('calculated_stock',''); x['collectionhours']=rr.get('calculated_collectionhours',''); x['blocker_reason']=rr.get('blocker_reason','')
        snap_fields=list(snaprows[0]) if snaprows else ['variant_gid']
        (run/'shopify_inventory_snapshot.csv').write_bytes(csv_bytes(snaprows,snap_fields))
        (run/'master.csv').write_bytes(master_raw); (run/'sia.csv').write_bytes(sia_raw); (run/'runtime-dataset.csv').write_bytes(dataset); (run/'blockers.csv').write_bytes(blocker_b); (run/'220-stock-master-driven.xml').write_bytes(xml)
        baseline={r['220_sku']:r for r in read_csv(BASELINE,BASELINE_B64)}
        diffs=[]
        for r in rows:
            b=baseline.get(r['220_sku'],{}); bs=norm(b.get('baseline_stock')); bh=norm(b.get('baseline_collectionhours'))
            diffs.append({'220_sku':r['220_sku'],'stock_diff':str(norm(r['calculated_stock'])!=bs).lower(),'hours_diff':str(norm(r['calculated_collectionhours'])!=bh).lower(),'price_diff':'false','current_stock':r['calculated_stock'],'baseline_stock':bs,'current_hours':r['calculated_collectionhours'],'baseline_hours':bh,'blocker_reason':r['blocker_reason']})
        (run/'production-equivalent-comparison.csv').write_bytes(csv_bytes(diffs,list(diffs[0])))
        audit={'service_status':'ok','stale':False,'refresh_id':run.name,'source_timestamps':{'refresh_started_at':attempt,'refresh_completed_at':now()},'source_snapshot_ids':{'master_sha256':sha256_bytes(master_raw),'sia_sha256':sha256_bytes(sia_raw),'shopify_snapshot_sha256':sha256_bytes((run/'shopify_inventory_snapshot.csv').read_bytes())},'identity_coverage':f'{len(rows)}/{EXPECTED_TOTAL}','source_coverage':f'{len(shop_source)+len(ext_source)}/{EXPECTED_TOTAL}','master_identities':len(rows),'shopify_mapped_expected':EXPECTED_SHOPIFY,'shopify_fetched':len(shop),'sia_external_expected':EXPECTED_EXTERNAL,'sia_external_fetched':len(ext_source)-len(missing_sia),'missing_shopify_gid_count':EXPECTED_SHOPIFY-len(shop),'duplicate_shopify_gid_count':len(shop_source)-len({r["inventory_source_key"] for r in shop_source}),'unknown_shopify_locations':unknown,'location_conflicts':sum('BLOCKED_LOCATION_CONFLICT_LOWA_FJORD' in r['blocker_reason'] for r in rows),'invalid_external_stock':sum('EXTERNAL_STOCK_VALUE_MISSING_OR_INVALID' in r['blocker_reason'] for r in rows),'zero_stock_hours_blockers':sum('BLOCKED_ZERO_STOCK_COLLECTIONHOURS_UNDEFINED' in r['blocker_reason'] for r in rows),'lifecycle_blockers':sum('LIFECYCLE_' in r['blocker_reason'] for r in rows),'price_blockers':sum('PRICE_' in r['blocker_reason'] for r in rows),'runtime_ok':len(ok),'blocked':len(blockers),'generated_xml_rows':len(ok),'dataset_sha256':dh,'shopify_price_used':False,'old_production_xml_dependency':False,'silent_fallback':False,'stock_diffs':sum(d['stock_diff']=='true' for d in diffs),'hours_diffs':sum(d['hours_diff']=='true' for d in diffs),'price_diffs':0,'explicit_excluded_skus':sorted(EXPLICIT_BLOCKERS)}
        (run/'audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
        if len(rows)!=EXPECTED_TOTAL or len(shop)!=EXPECTED_SHOPIFY or len(ext_source)!=EXPECTED_EXTERNAL: raise RuntimeError('validation gate failed after build')
        current=SNAP/'CURRENT'; current.write_text(str(run),encoding='utf-8')
        with _state_lock:_state.update(service_status='ok',stale=False,last_successful_refresh=audit['source_timestamps']['refresh_completed_at'],error=None,validated_dir=str(run),audit=audit)
        return audit
    except Exception as ex:
        with _state_lock:
            prev=_state.get('validated_dir')
            if not prev and (SNAP/'CURRENT').exists(): prev=(SNAP/'CURRENT').read_text(encoding='utf-8').strip()
            _state.update(service_status='degraded',stale=bool(prev),error=f'{type(ex).__name__}: {ex}',validated_dir=prev)
        raise

def current_state():
    with _state_lock:return json.loads(json.dumps(_state))

def current_file(name):
    st=current_state(); d=st.get('validated_dir')
    if not d:return None
    p=Path(d)/name
    return p if p.exists() else None
