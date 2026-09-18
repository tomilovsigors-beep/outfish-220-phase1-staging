from __future__ import annotations
import json, os, time
from urllib.parse import urljoin
import requests
from pmp_api_probe import BASE,_api_login,_api_get

SKU='CNK2450DS012G'
EAN='6975641883678'
SELLER_ID='9990696'

TITLE_LV='Trekinga nūja Naturehike Gale UL, 3K oglekļa šķiedra un 7075 alumīnijs, saliekama, 103–120 cm, tumši pelēka'
DESCRIPTION_LV=(
    'Ultraviegla saliekama Naturehike Gale UL trekinga nūja pārgājieniem un trekingam. '
    'Kāts izgatavots no 3K oglekļa šķiedras ar 7075 alumīnija sakausējuma pastiprinājumiem. '
    'Piecu sekciju Z-veida saliekamā konstrukcija; regulējams garums 103–120 cm. '
    'EVA rokturis ar regulējamu neilona siksnu. Volframa uzgalis. '
    'Komplektā ir divi maināmi uzgaļi, tostarp plats uzgalis sniegam un mīkstam segumam. '
    'Krāsa: Deep Space Gray. Modelis: CNK2450DS012.'
)
IMAGE='https://cdn.shopify.com/s/files/1/0771/7188/4370/files/Untitleddesign-2026-08-05T002348.997.jpg?v=1785878644'

PAYLOAD={
  'category_id':4205,
  'title_lv':TITLE_LV,
  'long_description_lv':DESCRIPTION_LV,
  'images':[IMAGE],
  'product_features':[
    {'name':'Preču zīme','value':'Naturehike'},
    {'name':'Saliekamas nūjas','value':'Jā'},
    {'name':'Nūjas materiāls','value':'3K oglekļa šķiedra, 7075 alumīnija sakausējums'},
    {'name':'Roktura materiāls','value':'EVA'},
    {'name':'Sniega uzgaļi','value':'Jā'},
    {'name':'Tips','value':'Trekinga nūja'},
  ],
  'modifications':[{
    'sku':SKU,
    'manufacturer_code':'CNK2450DS012',
    'eans':[EAN],
    'package_weight':1.0,
    'package_length':0.30,
    'package_width':0.08,
    'package_height':0.08,
    'tare_deposit_quantity':0,
  }]
}

def _headers(token):
    return {'User-Agent':'outfish-phh-naturehike-create/15','Accept':'application/json','Content-Type':'application/json','Authorization':'Pigu-mp '+token}

def _scalars(x):
    out=[]
    if isinstance(x,dict):
        for v in x.values(): out.extend(_scalars(v))
    elif isinstance(x,list):
        for v in x: out.extend(_scalars(v))
    elif isinstance(x,(str,int,float)) and not isinstance(x,bool):
        out.append(str(x))
    return out

def _identity_exists(token,seller_id):
    offset=0; limit=100; scanned=0
    while offset<20000:
        r=_api_get(f'/v2/sellers/{seller_id}/offers?app_name=220.lv&limit={limit}&offset={offset}',token)
        r.raise_for_status(); d=r.json()
        items=(d.get('offers') if isinstance(d,dict) else None) or (d.get('items') if isinstance(d,dict) else None) or (d if isinstance(d,list) else [])
        if not items: return False,scanned
        scanned += len(items)
        for it in items:
            vals=set(_scalars(it))
            if SKU in vals or EAN in vals:
                return True,scanned
        if len(items)<limit: return False,scanned
        offset += len(items)
    return False,scanned

def run():
    if os.getenv('RUN_NATUREHIKE_GALE_CREATE','').strip()!='APPROVED_ONCE':
        return {'status':'SKIPPED'}
    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('PHH login failed')
    token=lr.json().get('token')
    me=_api_get('/v3/sellers/me',token); me.raise_for_status(); md=me.json()
    seller_id=str(md.get('id') or (md.get('seller') or {}).get('id') or '')
    if seller_id!=SELLER_ID: raise RuntimeError(f'unexpected seller id {seller_id}')

    exists,scanned=_identity_exists(token,seller_id)
    if exists:
        out={'status':'ABORT_ALREADY_EXISTS','seller_id':seller_id,'sku':SKU,'ean':EAN,'offers_scanned':scanned,
             'safety':{'execution_created':0,'product_posts':0,'stock_changes':0,'price_changes':0}}
        print('PHH_NATUREHIKE_GALE_CREATE_V15 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
        return out

    er=requests.post(urljoin(BASE,f'/v3/sellers/{seller_id}/product/import/execution'),headers=_headers(token),timeout=30)
    try: ej=er.json()
    except Exception: ej={'raw':er.text[:1000]}
    if er.status_code!=201 or not isinstance(ej,dict) or not ej.get('id'):
        raise RuntimeError(f'execution create failed HTTP {er.status_code}: {str(ej)[:1500]}')
    execution_id=str(ej['id'])

    pr=requests.post(urljoin(BASE,f'/v3/sellers/product/import/execution/{execution_id}'),headers=_headers(token),json=PAYLOAD,timeout=45)
    try: pj=pr.json()
    except Exception: pj={'raw':pr.text[:1500]}

    out={
      'status':'SUBMITTED' if pr.status_code==200 else 'VALIDATION_ERROR',
      'seller_id':seller_id,'sku':SKU,'ean':EAN,'category_id':4205,'execution_id':execution_id,
      'execution_http_status':er.status_code,'product_post_http_status':pr.status_code,
      'product_post_response':pj,
      'payload_summary':{
        'title_lv':TITLE_LV,'image_count':1,'feature_count':len(PAYLOAD['product_features']),
        'manufacturer_code':'CNK2450DS012','package_weight':1.0,
        'package_length':0.30,'package_width':0.08,'package_height':0.08,'tare_deposit_quantity':0
      },
      'safety':{'stock_changes':0,'price_changes':0,'Master_writes':0,'Shopify_writes':0}
    }

    if pr.status_code==200:
        polls=[]
        for _ in range(12):
            time.sleep(3)
            rr=_api_get(f'/v3/sellers/{seller_id}/product/import/execution/{execution_id}/results?limit=20&offset=0',token)
            try: rj=rr.json()
            except Exception: rj={'raw':rr.text[:1000]}
            polls.append({'http_status':rr.status_code,'body':rj})
            items=(rj.get('items') if isinstance(rj,dict) else None) or []
            target=[x for x in items if isinstance(x,dict) and str(x.get('sku') or '')==SKU]
            if target and target[0].get('status') in ('success','error'):
                out['result']=target[0]
                out['results_response']=rj
                out['status']=str(target[0].get('status')).upper()
                break
        if 'result' not in out:
            out['status']='PROCESSING'
            out['last_results_response']=polls[-1]['body'] if polls else None

    print('PHH_NATUREHIKE_GALE_CREATE_V15 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
