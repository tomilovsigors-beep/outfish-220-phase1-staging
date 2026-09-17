from __future__ import annotations
import json, os
from collections import defaultdict
import psycopg
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
from phh_category_sheet_batches_v11u import friendly

SHEET_ID=os.getenv('PHH_MANUAL_INPUT_SHEET_ID','1139Zz6NXW_YdEBhUwGMFxuppR7QpUOPClM0qYhJvhPM')
MASTER_ID=os.getenv('MASTER_SHEET_ID','1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I')
BASE='https://sheets.googleapis.com/v4/spreadsheets'
CATEGORY_TITLES={
 '11810':'PHH 11810 Hammocks','17977':'PHH 17977 Trousers','19576':'PHH 19576 Shorts',
 '20523':'PHH 20523 Tights','433':'PHH 433 Tents','434':'PHH 434 Sleeping Bags',
 '4391':'PHH 4391 Glue','5669':'PHH 5669 Women Rubber Boots','9050':'PHH 9050 Men Jackets',
}

def _session():
    raw=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not raw: raise RuntimeError('GOOGLE_SERVICE_ACCOUNT_JSON missing')
    creds=service_account.Credentials.from_service_account_info(json.loads(raw),scopes=['https://www.googleapis.com/auth/spreadsheets'])
    return AuthorizedSession(creds)

def _rows():
    with psycopg.connect(os.environ['DATABASE_URL']) as c:
        with c.cursor() as cur:
            cur.execute('''select sku,ean,category_id,category_name,field_id,semantic,title_en,title_lv,title_lt,title_ee,title_fi,title_ru,source_status,source,candidate_value
                           from phh_manual_input_rows_v11 order by category_id,sku,ean,field_id''')
            cols=[d.name for d in cur.description]
            return [dict(zip(cols,r)) for r in cur.fetchall()]

def _value(v):
    # Only prefill independently structured exact values. Everything else remains for human confirmation/input.
    return str(v.get('candidate_value') or '') if v.get('source_status')=='STRUCTURED_EXACT' else ''

def _batch(s, requests):
    r=s.post(f'{BASE}/{SHEET_ID}:batchUpdate',json={'requests':requests},timeout=60)
    r.raise_for_status(); return r.json()

def _values(s, range_a1, values):
    r=s.put(f'{BASE}/{SHEET_ID}/values/{range_a1}',params={'valueInputOption':'RAW'},json={'values':values},timeout=60)
    r.raise_for_status(); return r.json()

def run():
    if SHEET_ID==MASTER_ID: raise RuntimeError('Refusing to write PHH manual workbook into Master spreadsheet')
    rows=_rows(); bycat=defaultdict(list)
    for r in rows: bycat[str(r['category_id'])].append(r)
    s=_session()
    meta=s.get(f'{BASE}/{SHEET_ID}',params={'fields':'sheets.properties'},timeout=30); meta.raise_for_status()
    sheets={x['properties']['title']:x['properties']['sheetId'] for x in meta.json().get('sheets',[])}
    requests=[]
    for cid in sorted(bycat):
        title=CATEGORY_TITLES.get(cid,f'PHH {cid}')
        if title not in sheets:
            requests.append({'addSheet':{'properties':{'title':title,'gridProperties':{'rowCount':300,'columnCount':40,'frozenRowCount':2,'frozenColumnCount':2}}}})
    if requests:
        _batch(s,requests)
        meta=s.get(f'{BASE}/{SHEET_ID}',params={'fields':'sheets.properties'},timeout=30); meta.raise_for_status()
        sheets={x['properties']['title']:x['properties']['sheetId'] for x in meta.json().get('sheets',[])}

    summary={}
    for cid,rs in sorted(bycat.items()):
        title=CATEGORY_TITLES.get(cid,f'PHH {cid}'); sid=sheets[title]
        fields=[]; seen=set(); products={}
        for r in rs:
            fid=str(r['field_id'])
            if fid not in seen:
                seen.add(fid); fields.append((fid,friendly(cid,r)))
            p=products.setdefault((r['sku'],r['ean']),{'sku':r['sku'],'ean':r['ean'],'vals':{}})
            p['vals'][fid]=r
        fields=sorted(fields,key=lambda x:(x[1].lower(),int(x[0])))
        headers=['SKU','EAN']+[label for _,label in fields]+['Missing Required','Input Status']
        tech=['','']+[f'PHH field_id {fid}' for fid,_ in fields]+['','']
        values=[headers,tech]
        exact=0; missing=0
        for p in products.values():
            vals=[]; miss=0
            for fid,_ in fields:
                cell=_value(p['vals'].get(fid,{}))
                if cell: exact+=1
                else: miss+=1; missing+=1
                vals.append(cell)
            status='READY FOR ATTRIBUTE PREFLIGHT' if miss==0 else 'MANUAL INPUT REQUIRED'
            values.append([p['sku'],p['ean']]+vals+[miss,status])
        # Clear old data/formatting and write the human input table.
        end_col=len(headers)
        reqs=[
          {'updateSheetProperties':{'properties':{'sheetId':sid,'gridProperties':{'frozenRowCount':2,'frozenColumnCount':2}},'fields':'gridProperties.frozenRowCount,gridProperties.frozenColumnCount'}},
          {'repeatCell':{'range':{'sheetId':sid},'cell':{'userEnteredFormat':{'backgroundColor':{'red':1,'green':1,'blue':1},'textFormat':{'bold':False,'foregroundColor':{'red':0,'green':0,'blue':0}}}},'fields':'userEnteredFormat(backgroundColor,textFormat)'}},
          {'repeatCell':{'range':{'sheetId':sid,'startRowIndex':0,'endRowIndex':1,'startColumnIndex':0,'endColumnIndex':end_col},'cell':{'userEnteredFormat':{'backgroundColor':{'red':0.15,'green':0.25,'blue':0.38},'textFormat':{'bold':True,'foregroundColor':{'red':1,'green':1,'blue':1}},'wrapStrategy':'WRAP'}},'fields':'userEnteredFormat'}},
          {'repeatCell':{'range':{'sheetId':sid,'startRowIndex':1,'endRowIndex':2,'startColumnIndex':0,'endColumnIndex':end_col},'cell':{'userEnteredFormat':{'backgroundColor':{'red':0.92,'green':0.92,'blue':0.92},'textFormat':{'italic':True,'fontSize':8},'wrapStrategy':'WRAP'}},'fields':'userEnteredFormat'}},
          {'setBasicFilter':{'filter':{'range':{'sheetId':sid,'startRowIndex':0,'endRowIndex':len(values),'startColumnIndex':0,'endColumnIndex':end_col}}}},
        ]
        # Conditional formatting: yellow = required and blank, green = populated, status orange/green.
        if fields:
            reqs += [
              {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':2,'endRowIndex':len(values),'startColumnIndex':2,'endColumnIndex':2+len(fields)}],'booleanRule':{'condition':{'type':'BLANK'},'format':{'backgroundColor':{'red':1,'green':0.91,'blue':0.55}}}},'index':0}},
              {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':2,'endRowIndex':len(values),'startColumnIndex':2,'endColumnIndex':2+len(fields)}],'booleanRule':{'condition':{'type':'NOT_BLANK'},'format':{'backgroundColor':{'red':0.80,'green':0.94,'blue':0.82}}}},'index':0}},
            ]
        status_col=end_col-1
        reqs += [
          {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':2,'endRowIndex':len(values),'startColumnIndex':status_col,'endColumnIndex':status_col+1}],'booleanRule':{'condition':{'type':'TEXT_EQ','values':[{'userEnteredValue':'MANUAL INPUT REQUIRED'}]},'format':{'backgroundColor':{'red':1,'green':0.79,'blue':0.48}}}},'index':0}},
          {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':2,'endRowIndex':len(values),'startColumnIndex':status_col,'endColumnIndex':status_col+1}],'booleanRule':{'condition':{'type':'TEXT_EQ','values':[{'userEnteredValue':'READY FOR ATTRIBUTE PREFLIGHT'}]},'format':{'backgroundColor':{'red':0.70,'green':0.90,'blue':0.72}}}},'index':0}},
          {'updateDimensionProperties':{'range':{'sheetId':sid,'dimension':'COLUMNS','startIndex':0,'endIndex':2},'properties':{'pixelSize':150},'fields':'pixelSize'}},
          {'updateDimensionProperties':{'range':{'sheetId':sid,'dimension':'COLUMNS','startIndex':2,'endIndex':end_col},'properties':{'pixelSize':145},'fields':'pixelSize'}},
        ]
        _batch(s,reqs)
        # Clear values first so stale values cannot survive a schema change.
        cr=s.post(f'{BASE}/{SHEET_ID}/values/{title}!A1:AN300:clear',json={},timeout=60); cr.raise_for_status()
        _values(s,f"'{title}'!A1",values)
        summary[cid]={'sheet':title,'products':len(products),'required_fields':len(fields),'prefilled_exact_cells':exact,'manual_blank_cells':missing}

    guide=[
      ['PHH Manual Input Workbook','','','','',''],
      ['Purpose','Fill only highlighted required cells. Technical PHH IDs are kept in hidden-style row 2; visible column names are English.','','','',''],
      ['Yellow cells','Required value is missing and must be entered or confirmed by a person.','','','',''],
      ['Green cells','Value already exists from a structured source or has been filled manually.','','','',''],
      ['Orange status','One or more required values are still missing.','','','',''],
      ['Input language','Use English canonical values unless PHH explicitly requires a country/language-specific value.','','','',''],
      ['Numbers','Enter numbers only when the column name states the unit; do not append unit text unless specifically requested.','','','',''],
      ['Safety','This workbook is an input/preflight workspace. It does not publish to PHH and does not write to 220 Master.','','','',''],
    ]
    _values(s,"'PHH Input Guide'!A1",guide)
    print('PHH_MANUAL_INPUT_SHEET_WRITER_V11W '+json.dumps({'status':'PASS','spreadsheet_id':SHEET_ID,'categories':summary,'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','manual_workbook_writes':1}},ensure_ascii=False,sort_keys=True),flush=True)
    return summary
