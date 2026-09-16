from __future__ import annotations
import csv, io, json, os, threading, time
from pathlib import Path
import requests
from flask import Flask, Response
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
from generator import build

app=Flask(__name__)
MASTER_SHEET_ID=os.getenv('MASTER_SHEET_ID','1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I')
MASTER_GID=os.getenv('MASTER_GID','837961277')
ROOT=Path(__file__).resolve().parent
STATE_LOCK=threading.RLock()
STATE={'status':'starting','error':None,'last_refresh':None,'artifacts':{},'validation':{}}
TOKEN_LOCK=threading.RLock(); TOKEN_EXPIRES=0.0


def _json(obj,status=200):
    return Response(json.dumps(obj,indent=2,sort_keys=True),status=status,mimetype='application/json')


def _master_rows():
    url=os.getenv('MASTER_CSV_URL') or f'https://docs.google.com/spreadsheets/d/{MASTER_SHEET_ID}/export?format=csv&gid={MASTER_GID}'
    sa=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not sa: raise RuntimeError('GOOGLE_SERVICE_ACCOUNT_JSON missing')
    creds=service_account.Credentials.from_service_account_info(json.loads(sa),scopes=['https://www.googleapis.com/auth/drive.readonly','https://www.googleapis.com/auth/spreadsheets.readonly'])
    r=AuthorizedSession(creds).get(url,timeout=45); r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.content.decode('utf-8-sig'))))


def _shopify_token():
    global TOKEN_EXPIRES
    cid=os.getenv('SHOPIFY_CLIENT_ID'); secret=os.getenv('SHOPIFY_CLIENT_SECRET')
    if not cid or not secret: raise RuntimeError('SHOPIFY_CLIENT_ID/SHOPIFY_CLIENT_SECRET missing')
    with TOKEN_LOCK:
        token=os.getenv('_PRODUCT_XML_SHOPIFY_ACCESS_TOKEN')
        if token and time.time()<TOKEN_EXPIRES-60: return token
        shop=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
        r=requests.post(f'https://{shop}/admin/oauth/access_token',headers={'Content-Type':'application/x-www-form-urlencoded'},data={'grant_type':'client_credentials','client_id':cid,'client_secret':secret},timeout=30)
        r.raise_for_status(); p=r.json(); token=p.get('access_token'); expires=int(p.get('expires_in') or 0)
        if not token or expires<=0: raise RuntimeError('Shopify token response invalid')
        os.environ['_PRODUCT_XML_SHOPIFY_ACCESS_TOKEN']=token; TOKEN_EXPIRES=time.time()+expires
        return token


def _shopify_products(master_rows):
    ids=sorted({str(r.get('shopify_product_id') or '').strip() for r in master_rows if str(r.get('shopify_product_id') or '').strip() and str(r.get('shopify_variant_id') or '').strip()})
    token=_shopify_token(); shop=os.getenv('SHOPIFY_SHOP_DOMAIN','153ac6-2.myshopify.com').strip()
    query='''query ProductXmlSourceV2($ids:[ID!]!){nodes(ids:$ids){... on Product{id title description vendor productType status category{id fullName} media(first:20){nodes{... on MediaImage{mimeType image{url width height}}}} variants(first:100){nodes{id title selectedOptions{name value} inventoryItem{measurement{weight{value unit}}}}}}}}'''
    out={}
    for i in range(0,len(ids),50):
        batch=ids[i:i+50]
        r=requests.post(f'https://{shop}/admin/api/2026-07/graphql.json',headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},json={'query':query,'variables':{'ids':batch}},timeout=90)
        r.raise_for_status(); payload=r.json()
        if payload.get('errors'): raise RuntimeError('Shopify GraphQL errors: '+json.dumps(payload['errors'])[:1500])
        nodes=(payload.get('data') or {}).get('nodes') or []
        if len(nodes)!=len(batch): raise RuntimeError('Shopify product source batch incomplete')
        for expected,node in zip(batch,nodes):
            if not node or node.get('id')!=expected: raise RuntimeError(f'Shopify product missing/mismatch {expected}')
            cat=node.get('category') or {}
            images=[]
            for media in (node.get('media') or {}).get('nodes') or []:
                image=(media or {}).get('image') or {}
                url=str(image.get('url') or '')
                if not url: continue
                images.append({'url':url,'mime_type':media.get('mimeType') or '','width':image.get('width'),'height':image.get('height')})
            variants={}
            for v in (node.get('variants') or {}).get('nodes') or []:
                weight=(((v.get('inventoryItem') or {}).get('measurement') or {}).get('weight') or {})
                variants[v['id']]={'id':v['id'],'title':v.get('title') or '','selected_options':v.get('selectedOptions') or [],'weight':{'value':weight.get('value'),'unit':weight.get('unit')} if weight else {}}
            out[expected]={
                'title':node.get('title') or '',
                'description':node.get('description') or '',
                'vendor':node.get('vendor') or '',
                'product_type':node.get('productType') or '',
                'status':node.get('status') or '',
                'category_id':cat.get('id') or '',
                'category_name':cat.get('fullName') or '',
                'main_image_url':images[0]['url'] if images else '',
                'images':images,
                'variants_by_id':variants,
                # These two PHH image checks are intentionally fail-closed until separately verified.
                'images_direct_no_redirect_verified':False,
                'main_background_verified':False,
            }
    return out


def _load_json(name):
    p=ROOT/name
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}


def refresh():
    try:
        master=_master_rows()
        shopify=_shopify_products(master)
        category_mapping=_load_json('phh-category-mapping.json')
        category_fields=_load_json('phh-category-fields.json')
        artifacts=build(master,shopify,category_mapping,category_fields)
        validation=json.loads(artifacts['product-xml-validation.json'].decode())
        with STATE_LOCK:
            STATE.update(status='ok' if validation.get('publish_gate')=='PASS' else 'blocked',error=None,last_refresh=time.time(),artifacts=artifacts,validation=validation)
        return validation
    except Exception as e:
        with STATE_LOCK: STATE.update(status='degraded',error=f'{type(e).__name__}: {e}')
        raise


def _artifact(name,mime):
    with STATE_LOCK: b=STATE['artifacts'].get(name); status=STATE['status']; err=STATE['error']
    if not b: return _json({'error':'no Product XML dry-run snapshot available','service_status':status,'detail':err},503)
    return Response(b,status=200,mimetype=mime,headers={'Cache-Control':'no-store'})

@app.get('/health')
def health():
    with STATE_LOCK: s={k:v for k,v in STATE.items() if k!='artifacts'}
    return _json(s,200 if s['status'] in {'ok','blocked'} else 503)

@app.post('/refresh')
def refresh_endpoint():
    try: return _json(refresh())
    except Exception: return health()

@app.get('/product-xml-validation.json')
def validation(): return _artifact('product-xml-validation.json','application/json')

@app.get('/product-xml-readiness.csv')
def readiness(): return _artifact('product-xml-readiness.csv','text/csv')

@app.get('/product-xml-blockers.csv')
def blockers(): return _artifact('product-xml-blockers.csv','text/csv')

@app.get('/product-xml-dry-run.xml')
def xml(): return _artifact('product-xml-dry-run.xml','application/xml')
