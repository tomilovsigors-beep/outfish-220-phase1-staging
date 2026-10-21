"""Owner approved ss8375 CREATE, 2026-10-22. One attempt; no offer/Shopify writes."""
import json,os,time,hashlib,struct
from urllib.parse import quote,urljoin
import requests,psycopg
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
from pmp_api_probe import BASE,_api_login,_api_get,_docs_get,_embedded_spec
PAYLOAD=json.loads("{\"category_id\":9068,\"title\":\"Žygio kojinės Fjord Nansen New Hike Kevlar pilkos\",\"composition\":\"69 % Amicor, 14 % Coolmax, 11 % poliamidas, 4 % Kevlar, 2 % elastanas\",\"long_description\":\"<h2>Žygio kojinės Fjord Nansen New Hike Kevlar pilkos</h2><p><br></p><p>Aukštos žygio kojinės Fjord Nansen New Hike Kevlar skirtos kalnams ir pereinamojo sezono sąlygoms. Audinyje dera 69 % Amicor, 14 % Coolmax, 11 % poliamido, 4 % Kevlar ir 2 % elastano.</p><h3>Pagrindinis privalumas</h3><p>Konstrukcija sujungia drėgmės pernešimą, Amicor pluoštą ir intensyviai dėvimų vietų sutvirtinimą. Kevlar stiprina kulnus ir pirštų sritį, o Coolmax padeda pašalinti drėgmę ir šilumą nuo pėdos.</p><h3>Medžiagos ir technologijos</h3><ul><li>69 % Amicor: gamintojo nurodytas pluoštas.</li><li>14 % Coolmax: padeda pernešti drėgmę ir šilumą į išorinį kojinės paviršių.</li><li>11 % poliamido: padidina patvarumą.</li><li>4 % Kevlar: sutvirtina dėvėjimuisi ir trinčiai jautrias vietas.</li><li>2 % elastano: padeda išlaikyti prigludimą.</li><li>OEKO-TEX STANDARD 100: gamintojas nurodo šį tekstilės sertifikatą.</li></ul><h3>Techniniai duomenys</h3><table><tbody><tr><th>Sudėtis</th><td>69 % Amicor, 14 % Coolmax, 11 % poliamidas, 4 % Kevlar, 2 % elastanas</td></tr><tr><th>Galimi dydžiai</th><td>35–38, 39–42, 43–46</td></tr><tr><th>Spalva</th><td>Pilka / grafito</td></tr></tbody></table><h3>Apie Fjord Nansen</h3><p>Fjord Nansen prekės ženklas pradėjo veiklą 1996 m. šiaurės Lenkijoje. Jis kuria lauko veiklai skirtą įrangą, daug dėmesio skirdamas kokybei, funkcionalumui ir bandymams realiomis sąlygomis.</p>\",\"title_lv\":\"Pārgājienu zeķes Fjord Nansen New Hike Kevlar pelēkas\",\"composition_lv\":\"69 % Amicor, 14 % Coolmax, 11 % poliamīds, 4 % Kevlar, 2 % elastāns\",\"long_description_lv\":\"<h2>Pārgājienu zeķes Fjord Nansen New Hike Kevlar pelēkas</h2><p><br></p><p>Augstās pārgājienu zeķes Fjord Nansen New Hike Kevlar ir paredzētas kalniem un pārejas sezonai. Audumā apvienoti 69 % Amicor, 14 % Coolmax, 11 % poliamīda, 4 % Kevlar un 2 % elastāna.</p><h3>Galvenā priekšrocība</h3><p>Konstrukcija apvieno mitruma novadīšanu, Amicor šķiedru un pastiprinājumus nodilumam pakļautajās zonās. Kevlar pastiprina papēžus un pirkstgalus, bet Coolmax palīdz novadīt mitrumu un siltumu no pēdas.</p><h3>Materiāli un tehnoloģijas</h3><ul><li>69 % Amicor: ražotāja norādīta šķiedra.</li><li>14 % Coolmax: palīdz novadīt mitrumu un siltumu uz zeķes ārējo virsmu.</li><li>11 % poliamīda: palielina izturību.</li><li>4 % Kevlar: pastiprina nodilumam un berzei pakļautās zonas.</li><li>2 % elastāna: palīdz saglabāt ciešu piegulumu.</li><li>OEKO-TEX STANDARD 100: ražotājs norāda šo tekstilizstrādājumu sertifikātu.</li></ul><h3>Tehniskie dati</h3><table><tbody><tr><th>Sastāvs</th><td>69 % Amicor, 14 % Coolmax, 11 % poliamīds, 4 % Kevlar, 2 % elastāns</td></tr><tr><th>Pieejamie izmēri</th><td>35–38, 39–42, 43–46</td></tr><tr><th>Krāsa</th><td>Pelēka / grafīta</td></tr></tbody></table><h3>Par Fjord Nansen</h3><p>Fjord Nansen zīmols sāka darbību 1996. gadā Polijas ziemeļos. Tas izstrādā inventāru aktivitātēm dabā, pievēršot uzmanību kvalitātei, funkcionalitātei un pārbaudēm reālos apstākļos.</p>\",\"title_ee\":\"Matkasokid Fjord Nansen New Hike Kevlar hallid\",\"composition_ee\":\"69% Amicor, 14% Coolmax, 11% polüamiid, 4% Kevlar, 2% elastaan\",\"long_description_ee\":\"<h2>Matkasokid Fjord Nansen New Hike Kevlar hallid</h2><p><br></p><p>Kõrged matkasokid Fjord Nansen New Hike Kevlar on mõeldud mägedesse ja üleminekuhooaja oludesse. Kangas ühendab 69% Amicori, 14% Coolmaxi, 11% polüamiidi, 4% Kevlarit ja 2% elastaani.</p><h3>Peamine eelis</h3><p>Konstruktsioon ühendab niiskuse ärajuhtimise, Amicori kiu ja suurema kulumisega piirkondade tugevdamise. Kevlar tugevdab kandu ja varbaosa ning Coolmax aitab juhtida niiskust ja soojust jalast eemale.</p><h3>Materjalid ja tehnoloogiad</h3><ul><li>69% Amicori: tootja nimetatud kiud.</li><li>14% Coolmaxi: aitab juhtida niiskust ja soojust soki välispinnale.</li><li>11% polüamiidi: suurendab vastupidavust.</li><li>4% Kevlarit: tugevdab kulumisele ja hõõrdumisele avatud piirkondi.</li><li>2% elastaani: aitab säilitada tihedat istuvust.</li><li>OEKO-TEX STANDARD 100: tootja viitab sellele tekstiilisertifikaadile.</li></ul><h3>Tehnilised andmed</h3><table><tbody><tr><th>Koostis</th><td>69% Amicor, 14% Coolmax, 11% polüamiid, 4% Kevlar, 2% elastaan</td></tr><tr><th>Saadaolevad suurused</th><td>35–38, 39–42, 43–46</td></tr><tr><th>Värv</th><td>Hall / grafiithall</td></tr></tbody></table><h3>Fjord Nansenist</h3><p>Fjord Nanseni kaubamärk alustas tegevust 1996. aastal Põhja-Poolas. See arendab varustust õues tegutsemiseks, keskendudes kvaliteedile, funktsionaalsusele ja katsetamisele tegelikes oludes.</p>\",\"title_ru\":\"Треккинговые носки Fjord Nansen New Hike Kevlar серые\",\"composition_ru\":\"69 % Amicor, 14 % Coolmax, 11 % полиамид, 4 % Kevlar, 2 % эластан\",\"long_description_ru\":\"<h2>Треккинговые носки Fjord Nansen New Hike Kevlar серые</h2><p><br></p><p>Высокие треккинговые носки Fjord Nansen New Hike Kevlar предназначены для гор и межсезонья. В ткани сочетаются 69 % Amicor, 14 % Coolmax, 11 % полиамида, 4 % Kevlar и 2 % эластана.</p><h3>Главное преимущество</h3><p>Конструкция сочетает отвод влаги, волокно Amicor и усиление участков повышенного износа. Kevlar укрепляет пятки и мыски, а Coolmax помогает отводить влагу и тепло от стопы.</p><h3>Материалы и технологии</h3><ul><li>69 % Amicor: волокно, указанное производителем.</li><li>14 % Coolmax: помогает переносить влагу и тепло к наружной поверхности носка.</li><li>11 % полиамида: повышает прочность.</li><li>4 % Kevlar: укрепляет участки, подверженные износу и истиранию.</li><li>2 % эластана: помогает сохранять плотную посадку.</li><li>OEKO-TEX STANDARD 100: производитель указывает этот текстильный сертификат.</li></ul><h3>Технические характеристики</h3><table><tbody><tr><th>Состав</th><td>69 % Amicor, 14 % Coolmax, 11 % полиамид, 4 % Kevlar, 2 % эластан</td></tr><tr><th>Доступные размеры</th><td>35–38, 39–42, 43–46</td></tr><tr><th>Цвет</th><td>Серый / графитовый</td></tr></tbody></table><h3>О Fjord Nansen</h3><p>Бренд Fjord Nansen начал работу в 1996 году на севере Польши. Он разрабатывает снаряжение для активного отдыха на природе, уделяя внимание качеству, функциональности и испытаниям в реальных условиях.</p>\",\"title_fi\":\"Vaellussukat Fjord Nansen New Hike Kevlar harmaat\",\"composition_fi\":\"69 % Amicor, 14 % Coolmax, 11 % polyamidi, 4 % Kevlar, 2 % elastaani\",\"long_description_fi\":\"<h2>Vaellussukat Fjord Nansen New Hike Kevlar harmaat</h2><p><br></p><p>Korkeavartiset Fjord Nansen New Hike Kevlar -vaellussukat on tarkoitettu vuoristoon ja välikauden olosuhteisiin. Kangas yhdistää 69 % Amicoria, 14 % Coolmaxia, 11 % polyamidia, 4 % Kevlaria ja 2 % elastaania.</p><h3>Tärkein etu</h3><p>Rakenne yhdistää kosteudensiirron, Amicor-kuidun ja kovalle kulutukselle altistuvien alueiden vahvistamisen. Kevlar vahvistaa kantapäitä ja varvasosia, ja Coolmax auttaa siirtämään kosteutta ja lämpöä pois jalasta.</p><h3>Materiaalit ja teknologiat</h3><ul><li>69 % Amicoria: valmistajan ilmoittama kuitu.</li><li>14 % Coolmaxia: auttaa siirtämään kosteutta ja lämpöä sukan ulkopinnalle.</li><li>11 % polyamidia: parantaa kestävyyttä.</li><li>4 % Kevlaria: vahvistaa kulutukselle ja hankaukselle altistuvia alueita.</li><li>2 % elastaania: auttaa säilyttämään napakan istuvuuden.</li><li>OEKO-TEX STANDARD 100: valmistaja ilmoittaa tämän tekstiilisertifikaatin.</li></ul><h3>Tekniset tiedot</h3><table><tbody><tr><th>Koostumus</th><td>69 % Amicor, 14 % Coolmax, 11 % polyamidi, 4 % Kevlar, 2 % elastaani</td></tr><tr><th>Saatavilla olevat koot</th><td>35–38, 39–42, 43–46</td></tr><tr><th>Väri</th><td>Harmaa / grafiitinharmaa</td></tr></tbody></table><h3>Tietoa Fjord Nansenista</h3><p>Fjord Nansen aloitti toimintansa vuonna 1996 Pohjois-Puolassa. Brändi kehittää ulkoiluvarusteita ja painottaa laatua, toimivuutta sekä testausta todellisissa käyttöolosuhteissa.</p>\",\"images\":[\"https://cdn.shopify.com/s/files/1/0771/7188/4370/files/eng_pm_NEW-HIKE-KEVLAR-SOCKS-42145_1.jpg\",\"https://cdn.shopify.com/s/files/1/0771/7188/4370/files/Task-57927925-1-2.jpg\",\"https://cdn.shopify.com/s/files/1/0771/7188/4370/files/Untitleddesign-2025-12-06T205523.929.jpg\",\"https://cdn.shopify.com/s/files/1/0771/7188/4370/files/Untitleddesign-2025-12-09T234755.970.jpg\"],\"product_features\":[{\"name\":\"Medžiaga\",\"value\":\"Poliamidas\"},{\"name\":\"Tipas\",\"value\":\"Sportinės kojinės\"},{\"name\":\"Spalva\",\"value\":\"Pilka\"},{\"name\":\"Prekės ženklas\",\"value\":\"Fjord Nansen\"},{\"name\":\"Dydis\",\"value\":\"35-38\"},{\"name\":\"Dydis\",\"value\":\"39-42\"},{\"name\":\"Dydis\",\"value\":\"43-46\"},{\"name\":\"Skirta\",\"value\":\"Unisex\"},{\"name\":\"Kiekis pakuotėje\",\"value\":\"1\"},{\"name\":\"Aksesuaro modelis\",\"value\":\"Kiti modeliai\"}],\"manufacturer_name\":\"SZANTI Dariusz Staniszewski\",\"manufacturer_address\":\"ul. Szafirowa 1, 80-209 Chwaszczyno, Poland\",\"manufacturer_email\":\"produkcja@fjordnansen.pl\",\"representative_name\":null,\"representative_address\":null,\"representative_email\":null,\"modifications\":[{\"sku\":\"ss8375-35-38\",\"manufacturer_code\":\"ss8375-35-38\",\"eans\":[\"5908221355327\"],\"title\":\"35-38\",\"package_weight\":0.2,\"package_length\":0.62,\"package_width\":0.36,\"package_height\":0.07,\"tare_deposit_quantity\":0},{\"sku\":\"ss8375-39-42\",\"manufacturer_code\":\"ss8375-39-42\",\"eans\":[\"5908221355402\"],\"title\":\"39-42\",\"package_weight\":0.2,\"package_length\":0.62,\"package_width\":0.36,\"package_height\":0.07,\"tare_deposit_quantity\":0},{\"sku\":\"ss8375-43-46\",\"manufacturer_code\":\"ss8375-43-46\",\"eans\":[\"5908221355419\"],\"title\":\"43-46\",\"package_weight\":0.2,\"package_length\":0.62,\"package_width\":0.36,\"package_height\":0.07,\"tare_deposit_quantity\":0}]}")
SHEET='1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I'
PREFIX='OWNER_SS8375_20261022'
def emit(event,**data):
    print(PREFIX+' '+json.dumps(dict(event=event,**data),ensure_ascii=False),flush=True)
def body(r):
    try:return r.json()
    except Exception:return {'raw':r.text}
def dimensions(data):
    if data[:2]!=b'\xff\xd8':raise ValueError('Not JPEG bytes')
    p=2
    while p<len(data):
        if data[p]!=255:p+=1;continue
        while p<len(data) and data[p]==255:p+=1
        m=data[p];p+=1
        if m in (0xd8,0xd9) or 0xd0<=m<=0xd7:continue
        n=int.from_bytes(data[p:p+2],'big')
        if m in (0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf):
            h,w=struct.unpack('>HH',data[p+3:p+7]);return w,h
        if n<2:raise ValueError('Invalid JPEG')
        p+=n
    raise ValueError('JPEG dimensions unavailable')
def run():
    # Session advisory lock serializes workers. Sheet journal survives deploys.
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True) as lock:
        if not lock.execute('select pg_try_advisory_lock(837520261022)').fetchone()[0]:return
        creds=service_account.Credentials.from_service_account_info(json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']),scopes=['https://www.googleapis.com/auth/spreadsheets'])
        sess=AuthorizedSession(creds)
        def read(rng):
            r=sess.get('https://sheets.googleapis.com/v4/spreadsheets/'+SHEET+'/values/'+quote(rng,safe=''),timeout=30);r.raise_for_status()
            return r.json().get('values') or []
        state={}
        def save():
            r=sess.put('https://sheets.googleapis.com/v4/spreadsheets/'+SHEET+'/values/'+quote('CREATE_BATCH_02!D130',safe=''),params={'valueInputOption':'RAW'},json={'values':[[json.dumps(state,ensure_ascii=False)]]},timeout=30);r.raise_for_status()
        prior=read('CREATE_BATCH_02!D130')
        if prior and prior[0] and prior[0][0]:emit('ALREADY_CLAIMED');return
        live=json.loads(read('CREATE_BATCH_02!D66')[0][0])
        if live.get('phh_payload')!=PAYLOAD or live.get('dry_run_status')!='OWNER_APPROVED_CREATE_ONCE':
            emit('STOP_APPROVED_JSON_MISMATCH');return
        state.update(status='CLAIMED',execution_id=None,post_attempted=False,shopify_writes=0,sia_fhm_writes=0,phh_payload_sha256=hashlib.sha256(json.dumps(PAYLOAD,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
        save()
        try:
            checks=[]
            for url in PAYLOAD['images']:
                c={'url':url,'pass':False}
                try:
                    r=requests.get(url,timeout=45)
                    ct=r.headers.get('Content-Type','').split(';')[0].strip().lower()
                    w,h=dimensions(r.content) if r.status_code==200 and ct=='image/jpeg' else (0,0)
                    c.update(http=r.status_code,content_type=ct,width=w,height=h)
                    c['pass']=r.status_code==200 and ct=='image/jpeg' and w>=600 and h>=600 and '?' not in url
                except Exception as e:c['error']=type(e).__name__+': '+str(e)
                checks.append(c);emit('IMAGE_PREFLIGHT',**c)
            state['images']=checks;save()
            if not all(c['pass'] for c in checks):
                state['status']='STOP_IMAGE_PREFLIGHT_FAILED';save();return
            lr=_api_login('v3')
            if lr is None or not lr.ok:raise RuntimeError('PHH login unavailable')
            token=lr.json()['token']
            me=_api_get('/v3/sellers/me',token);me.raise_for_status();md=me.json()
            if str(md.get('id') or (md.get('seller') or {}).get('id'))!='9990696':raise RuntimeError('Seller mismatch')
            headers={'Authorization':'Pigu-mp '+token,'Content-Type':'application/json','Accept':'application/json'}
            state['status']='EXECUTION_INTENT';save()
            r=requests.post(urljoin(BASE,'/v3/sellers/9990696/product/import/execution'),headers=headers,timeout=40)
            state.update(execution_http=r.status_code,execution_response=body(r));save()
            if r.status_code!=201 or not isinstance(body(r),dict) or not body(r).get('id'):
                state['status']='STOP_EXECUTION_REJECTED';save();emit('STOP',state=state);return
            eid=str(body(r)['id']);state['execution_id']=eid
            state.update(status='POST_INTENT',post_attempted=True);save();emit('EXECUTION',execution_id=eid)
            r=requests.post(urljoin(BASE,'/v3/sellers/product/import/execution/'+eid),headers=headers,json=PAYLOAD,timeout=60)
            state.update(status='POST_RESPONSE',post_http=r.status_code,post_response=body(r));save();emit('POST',http=r.status_code,body=body(r))
            if r.status_code!=200:
                state['status']='STOP_POST_REJECTED';save();return
            final=False;last=None
            for n in range(180):
                rr=_api_get('/v3/sellers/9990696/product/import/execution/'+eid+'/results?limit=100&offset=0',token)
                b=body(rr);serial=json.dumps(b,sort_keys=True)
                if serial!=last:
                    state.update(status='POLLING',results_http=rr.status_code,results=b);save();emit('RESULTS',http=rr.status_code,body=b);last=serial
                items=b.get('items',[]) if isinstance(b,dict) else b if isinstance(b,list) else []
                if rr.ok and items and all(str(x.get('status','')).lower() in ('success','error','failed') for x in items):final=True;break
                time.sleep(5)
            state['status']='FINAL' if final else 'PROCESSING_TIMEOUT';save()
            identities=[]
            for attempt in range(20):
                identities=[]
                for m in PAYLOAD['modifications']:
                    rr=_api_get('/v3/products/product-modifications/barcodes?ean='+m['eans'][0],token)
                    identities.append({'sku':m['sku'],'ean':m['eans'][0],'http':rr.status_code,'body':body(rr)})
                if all(i['http']==200 for i in identities):break
                if attempt<19:time.sleep(15)
            state['identities']=identities;state['dydis_observation']='NOT_PROVEN_BY_IMPORT_SUCCESS';save()
            emit('IDENTITIES',items=identities)
            checks=[]
            for entry in identities:
                entries=entry['body'] if isinstance(entry['body'],list) else []
                for item in entries:
                    if (item.get('modification') or {}).get('app_name')!='220.lv':continue
                    ext=item.get('id')
                    if ext:
                        rr=_api_get('/v3/product-modification/pigu-external-id/'+str(ext)+'/auto-check-errors',token)
                        checks.append({'sku':entry['sku'],'pigu_external_id':ext,'http':rr.status_code,'body':body(rr)})
            state['autocheck']=checks;save();emit('DONE',state=state)
        except Exception as e:
            state.update(status='STOP_EXCEPTION',error=type(e).__name__+': '+str(e),no_automatic_retry=True)
            save();emit('STOP_EXCEPTION',state=state)
def guarded_run():
    try:run()
    except Exception as e:emit('BOOT_ERROR',error=type(e).__name__+': '+str(e)[:1000])
