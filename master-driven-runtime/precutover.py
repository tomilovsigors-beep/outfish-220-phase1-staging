from __future__ import annotations
import csv, hashlib, io, json, os, zipfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import xml.etree.ElementTree as ET
import psycopg, requests

V13_URL=os.getenv('V13_CANDIDATE_XML_URL','https://outfish-220-stock-feed-v13-candidate.onrender.com/220-stock-merged.xml')
V13_EXCLUSIONS={
    'B004OUBJES':'PHASE1_SKIP_NOT_PRESENT_IN_LIVE_SHOPIFY',
    'B004OU7XOI':'PHASE1_SKIP_NOT_PRESENT_IN_LIVE_SHOPIFY',
}

def now(): return datetime.now(timezone.utc).isoformat()
def norm(v): return '' if v is None else str(v).strip()
def sha(b): return hashlib.sha256(b).hexdigest()
def dburl():
    v=os.getenv('AUDIT_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not v: raise RuntimeError('AUDIT_DATABASE_URL missing')
    return v

def _csv_bytes(rows,fields):
    s=io.StringIO(newline=''); w=csv.DictWriter(s,fieldnames=fields); w.writeheader(); w.writerows(rows); return s.getvalue().encode()

def _canon_num(v):
    s=norm(v)
    if not s:return ''
    try:
        d=Decimal(s)
        out=format(d.normalize(),'f')
        if '.' in out: out=out.rstrip('0').rstrip('.')
        return out or '0'
    except (InvalidOperation,ValueError): return s

def _first_text(node,names):
    for n in names:
        x=node.find(n)
        if x is not None:return norm(x.text)
    return ''

def parse_xml(raw):
    root=ET.fromstring(raw); out={}; dup=[]
    for p in root.findall('.//product'):
        sku=_first_text(p,['sku'])
        if not sku: continue
        if sku in out: dup.append(sku)
        out[sku]={
            'sku':sku,
            'ean':_first_text(p,['ean','gtin']),
            'stock':_canon_num(_first_text(p,['stock'])),
            'price_before':_canon_num(_first_text(p,['price-before-discount','price_before_discount'])),
            'price_after':_canon_num(_first_text(p,['price-after-discount','price_after_discount'])),
            'collectionhours':_canon_num(_first_text(p,['collectionhours'])),
        }
    if dup: raise RuntimeError(f'duplicate XML SKU count={len(dup)} sample={dup[:5]}')
    return out

def _read_current_runtime_artifacts():
    with psycopg.connect(dburl(),connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT refresh_id FROM runtime_current WHERE singleton=true'); row=cur.fetchone()
            if not row: raise RuntimeError('runtime_current missing')
            rid=row[0]
            cur.execute('SELECT manifest FROM runtime_snapshots WHERE refresh_id=%s AND published=true',(rid,)); row=cur.fetchone()
            if not row: raise RuntimeError('current runtime snapshot not published')
            manifest=dict(row[0])
            cur.execute("SELECT name,sha256,content FROM runtime_snapshot_artifacts WHERE refresh_id=%s AND name IN ('runtime-dataset.csv','blockers.csv','220-stock-master-driven.xml')",(rid,))
            arts={}
            for name,h,content in cur.fetchall():
                b=bytes(content)
                if sha(b)!=h: raise RuntimeError(f'current runtime artifact hash mismatch {name}')
                arts[name]=b
    need={'runtime-dataset.csv','blockers.csv','220-stock-master-driven.xml'}
    if set(arts)!=need: raise RuntimeError(f'current runtime artifact set incomplete: {sorted(set(need)-set(arts))}')
    return rid,manifest,arts

def _csv_rows(raw,key):
    rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))); out={}
    for r in rows:
        k=norm(r.get(key))
        if k: out[k]=r
    return out

def _ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS precutover_packages(
            package_id text PRIMARY KEY, created_at timestamptz NOT NULL,
            staging_refresh_id text NOT NULL, staging_dataset_sha256 text NOT NULL,
            v13_xml_sha256 text NOT NULL, summary jsonb NOT NULL,
            zip_sha256 text NOT NULL, zip_content bytea NOT NULL, published boolean NOT NULL DEFAULT false)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS precutover_current(
            singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
            package_id text NOT NULL REFERENCES precutover_packages(package_id))''')

def generate_and_persist_package():
    rid,manifest,arts=_read_current_runtime_artifacts()
    r=requests.get(V13_URL,headers={'User-Agent':'outfish-220-master-driven-staging-precutover/1.0'},timeout=120)
    if not r.ok: raise RuntimeError(f'V13 candidate fetch HTTP {r.status_code}')
    v13_raw=r.content
    if b'<html' in v13_raw[:500].lower(): raise RuntimeError('V13 candidate returned HTML')
    staging_xml=arts['220-stock-master-driven.xml']
    staging=parse_xml(staging_xml); v13=parse_xml(v13_raw)
    runtime=_csv_rows(arts['runtime-dataset.csv'],'220_sku'); blockers=_csv_rows(arts['blockers.csv'],'220_sku')

    universe=[]
    for sku in sorted(set(runtime)|set(staging)|set(v13)):
        rr=runtime.get(sku,{})
        universe.append({
            'sku':sku,
            'in_runtime_dataset':str(sku in runtime).lower(),
            'in_staging_xml':str(sku in staging).lower(),
            'in_v13_xml':str(sku in v13).lower(),
            'runtime_validation_status':rr.get('runtime_validation_status',''),
            'blocker_reason':rr.get('blocker_reason',''),
        })

    field_rows=[]; counts={'ean':0,'price_before':0,'price_after':0,'stock':0,'collectionhours':0}
    for sku in sorted(set(staging)&set(v13)):
        a=staging[sku]; b=v13[sku]
        flags={k:a[k]!=b[k] for k in counts}
        if any(flags.values()):
            for k,v in flags.items(): counts[k]+=int(v)
            field_rows.append({
                'sku':sku,
                'staging_ean':a['ean'],'v13_ean':b['ean'],'ean_diff':str(flags['ean']).lower(),
                'staging_price_before':a['price_before'],'v13_price_before':b['price_before'],'price_before_diff':str(flags['price_before']).lower(),
                'staging_price_after':a['price_after'],'v13_price_after':b['price_after'],'price_after_diff':str(flags['price_after']).lower(),
                'staging_stock':a['stock'],'v13_stock':b['stock'],'stock_diff':str(flags['stock']).lower(),
                'staging_collectionhours':a['collectionhours'],'v13_collectionhours':b['collectionhours'],'collectionhours_diff':str(flags['collectionhours']).lower(),
            })

    be=[]
    for sku in sorted(set(blockers)|set(V13_EXCLUSIONS)):
        br=blockers.get(sku,{})
        be.append({'sku':sku,'staging_blocked':str(sku in blockers).lower(),'staging_blocker_reason':br.get('blocker_reason',''),'v13_approved_exclusion':str(sku in V13_EXCLUSIONS).lower(),'v13_exclusion_reason':V13_EXCLUSIONS.get(sku,'')})

    summary={
        'created_at':now(),
        'staging_refresh_id':rid,
        'staging_dataset_sha256':manifest.get('runtime_dataset_sha256'),
        'staging_xml_sha256':sha(staging_xml),
        'v13_url':V13_URL,
        'v13_xml_sha256':sha(v13_raw),
        'runtime_dataset_skus':len(runtime),
        'staging_xml_skus':len(staging),
        'v13_xml_skus':len(v13),
        'runtime_missing_in_v13':sorted(set(runtime)-set(v13)),
        'v13_missing_in_runtime':sorted(set(v13)-set(runtime)),
        'staging_xml_missing_in_v13':sorted(set(staging)-set(v13)),
        'v13_missing_in_staging_xml':sorted(set(v13)-set(staging)),
        'overlap_published_rows':len(set(staging)&set(v13)),
        'ean_diff_count':counts['ean'],
        'price_before_diff_count':counts['price_before'],
        'price_after_diff_count':counts['price_after'],
        'stock_diff_count':counts['stock'],
        'collectionhours_diff_count':counts['collectionhours'],
        'staging_blockers':len(blockers),
        'v13_approved_exclusions':len(V13_EXCLUSIONS),
        'cutover_performed':False,
    }
    summary_b=json.dumps(summary,indent=2,sort_keys=True).encode()
    universe_b=_csv_bytes(universe,['sku','in_runtime_dataset','in_staging_xml','in_v13_xml','runtime_validation_status','blocker_reason'])
    diff_fields=['sku','staging_ean','v13_ean','ean_diff','staging_price_before','v13_price_before','price_before_diff','staging_price_after','v13_price_after','price_after_diff','staging_stock','v13_stock','stock_diff','staging_collectionhours','v13_collectionhours','collectionhours_diff']
    diffs_b=_csv_bytes(field_rows,diff_fields)
    be_b=_csv_bytes(be,['sku','staging_blocked','staging_blocker_reason','v13_approved_exclusion','v13_exclusion_reason'])
    bio=io.BytesIO()
    with zipfile.ZipFile(bio,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('summary.json',summary_b)
        z.writestr('master-driven-staging.xml',staging_xml)
        z.writestr('v13-candidate-current.xml',v13_raw)
        z.writestr('sku-universe-comparison.csv',universe_b)
        z.writestr('field-diffs.csv',diffs_b)
        z.writestr('blocker-exclusion-diff.csv',be_b)
        z.writestr('staging-blockers.csv',arts['blockers.csv'])
    zip_b=bio.getvalue(); package_id=summary['created_at'].replace(':','').replace('+','_')
    with psycopg.connect(dburl(),connect_timeout=10) as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO precutover_packages(package_id,created_at,staging_refresh_id,staging_dataset_sha256,v13_xml_sha256,summary,zip_sha256,zip_content,published)
                           VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,false)''',(package_id,summary['created_at'],rid,summary['staging_dataset_sha256'],summary['v13_xml_sha256'],json.dumps(summary),sha(zip_b),zip_b))
        conn.commit()
    with psycopg.connect(dburl(),connect_timeout=10) as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT summary,zip_sha256,zip_content FROM precutover_packages WHERE package_id=%s',(package_id,)); row=cur.fetchone()
            if not row: raise RuntimeError('precutover package read-back missing')
            rb_summary,rb_sha,rb_zip=row; rb_zip=bytes(rb_zip)
            if dict(rb_summary)!=summary or rb_sha!=sha(zip_b) or rb_zip!=zip_b: raise RuntimeError('precutover package read-back mismatch')
            cur.execute('UPDATE precutover_packages SET published=true WHERE package_id=%s',(package_id,))
            cur.execute('''INSERT INTO precutover_current(singleton,package_id) VALUES(true,%s)
                           ON CONFLICT(singleton) DO UPDATE SET package_id=EXCLUDED.package_id''',(package_id,))
        conn.commit()
    return {'package_id':package_id,'zip_sha256':sha(zip_b),**summary}

def load_current_package():
    with psycopg.connect(dburl(),connect_timeout=10) as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT p.package_id,p.summary,p.zip_sha256,p.zip_content FROM precutover_current c JOIN precutover_packages p ON p.package_id=c.package_id WHERE c.singleton=true AND p.published=true''')
            row=cur.fetchone()
            if not row:return None
            pid,summary,zsha,z=row; z=bytes(z)
            if sha(z)!=zsha: raise RuntimeError('current precutover package hash mismatch')
            return {'package_id':pid,'summary':dict(summary),'zip_sha256':zsha,'zip_content':z}
