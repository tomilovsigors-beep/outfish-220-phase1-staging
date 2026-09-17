from __future__ import annotations
import csv, io, json, os, re, hashlib
from collections import defaultdict
from urllib.parse import quote
import psycopg
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

SHEET_ID=os.getenv('PHH_MANUAL_INPUT_SHEET_ID','1139Zz6NXW_YdEBhUwGMFxuppR7QpUOPClM0qYhJvhPM')
BASE='https://sheets.googleapis.com/v4/spreadsheets'
CATEGORY_TITLES={
 '11810':'PHH 11810 Hammocks','17977':'PHH 17977 Trousers','19576':'PHH 19576 Shorts',
 '20523':'PHH 20523 Tights','433':'PHH 433 Tents','434':'PHH 434 Sleeping Bags',
 '4391':'PHH 4391 Glue','5669':'PHH 5669 Women Rubber Boots','9050':'PHH 9050 Men Jackets',
}
PACK_KEYS=('package_weight','package_length','package_width','package_height','tare_deposit_quantity')

def _session():
    raw=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not raw: raise RuntimeError('GOOGLE_SERVICE_ACCOUNT_JSON missing')
    creds=service_account.Credentials.from_service_account_info(json.loads(raw),scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
    return AuthorizedSession(creds)

def _get(s,rng):
    enc=quote(rng,safe="'!:")
    r=s.get(f'{BASE}/{SHEET_ID}/values/{enc}',timeout=30); r.raise_for_status()
    return r.json().get('values') or []

def _registry():
    with psycopg.connect(os.environ['DATABASE_URL']) as c:
        with c.cursor() as cur:
            cur.execute('''select sku,ean,category_id,category_name,field_id,title_lv,title_lt,title_ee,title_fi,title_ru
                           from phh_manual_input_rows_v11 order by category_id,sku,ean,field_id''')
            rows=cur.fetchall()
    products={}; fields=defaultdict(dict)
    for sku,ean,cid,cname,fid,lv,lt,ee,fi,ru in rows:
        key=(str(sku),str(ean)); cid=str(cid); fid=str(fid)
        products[key]={'category_id':cid,'category_name':str(cname)}
        fields[cid][fid]={'title_lv':lv or '','title_lt':lt or '','title_ee':ee or '','title_fi':fi or '','title_ru':ru or ''}
    return products,fields

def _table(rows,tech_prefix):
    if len(rows)<2:return {},{},['sheet_has_less_than_2_rows']
    headers=[str(x or '') for x in rows[0]]; tech=[str(x or '') for x in rows[1]]
    field_cols={}; errors=[]
    for i,v in enumerate(tech):
        if tech_prefix=='field':
            m=re.fullmatch(r'PHH field_id\s+(\d+)',v.strip())
        else:
            m=re.fullmatch(r'PHH modification\.([a-z_]+)',v.strip())
        if m:
            key=m.group(1)
            if key in field_cols.values(): errors.append('duplicate_technical_key:'+key)
            field_cols[i]=key
    data={}
    for row_no,row in enumerate(rows[2:],start=3):
        if len(row)<2:continue
        sku=str(row[0] or '').strip(); ean=str(row[1] or '').strip()
        if not sku and not ean:continue
        if not sku or not ean:
            errors.append(f'incomplete_identity_row:{row_no}'); continue
        key=(sku,ean)
        if key in data:
            errors.append(f'duplicate_identity:{sku}|{ean}'); continue
        vals={}
        for col,tkey in field_cols.items():
            vals[tkey]=str(row[col] if col<len(row) else '').strip()
        data[key]={'row':row_no,'values':vals,'headers':{tkey:(headers[col] if col<len(headers) else '') for col,tkey in field_cols.items()}}
    return field_cols,data,errors

def _numeric(v):
    if v=='': return False,'MISSING'
    if ',' in v: return False,'USE_DECIMAL_DOT'
    try:
        x=float(v)
    except Exception:
        return False,'NOT_NUMERIC'
    if x<0:return False,'NEGATIVE_NOT_ACCEPTED_WITHOUT_AUTHORITY'
    return True,'OK'

def run(persist=False):
    expected,fields=_registry(); s=_session()
    sheet_errors=[]; resolved={}
    for cid,title in CATEGORY_TITLES.items():
        rows=_get(s,f"'{title}'!A1:AN300")
        _,data,errs=_table(rows,'field'); sheet_errors += [f'{title}:{e}' for e in errs]
        expected_ids=set(fields[cid])
        for key,meta in [(k,v) for k,v in expected.items() if v['category_id']==cid]:
            rec=data.get(key)
            if not rec:
                sheet_errors.append(f'{title}:missing_identity:{key[0]}|{key[1]}')
                values={}
            else: values=rec['values']
            actual_ids=set(values)
            if actual_ids!=expected_ids:
                sheet_errors.append(f'{title}:field_set_mismatch:{key[0]}|{key[1]}')
            missing=[fid for fid in sorted(expected_ids,key=int) if not values.get(fid,'')]
            features=[]
            for fid in sorted(expected_ids,key=int):
                val=values.get(fid,'')
                if not val: continue
                features.append({'field_id':fid,'human_label':rec['headers'].get(fid,'') if rec else '', 'value':val,'phh_name_by_locale':fields[cid][fid]})
            resolved[key]={'sku':key[0],'ean':key[1],'category_id':cid,'category_name':meta['category_name'],'attribute_ready':not missing and actual_ids==expected_ids,'missing_field_ids':missing,'resolved_features':features}

    # Package modification inputs are globally required by ModificationImportRequest create schema.
    prows=_get(s,"'PHH Packaging'!A1:O500")
    _,pdata,perrs=_table(prows,'mod'); sheet_errors += ['PHH Packaging:'+e for e in perrs]
    for key,prod in resolved.items():
        rec=pdata.get(key)
        vals=rec['values'] if rec else {}
        missing=[k for k in PACK_KEYS if not vals.get(k,'')]
        invalid=[]; normalized={}
        for k in PACK_KEYS:
            v=vals.get(k,'')
            if not v:continue
            ok,reason=_numeric(v)
            if not ok: invalid.append({'field':k,'value':v,'reason':reason})
            else: normalized[k]=float(v)
        prod['package_ready']=not missing and not invalid and set(vals)==set(PACK_KEYS)
        prod['package_missing']=missing; prod['package_invalid']=invalid; prod['package_values']=normalized
        prod['infrastructure_ready']=bool(prod['attribute_ready'] and prod['package_ready'])
        prod['publish_action']='NONE_XML_OFF'

    # Detect unexpected workbook identities instead of silently accepting them.
    expected_keys=set(expected)
    all_sheet_keys=set()
    for title in CATEGORY_TITLES.values():
        _,d,_=_table(_get(s,f"'{title}'!A1:AN300"),'field'); all_sheet_keys.update(d)
    unexpected=sorted(all_sheet_keys-expected_keys)
    if unexpected: sheet_errors += [f'unexpected_identity:{a}|{b}' for a,b in unexpected]

    products=list(resolved.values())
    summary={
      'status':'PASS' if not sheet_errors else 'SCHEMA_ERROR',
      'products':len(products),
      'attribute_ready':sum(x['attribute_ready'] for x in products),
      'package_ready':sum(x['package_ready'] for x in products),
      'infrastructure_ready':sum(x['infrastructure_ready'] for x in products),
      'attribute_missing_cells':sum(len(x['missing_field_ids']) for x in products),
      'package_missing_cells':sum(len(x['package_missing']) for x in products),
      'package_invalid_cells':sum(len(x['package_invalid']) for x in products),
      'sheet_schema_errors':sheet_errors,
      'publish_gate':'BLOCKED_XML_OFF',
      'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','stock_changes':0,'price_changes':0},
    }
    raw=json.dumps({'summary':summary,'products':products},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    summary['dataset_hash']=hashlib.sha256(raw).hexdigest()
    out={'summary':summary,'products':products}
    if persist:
        with psycopg.connect(os.environ['DATABASE_URL']) as c:
            with c.cursor() as cur:
                cur.execute('''create table if not exists phh_manual_input_preflight_v11z_snapshots(id bigserial primary key,created_at timestamptz not null default now(),dataset_hash text not null,summary jsonb not null,payload jsonb not null)''')
                cur.execute('insert into phh_manual_input_preflight_v11z_snapshots(dataset_hash,summary,payload) values(%s,%s::jsonb,%s::jsonb)',(summary['dataset_hash'],json.dumps(summary,ensure_ascii=False),json.dumps(out,ensure_ascii=False)))
            c.commit()
    print('PHH_MANUAL_INPUT_PREFLIGHT_V11Z '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
    return out

def to_csv(out):
    f=io.StringIO(); cols=['sku','ean','category_id','category_name','attribute_ready','package_ready','infrastructure_ready','missing_required_attributes','missing_package_fields','invalid_package_fields','publish_action']
    w=csv.DictWriter(f,fieldnames=cols); w.writeheader()
    for p in out['products']:
        w.writerow({'sku':p['sku'],'ean':p['ean'],'category_id':p['category_id'],'category_name':p['category_name'],'attribute_ready':'YES' if p['attribute_ready'] else 'NO','package_ready':'YES' if p['package_ready'] else 'NO','infrastructure_ready':'YES' if p['infrastructure_ready'] else 'NO','missing_required_attributes':'|'.join(p['missing_field_ids']),'missing_package_fields':'|'.join(p['package_missing']),'invalid_package_fields':'|'.join(x['field']+':'+x['reason'] for x in p['package_invalid']),'publish_action':p['publish_action']})
    return f.getvalue().encode('utf-8-sig')
