from __future__ import annotations

SKU='68150'
EAN13='0021563681505'
UPC12='021563681505'
CATEGORY_ID=3377
CN8='63029390'
ORIGIN_COUNTRY='China'

TITLES={
 'lt':'Greitai džiūstantis mikropluošto rankšluostis GearAid Cobalt Blue M 51 x 102 cm',
 'lv':'Ātri žūstošs mikrošķiedras dvielis GearAid Cobalt Blue M 51 x 102 cm',
 'ee':'Kiiresti kuivav mikrokiust rätik GearAid Cobalt Blue M 51 x 102 cm',
 'ru':'Быстросохнущее полотенце из микрофибры GearAid Cobalt Blue M 51 x 102 см',
 'fi':'Nopeasti kuivuva mikrokuitupyyhe GearAid Cobalt Blue M 51 x 102 cm',
}

DESCRIPTIONS={
 'lt':f"<h2>{TITLES['lt']}</h2><p>Lengvas ir kompaktiškas mikropluošto rankšluostis kelionėms, žygiams, vandens sportui ir stovyklavimui. Mikropluoštas gerai sugeria drėgmę ir greitai džiūsta. Rankšluostį lengva suspausti ir nešiotis kuprinėje.</p><ul><li>Medžiaga: mikropluoštas, 100% poliesteris.</li><li>Dydis: 51 x 102 cm.</li><li>Spalva: Cobalt Blue.</li><li>Rankšluosčio svoris: apie 100 g.</li><li>Komplekte: 1 rankšluostis ir laikymo dėklas.</li><li>SKU: {SKU}</li><li>EAN: {EAN13}</li><li>Product code: {SKU}</li></ul>",
 'lv':f"<h2>{TITLES['lv']}</h2><p>Viegls un kompakts mikrošķiedras dvielis ceļošanai, pārgājieniem, ūdens sportam un kempingam. Mikrošķiedra labi uzsūc mitrumu un ātri žūst. Dvielis ir viegli saspiežams un ērti ievietojams mugursomā.</p><ul><li>Materiāls: mikrošķiedra, 100% poliesters.</li><li>Izmērs: 51 x 102 cm.</li><li>Krāsa: Cobalt Blue.</li><li>Dvieļa svars: apmēram 100 g.</li><li>Komplektā: 1 dvielis un uzglabāšanas soma.</li><li>SKU: {SKU}</li><li>EAN: {EAN13}</li><li>Product code: {SKU}</li></ul>",
 'ee':f"<h2>{TITLES['ee']}</h2><p>Kerge ja kompaktne mikrokiust rätik reisimiseks, matkamiseks, veespordiks ja telkimiseks. Mikrokiud imab hästi niiskust ja kuivab kiiresti. Rätikut on lihtne kokku pakkida ja seljakotis kaasas kanda.</p><ul><li>Materjal: mikrokiud, 100% polüester.</li><li>Mõõt: 51 x 102 cm.</li><li>Värv: Cobalt Blue.</li><li>Rätiku kaal: umbes 100 g.</li><li>Komplektis: 1 rätik ja hoiukott.</li><li>SKU: {SKU}</li><li>EAN: {EAN13}</li><li>Product code: {SKU}</li></ul>",
 'ru':f"<h2>{TITLES['ru']}</h2><p>Лёгкое и компактное полотенце из микрофибры для путешествий, походов, водного спорта и кемпинга. Микрофибра хорошо впитывает влагу и быстро сохнет. Полотенце компактно складывается и удобно для переноски в рюкзаке.</p><ul><li>Материал: микрофибра, 100% полиэстер.</li><li>Размер: 51 x 102 см.</li><li>Цвет: Cobalt Blue.</li><li>Вес полотенца: около 100 г.</li><li>В комплекте: 1 полотенце и сумка для хранения.</li><li>SKU: {SKU}</li><li>EAN: {EAN13}</li><li>Product code: {SKU}</li></ul>",
 'fi':f"<h2>{TITLES['fi']}</h2><p>Kevyt ja kompakti mikrokuitupyyhe matkailuun, retkeilyyn, vesiurheiluun ja leirintään. Mikrokuitu imee kosteutta tehokkaasti ja kuivuu nopeasti. Pyyhe pakkautuu pieneen tilaan ja kulkee helposti repussa.</p><ul><li>Materiaali: mikrokuitu, 100% polyesteri.</li><li>Koko: 51 x 102 cm.</li><li>Väri: Cobalt Blue.</li><li>Pyyhkeen paino: noin 100 g.</li><li>Pakkauksessa: 1 pyyhe ja säilytyspussi.</li><li>SKU: {SKU}</li><li>EAN: {EAN13}</li><li>Product code: {SKU}</li></ul>",
}

IMAGES=[
 'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/Untitleddesign-2026-01-26T122610.754.jpg?v=1769423223',
 'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/Untitleddesign-2026-01-26T134940.876.jpg?v=1769428223',
 'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/atri-zustoss-mikroskiedras-dvielis-2.png?v=1769428223',
]

PRODUCT_FEATURES=[
 {'name':'Materiāls','value':'Mikrošķiedra'},
 {'name':'Krāsa','value':'Zila'},
 {'name':'Izmērs','value':'51x102 cm'},
 {'name':'Komplektā','value':'1 gab.'},
 {'name':'Dvieļu tips','value':'Sporta'},
]

MANUFACTURER={
 'manufacturer_name':'GEAR AID Inc.',
 'manufacturer_email':'info@gearaid.com',
 'manufacturer_address':'1411 Meador Ave, Bellingham, WA 98229, USA',
 'representative_name':'GEAR AID Europe GmbH',
 'representative_email':'info@gearaid.eu',
 'representative_address':'Kirchplatz 5, 29664 Walsrode, Germany',
}

PACKAGE={'package_length':0.62,'package_width':0.36,'package_height':0.07,'package_weight':0.30}

def preflight():
    errors=[]
    if CATEGORY_ID != 3377: errors.append('CATEGORY')
    if len(IMAGES) < 2: errors.append('IMAGES_LT_2')
    if set(TITLES) != {'lt','lv','ee','ru','fi'} or not all(TITLES.values()): errors.append('TITLE_LOCALES')
    if set(DESCRIPTIONS) != {'lt','lv','ee','ru','fi'} or not all(DESCRIPTIONS.values()): errors.append('DESCRIPTION_LOCALES')
    for _lang,_html in DESCRIPTIONS.items():
        for _required in (f'SKU: {SKU}', f'EAN: {EAN13}', f'Product code: {SKU}'):
            if _required not in _html: errors.append(f'{_lang.upper()}_IDENTITY_FIELDS_MISSING')
    if len(PRODUCT_FEATURES) != 5: errors.append('FEATURE_COUNT')
    if not CN8 or len(CN8) != 8: errors.append('CN8')
    if not ORIGIN_COUNTRY: errors.append('ORIGIN_COUNTRY')
    if min(PACKAGE.values()) <= 0: errors.append('PACKAGE')
    if not all(MANUFACTURER.values()): errors.append('MANUFACTURER_OR_REPRESENTATIVE')
    return {
      'status':'PASS' if not errors else 'BLOCKED',
      'marketplace_writes':0,
      'target_products':1,
      'sku':SKU,
      'ean13':EAN13,
      'upc12':UPC12,
      'category_id':CATEGORY_ID,
      'cn8':CN8,
      'origin_country':ORIGIN_COUNTRY,
      'languages':['lt','lv','ee','ru','fi'],
      'image_count':len(IMAGES),
      'required_features':PRODUCT_FEATURES,
      'package':PACKAGE,
      'manufacturer':MANUFACTURER,
      'errors':errors,
      'safety':{'product_creates':0,'stock_changes':0,'price_changes':0,'shopify_writes':0,'master_writes':0},
    }
