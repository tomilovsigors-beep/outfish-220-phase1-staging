from __future__ import annotations
import json, os, re
from collections import OrderedDict
from urllib.parse import quote
import psycopg
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

SHEET_ID=os.getenv('PHH_MANUAL_INPUT_SHEET_ID','1139Zz6NXW_YdEBhUwGMFxuppR7QpUOPClM0qYhJvhPM')
MASTER_ID=os.getenv('MASTER_SHEET_ID','1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I')
BASE='https://sheets.googleapis.com/v4/spreadsheets'
CATEGORY_TITLES={
 '11810':'PHH 11810 Hammocks','17977':'PHH 17977 Trousers','19576':'PHH 19576 Shorts',
 '20523':'PHH 20523 Tights','433':'PHH 433 Tents','434':'PHH 434 Sleeping Bags',
 '4391':'PHH 4391 Glue','5669':'PHH 5669 Women Rubber Boots','9050':'PHH 9050 Men Jackets',
}
PACK_FIELDS=[
 ('package_weight','Package Weight'),('package_length','Package Length'),('package_width','Package Width'),('package_height','Package Height'),('tare_deposit_quantity','Tare Deposit Quantity')
]

def _session():
    raw=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not raw: raise RuntimeError('GOOGLE_SERVICE_ACCOUNT_JSON missing')
    creds=service_account.Credentials.from_service_account_info(json.loads(raw),scopes=['https://www.googleapis.com/auth/spreadsheets'])
    return AuthorizedSession(creds)

def _products():
    with psycopg.connect(os.environ['DATABASE_URL']) as c:
        with c.cursor() as cur:
            cur.execute('''select sku,ean,category_id,category_name from phh_manual_input_rows_v11 group by sku,ean,category_id,category_name order by category_id,sku,ean''')
            return [{'sku':str(a),'ean':str(b),'category_id':str(c),'category_name':str(d)} for a,b,c,d in cur.fetchall()]

def _batch(s,requests):
    if not requests:return {}
    r=s.post(f'{BASE}/{SHEET_ID}:batchUpdate',json={'requests':requests},timeout=60); r.raise_for_status(); return r.json()

def _put(s,rng,values):
    enc=quote(rng,safe="'!:")
    r=s.put(f'{BASE}/{SHEET_ID}/values/{enc}',params={'valueInputOption':'RAW'},json={'values':values},timeout=60); r.raise_for_status(); return r.json()

def _get(s,rng):
    enc=quote(rng,safe="'!:")
    r=s.get(f'{BASE}/{SHEET_ID}/values/{enc}',timeout=30)
    if r.status_code==400:return []
    r.raise_for_status(); return r.json().get('values') or []

def _clear(s,rng):
    enc=quote(rng,safe="'!:")
    r=s.post(f'{BASE}/{SHEET_ID}/values/{enc}:clear',json={},timeout=30); r.raise_for_status()

def _col(n0):
    n=n0+1; out=''
    while n:
        n,rem=divmod(n-1,26); out=chr(65+rem)+out
    return out

def _existing_pack(rows):
    if len(rows)<2:return {}
    tech=rows[1]; cols={}
    for i,v in enumerate(tech):
        m=re.fullmatch(r'PHH modification\.([a-z_]+)',str(v or '').strip())
        if m: cols[i]=m.group(1)
    out={}
    for row in rows[2:]:
        if len(row)<2:continue
        key=(str(row[0] or '').strip(),str(row[1] or '').strip())
        if not all(key):continue
        vals={}
        for i,k in cols.items():
            if i<len(row) and str(row[i]).strip():vals[k]=str(row[i]).strip()
        if vals:out[key]=vals
    return out

def run():
    if SHEET_ID==MASTER_ID:raise RuntimeError('Refusing to write packaging workspace into Master spreadsheet')
    products=_products(); s=_session()
    meta=s.get(f'{BASE}/{SHEET_ID}',params={'fields':'sheets(properties,conditionalFormats)'},timeout=30); meta.raise_for_status()
    ms=meta.json(); sheets={x['properties']['title']:x['properties']['sheetId'] for x in ms.get('sheets',[])}
    for required in ('PHH Packaging','PHH Manual Input','PHH Input Guide'):
        if required not in sheets: raise RuntimeError('Expected workbook sheet missing: '+required)
    cf_counts={x['properties']['sheetId']:len(x.get('conditionalFormats') or []) for x in ms.get('sheets',[])}

    # Packaging sheet: required modification-level inputs from official PHH ModificationImportRequest.
    title='PHH Packaging'; sid=sheets[title]
    old=_existing_pack(_get(s,"'PHH Packaging'!A1:O500"))
    headers=['SKU','EAN','Category ID','Category']+[x[1] for x in PACK_FIELDS]+['Missing Required','Input Status']
    tech=['','','','']+[f'PHH modification.{x[0]}' for x in PACK_FIELDS]+['','']
    values=[headers,tech]; preserved=0; blanks=0
    for p in products:
        vals=[]; prev=old.get((p['sku'],p['ean']),{})
        for key,_ in PACK_FIELDS:
            val=prev.get(key,'')
            if val:preserved+=1
            else:blanks+=1
            vals.append(val)
        values.append([p['sku'],p['ean'],p['category_id'],p['category_name']]+vals+['',''])
    end_col=len(headers); first_input=4; last_input=4+len(PACK_FIELDS)-1; miss_col=end_col-2; status_col=end_col-1
    reqs=[]
    for _ in range(cf_counts.get(sid,0)):reqs.append({'deleteConditionalFormatRule':{'sheetId':sid,'index':0}})
    reqs += [
      {'clearBasicFilter':{'sheetId':sid}},
      {'updateSheetProperties':{'properties':{'sheetId':sid,'gridProperties':{'frozenRowCount':2,'frozenColumnCount':2}},'fields':'gridProperties.frozenRowCount,gridProperties.frozenColumnCount'}},
      {'repeatCell':{'range':{'sheetId':sid},'cell':{'userEnteredFormat':{'backgroundColor':{'red':1,'green':1,'blue':1},'textFormat':{'bold':False}}},'fields':'userEnteredFormat(backgroundColor,textFormat.bold)'}},
      {'repeatCell':{'range':{'sheetId':sid,'startRowIndex':0,'endRowIndex':1,'startColumnIndex':0,'endColumnIndex':end_col},'cell':{'userEnteredFormat':{'backgroundColor':{'red':0.15,'green':0.25,'blue':0.38},'textFormat':{'bold':True,'foregroundColor':{'red':1,'green':1,'blue':1}},'wrapStrategy':'WRAP'}},'fields':'userEnteredFormat'}},
      {'repeatCell':{'range':{'sheetId':sid,'startRowIndex':1,'endRowIndex':2,'startColumnIndex':0,'endColumnIndex':end_col},'cell':{'userEnteredFormat':{'backgroundColor':{'red':0.92,'green':0.92,'blue':0.92},'textFormat':{'italic':True,'fontSize':8},'wrapStrategy':'WRAP'}},'fields':'userEnteredFormat'}},
      {'setBasicFilter':{'filter':{'range':{'sheetId':sid,'startRowIndex':0,'endRowIndex':len(values),'startColumnIndex':0,'endColumnIndex':end_col}}}},
      {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':2,'endRowIndex':len(values),'startColumnIndex':first_input,'endColumnIndex':last_input+1}],'booleanRule':{'condition':{'type':'BLANK'},'format':{'backgroundColor':{'red':1,'green':0.91,'blue':0.55}}}},'index':0}},
      {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':2,'endRowIndex':len(values),'startColumnIndex':first_input,'endColumnIndex':last_input+1}],'booleanRule':{'condition':{'type':'NOT_BLANK'},'format':{'backgroundColor':{'red':0.80,'green':0.94,'blue':0.82}}}},'index':0}},
      {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':2,'endRowIndex':len(values),'startColumnIndex':status_col,'endColumnIndex':status_col+1}],'booleanRule':{'condition':{'type':'TEXT_EQ','values':[{'userEnteredValue':'MANUAL INPUT REQUIRED'}]},'format':{'backgroundColor':{'red':1,'green':0.79,'blue':0.48}}}},'index':0}},
      {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':2,'endRowIndex':len(values),'startColumnIndex':status_col,'endColumnIndex':status_col+1}],'booleanRule':{'condition':{'type':'TEXT_EQ','values':[{'userEnteredValue':'READY FOR MODIFICATION PREFLIGHT'}]},'format':{'backgroundColor':{'red':0.70,'green':0.90,'blue':0.72}}}},'index':0}},
      {'updateDimensionProperties':{'range':{'sheetId':sid,'dimension':'COLUMNS','startIndex':0,'endIndex':end_col},'properties':{'pixelSize':155},'fields':'pixelSize'}},
    ]
    _batch(s,reqs); _clear(s,"'PHH Packaging'!A1:O500"); _put(s,"'PHH Packaging'!A1",values)
    formula_rows=[]; first=_col(first_input); last=_col(last_input); missletter=_col(miss_col)
    for rownum in range(3,3+len(products)):
        formula_rows.append({'values':[{'userEnteredValue':{'formulaValue':f'=COUNTBLANK({first}{rownum}:{last}{rownum})'}},{'userEnteredValue':{'formulaValue':f'=IF({missletter}{rownum}=0,"READY FOR MODIFICATION PREFLIGHT","MANUAL INPUT REQUIRED")'}}]})
    _batch(s,[{'updateCells':{'start':{'sheetId':sid,'rowIndex':2,'columnIndex':miss_col},'rows':formula_rows,'fields':'userEnteredValue'}}])

    # Dashboard with dynamic ready counts for each category plus packaging readiness.
    dash='PHH Manual Input'; dsid=sheets[dash]
    dashboard=[['PHH Manual Input Dashboard','','','','',''],['Category ID','Input Sheet','Products','Required Attributes','Ready for Attribute Preflight','Manual Input Remaining']]
    bycat=OrderedDict()
    for p in products: bycat.setdefault(p['category_id'],{'name':p['category_name'],'count':0}); bycat[p['category_id']]['count']+=1
    formula_specs=[]
    for idx,(cid,info) in enumerate(bycat.items(),start=3):
        st=CATEGORY_TITLES[cid]
        # field count from row 2: all technical ids between SKU/EAN and final two cols; query DB for exact count.
        with psycopg.connect(os.environ['DATABASE_URL']) as c:
            with c.cursor() as cur:
                cur.execute('select count(distinct field_id) from phh_manual_input_rows_v11 where category_id=%s',(cid,)); field_count=int(cur.fetchone()[0])
        status_letter=_col(2+field_count+1)
        dashboard.append([cid,st,info['count'],field_count,'',''])
        formula_specs.append((idx,st,status_letter,info['count']))
    dashboard.append(['PACKAGING','PHH Packaging',len(products),len(PACK_FIELDS),'',''])
    pack_row=len(dashboard); pack_status_letter=_col(status_col)
    _clear(s,"'PHH Manual Input'!A1:H100"); _put(s,"'PHH Manual Input'!A1",dashboard)
    fr=[]
    for rownum,st,status_letter,count in formula_specs:
        fr.append({'updateCells':{'start':{'sheetId':dsid,'rowIndex':rownum-1,'columnIndex':4},'rows':[{'values':[{'userEnteredValue':{'formulaValue':f'=COUNTIF(\'{st}\'!{status_letter}3:{status_letter},"READY FOR ATTRIBUTE PREFLIGHT")'}},{'userEnteredValue':{'formulaValue':f'=C{rownum}-E{rownum}'}}]}],'fields':'userEnteredValue'}})
    fr.append({'updateCells':{'start':{'sheetId':dsid,'rowIndex':pack_row-1,'columnIndex':4},'rows':[{'values':[{'userEnteredValue':{'formulaValue':f'=COUNTIF(\'PHH Packaging\'!{pack_status_letter}3:{pack_status_letter},"READY FOR MODIFICATION PREFLIGHT")'}},{'userEnteredValue':{'formulaValue':f'=C{pack_row}-E{pack_row}'}}]}],'fields':'userEnteredValue'}})
    fr += [
      {'repeatCell':{'range':{'sheetId':dsid,'startRowIndex':0,'endRowIndex':1,'startColumnIndex':0,'endColumnIndex':6},'cell':{'userEnteredFormat':{'backgroundColor':{'red':0.15,'green':0.25,'blue':0.38},'textFormat':{'bold':True,'foregroundColor':{'red':1,'green':1,'blue':1}}}},'fields':'userEnteredFormat'}},
      {'repeatCell':{'range':{'sheetId':dsid,'startRowIndex':1,'endRowIndex':2,'startColumnIndex':0,'endColumnIndex':6},'cell':{'userEnteredFormat':{'backgroundColor':{'red':0.85,'green':0.89,'blue':0.94},'textFormat':{'bold':True},'wrapStrategy':'WRAP'}},'fields':'userEnteredFormat'}},
      {'updateDimensionProperties':{'range':{'sheetId':dsid,'dimension':'COLUMNS','startIndex':0,'endIndex':6},'properties':{'pixelSize':180},'fields':'pixelSize'}},
    ]
    _batch(s,fr)

    # Append explicit package-contract guidance without guessing units.
    guide_rows=[
      ['Packaging fields','PHH OpenAPI ModificationImportRequest requires Package Weight, Length, Width, Height and Tare Deposit Quantity on create.','','','',''],
      ['Packaging units','The current PHH OpenAPI contract defines these as numeric fields but does not state their units. Do not guess or convert units until an official PHH source confirms them.','','','',''],
    ]
    _put(s,"'PHH Input Guide'!A11",guide_rows)
    out={'status':'PASS','products':len(products),'packaging_required_fields':len(PACK_FIELDS),'preserved_packaging_cells':preserved,'blank_packaging_cells':blanks,'dashboard_categories':len(bycat),'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','manual_workbook_writes':1}}
    print('PHH_PACKAGING_DASHBOARD_V11Y '+json.dumps(out,sort_keys=True),flush=True)
    return out
