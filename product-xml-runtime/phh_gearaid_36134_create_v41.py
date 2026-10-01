from __future__ import annotations
import json, os, time
from urllib.parse import urljoin
import requests
from pmp_api_probe import BASE, _api_login, _api_get

# Single-product pilot only. Do not generalize this file to batches.
SKU = '36134-010'
EAN13 = '0021563361346'
UPC12 = '021563361346'
CATEGORY_ID = 3710
SELLER_ID = '9990696'

TITLES = {
  'lt': 'Kvapų neutralizatorius GearAid Revivex Odor Eliminator 250 ml',
  'lv': 'Smaku neitralizators GearAid Revivex Odor Eliminator 250 ml',
  'ee': 'Lõhnaeemaldaja GearAid Revivex Odor Eliminator 250 ml',
  'ru': 'Нейтрализатор запаха GearAid Revivex Odor Eliminator 250 мл',
  'fi': 'Hajunpoistaja GearAid Revivex Odor Eliminator 250 ml',
}

DESCRIPTIONS = {
  'lt': '<h2>'+TITLES['lt']+'</h2><p>Koncentruota, vandeniu aktyvuojama priemonė nemaloniems kvapams šalinti iš turistinės įrangos, avalynės ir drabužių. Mikroorganizmų pagrindu veikianti formulė skirta kvapą sukeliančioms organinėms liekanoms mažinti, o ne kvapui maskuoti.</p><ul><li>Tinka neoprenui, nailonui, poliesteriui, GORE-TEX, flisui, medvilnei ir kitai plaunamai įrangai.</li><li>Galima naudoti mirkant, purškiant arba skalbyklėje.</li><li>Talpa: 250 ml.</li><li>Prekės kodas: 36134-010.</li></ul>',
  'lv': '<h2>'+TITLES['lv']+'</h2><p>Koncentrēts, ar ūdeni aktivizējams līdzeklis nepatīkamu smaku mazināšanai tūrisma ekipējumā, apavos un apģērbā. Mikroorganismu formula iedarbojas uz smaku izraisošām organiskām atliekām, nevis tikai maskē smaku.</p><ul><li>Piemērots neoprēnam, neilonam, poliesteram, GORE-TEX, flīsam, kokvilnai un citam mazgājamam ekipējumam.</li><li>Var izmantot mērcējot, izsmidzinot vai veļas mašīnā.</li><li>Tilpums: 250 ml.</li><li>Preces kods: 36134-010.</li></ul>',
  'ee': '<h2>'+TITLES['ee']+'</h2><p>Kontsentreeritud veega aktiveeritav vahend ebameeldiva lõhna vähendamiseks matkavarustuses, jalatsites ja rõivastes. Mikroorganismidel põhinev koostis toimib lõhna põhjustavatele orgaanilistele jääkidele ega kata lõhna lihtsalt kinni.</p><ul><li>Sobib neopreenile, nailonile, polüestrile, GORE-TEXile, fliisile, puuvillale ja muule pestavale varustusele.</li><li>Võib kasutada leotades, pihustades või pesumasinas.</li><li>Maht: 250 ml.</li><li>Tootekood: 36134-010.</li></ul>',
  'ru': '<h2>'+TITLES['ru']+'</h2><p>Концентрированное средство, активируемое водой, для уменьшения неприятных запахов на туристическом снаряжении, обуви и одежде. Формула на основе микроорганизмов воздействует на органические остатки, вызывающие запах, а не маскирует его.</p><ul><li>Подходит для неопрена, нейлона, полиэстера, GORE-TEX, флиса, хлопка и другого моющегося снаряжения.</li><li>Можно применять замачиванием, распылением или в стиральной машине.</li><li>Объём: 250 мл.</li><li>Код товара: 36134-010.</li></ul>',
  'fi': '<h2>'+TITLES['fi']+'</h2><p>Tiivistetty vedellä aktivoituva hajunpoistoaine retkeilyvarusteille, jalkineille ja vaatteille. Mikro-organismeihin perustuva koostumus vaikuttaa hajua aiheuttaviin orgaanisiin jäämiin eikä vain peitä hajua.</p><ul><li>Sopii neopreenille, nailonille, polyesterille, GORE-TEXille, fleecelle, puuvillalle ja muille pestäville varusteille.</li><li>Voidaan käyttää liottamalla, suihkuttamalla tai pesukoneessa.</li><li>Tilavuus: 250 ml.</li><li>Tuotekoodi: 36134-010.</li></ul>',
}

# Gear Aid official ingredient list. Keep ingredient names un-translated.
COMPOSITION = 'Water; C9-11 Alcohols Ethoxylated; Acrylate Thickener; Dipropylene Glycol Methyl Ether; Dodecylbenzene Sulfonic Acid; Sodium Xylene Sulfonate; 2-undecoxyethanol; Bacillus Blend; Fragrance; Sodium Bicarbonate; Lauryl Glucoside; Triethanolamine'

USAGE = {
  'lt': 'Prieš naudojimą praskieskite vandeniu pagal gamintojo nurodymus. Priemonę galima naudoti mirkant, purškiant arba skalbyklėje. Po apdorojimo neskalaukite; leiskite gaminiui visiškai išdžiūti ore be papildomo karščio. Paruoštą tirpalą sunaudokite per 72 valandas.',
  'lv': 'Pirms lietošanas atšķaidiet ar ūdeni atbilstoši ražotāja norādījumiem. Līdzekli var izmantot mērcējot, izsmidzinot vai veļas mašīnā. Pēc apstrādes neskalojiet; ļaujiet izstrādājumam pilnībā nožūt gaisā bez papildu karstuma. Sagatavoto šķīdumu izmantojiet 72 stundu laikā.',
  'ee': 'Enne kasutamist lahjendage veega vastavalt tootja juhistele. Vahendit võib kasutada leotades, pihustades või pesumasinas. Pärast töötlemist ärge loputage; laske esemel täielikult õhu käes kuivada ilma lisakuumuseta. Kasutage valmissegu 72 tunni jooksul.',
  'ru': 'Перед применением разбавьте водой согласно инструкции производителя. Средство можно применять замачиванием, распылением или в стиральной машине. После обработки не ополаскивайте; дайте изделию полностью высохнуть на воздухе без дополнительного нагрева. Готовый раствор используйте в течение 72 часов.',
  'fi': 'Laimenna vedellä valmistajan ohjeiden mukaisesti ennen käyttöä. Ainetta voidaan käyttää liottamalla, suihkuttamalla tai pesukoneessa. Älä huuhtele käsittelyn jälkeen; anna tuotteen kuivua kokonaan ilmassa ilman lisälämpöä. Käytä valmis seos 72 tunnin kuluessa.',
}

IMAGES = [
 'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/36134-01036134-010.jpg?v=1773405488',
 'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/36134-01036134-010_1.jpg?v=1756984669',
 'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/36134-01036134-010_3.jpg?v=1756984669',
]

PAYLOAD = {
  'category_id': CATEGORY_ID,
  'title': TITLES['lt'], 'title_lv': TITLES['lv'], 'title_ee': TITLES['ee'], 'title_ru': TITLES['ru'], 'title_fi': TITLES['fi'],
  'long_description': DESCRIPTIONS['lt'], 'long_description_lv': DESCRIPTIONS['lv'], 'long_description_ee': DESCRIPTIONS['ee'], 'long_description_ru': DESCRIPTIONS['ru'], 'long_description_fi': DESCRIPTIONS['fi'],
  'composition': COMPOSITION, 'composition_lv': COMPOSITION, 'composition_ee': COMPOSITION, 'composition_ru': COMPOSITION, 'composition_fi': COMPOSITION,
  'usage_information': USAGE['lt'], 'usage_information_lv': USAGE['lv'], 'usage_information_ee': USAGE['ee'], 'usage_information_ru': USAGE['ru'], 'usage_information_fi': USAGE['fi'],
  'images': IMAGES,
  'manufacturer_name': 'GEAR AID Inc.',
  'manufacturer_email': 'info@gearaid.com',
  'manufacturer_address': '1411 Meador Ave, Bellingham, WA 98229, USA',
  'representative_name': 'GEAR AID Europe GmbH',
  'representative_email': 'sales@gearaid.eu',
  'representative_address': 'Kirchplatz 5, 29664 Walsrode, Germany',
  'product_features': [
    {'name': 'Preču zīme', 'value': 'Revivex'},
    {'name': 'Tips', 'value': 'Tīrīšanas līdzekļi'},
    {'name': 'Saderība', 'value': 'Tūristu inventāram'},
    {'name': 'Tilpums', 'value': '250'},
  ],
  'modifications': [{
    'sku': SKU,
    'manufacturer_code': SKU,
    'eans': [EAN13],
    'package_weight': 0.25,
    'package_length': 0.25,
    'package_width': 0.05,
    'package_height': 0.05,
    'tare_deposit_quantity': 0,
  }],
}

def _headers(token):
    return {
      'User-Agent':'outfish-phh-single-pilot-36134/41',
      'Accept':'application/json',
      'Content-Type':'application/json',
      'Authorization':'Pigu-mp '+token,
    }

def _scalars(x):
    out=[]
    if isinstance(x,dict):
        for v in x.values(): out.extend(_scalars(v))
    elif isinstance(x,list):
        for v in x: out.extend(_scalars(v))
    elif isinstance(x,(str,int,float)) and not isinstance(x,bool):
        out.append(str(x))
    return out

def _identity_guard(token, seller_id):
    # Check the PHH barcode lookup for both valid GTIN representations.
    barcode_reads={}
    for ean in (EAN13, UPC12):
        r=_api_get(f'/v3/products/product-modifications/barcodes?ean={ean}',token)
        try: body=r.json()
        except Exception: body={'raw':r.text[:1000]}
        barcode_reads[ean]={'http_status':r.status_code,'body':body}
        vals=set(_scalars(body))
        if SKU in vals or EAN13 in vals or UPC12 in vals:
            return False, {'reason':'BARCODE_ALREADY_EXISTS','barcode_reads':barcode_reads}

    # Also scan this seller's offers because a product may already exist under either barcode.
    offset=0; limit=100; scanned=0
    while offset < 20000:
        r=_api_get(f'/v2/sellers/{seller_id}/offers?app_name=220.lv&limit={limit}&offset={offset}',token)
        r.raise_for_status()
        d=r.json()
        items=(d.get('offers') if isinstance(d,dict) else None) or (d.get('items') if isinstance(d,dict) else None) or (d if isinstance(d,list) else [])
        if not items: break
        scanned += len(items)
        for it in items:
            vals=set(_scalars(it))
            if SKU in vals or EAN13 in vals or UPC12 in vals:
                return False, {'reason':'SELLER_IDENTITY_ALREADY_EXISTS','offers_scanned':scanned,'barcode_reads':barcode_reads}
        if len(items)<limit: break
        offset += len(items)
    return True, {'offers_scanned':scanned,'barcode_reads':barcode_reads}

def preflight():
    # Local deterministic gates. This does not contact PHH.
    errors=[]
    if CATEGORY_ID != 3710: errors.append('CATEGORY_ID')
    if len(IMAGES) < 2: errors.append('IMAGES_LT_2')
    if not all(TITLES.values()): errors.append('MISSING_TITLE_LOCALE')
    if not all(DESCRIPTIONS.values()): errors.append('MISSING_DESCRIPTION_LOCALE')
    if not all(USAGE.values()): errors.append('MISSING_USAGE_LOCALE')
    if not COMPOSITION: errors.append('MISSING_COMPOSITION')
    m=PAYLOAD['modifications'][0]
    if min(float(m[k]) for k in ('package_weight','package_length','package_width','package_height')) <= 0:
        errors.append('PACKAGE_DATA')
    return {
      'status':'PASS' if not errors else 'BLOCKED',
      'target_products':1,
      'sku':SKU,'ean13':EAN13,'upc12':UPC12,'category_id':CATEGORY_ID,
      'image_count':len(IMAGES),'languages':['lt','lv','ee','ru','fi'],
      'feature_count':len(PAYLOAD['product_features']),
      'package':{k:m[k] for k in ('package_weight','package_length','package_width','package_height')},
      'errors':errors,
      'marketplace_writes':0,
    }

def run():
    local=preflight()
    if local['status']!='PASS':
        return local
    if os.getenv('RUN_GEARAID_36134_CREATE','').strip()!='APPROVED_ONCE':
        out=dict(local); out['status']='READY_NOT_AUTHORIZED'; return out

    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('PHH login failed')
    token=lr.json().get('token')
    me=_api_get('/v3/sellers/me',token); me.raise_for_status(); md=me.json()
    seller_id=str(md.get('id') or (md.get('seller') or {}).get('id') or '')
    if seller_id != SELLER_ID:
        raise RuntimeError(f'unexpected seller id {seller_id}')

    safe,identity=_identity_guard(token,seller_id)
    if not safe:
        out=dict(local); out.update({'status':'ABORT_IDENTITY_EXISTS','identity':identity}); return out

    er=requests.post(urljoin(BASE,f'/v3/sellers/{seller_id}/product/import/execution'),headers=_headers(token),timeout=30)
    try: ej=er.json()
    except Exception: ej={'raw':er.text[:1000]}
    if er.status_code!=201 or not isinstance(ej,dict) or not ej.get('id'):
        raise RuntimeError(f'execution create failed HTTP {er.status_code}: {str(ej)[:1500]}')
    execution_id=str(ej['id'])

    pr=requests.post(urljoin(BASE,f'/v3/sellers/product/import/execution/{execution_id}'),headers=_headers(token),json=PAYLOAD,timeout=60)
    try: pj=pr.json()
    except Exception: pj={'raw':pr.text[:2000]}
    out={
      'status':'SUBMITTED' if pr.status_code==200 else 'VALIDATION_ERROR',
      'target_products':1,'sku':SKU,'ean13':EAN13,'upc12':UPC12,'category_id':CATEGORY_ID,
      'execution_id':execution_id,'execution_http_status':er.status_code,
      'product_post_http_status':pr.status_code,'product_post_response':pj,
      'identity_preflight':identity,
      'safety':{'target_products':1,'other_product_writes':0,'stock_changes':0,'price_changes':0,'Master_writes':0,'Shopify_writes':0},
    }
    if pr.status_code==200:
        for _ in range(15):
            time.sleep(3)
            rr=_api_get(f'/v3/sellers/{seller_id}/product/import/execution/{execution_id}/results?limit=20&offset=0',token)
            try:rj=rr.json()
            except Exception:rj={'raw':rr.text[:1000]}
            items=(rj.get('items') if isinstance(rj,dict) else None) or []
            target=[x for x in items if isinstance(x,dict) and str(x.get('sku') or '')==SKU]
            if target and target[0].get('status') in ('success','error'):
                out['result']=target[0]
                out['results_response']=rj
                out['status']=str(target[0].get('status')).upper()
                break
        if 'result' not in out:
            out['status']='PROCESSING'
    print('PHH_GEARAID_36134_CREATE_V41 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
