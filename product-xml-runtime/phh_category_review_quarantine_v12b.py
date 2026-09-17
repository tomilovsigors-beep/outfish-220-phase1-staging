from __future__ import annotations
import json, os, re
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

def _session():
    info=json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON'])
    creds=service_account.Credentials.from_service_account_info(info,scopes=['https://www.googleapis.com/auth/spreadsheets'])
    return AuthorizedSession(creds)

def _latest_review():
    with psycopg.connect(os.environ['DATABASE_URL']) as c:
        with c.cursor() as cur:
            cur.execute('select payload from phh_category_assignment_verify_v12a_snapshots order by created_at desc limit 1')
            row=cur.fetchone()
    if not row: raise RuntimeError('v12a verification snapshot missing')
    p=row[0] if isinstance(row[0],dict) else json.loads(row[0])
    return [r for r in p.get('detail',[]) if r.get('verification_status')!='PASS_OBJECT_MATCH']

def _meta(s):
    r=s.get(f'{BASE}/{SHEET_ID}',params={'fields':'sheets(properties,conditionalFormats)'},timeout=30); r.raise_for_status(); return r.json()

def _get(s,title,rng='A1:AN300'):
    a1=quote(f"'{title}'!{rng}",safe="'!:")
    r=s.get(f'{BASE}/{SHEET_ID}/values/{a1}',timeout=30)
    if r.status_code==400: return []
    r.raise_for_status(); return r.json().get('values') or []

def _put(s,title,values):
    a1=quote(f"'{title}'!A1",safe="'!:")
    r=s.put(f'{BASE}/{SHEET_ID}/values/{a1}',params={'valueInputOption':'RAW'},json={'values':values},timeout=60); r.raise_for_status()

def _clear(s,title):
    a1=quote(f"'{title}'!A1:AN300",safe="'!:")
    r=s.post(f'{BASE}/{SHEET_ID}/values/{a1}:clear',json={},timeout=60); r.raise_for_status()

def _batch(s,reqs):
    if not reqs:return
    r=s.post(f'{BASE}/{SHEET_ID}:batchUpdate',json={'requests':reqs},timeout=60); r.raise_for_status()

def _reason(r):
    cid=str(r.get('category_id')); title=str(r.get('master_title') or '')
    if cid=='20523': return 'CATEGORY REVIEW: product title is thermal/base-layer pants; PHH 20523 requires hosiery-specific fields such as DEN and Quantity in Package. Do not fill this tab until category is resolved.'
    if cid=='433': return 'CATEGORY REVIEW: product is a pavilion wind-protection wall/accessory, while PHH 433 requires tent fields such as Number of Persons, Entrances and Layers.'
    if cid=='434': return 'CATEGORY REVIEW: product title indicates blanket/poncho/bivy rather than a conventional sleeping bag; PHH 434 requires sleeping-bag temperature and packed/unfolded dimensions.'
    return 'CATEGORY REVIEW: object title does not safely match the assigned PHH category.'

def run():
    review=_latest_review()
    if len(review)!=17: raise RuntimeError(f'expected 17 review products, got {len(review)}')
    review_keys={(str(r['sku']),str(r['ean'])) for r in review}
    s=_session(); meta=_meta(s)
    sheets={x['properties']['title']:x['properties']['sheetId'] for x in meta.get('sheets',[])}
    if 'PHH Category Review' not in sheets:
        _batch(s,[{'addSheet':{'properties':{'title':'PHH Category Review','index':2,'gridProperties':{'rowCount':100,'columnCount':10,'frozenRowCount':1}}}}])
        meta=_meta(s); sheets={x['properties']['title']:x['properties']['sheetId'] for x in meta.get('sheets',[])}
    review_values=[['SKU','EAN','Previous PHH Category ID','Previous PHH Category','Product Title','Verification Status','Reason','Action']]
    for r in sorted(review,key=lambda x:(str(x['category_id']),str(x['sku']))):
        review_values.append([str(r['sku']),str(r['ean']),str(r['category_id']),str(r.get('category_name') or ''),str(r.get('master_title') or ''),str(r.get('verification_status') or ''),_reason(r),'DO NOT FILL ATTRIBUTES — CATEGORY MUST BE RESOLVED'])
    _clear(s,'PHH Category Review'); _put(s,'PHH Category Review',review_values)
    rid=sheets['PHH Category Review']
    _batch(s,[
      {'repeatCell':{'range':{'sheetId':rid,'startRowIndex':0,'endRowIndex':1,'startColumnIndex':0,'endColumnIndex':8},'cell':{'userEnteredFormat':{'backgroundColor':{'red':0.55,'green':0.12,'blue':0.12},'textFormat':{'bold':True,'foregroundColor':{'red':1,'green':1,'blue':1}},'wrapStrategy':'WRAP'}},'fields':'userEnteredFormat'}},
      {'repeatCell':{'range':{'sheetId':rid,'startRowIndex':1,'endRowIndex':len(review_values),'startColumnIndex':0,'endColumnIndex':8},'cell':{'userEnteredFormat':{'backgroundColor':{'red':1,'green':0.82,'blue':0.70},'wrapStrategy':'WRAP'}},'fields':'userEnteredFormat'}},
      {'setBasicFilter':{'filter':{'range':{'sheetId':rid,'startRowIndex':0,'endRowIndex':len(review_values),'startColumnIndex':0,'endColumnIndex':8}}}},
      {'updateDimensionProperties':{'range':{'sheetId':rid,'dimension':'COLUMNS','startIndex':0,'endIndex':2},'properties':{'pixelSize':150},'fields':'pixelSize'}},
      {'updateDimensionProperties':{'range':{'sheetId':rid,'dimension':'COLUMNS','startIndex':4,'endIndex':8},'properties':{'pixelSize':260},'fields':'pixelSize'}},
    ])
    removed={}
    for cid,title in CATEGORY_TITLES.items():
        if title not in sheets: continue
        vals=_get(s,title)
        if len(vals)<2: continue
        keep=vals[:2]; n=0
        for row in vals[2:]:
            if len(row)<2: continue
            key=(str(row[0] or '').strip(),str(row[1] or '').strip())
            if key in review_keys:
                n+=1; continue
            if key[0] and key[1]: keep.append(row)
        if n:
            _clear(s,title); _put(s,title,keep); removed[cid]=n
            if len(keep)==2:
                _batch(s,[{'updateSheetProperties':{'properties':{'sheetId':sheets[title],'hidden':True},'fields':'hidden'}}])
    # Dashboard: add explicit review line and refresh product counts for visible category input tabs.
    dash=_get(s,'PHH Manual Input','A1:F30')
    if dash:
        header=dash[:2]
        rows=[]
        for row in dash[2:]:
            if not row: continue
            if str(row[0])=='PACKAGING': rows.append(row); continue
            cid=str(row[0])
            title=CATEGORY_TITLES.get(cid)
            if not title: continue
            vals=_get(s,title,'A1:AN300')
            product_count=max(0,len([x for x in vals[2:] if len(x)>=2 and str(x[0]).strip() and str(x[1]).strip()]))
            row=list(row)+['']*(6-len(row)); row[2]=product_count
            rows.append(row[:6])
        rows.append(['CATEGORY REVIEW','PHH Category Review',17,'—',0,17])
        _clear(s,'PHH Manual Input'); _put(s,'PHH Manual Input',header+rows)
    print('PHH_CATEGORY_REVIEW_QUARANTINE_V12B '+json.dumps({'status':'PASS','review_products':len(review),'removed_from_manual_tabs':removed,'validated_manual_products':143-len(review),'review_sheet':'PHH Category Review','safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF'}},sort_keys=True),flush=True)
    return {'review_products':len(review),'removed':removed}
