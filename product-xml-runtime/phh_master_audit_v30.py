from __future__ import annotations
import json, os, time, requests
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from pmp_api_probe import BASE,_api_login,_api_get
from app import _master_rows

SELLER_ID='9990696'

def _h(token):
    return {'User-Agent':'outfish-phh-master-audit/30','Accept':'application/json','Authorization':'Pigu-mp '+token}

def _s(v): return str(v or '').strip()

def _offer_eans(o):
    m=(o or {}).get('modification') or {}
    out=set()
    for k in ('ean',):
        if _s(m.get(k)): out.add(_s(m.get(k)))
    for x in (m.get('ean_codes') or []):
        if _s(x): out.add(_s(x))
    return out

def _offer_skus(o):
    m=(o or {}).get('modification') or {}
    out=set()
    for k in ('sku','manufacturer_code'):
        if _s(m.get(k)): out.add(_s(m.get(k)))
    if _s(o.get('manufacturer_code')): out.add(_s(o.get('manufacturer_code')))
    return out

def scan_offers(token):
    all_items=[]; offset=0; limit=100
    while offset < 50000:
        r=_api_get(f'/v2/sellers/{SELLER_ID}/offers?app_name=220.lv&limit={limit}&offset={offset}',token)
        r.raise_for_status(); d=r.json()
        items=(d.get('offers') if isinstance(d,dict) else None) or (d.get('items') if isinstance(d,dict) else None) or (d if isinstance(d,list) else [])
        if not items: break
        all_items.extend(items)
        if len(items)<limit: break
        offset += len(items)
    return all_items

def lookup_ean(token,ean):
    url=urljoin(BASE,'/v3/products/product-modifications/barcodes')
    for attempt in range(4):
        try:
            r=requests.get(url,headers=_h(token),params={'ean':ean},timeout=20)
            if r.status_code==404: return {'exists_220':False,'http':404,'items':[]}
            if r.status_code==200:
                d=r.json()
                items=d if isinstance(d,list) else (d.get('items') if isinstance(d,dict) else [])
                exists=any(isinstance(x,dict) and ((x.get('modification') or {}).get('app_name')=='220.lv') for x in (items or []))
                return {'exists_220':bool(exists),'http':200,'items':items or []}
            if r.status_code in (429,500,502,503,504):
                time.sleep(0.7*(attempt+1)); continue
            return {'exists_220':False,'http':r.status_code,'error':r.text[:500],'items':[]}
        except Exception as e:
            if attempt==3: return {'exists_220':False,'http':0,'error':f'{type(e).__name__}: {e}','items':[]}
            time.sleep(0.7*(attempt+1))
    return {'exists_220':False,'http':0,'error':'unknown','items':[]}

def persist(rows,summary):
    db=os.getenv('DATABASE_URL')
    if not db: return
    try:
        import psycopg
        with psycopg.connect(db) as c:
            with c.cursor() as cur:
                cur.execute('''create table if not exists phh_master_audit_v30 (
                    audit_ts timestamptz not null default now(),
                    row_no integer not null,
                    shopify_sku text, shopify_barcode text, master_220_sku text, master_220_ean text,
                    master_220_status text, master_match_status text,
                    live_state text, offer_id text, offer_status text, offer_amount integer,
                    offer_price numeric, live_modification_id text, live_pigu_external_id text,
                    live_ean text, live_sku text, match_basis text, mismatch_code text,
                    primary key(audit_ts,row_no)
                )''')
                cur.execute('create table if not exists phh_master_audit_summary_v30 (audit_ts timestamptz not null default now(), summary jsonb not null)')
                cur.execute('insert into phh_master_audit_summary_v30(summary) values(%s)',(json.dumps(summary),))
                vals=[]
                for x in rows:
                    vals.append((x['row_no'],x['shopify_sku'],x['shopify_barcode'],x['master_220_sku'],x['master_220_ean'],
                        x['master_220_status'],x['master_match_status'],x['live_state'],x.get('offer_id'),x.get('offer_status'),
                        x.get('offer_amount'),x.get('offer_price'),x.get('live_modification_id'),x.get('live_pigu_external_id'),
                        x.get('live_ean'),x.get('live_sku'),x.get('match_basis'),x.get('mismatch_code')))
                cur.executemany('''insert into phh_master_audit_v30(
                    row_no,shopify_sku,shopify_barcode,master_220_sku,master_220_ean,master_220_status,master_match_status,
                    live_state,offer_id,offer_status,offer_amount,offer_price,live_modification_id,live_pigu_external_id,
                    live_ean,live_sku,match_basis,mismatch_code) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',vals)
            c.commit()
    except Exception as e:
        print('PHH_MASTER_AUDIT_PERSIST_FAILED',type(e).__name__,str(e),flush=True)

def run():
    master=_master_rows()
    lr=_api_login('v3'); lr.raise_for_status(); token=lr.json()['token']
    offers=scan_offers(token)

    by_ean=defaultdict(list); by_sku=defaultdict(list)
    for o in offers:
        for e in _offer_eans(o): by_ean[e].append(o)
        for s in _offer_skus(o): by_sku[s].append(o)

    preliminary=[]
    unmatched_eans=set()
    for i,row in enumerate(master,start=2):
        se=_s(row.get('shopify_barcode')); e220=_s(row.get('220_ean'))
        ss=_s(row.get('shopify_sku')); s220=_s(row.get('220_sku'))
        ekeys=[x for x in (e220,se) if x]
        skeys=[x for x in (s220,ss) if x]
        matches=[]; basis=[]
        for e in ekeys:
            for o in by_ean.get(e,[]):
                if id(o) not in [id(z) for z in matches]: matches.append(o)
                basis.append('EAN:'+e)
        if not matches:
            for s in skeys:
                for o in by_sku.get(s,[]):
                    if id(o) not in [id(z) for z in matches]: matches.append(o)
                    basis.append('SKU:'+s)
        preliminary.append((i,row,matches,','.join(sorted(set(basis)))))
        if not matches:
            for e in ekeys: unmatched_eans.add(e)

    lookup={}
    todo=sorted(unmatched_eans)
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs={ex.submit(lookup_ean,token,e):e for e in todo}
        done=0
        for f in as_completed(futs):
            e=futs[f]
            try: lookup[e]=f.result()
            except Exception as er: lookup[e]={'exists_220':False,'http':0,'error':str(er),'items':[]}
            done+=1
            if done%250==0:
                print('PHH_MASTER_AUDIT_PROGRESS '+json.dumps({'barcode_lookups_done':done,'barcode_lookups_total':len(todo)}),flush=True)

    rows=[]; counts=Counter(); mismatch_counts=Counter(); lookup_errors=0
    for row_no,row,matches,basis in preliminary:
        ss=_s(row.get('shopify_sku')); se=_s(row.get('shopify_barcode')); s220=_s(row.get('220_sku')); e220=_s(row.get('220_ean'))
        mst=_s(row.get('220_status')); mms=_s(row.get('match_status'))
        offer=matches[0] if matches else None
        if len(matches)>1:
            # Prefer exact 220.lv active offer, then first.
            active=[o for o in matches if _s(o.get('status')).lower()=='active']
            if active: offer=active[0]
        live_state='NOT_IN_220'; card_exists=False; match_basis=basis
        if offer:
            live_state='OFFER_ACTIVE' if _s(offer.get('status')).lower()=='active' else 'OFFER_INACTIVE'
            card_exists=True
        else:
            for e in [x for x in (e220,se) if x]:
                q=lookup.get(e) or {}
                if q.get('http') not in (200,404): lookup_errors+=1
                if q.get('exists_220'):
                    card_exists=True; live_state='CARD_ONLY'; match_basis='BARCODE:'+e; break

        mismatch=[]
        if mst=='NOT_IN_220' and live_state!='NOT_IN_220': mismatch.append('MASTER_FALSE_NOT_IN_220')
        if mst=='ACTIVE_220' and live_state=='NOT_IN_220': mismatch.append('MASTER_FALSE_ACTIVE_220')
        if mst=='ACTIVE_220' and live_state=='CARD_ONLY': mismatch.append('ACTIVE_220_WITHOUT_SELLER_OFFER')
        if live_state.startswith('OFFER_') and not (s220 or e220): mismatch.append('LIVE_OFFER_IDENTITY_MISSING_IN_MASTER')

        live={}
        if offer:
            mod=offer.get('modification') or {}
            live={
                'offer_id':_s(offer.get('id')),'offer_status':_s(offer.get('status')),
                'offer_amount':offer.get('amount'),'offer_price':offer.get('sell_price'),
                'live_modification_id':_s(mod.get('id')),'live_pigu_external_id':_s(mod.get('pigu_external_id')),
                'live_ean':_s(mod.get('ean')),'live_sku':_s(mod.get('sku'))
            }
            if e220 and live['live_ean'] and e220!=live['live_ean'] and se!=live['live_ean']:
                mismatch.append('EAN_IDENTITY_MISMATCH')

        rec={'row_no':row_no,'shopify_sku':ss,'shopify_barcode':se,'master_220_sku':s220,'master_220_ean':e220,
             'master_220_status':mst,'master_match_status':mms,'live_state':live_state,'match_basis':match_basis,
             'mismatch_code':'|'.join(sorted(set(mismatch))),**live}
        rows.append(rec); counts[live_state]+=1
        if rec['mismatch_code']:
            for x in rec['mismatch_code'].split('|'): mismatch_counts[x]+=1

    master_eans=set()
    for _,row,_,_ in preliminary:
        for e in (_s(row.get('220_ean')),_s(row.get('shopify_barcode'))):
            if e: master_eans.add(e)
    offer_eans=set(by_ean)
    orphan_offer_eans=sorted(offer_eans-master_eans)

    summary={
        'status':'PASS','master_rows':len(master),'seller_offers_220':len(offers),
        'unique_master_eans':len(master_eans),'unique_offer_eans':len(offer_eans),
        'barcode_lookups':len(todo),'barcode_lookup_errors':lookup_errors,
        'live_state_counts':dict(counts),'mismatch_counts':dict(mismatch_counts),
        'mismatch_rows':sum(1 for x in rows if x['mismatch_code']),
        'orphan_offer_eans_not_in_master':len(orphan_offer_eans),
        'safety':{'phh_writes':0,'master_writes':0,'shopify_writes':0}
    }
    persist(rows,summary)
    print('PHH_MASTER_AUDIT_SUMMARY_V30 '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)

    mism=[x for x in rows if x['mismatch_code']]
    chunk=80; total=(len(mism)+chunk-1)//chunk
    for i in range(total):
        slim=[]
        for x in mism[i*chunk:(i+1)*chunk]:
            slim.append({k:x.get(k) for k in ('row_no','shopify_sku','shopify_barcode','master_220_sku','master_220_ean','master_220_status','live_state','offer_id','offer_status','offer_amount','offer_price','live_ean','live_sku','match_basis','mismatch_code')})
        print(f'PHH_MASTER_AUDIT_MISMATCH_V30 {i+1}/{total} '+json.dumps(slim,ensure_ascii=False,separators=(',',':')),flush=True)
    if orphan_offer_eans:
        c=100; t=(len(orphan_offer_eans)+c-1)//c
        for i in range(t):
            print(f'PHH_MASTER_AUDIT_ORPHAN_EANS_V30 {i+1}/{t} '+json.dumps(orphan_offer_eans[i*c:(i+1)*c],ensure_ascii=False,separators=(',',':')),flush=True)
    return summary
