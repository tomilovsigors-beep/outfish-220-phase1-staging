from __future__ import annotations
import json, os, re
from collections import defaultdict
from urllib.parse import quote
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
    return str(v.get('candidate_value') or '') if v.get('source_status')=='STRUCTURED_EXACT' else ''

def _batch(s, requests):
    if not requests: return {}
    r=s.post(f'{BASE}/{SHEET_ID}:batchUpdate',json={'requests':requests},timeout=60)
    r.raise_for_status(); return r.json()

def _values(s, range_a1, values):
    encoded=quote(range_a1,safe="'!:")
    r=s.put(f'{BASE}/{SHEET_ID}/values/{encoded}',params={'valueInputOption':'RAW'},json={'values':values},timeout=60)
    r.raise_for_status(); return r.json()

def _get_values(s,title):
    rng=quote(f"'{title}'!A1:AN300",safe="'!:")
    r=s.get(f'{BASE}/{SHEET_ID}/values/{rng}',timeout=30)
    if r.status_code==400: return []
    r.raise_for_status(); return r.json().get('values') or []

def _existing_manual(values):
    if len(values)<2: return {}
    tech=values[1]
    field_cols={}
    for idx,val in enumerate(tech):
        m=re.fullmatch(r'PHH field_id\s+(\d+)',str(val).strip())
        if m: field_cols[idx]=m.group(1)
    out={}
    for row in values[2:]:
        if len(row)<2: continue
        sku=str(row[0] or '').strip(); ean=str(row[1] or '').strip()
        if not sku or not ean: continue
        vals={}
        for idx,fid in field_cols.items():
            if idx<len(row) and str(row[idx]).strip(): vals[fid]=str(row[idx]).strip()
        if vals: out[(sku,ean)]=vals
    return out

def _col(n0):
    n=n0+1; out=''
    while n:
        n,rem=divmod(n-1,26); out=chr(65+rem)+out
    return out

def _meta(s):
    r=s.get(f'{BASE}/{SHEET_ID}',params={'fields':'sheets(properties,conditionalFormats)'},timeout=30)
    r.raise_for_status(); return r.json()

def run():
    if SHEET_ID==MASTER_ID: raise RuntimeError('Refusing to write PHH manual workbook into Master spreadsheet')
    rows=_rows(); bycat=defaultdict(list)
    for r in rows: bycat[str(r['category_id'])].append(r)
    s=_session(); meta=_meta(s)
    sheets={x['properties']['title']:x['properties']['sheetId'] for x in meta.get('sheets',[])}
    requests=[]
    for cid in sorted(bycat):
        title=CATEGORY_TITLES.get(cid,f'PHH {cid}')
        if title not in sheets:
            requests.append({'addSheet':{'properties':{'title':title,'gridProperties':{'rowCount':300,'columnCount':40,'frozenRowCount':2,'frozenColumnCount':2}}}})
    if requests:
        _batch(s,requests); meta=_meta(s)
        sheets={x['properties']['title']:x['properties']['sheetId'] for x in meta.get('sheets',[])}
    cf_counts={x['properties']['sheetId']:len(x.get('conditionalFormats') or []) for x in meta.get('sheets',[])}

    summary={}; total_preserved=0
    for cid,rs in sorted(bycat.items()):
        title=CATEGORY_TITLES.get(cid,f'PHH {cid}'); sid=sheets[title]
        existing=_existing_manual(_get_values(s,title))
        fields=[]; seen=set(); products={}
        for r in rs:
            fid=str(r['field_id'])
            if fid not in seen:
                seen.add(fid); fields.append((fid,friendly(cid,r)))
            p=products.setdefault((str(r['sku']),str(r['ean'])),{'sku':str(r['sku']),'ean':str(r['ean']),'vals':{}})
            p['vals'][fid]=r
        fields=sorted(fields,key=lambda x:(x[1].lower(),int(x[0])))
        headers=['SKU','EAN']+[label for _,label in fields]+['Missing Required','Input Status']
        tech=['','']+[f'PHH field_id {fid}' for fid,_ in fields]+['','']
        values=[headers,tech]
        exact=0; preserved=0; blank=0
        for key,p in products.items():
            vals=[]
            old=existing.get(key,{})
            for fid,_ in fields:
                cell=_value(p['vals'].get(fid,{}))
                if cell:
                    exact+=1
                elif old.get(fid):
                    cell=old[fid]; preserved+=1; total_preserved+=1
                else:
                    blank+=1
                vals.append(cell)
            values.append([p['sku'],p['ean']]+vals+['',''])
        end_col=len(headers); status_col=end_col-1; miss_col=end_col-2
        reqs=[]
        for _ in range(cf_counts.get(sid,0)):
            reqs.append({'deleteConditionalFormatRule':{'sheetId':sid,'index':0}})
        reqs += [
          {'clearBasicFilter':{'sheetId':sid}},
          {'updateSheetProperties':{'properties':{'sheetId':sid,'gridProperties':{'frozenRowCount':2,'frozenColumnCount':2}},'fields':'gridProperties.frozenRowCount,gridProperties.frozenColumnCount'}},
          {'repeatCell':{'range':{'sheetId':sid},'cell':{'userEnteredFormat':{'backgroundColor':{'red':1,'green':1,'blue':1},'textFormat':{'bold':False,'foregroundColor':{'red':0,'green':0,'blue':0}}}},'fields':'userEnteredFormat(backgroundColor,textFormat)'}},
          {'repeatCell':{'range':{'sheetId':sid,'startRowIndex':0,'endRowIndex':1,'startColumnIndex':0,'endColumnIndex':end_col},'cell':{'userEnteredFormat':{'backgroundColor':{'red':0.15,'green':0.25,'blue':0.38},'textFormat':{'bold':True,'foregroundColor':{'red':1,'green':1,'blue':1}},'wrapStrategy':'WRAP'}},'fields':'userEnteredFormat'}},
          {'repeatCell':{'range':{'sheetId':sid,'startRowIndex':1,'endRowIndex':2,'startColumnIndex':0,'endColumnIndex':end_col},'cell':{'userEnteredFormat':{'backgroundColor':{'red':0.92,'green':0.92,'blue':0.92},'textFormat':{'italic':True,'fontSize':8},'wrapStrategy':'WRAP'}},'fields':'userEnteredFormat'}},
          {'setBasicFilter':{'filter':{'range':{'sheetId':sid,'startRowIndex':0,'endRowIndex':len(values),'startColumnIndex':0,'endColumnIndex':end_col}}}},
          {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':2,'endRowIndex':len(values),'startColumnIndex':2,'endColumnIndex':2+len(fields)}],'booleanRule':{'condition':{'type':'BLANK'},'format':{'backgroundColor':{'red':1,'green':0.91,'blue':0.55}}}},'index':0}},
          {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':2,'endRowIndex':len(values),'startColumnIndex':2,'endColumnIndex':2+len(fields)}],'booleanRule':{'condition':{'type':'NOT_BLANK'},'format':{'backgroundColor':{'red':0.80,'green':0.94,'blue':0.82}}}},'index':0}},
          {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':2,'endRowIndex':len(values),'startColumnIndex':status_col,'endColumnIndex':status_col+1}],'booleanRule':{'condition':{'type':'TEXT_EQ','values':[{'userEnteredValue':'MANUAL INPUT REQUIRED'}]},'format':{'backgroundColor':{'red':1,'green':0.79,'blue':0.48}}}},'index':0}},
          {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':2,'endRowIndex':len(values),'startColumnIndex':status_col,'endColumnIndex':status_col+1}],'booleanRule':{'condition':{'type':'TEXT_EQ','values':[{'userEnteredValue':'READY FOR ATTRIBUTE PREFLIGHT'}]},'format':{'backgroundColor':{'red':0.70,'green':0.90,'blue':0.72}}}},'index':0}},
          {'updateDimensionProperties':{'range':{'sheetId':sid,'dimension':'COLUMNS','startIndex':0,'endIndex':2},'properties':{'pixelSize':150},'fields':'pixelSize'}},
          {'updateDimensionProperties':{'range':{'sheetId':sid,'dimension':'COLUMNS','startIndex':2,'endIndex':end_col},'properties':{'pixelSize':145},'fields':'pixelSize'}},
        ]
        _batch(s,reqs)
        rng=quote(f"'{title}'!A1:AN300",safe="'!:")
        cr=s.post(f'{BASE}/{SHEET_ID}/values/{rng}:clear',json={},timeout=60); cr.raise_for_status()
        _values(s,f"'{title}'!A1",values)
        # Dynamic formulas recalculate immediately as the user fills highlighted cells.
        formula_rows=[]
        first_attr=_col(2); last_attr=_col(1+len(fields)); miss_letter=_col(miss_col)
        for sheet_row in range(3,3+len(products)):
            formula_rows.append({'values':[{'userEnteredValue':{'formulaValue':f'=COUNTBLANK({first_attr}{sheet_row}:{last_attr}{sheet_row})'}},{'userEnteredValue':{'formulaValue':f'=IF({miss_letter}{sheet_row}=0,"READY FOR ATTRIBUTE PREFLIGHT","MANUAL INPUT REQUIRED")'}}]})
        _batch(s,[{'updateCells':{'start':{'sheetId':sid,'rowIndex':2,'columnIndex':miss_col},'rows':formula_rows,'fields':'userEnteredValue'}}])
        summary[cid]={'sheet':title,'products':len(products),'required_fields':len(fields),'prefilled_exact_cells':exact,'preserved_manual_cells':preserved,'manual_blank_cells':blank}

    guide=[
      ['PHH Manual Input Workbook','','','','',''],
      ['Purpose','Fill only highlighted required cells. Visible column names are English; row 2 keeps the technical PHH field ID for the pipeline.','','','',''],
      ['Yellow cells','Required value is missing and must be entered or confirmed by a person.','','','',''],
      ['Green cells','Value already exists from a structured source or has been filled manually.','','','',''],
      ['Input Status','Updates automatically as required cells are filled. READY FOR ATTRIBUTE PREFLIGHT means this worksheet has no blank required attributes for that SKU.','','','',''],
      ['Input language','Use English canonical values unless PHH explicitly requires a country/language-specific value.','','','',''],
      ['Numbers','Enter a plain number only when the column explicitly states a unit. Do not invent or convert units.','','','',''],
      ['Preservation','Manual values are preserved by PHH field ID when the workbook is refreshed. Structured exact values remain authoritative for auto-prefill.','','','',''],
      ['Safety','This workbook is an input/preflight workspace. It does not publish to PHH and does not write to 220 Master.','','','',''],
    ]
    _values(s,"'PHH Input Guide'!A1",guide)
    print('PHH_MANUAL_INPUT_SHEET_WRITER_V11W '+json.dumps({'status':'PASS','spreadsheet_id':SHEET_ID,'categories':summary,'preserved_manual_cells_total':total_preserved,'safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','manual_workbook_writes':1}},ensure_ascii=False,sort_keys=True),flush=True)
    return summary
