from __future__ import annotations
import json, os, time
from urllib.parse import urljoin
import requests
from pmp_api_probe import BASE,_api_login,_api_get

SKU='CNK2450DS012G'
EAN='6975641883678'
SELLER_ID='9990696'
IMAGE='https://cdn.shopify.com/s/files/1/0771/7188/4370/files/Untitleddesign-2026-08-05T002348.997.jpg'
TITLE_LV='Trekinga nūja Naturehike Gale UL, 3K oglekļa šķiedra un 7075 alumīnijs, saliekama, 103–120 cm, tumši pelēka'
DESCRIPTION_LV=(
    'Ultraviegla saliekama Naturehike Gale UL trekinga nūja pārgājieniem un trekingam. '
    'Kāts izgatavots no 3K oglekļa šķiedras ar 7075 alumīnija sakausējuma pastiprinājumiem. '
    'Piecu sekciju Z-veida saliekamā konstrukcija; regulējams garums 103–120 cm. '
    'EVA rokturis ar regulējamu neilona siksnu. Volframa uzgalis. '
    'Komplektā ir divi maināmi uzgaļi, tostarp plats uzgalis sniegam un mīkstam segumam. '
    'Krāsa: Deep Space Gray. Modelis: CNK2450DS012.'
)
PAYLOAD={
  'category_id':4205,
  'title_lv':TITLE_LV,
  'long_description_lv':DESCRIPTION_LV,
  'images':[IMAGE],
  'product_features':[
    {'name':'Prekės ženklas','value':'Naturehike'},
    {'name':'Teleskopinė lazda','value':'Taip'},
    {'name':'Lazdos medžiaga','value':'3K anglies pluoštas, 7075 aliuminio lydinys'},
    {'name':'Rankenos medžiaga','value':'EVA'},
    {'name':'Sniego čiuptuvai','value':'Taip'},
    {'name':'Tipas','value':'Trekking'},
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

def headers(token):
    return {'User-Agent':'outfish-phh-naturehike-patch/15','Accept':'application/json','Content-Type':'application/json','Authorization':'Pigu-mp '+token}

def run():
    if os.getenv('RUN_NATUREHIKE_GALE_PATCH','').strip()!='APPROVED_ONCE':
        return {'status':'SKIPPED'}
    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('PHH login failed')
    token=lr.json().get('token')
    me=_api_get('/v3/sellers/me',token); me.raise_for_status(); md=me.json()
    seller_id=str(md.get('id') or (md.get('seller') or {}).get('id') or '')
    if seller_id!=SELLER_ID: raise RuntimeError(f'unexpected seller id {seller_id}')

    er=requests.post(urljoin(BASE,f'/v3/sellers/{seller_id}/product/import/execution'),headers=headers(token),timeout=30)
    ej=er.json() if er.content else {}
    if er.status_code!=201 or not ej.get('id'):
        raise RuntimeError(f'execution create failed HTTP {er.status_code}: {str(ej)[:1200]}')
    execution_id=str(ej['id'])

    pr=requests.patch(urljoin(BASE,f'/v3/sellers/product/import/execution/{execution_id}'),headers=headers(token),json=PAYLOAD,timeout=45)
    try: pj=pr.json()
    except Exception: pj={'raw':pr.text[:1500]}
    out={'status':'SUBMITTED' if pr.status_code==200 else 'VALIDATION_ERROR','execution_id':execution_id,
         'execution_http_status':er.status_code,'product_patch_http_status':pr.status_code,
         'product_patch_response':pj,'sku':SKU,'ean':EAN,'category_id':4205,
         'safety':{'stock_changes':0,'price_changes':0,'Master_writes':0,'Shopify_writes':0}}
    if pr.status_code==200:
        for _ in range(12):
            time.sleep(3)
            rr=_api_get(f'/v3/sellers/{seller_id}/product/import/execution/{execution_id}/results?limit=20&offset=0',token)
            rj=rr.json() if rr.content else {}
            items=(rj.get('items') if isinstance(rj,dict) else None) or []
            target=[x for x in items if isinstance(x,dict) and str(x.get('sku') or '')==SKU]
            if target and target[0].get('status') in ('success','error'):
                out['result']=target[0]; out['results_response']=rj; out['status']=str(target[0].get('status')).upper(); break
    print('PHH_NATUREHIKE_GALE_PATCH_V15 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
