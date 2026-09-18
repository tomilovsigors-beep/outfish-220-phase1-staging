from __future__ import annotations
import json, os, time
from urllib.parse import urljoin
import requests
from pmp_api_probe import BASE,_api_login,_api_get

SKU='CNK2450DS012G'
EAN='6975641883678'
SELLER_ID='9990696'

TITLE_LT='Trekingo lazda Naturehike Gale UL, tamsiai pilka'
TITLE_LV='Trekinga nūja Naturehike Gale UL, tumši pelēka'
TITLE_EE='Matkakepp Naturehike Gale UL, tumehall'
TITLE_RU='Треккинговая палка Naturehike Gale UL, тёмно-серая'
TITLE_FI='Vaellussauva Naturehike Gale UL, tummanharmaa'

DESC_LT='''<p><strong>Naturehike Gale UL trekingo lazda</strong> skirta žygiams ir trekingui. Lazdos konstrukcijoje naudojamas 3K anglies pluoštas ir 7075 aliuminio lydinys. Rankena pagaminta iš EVA putų. Penkių dalių Z tipo sulankstymo konstrukcija ir išorinis Quick-lock fiksavimo mechanizmas leidžia greitai išskleisti ir patikimai užfiksuoti lazdą. Ilgis reguliuojamas nuo 103 iki 120 cm. Komplekte yra 1 lazda.</p><ul><li>Medžiaga: 3K anglies pluoštas ir 7075 aliuminio lydinys.</li><li>Rankenos medžiaga: EVA putos.</li><li>Fiksavimo sistema: išorinis Quick-lock.</li><li>Konstrukcija: 5 dalių, Z tipo sulankstoma.</li><li>Ilgis: 103–120 cm, reguliuojamas.</li><li>Komplekte: 1 vnt.</li><li>Spalva: tamsiai pilka.</li><li>Pridedami nuimami purvo/sniego krepšeliai ir antgalių apsaugos.</li></ul><p><strong>Apie gamintoją Naturehike</strong></p><p>Naturehike įkurta 2010 m. ir kuria stovyklavimo bei lauko įrangą žmonėms, mėgstantiems gamtą. Gamintojas daug dėmesio skiria lengvai, patikimai ir lauko sąlygoms pritaikytai įrangai bei gaminių kokybės kontrolei ir bandymams.</p>'''
DESC_LV='''<p><strong>Naturehike Gale UL trekinga nūja</strong> paredzēta pārgājieniem un trekingam. Nūjas konstrukcijā izmantota 3K oglekļa šķiedra un 7075 alumīnija sakausējums. Rokturis izgatavots no EVA putām. Piecu sekciju Z veida saliekamā konstrukcija un ārējais Quick-lock fiksācijas mehānisms ļauj nūju ātri salikt un droši nofiksēt. Garums ir regulējams no 103 līdz 120 cm. Komplektā ir 1 nūja.</p><ul><li>Materiāls: 3K oglekļa šķiedra un 7075 alumīnija sakausējums.</li><li>Roktura materiāls: EVA putas.</li><li>Fiksācijas sistēma: ārējais Quick-lock mehānisms.</li><li>Konstrukcija: 5 sekcijas, Z veida saliekama.</li><li>Garums: 103–120 cm, regulējams.</li><li>Komplektā: 1 gab.</li><li>Krāsa: tumši pelēka.</li><li>Komplektācijā ir noņemami dubļu/sniega grozi un uzgaļu aizsargi.</li></ul><p><strong>Par ražotāju Naturehike</strong></p><p>Naturehike ir dibināts 2010. gadā un izstrādā kempinga un āra aktivitāšu aprīkojumu cilvēkiem, kuri vēlas vairāk laika pavadīt dabā. Ražotājs koncentrējas uz vieglu, uzticamu un āra apstākļiem piemērotu aprīkojumu, kā arī produktu kvalitātes kontroli un testēšanu.</p>'''
DESC_EE='''<p><strong>Naturehike Gale UL matkakepp</strong> on mõeldud matkamiseks ja trekkinguks. Kepi konstruktsioonis kasutatakse 3K süsinikkiudu ja 7075 alumiiniumisulamit. Käepide on valmistatud EVA-vahust. Viieosaline Z-kujuline kokkupandav konstruktsioon ja väline Quick-lock lukustusmehhanism võimaldavad kepi kiiresti kokku panna ja kindlalt fikseerida. Pikkus on reguleeritav vahemikus 103–120 cm. Pakendis on 1 matkakepp.</p><ul><li>Materjal: 3K süsinikkiud ja 7075 alumiiniumisulam.</li><li>Käepideme materjal: EVA-vaht.</li><li>Lukustussüsteem: väline Quick-lock.</li><li>Konstruktsioon: 5-osaline, Z-kujuliselt kokkupandav.</li><li>Pikkus: 103–120 cm, reguleeritav.</li><li>Pakendis: 1 tk.</li><li>Värv: tumehall.</li><li>Kaasas eemaldatavad muda-/lumekorvid ja otsakaitsmed.</li></ul><p><strong>Tootjast Naturehike</strong></p><p>Naturehike asutati 2010. aastal ning ettevõte arendab telkimis- ja matkavarustust inimestele, kes naudivad looduses viibimist. Tootja keskendub kergele ja töökindlale välivarustusele ning toodete kvaliteedikontrollile ja testimisele.</p>'''
DESC_RU='''<p><strong>Треккинговая палка Naturehike Gale UL</strong> предназначена для походов и треккинга. В конструкции используется углеволокно 3K и алюминиевый сплав 7075. Рукоятка изготовлена из пены EVA. Пятисекционная Z-образная складная конструкция и внешний механизм фиксации Quick-lock позволяют быстро сложить палку и надёжно зафиксировать её. Длина регулируется от 103 до 120 см. В комплекте 1 палка.</p><ul><li>Материал: углеволокно 3K и алюминиевый сплав 7075.</li><li>Материал рукоятки: EVA.</li><li>Система фиксации: внешний Quick-lock.</li><li>Конструкция: 5 секций, Z-образная складная.</li><li>Длина: 103–120 см, регулируемая.</li><li>Комплектация: 1 шт.</li><li>Цвет: тёмно-серый.</li><li>В комплект входят съёмные грязевые/снежные кольца и защитные наконечники.</li></ul><p><strong>О производителе Naturehike</strong></p><p>Naturehike основан в 2010 году и разрабатывает туристическое и кемпинговое снаряжение для любителей активного отдыха на природе. Производитель уделяет внимание лёгкости и надёжности снаряжения, а также контролю качества и испытаниям продукции.</p>'''
DESC_FI='''<p><strong>Naturehike Gale UL -vaellussauva</strong> on tarkoitettu retkeilyyn ja vaellukseen. Sauvan rakenteessa käytetään 3K-hiilikuitua ja 7075-alumiiniseosta. Kahva on EVA-vaahtoa. Viisiosainen Z-taittorakenne ja ulkoinen Quick-lock-lukitusmekanismi mahdollistavat nopean kokoamisen ja tukevan lukituksen. Pituus on säädettävissä välillä 103–120 cm. Pakkauksessa on 1 sauva.</p><ul><li>Materiaali: 3K-hiilikuitu ja 7075-alumiiniseos.</li><li>Kahvan materiaali: EVA-vaahto.</li><li>Lukitusjärjestelmä: ulkoinen Quick-lock.</li><li>Rakenne: 5-osainen, Z-taitettava.</li><li>Pituus: 103–120 cm, säädettävä.</li><li>Pakkauksessa: 1 kpl.</li><li>Väri: tummanharmaa.</li><li>Mukana irrotettavat muta-/lumikorit ja kärjensuojat.</li></ul><p><strong>Tietoa Naturehikesta</strong></p><p>Naturehike perustettiin vuonna 2010 ja se kehittää retkeily- ja ulkoiluvarusteita luonnossa liikkumisesta nauttiville käyttäjille. Valmistaja keskittyy kevyisiin ja luotettaviin ulkoiluvarusteisiin sekä tuotteiden laadunvalvontaan ja testaukseen.</p>'''

IMAGES=[
'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/Untitleddesign-2026-08-05T002348.997.jpg',
'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/treckingpoletelescopicgrey.jpg',
'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/treckingpoletelescopicgrey.1.jpg',
'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/treckingpoletelescopicgrey.2.jpg',
'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/treckingpoletelescopicgrey.3.jpg',
'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/Untitleddesign-2026-08-05T002522.123.jpg',
'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/Untitleddesign-2026-08-05T001431.204.jpg',
'https://cdn.shopify.com/s/files/1/0771/7188/4370/files/Untitleddesign-2026-08-05T001709.513.jpg'
]

PAYLOAD={
  'category_id':4205,
  'title':TITLE_LT,
  'title_lv':TITLE_LV,
  'title_ee':TITLE_EE,
  'title_ru':TITLE_RU,
  'title_fi':TITLE_FI,
  'long_description':DESC_LT,
  'long_description_lv':DESC_LV,
  'long_description_ee':DESC_EE,
  'long_description_ru':DESC_RU,
  'long_description_fi':DESC_FI,
  'images':IMAGES,
  'manufacturer_name':'HONGKONG NATUREHIKE INTERNATIONAL LIMITED',
  'manufacturer_email':'support@naturehike.com',
  'manufacturer_address':'300 Lockhart Road, FLAT/RM A, 20/F, ZJ 300, Wan Chai, HONGKONG',
  'product_features':[
    {'name':'Prekės ženklas','value':'Naturehike'},
    {'name':'Teleskopinė lazda','value':'Taip'},
    {'name':'Lazdos medžiaga','value':'Alumīnijs un oglekļa šķiedra'},
    {'name':'Rankenos medžiaga','value':'EVA'},
    {'name':'Sniego čiuptuvai','value':'Taip'},
    {'name':'Tipas','value':'Trekinga nūjas'},
  ],
  'modifications':[{
    'sku':SKU,
    'manufacturer_code':'CNK2450DS012G',
    'eans':[EAN],
    'package_weight':1.0,
    'package_length':0.62,
    'package_width':0.36,
    'package_height':0.07,
    'tare_deposit_quantity':0,
  }]
}

def headers(token):
    return {'User-Agent':'outfish-phh-gale-full-content/20','Accept':'application/json','Content-Type':'application/json','Authorization':'Pigu-mp '+token}

def run():
    if os.getenv('RUN_NATUREHIKE_GALE_FULL_PATCH','').strip()!='APPROVED_ONCE':
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

    pr=requests.patch(urljoin(BASE,f'/v3/sellers/product/import/execution/{execution_id}'),headers=headers(token),json=PAYLOAD,timeout=60)
    try: pj=pr.json()
    except Exception: pj={'raw':pr.text[:2000]}
    out={'status':'SUBMITTED' if pr.status_code==200 else 'VALIDATION_ERROR',
         'execution_id':execution_id,'product_patch_http_status':pr.status_code,'response':pj,
         'sku':SKU,'ean':EAN,'image_count':len(IMAGES),'languages':['lt','lv','ee','ru','fi'],
         'package':{'length':0.62,'width':0.36,'height':0.07,'weight':1.0},
         'safety':{'stock_changes':0,'price_changes':0,'Shopify_writes':0,'target_products':1}}
    if pr.status_code==200:
        for _ in range(15):
            time.sleep(3)
            rr=_api_get(f'/v3/sellers/{seller_id}/product/import/execution/{execution_id}/results?limit=20&offset=0',token)
            try:rj=rr.json()
            except Exception:rj={'raw':rr.text[:1000]}
            items=(rj.get('items') if isinstance(rj,dict) else None) or []
            target=[x for x in items if isinstance(x,dict) and str(x.get('sku') or '')==SKU]
            if target and target[0].get('status') in ('success','error'):
                out['result']=target[0]; out['status']=str(target[0].get('status')).upper(); break
    print('PHH_NATUREHIKE_GALE_FULL_PATCH_V20 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
