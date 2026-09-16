from __future__ import annotations
import csv, io, json, os, re, statistics, zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
import psycopg
from runtime import fetch_master, fetch_sia, sha

ROOT=Path(__file__).resolve().parent

def norm(v): return '' if v is None else str(v).strip()
def dec(v):
    s=norm(v)
    if not s:return None
    try:return Decimal(s)
    except InvalidOperation:return None

def eq(a,b):
    da,db=dec(a),dec(b)
    if da is None or db is None:return da is None and db is None
    return da==db

def dburl():
    v=os.getenv('AUDIT_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not v: raise RuntimeError('AUDIT_DATABASE_URL missing')
    return v

def parse_xml(raw):
    import xml.etree.ElementTree as ET
    root=ET.fromstring(raw); out={}
    for p in root.findall('.//product'):
        sku=norm(p.findtext('sku'))
        if not sku:continue
        def t(*names):
            for name in names:
                n=p.find(name)
                if n is not None:return norm(n.text)
            return ''
        out[sku]={'sku':sku,'before':t('price_before_discount','price-before-discount'),'after':t('price_after_discount','price-after-discount')}
    return out

def load_package_and_snapshot():
    with psycopg.connect(dburl(),connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT p.package_id,p.summary,p.zip_sha256,p.zip_content FROM precutover_current c JOIN precutover_packages p ON p.package_id=c.package_id WHERE c.singleton=true AND p.published=true''')
            row=cur.fetchone()
            if not row:raise RuntimeError('current precutover package missing')
            pid,summary,zsha,z=row; z=bytes(z)
            if sha(z)!=zsha:raise RuntimeError('precutover ZIP hash mismatch')
            srid=dict(summary)['staging_refresh_id']
            cur.execute('SELECT manifest,audit FROM runtime_snapshots WHERE refresh_id=%s AND published=true',(srid,)); srow=cur.fetchone()
            if not srow:raise RuntimeError('staging snapshot referenced by precutover package missing')
            manifest,audit=map(dict,srow)
    with zipfile.ZipFile(io.BytesIO(z),'r') as zz:
        func=zz.read('v13-functional-equivalent.xml'); staging=zz.read('master-driven-staging.xml')
    return pid,dict(summary),manifest,audit,func,staging

def pct(delta,old):
    if old==0:return None
    return (delta/old)*Decimal(100)

def fnum(x):
    if x is None:return None
    return float(x)

def run_price_impact_audit():
    pid,summary,manifest,audit,func_raw,staging_raw=load_package_and_snapshot()
    if summary.get('price_after_diff_count')!=927:raise RuntimeError(f'precutover package price_after_diff_count={summary.get("price_after_diff_count")} expected 927')
    functional=parse_xml(func_raw); staging=parse_xml(staging_raw)
    raw_diff_skus=sorted(sku for sku in set(staging)&set(functional) if not eq(staging[sku]['after'],functional[sku]['after']))
    if len(raw_diff_skus)!=927:raise RuntimeError(f'reconstructed raw price-after diffs {len(raw_diff_skus)}/927')

    mraw,mrows=fetch_master(); sraw,srows=fetch_sia()
    ids=audit.get('source_snapshot_ids') or {}
    if sha(mraw)!=ids.get('master_sha256'):raise RuntimeError('Master source hash drift vs audited staging snapshot')
    if sha(sraw)!=ids.get('sia_sha256'):raise RuntimeError('SIA source hash drift vs audited staging snapshot')
    master={norm(r.get('220_sku')):r for r in mrows if norm(r.get('220_sku'))}
    sia={norm(r.get('sku')):r for r in srows if norm(r.get('sku'))}

    rtext=(ROOT/'runtime.py').read_text(encoding='utf-8')
    mq=re.search(r"q='''(query RuntimeInventory.*?)'''",rtext,re.S)
    if not mq:raise RuntimeError('cannot locate Shopify RuntimeInventory GraphQL query')
    shopify_query=mq.group(1)
    if re.search(r'\bprice\b',shopify_query,re.I):raise RuntimeError('Shopify RuntimeInventory query unexpectedly contains price')
    if audit.get('shopify_price_used') is not False:raise RuntimeError('persisted audit does not assert shopify_price_used=false')

    rows=[]; unexplained=[]
    for sku in raw_diff_skus:
        m=master.get(sku); s=sia.get(sku)
        if not m or not s:
            unexplained.append({'sku':sku,'reason':'MASTER_OR_SIA_PROVENANCE_ROW_MISSING'}); continue
        mb,ma=dec(m.get('220_price_before_discount')),dec(m.get('220_price_after_discount'))
        old=dec(functional[sku]['after'])
        if mb is None or mb<=0 or old is None or old<=0:
            unexplained.append({'sku':sku,'reason':'INVALID_BASELINE_OR_MASTER_PRICE','v13_after':functional[sku]['after'],'master_before':norm(m.get('220_price_before_discount')),'master_after':norm(m.get('220_price_after_discount'))}); continue
        if not eq(staging[sku]['before'],m.get('220_price_before_discount')) or not eq(staging[sku]['after'],m.get('220_price_after_discount')):
            unexplained.append({'sku':sku,'reason':'STAGING_XML_MASTER_PRICE_MISMATCH','staging_before':staging[sku]['before'],'staging_after':staging[sku]['after'],'master_before':norm(m.get('220_price_before_discount')),'master_after':norm(m.get('220_price_after_discount'))}); continue
        effective=ma if ma is not None else mb
        if effective<=0:
            unexplained.append({'sku':sku,'reason':'MASTER_EFFECTIVE_PRICE_NONPOSITIVE'}); continue
        sb=dec(s.get('(1) price before discount')); sa=dec(s.get('(2) price after discount'))
        master_matches_sia=(sb is not None and mb==sb and ((ma is None and sa is None) or (ma is not None and sa is not None and ma==sa)))
        reason=norm(m.get('price_override_reason'))
        notes=norm(m.get('notes'))
        if master_matches_sia:
            cls='LEGACY_BASELINE_MIGRATION'; origin='SIA_FHM_PRICE_FIELDS'
        elif reason:
            cls='EXPECTED_MASTER_PRICE'; origin='MASTER_PRICE_OVERRIDE_REASON'
        else:
            unexplained.append({'sku':sku,'reason':'MASTER_PRICE_NOT_TRACEABLE_TO_SIA_AND_NO_OVERRIDE_REASON','v13_after':str(old),'master_before':str(mb),'master_after':'' if ma is None else str(ma),'sia_before':'' if sb is None else str(sb),'sia_after':'' if sa is None else str(sa),'price_override_reason':reason,'notes':notes}); continue
        delta=effective-old; pp=pct(delta,old)
        rows.append({'sku':sku,'v13_after':old,'master_raw_after':ma,'master_effective_after':effective,'delta':delta,'pct':pp,'classification':cls,'origin':origin})

    if unexplained:
        return {'gate':'FAIL','raw_diff_count':927,'unexplained_count':len(unexplained),'unexplained':unexplained,'shopify_price_used':False,'package_id':pid}
    if len(rows)!=927:raise RuntimeError(f'classified rows {len(rows)}/927')

    ups=[r for r in rows if r['delta']>0]; downs=[r for r in rows if r['delta']<0]; same=[r for r in rows if r['delta']==0]
    abs_changed=[r for r in rows if r['delta']!=0]
    def med_amount(rs):return statistics.median([r['delta'] for r in rs]) if rs else Decimal(0)
    def med_abs_down(rs):return statistics.median([-r['delta'] for r in rs]) if rs else Decimal(0)
    thresholds={str(t):sum(1 for r in abs_changed if r['pct'] is not None and abs(r['pct'])>Decimal(t)) for t in (10,25,50)}
    top_abs=sorted(abs_changed,key=lambda r:(abs(r['delta']),r['sku']),reverse=True)[:20]
    top_pct=sorted([r for r in abs_changed if r['pct'] is not None],key=lambda r:(abs(r['pct']),r['sku']),reverse=True)[:20]
    classes={c:sum(1 for r in rows if r['classification']==c) for c in ('EXPECTED_MASTER_PRICE','LEGACY_BASELINE_MIGRATION')}
    def pub(r):return {'sku':r['sku'],'v13_after':fnum(r['v13_after']),'master_effective_after':fnum(r['master_effective_after']),'change':fnum(r['delta']),'change_pct':fnum(r['pct']),'classification':r['classification']}
    return {'gate':'PASS','package_id':pid,'raw_price_after_diffs':927,'increase_count':len(ups),'decrease_count':len(downs),'normalized_same_count':len(same),'median_increase':fnum(med_amount(ups)),'max_increase':fnum(max([r['delta'] for r in ups],default=Decimal(0))),'median_decrease':fnum(med_abs_down(downs)),'max_decrease':fnum(max([-r['delta'] for r in downs],default=Decimal(0))),'changes_over_pct':thresholds,'classification_counts':{**classes,'UNEXPLAINED_PRICE_CHANGE':0},'shopify_price_used':False,'shopify_price_query_absent':True,'provenance_verified_count':927,'master_hash':ids.get('master_sha256'),'sia_hash':ids.get('sia_sha256'),'top20_absolute':[pub(r) for r in top_abs],'top20_percentage':[pub(r) for r in top_pct]}

def _print_startup_gate():
    try: print('FINAL_PRICE_IMPACT_AUDIT '+json.dumps(run_price_impact_audit(),sort_keys=True),flush=True)
    except Exception as e: print('FINAL_PRICE_IMPACT_AUDIT_ERROR '+repr(e),flush=True)

if __name__=='__main__':
    _print_startup_gate()
else:
    _print_startup_gate()
