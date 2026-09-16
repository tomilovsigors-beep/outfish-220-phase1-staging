from __future__ import annotations
import csv, hashlib, io, json, os, subprocess, sys, tempfile, time, zipfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import xml.etree.ElementTree as ET
import psycopg, requests

V13_URL=os.getenv('V13_CANDIDATE_XML_URL','https://outfish-220-stock-feed-v13-candidate.onrender.com/220-stock-merged.xml')
V13_HEALTH_URL=os.getenv('V13_CANDIDATE_HEALTH_URL','https://outfish-220-stock-feed-v13-candidate.onrender.com/health')
PRODUCTION_URL=os.getenv('PRODUCTION_FEED_URL','https://outfish-220-stock-feed.onrender.com/220-stock.xml')
FROZEN_V13_COMMIT='59b4cbe191c1260bc61147f35d526d82c2f956d0'
ROOT=Path(__file__).resolve().parent
REPO=ROOT.parent
V13_DIR=REPO/'outfish-220-phase1-live-generator-v13-independent-220-pricing'
V13_EXCLUSIONS={'B004OUBJES':'PHASE1_SKIP_NOT_PRESENT_IN_LIVE_SHOPIFY','B004OU7XOI':'PHASE1_SKIP_NOT_PRESENT_IN_LIVE_SHOPIFY'}

def now(): return datetime.now(timezone.utc).isoformat()
def norm(v): return '' if v is None else str(v).strip()
def sha(b): return hashlib.sha256(b).hexdigest()
def file_sha(p): return sha(Path(p).read_bytes())
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
        d=Decimal(s); out=format(d.normalize(),'f')
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
        out[sku]={'sku':sku,'ean':_first_text(p,['ean','gtin']),'stock':_canon_num(_first_text(p,['stock'])),'price_before':_canon_num(_first_text(p,['price-before-discount','price_before_discount'])),'price_after':_canon_num(_first_text(p,['price-after-discount','price_after_discount'])),'collectionhours':_canon_num(_first_text(p,['collectionhours']))}
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
        cur.execute('''CREATE TABLE IF NOT EXISTS precutover_packages(package_id text PRIMARY KEY,created_at timestamptz NOT NULL,staging_refresh_id text NOT NULL,staging_dataset_sha256 text NOT NULL,v13_xml_sha256 text NOT NULL,summary jsonb NOT NULL,zip_sha256 text NOT NULL,zip_content bytea NOT NULL,published boolean NOT NULL DEFAULT false)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS precutover_current(singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),package_id text NOT NULL REFERENCES precutover_packages(package_id))''')

def _bounded_probe(url,attempts=3,timeout=45):
    out=[]
    for i in range(attempts):
        try:
            r=requests.get(url,headers={'User-Agent':'outfish-220-master-driven-staging-precutover/2.0'},timeout=timeout)
            out.append({'attempt':i+1,'status_code':r.status_code,'content_type':r.headers.get('content-type',''),'bytes':len(r.content),'sha256':sha(r.content) if r.ok else None,'body_prefix':r.text[:160] if not r.ok else None})
            if r.ok:return r.content,out
        except Exception as e: out.append({'attempt':i+1,'error':f'{type(e).__name__}: {e}'})
        if i+1<attempts: time.sleep(3)
    return None,out

def _money(v):
    d=Decimal(str(v).strip()).quantize(Decimal('0.01')); s=format(d,'f').rstrip('0').rstrip('.'); return s or '0'

def _load_overrides(path):
    out={}
    with open(path,'r',encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            sku=norm(r.get('sku'))
            if sku: out[sku]={'before':norm(r.get('price-before-discount')),'after':norm(r.get('price-after-discount')),'reason':norm(r.get('reason'))}
    return out

def _functional_reproduce():
    required=['generator.py','pilot_input_674.csv','approved_exclusions.json','220_price_overrides.csv','marketplace_pricing.py','production_merge.py']
    for name in required:
        if not (V13_DIR/name).exists(): raise RuntimeError(f'frozen V13 file missing: {name}')
    production_raw,production_attempts=_bounded_probe(PRODUCTION_URL,3,60)
    if production_raw is None: raise RuntimeError('production feed unavailable for frozen V13 functional reproduction: '+json.dumps(production_attempts))
    prod=parse_xml(production_raw)
    with tempfile.TemporaryDirectory(prefix='v13-functional-') as td:
        tdp=Path(td)
        env=os.environ.copy()
        p=subprocess.run([sys.executable,str(V13_DIR/'generator.py'),'--pilot-csv',str(V13_DIR/'pilot_input_674.csv'),'--approved-exclusions',str(V13_DIR/'approved_exclusions.json'),'--output-dir',str(tdp),'--live'],cwd=str(V13_DIR),env=env,capture_output=True,text=True,timeout=1200)
        if p.returncode!=0: raise RuntimeError(f'frozen V13 generator failed rc={p.returncode}; stderr={p.stderr[-2000:]}')
        candidate=tdp/'stock-price-candidate.xml'
        if not candidate.exists(): raise RuntimeError('frozen V13 candidate XML missing after generator')
        tree=ET.parse(candidate); root=tree.getroot(); overrides=_load_overrides(V13_DIR/'220_price_overrides.csv')
        candidate_skus=[]
        for node in root.findall('.//product'):
            sku=norm(node.findtext('sku')); candidate_skus.append(sku)
            if sku not in prod: raise RuntimeError(f'frozen V13 candidate SKU not in production universe: {sku}')
            b=node.find('price-before-discount'); a=node.find('price-after-discount')
            if b is None or a is None: raise RuntimeError(f'frozen V13 candidate price fields missing: {sku}')
            src=overrides.get(sku)
            if src:
                b.text=_money(src['before']); a.text=_money(src['after'])
            else:
                b.text=prod[sku]['price_before']; a.text=prod[sku]['price_after']
        priced=ET.tostring(root,encoding='utf-8',xml_declaration=True)
        cand=parse_xml(priced)
        prod_root=ET.fromstring(production_raw)
        direct=list(prod_root.findall('product')); allnodes=list(prod_root.findall('.//product'))
        if len(direct)!=len(allnodes): raise RuntimeError('frozen V13 production products are not direct root children')
        replaced=0
        for i,node in enumerate(list(prod_root)):
            if node.tag!='product': continue
            sku=norm(node.findtext('sku'))
            if sku in cand:
                src=next(x for x in root.findall('.//product') if norm(x.findtext('sku'))==sku)
                prod_root.remove(node); prod_root.insert(i,ET.fromstring(ET.tostring(src,encoding='utf-8'))); replaced+=1
        functional=ET.tostring(prod_root,encoding='utf-8',xml_declaration=True)
        parsed=parse_xml(functional)
        if len(parsed)!=len(prod): raise RuntimeError(f'frozen V13 functional row count {len(parsed)}/{len(prod)}')
        meta={'frozen_v13_commit':FROZEN_V13_COMMIT,'generator_stdout_tail':p.stdout[-2000:],'generator_stderr_tail':p.stderr[-2000:],'candidate_rows':len(cand),'replaced_rows':replaced,'production_rows':len(prod),'production_feed_sha256':sha(production_raw),'production_attempts':production_attempts,'static_input_hashes':{name:file_sha(V13_DIR/name) for name in required},'shopify_price_used_in_final_baseline':False}
        return functional,meta

def _comparison(staging_raw,baseline_raw,runtime,blockers,baseline_kind):
    staging=parse_xml(staging_raw); base=parse_xml(baseline_raw)
    universe=[]
    for sku in sorted(set(runtime)|set(staging)|set(base)):
        rr=runtime.get(sku,{})
        universe.append({'sku':sku,'in_runtime_dataset':str(sku in runtime).lower(),'in_staging_xml':str(sku in staging).lower(),'in_v13_baseline':str(sku in base).lower(),'runtime_validation_status':rr.get('runtime_validation_status',''),'blocker_reason':rr.get('blocker_reason','')})
    fields=['ean','price_before','price_after','stock','collectionhours']; counts={x:0 for x in fields}; diffs=[]
    for sku in sorted(set(staging)&set(base)):
        a=staging[sku]; b=base[sku]; flags={k:a[k]!=b[k] for k in fields}
        if any(flags.values()):
            for k,v in flags.items(): counts[k]+=int(v)
            row={'sku':sku}
            for k in fields: row.update({f'staging_{k}':a[k],f'v13_{k}':b[k],f'{k}_diff':str(flags[k]).lower()})
            diffs.append(row)
    be=[]
    for sku in sorted(set(blockers)|set(V13_EXCLUSIONS)):
        br=blockers.get(sku,{})
        be.append({'sku':sku,'staging_blocked':str(sku in blockers).lower(),'staging_blocker_reason':br.get('blocker_reason',''),'v13_approved_exclusion':str(sku in V13_EXCLUSIONS).lower(),'v13_exclusion_reason':V13_EXCLUSIONS.get(sku,'')})
    metrics={'baseline_kind':baseline_kind,'runtime_dataset_skus':len(runtime),'staging_xml_skus':len(staging),'v13_baseline_skus':len(base),'runtime_missing_in_v13':sorted(set(runtime)-set(base)),'v13_missing_in_runtime':sorted(set(base)-set(runtime)),'staging_xml_missing_in_v13':sorted(set(staging)-set(base)),'v13_missing_in_staging_xml':sorted(set(base)-set(staging)),'overlap_published_rows':len(set(staging)&set(base)),'ean_diff_count':counts['ean'],'price_before_diff_count':counts['price_before'],'price_after_diff_count':counts['price_after'],'stock_diff_count':counts['stock'],'collectionhours_diff_count':counts['collectionhours'],'row_count_diff_staging_vs_v13':len(staging)-len(base),'staging_blockers':len(blockers),'v13_approved_exclusions':len(V13_EXCLUSIONS)}
    return metrics,universe,diffs,be

def generate_and_persist_package():
    rid,manifest,arts=_read_current_runtime_artifacts(); runtime=_csv_rows(arts['runtime-dataset.csv'],'220_sku'); blockers=_csv_rows(arts['blockers.csv'],'220_sku'); staging_xml=arts['220-stock-master-driven.xml']
    health_raw,health_attempts=_bounded_probe(V13_HEALTH_URL,3,30)
    exact_raw,xml_attempts=_bounded_probe(V13_URL,3,60)
    exact_status='PASS' if exact_raw is not None else 'BLOCKED'
    exact_reason=None if exact_raw is not None else 'FROZEN_V13_HTTP_BODY_UNAVAILABLE_AFTER_BOUNDED_RETRIES'
    functional_raw,functional_meta=_functional_reproduce()
    functional_status='PASS'
    baseline_for_diff=exact_raw if exact_raw is not None else functional_raw
    baseline_kind='EXACT_HTTP' if exact_raw is not None else 'FUNCTIONAL_EQUIVALENT_REPRODUCTION'
    metrics,universe,diffs,be=_comparison(staging_xml,baseline_for_diff,runtime,blockers,baseline_kind)
    summary={'created_at':now(),'staging_refresh_id':rid,'staging_dataset_sha256':manifest.get('runtime_dataset_sha256'),'staging_xml_sha256':sha(staging_xml),'frozen_v13_commit':FROZEN_V13_COMMIT,'exact_http_comparison_status':exact_status,'exact_http_blocker_reason':exact_reason,'v13_health_attempts':health_attempts,'v13_xml_attempts':xml_attempts,'exact_v13_xml_sha256':sha(exact_raw) if exact_raw is not None else None,'functional_equivalent_status':functional_status,'functional_v13_xml_sha256':sha(functional_raw),'functional_meta':functional_meta,'comparison_baseline_kind':baseline_kind,'cutover_performed':False,**metrics}
    summary_b=json.dumps(summary,indent=2,sort_keys=True).encode()
    universe_b=_csv_bytes(universe,['sku','in_runtime_dataset','in_staging_xml','in_v13_baseline','runtime_validation_status','blocker_reason'])
    diff_fields=['sku']+[x for k in ['ean','price_before','price_after','stock','collectionhours'] for x in (f'staging_{k}',f'v13_{k}',f'{k}_diff')]
    diffs_b=_csv_bytes(diffs,diff_fields); be_b=_csv_bytes(be,['sku','staging_blocked','staging_blocker_reason','v13_approved_exclusion','v13_exclusion_reason'])
    bio=io.BytesIO()
    with zipfile.ZipFile(bio,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('summary.json',summary_b); z.writestr('master-driven-staging.xml',staging_xml); z.writestr('v13-functional-equivalent.xml',functional_raw)
        if exact_raw is not None:z.writestr('v13-exact-http.xml',exact_raw)
        z.writestr('sku-universe-comparison.csv',universe_b); z.writestr('field-diffs.csv',diffs_b); z.writestr('blocker-exclusion-diff.csv',be_b); z.writestr('staging-blockers.csv',arts['blockers.csv'])
    zip_b=bio.getvalue(); package_id=summary['created_at'].replace(':','').replace('+','_'); stored_v13_hash=sha(exact_raw if exact_raw is not None else functional_raw)
    with psycopg.connect(dburl(),connect_timeout=10) as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:cur.execute('''INSERT INTO precutover_packages(package_id,created_at,staging_refresh_id,staging_dataset_sha256,v13_xml_sha256,summary,zip_sha256,zip_content,published) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,false)''',(package_id,summary['created_at'],rid,summary['staging_dataset_sha256'],stored_v13_hash,json.dumps(summary),sha(zip_b),zip_b))
        conn.commit()
    with psycopg.connect(dburl(),connect_timeout=10) as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT summary,zip_sha256,zip_content FROM precutover_packages WHERE package_id=%s',(package_id,)); row=cur.fetchone()
            if not row: raise RuntimeError('precutover package read-back missing')
            rb_summary,rb_sha,rb_zip=row; rb_zip=bytes(rb_zip)
            if dict(rb_summary)!=summary or rb_sha!=sha(zip_b) or rb_zip!=zip_b: raise RuntimeError('precutover package read-back mismatch')
            cur.execute('UPDATE precutover_packages SET published=true WHERE package_id=%s',(package_id,)); cur.execute('''INSERT INTO precutover_current(singleton,package_id) VALUES(true,%s) ON CONFLICT(singleton) DO UPDATE SET package_id=EXCLUDED.package_id''',(package_id,))
        conn.commit()
    return {'package_id':package_id,'zip_sha256':sha(zip_b),**summary}

def load_current_package():
    with psycopg.connect(dburl(),connect_timeout=10) as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT p.package_id,p.summary,p.zip_sha256,p.zip_content FROM precutover_current c JOIN precutover_packages p ON p.package_id=c.package_id WHERE c.singleton=true AND p.published=true'''); row=cur.fetchone()
            if not row:return None
            pid,summary,zsha,z=row; z=bytes(z)
            if sha(z)!=zsha: raise RuntimeError('current precutover package hash mismatch')
            return {'package_id':pid,'summary':dict(summary),'zip_sha256':zsha,'zip_content':z}
