from __future__ import annotations
import json, os, re
from collections import defaultdict, Counter
import psycopg
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

SHEET_ID=os.getenv('PHH_MANUAL_INPUT_SHEET_ID','1139Zz6NXW_YdEBhUwGMFxuppR7QpUOPClM0qYhJvhPM')
BASE='https://sheets.googleapis.com/v4/spreadsheets'
TITLE='PHH Family Defaults'
SAFE_TITLES={
 'brand','material','season','intended for','clothing model','hood','jacket length',
 'country of origin','type','style','outer material','inner material','sole material',
 'mosquito net','number of entrances','number of layers','number of persons',
 'number of vestibules','pockets','snow protection','ventilation','waterproofness',
 'filling','shape','glue type','aerosol volume','volume'
}
EXCLUDE_WORDS=('size','color','colour','weight','height','length','width','temperature','package','packed','unfolded','den','quantity')

def norm(s): return re.sub(r'\s+',' ',str(s or '').strip())
def family(sku):
    return re.sub(r'-(?:XS|S|M|L|XL|2XL|3XL|4XL|5XL|6XL|XXL|XXXL|XXXXL)$','',norm(sku),flags=re.I)
def safe_title(t):
    n=norm(t).casefold()
    if any(x in n for x in EXCLUDE_WORDS): return False
    return n in SAFE_TITLES

def session():
    raw=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    creds=service_account.Credentials.from_service_account_info(json.loads(raw),scopes=['https://www.googleapis.com/auth/spreadsheets'])
    return AuthorizedSession(creds)

def run():
    with psycopg.connect(os.environ['DATABASE_URL']) as c:
        with c.cursor() as cur:
            cur.execute("select payload from phh_category_assignment_verify_v12a_snapshots order by id desc limit 1")
            p=cur.fetchone()[0]
            approved={(str(r['sku']),str(r['ean'])) for r in p['detail'] if r['verification_status']=='PASS_OBJECT_MATCH'}
            cur.execute("""select sku,ean,category_id,category_name,field_id,coalesce(title_en,''),source_status,candidate_value
                           from phh_manual_input_rows_v11 order by category_id,sku,ean,field_id""")
            cols=[d.name for d in cur.description]
            rows=[dict(zip(cols,r)) for r in cur.fetchall()]
    rows=[r for r in rows if (str(r['sku']),str(r['ean'])) in approved and safe_title(r.get('title_en'))]
    groups=defaultdict(lambda:{'skus':set(),'fields':{}})
    for r in rows:
        key=(str(r['category_id']),family(r['sku']))
        g=groups[key]; g['skus'].add(str(r['sku']))
        f=g['fields'].setdefault(str(r['field_id']),{'title':norm(r['title_en']),'values':set(),'rows':0})
        f['rows']+=1
        if r.get('source_status')=='STRUCTURED_EXACT' and norm(r.get('candidate_value')):
            f['values'].add(norm(r['candidate_value']))
    matrix=[]
    all_fields=[]
    seen=set()
    for (cid,fam),g in sorted(groups.items()):
        if len(g['skus'])<2: continue
        for fid,f in g['fields'].items():
            k=(cid,fid,f['title'])
            if k not in seen: seen.add(k); all_fields.append(k)
    # Human-friendly long-form sheet: one row per family/field avoids hundreds of sparse columns.
    values=[['Category ID','SKU Family','Variant Count','PHH Field','PHH field_id','Family Default Value','Current Exact Evidence','Apply Policy','Status']]
    eligible=0; prefilled=0
    for (cid,fam),g in sorted(groups.items()):
        if len(g['skus'])<2: continue
        for fid,f in sorted(g['fields'].items(),key=lambda x:(x[1]['title'],int(x[0]))):
            vals=sorted(f['values'])
            evidence=' | '.join(vals)
            default=vals[0] if len(vals)==1 else ''
            status='EXACT FAMILY VALUE AVAILABLE' if len(vals)==1 else ('CONFLICT — DO NOT APPLY' if len(vals)>1 else 'MANUAL FAMILY DEFAULT NEEDED')
            policy='May apply to all variants in this SKU family after preflight; variant-sensitive fields are excluded from this sheet.'
            values.append([cid,fam,len(g['skus']),f['title'],fid,default,evidence,policy,status])
            eligible+=1
            if default: prefilled+=1
    s=session()
    meta=s.get(f'{BASE}/{SHEET_ID}',params={'fields':'sheets.properties'},timeout=30); meta.raise_for_status()
    sheets={x['properties']['title']:x['properties']['sheetId'] for x in meta.json().get('sheets',[])}
    if TITLE not in sheets:
        rr=s.post(f'{BASE}/{SHEET_ID}:batchUpdate',json={'requests':[{'addSheet':{'properties':{'title':TITLE,'index':3,'gridProperties':{'rowCount':1000,'columnCount':12,'frozenRowCount':1}}}}]},timeout=60); rr.raise_for_status()
        meta=s.get(f'{BASE}/{SHEET_ID}',params={'fields':'sheets.properties'},timeout=30); meta.raise_for_status()
        sheets={x['properties']['title']:x['properties']['sheetId'] for x in meta.json().get('sheets',[])}
    sid=sheets[TITLE]
    s.post(f"{BASE}/{SHEET_ID}/values/'{TITLE}'!A1:L1000:clear",json={},timeout=60).raise_for_status()
    s.put(f"{BASE}/{SHEET_ID}/values/'{TITLE}'!A1",params={'valueInputOption':'RAW'},json={'values':values},timeout=60).raise_for_status()
    reqs=[
      {'repeatCell':{'range':{'sheetId':sid,'startRowIndex':0,'endRowIndex':1,'startColumnIndex':0,'endColumnIndex':9},'cell':{'userEnteredFormat':{'backgroundColor':{'red':0.15,'green':0.25,'blue':0.38},'textFormat':{'bold':True,'foregroundColor':{'red':1,'green':1,'blue':1}},'wrapStrategy':'WRAP'}},'fields':'userEnteredFormat'}},
      {'setBasicFilter':{'filter':{'range':{'sheetId':sid,'startRowIndex':0,'endRowIndex':len(values),'startColumnIndex':0,'endColumnIndex':9}}}},
      {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':1,'endRowIndex':len(values),'startColumnIndex':5,'endColumnIndex':6}],'booleanRule':{'condition':{'type':'BLANK'},'format':{'backgroundColor':{'red':1,'green':0.91,'blue':0.55}}}},'index':0}},
      {'addConditionalFormatRule':{'rule':{'ranges':[{'sheetId':sid,'startRowIndex':1,'endRowIndex':len(values),'startColumnIndex':5,'endColumnIndex':6}],'booleanRule':{'condition':{'type':'NOT_BLANK'},'format':{'backgroundColor':{'red':0.80,'green':0.94,'blue':0.82}}}},'index':0}},
      {'updateDimensionProperties':{'range':{'sheetId':sid,'dimension':'COLUMNS','startIndex':0,'endIndex':9},'properties':{'pixelSize':170},'fields':'pixelSize'}}
    ]
    s.post(f'{BASE}/{SHEET_ID}:batchUpdate',json={'requests':reqs},timeout=60).raise_for_status()
    summary={'status':'PASS','validated_products':len(approved),'family_field_rows':eligible,'prefilled_family_defaults':prefilled,'manual_family_defaults':eligible-prefilled,'sheet':TITLE,'policy':'safe non-variant fields only; family-level defaults are staging inputs, not PHH writes','safety':{'Master_writes':0,'PHH_writes':0,'Shopify_writes':0,'EAN_writes':0,'Product_XML':'OFF','manual_workbook_writes':1}}
    print('PHH_FAMILY_DEFAULTS_V12E '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
    return summary
