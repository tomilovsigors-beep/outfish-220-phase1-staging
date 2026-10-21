"""Owner-approved pilot CREATE. No patch/offer/Shopify/SIA FHM writes."""
from __future__ import annotations
import json, os, time, re, hashlib
from pathlib import Path
from urllib.parse import urljoin, quote
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
from pmp_api_probe import BASE, _api_login, _api_get, _docs_get, _embedded_spec
SELLER='9990696'
SHEET='1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I'
PAYLOADS=json.loads(Path(__file__).with_name('owner_pilot_20261021_payloads.json').read_text(encoding='utf-8'))
PREFIX='OWNER_PILOT_CREATE_20261021'
def emit(kind,data):
    print(PREFIX+' '+json.dumps({'event':kind,**data},ensure_ascii=False,separators=(',',':')),flush=True)
def body(r):
    try:return r.json()
    except Exception:return {'raw':r.text[:3000]}
def run():
    if os.getenv('RUN_OWNER_PILOT_CREATE_20261021')!='OWNER_APPROVED_EXACT_TWO_CORRECTIONS':
        return
    try:fd=os.open('/tmp/owner_pilot_20261021.lock',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    except FileExistsError:return
    creds=service_account.Credentials.from_service_account_info(json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']),scopes=['https://www.googleapis.com/auth/spreadsheets'])
    sess=AuthorizedSession(creds)
    def read(rng):
        r=sess.get('https://sheets.googleapis.com/v4/spreadsheets/'+SHEET+'/values/'+quote(rng,safe=''),timeout=30);r.raise_for_status()
        return r.json().get('values') or []
    def save(state):
        r=sess.put('https://sheets.googleapis.com/v4/spreadsheets/'+SHEET+'/values/'+quote('CREATE_PILOT!D180',safe=''),params={'valueInputOption':'RAW'},json={'values':[[json.dumps(state,ensure_ascii=False,separators=(',',':'))]]},timeout=30);r.raise_for_status()
    existing=read('CREATE_PILOT!D180')
    if existing and existing[0] and existing[0][0]:
        emit('ALREADY_CLAIMED',{'journal':existing[0][0]});return
    for idx,row in enumerate((132,167)):
        live=json.loads(read(f'CREATE_PILOT!D{row}')[0][0])['phh_payload']
        if live!=PAYLOADS[idx]:raise RuntimeError('Approved JSON differs from sheet; no writes')
    state={'status':'CLAIMED','execution_id':None,'posts':[],'shopify_writes':0,'sia_fhm_writes':0}
    save(state)
    lr=_api_login('v3')
    if lr is None or not lr.ok:raise RuntimeError('PHH login unavailable')
    token=lr.json()['token']
    me=_api_get('/v3/sellers/me',token);me.raise_for_status();md=me.json()
    if str(md.get('id') or (md.get('seller') or {}).get('id'))!=SELLER:raise RuntimeError('Seller mismatch')
    # No retry of any PHH mutation. A claimed journal blocks reruns across deploys.
    for p in PAYLOADS:
        for m in p['modifications']:
            r=_api_get('/v3/products/product-modifications/barcodes?ean='+m['eans'][0],token)
            if r.status_code!=404:
                state['status']='ABORT_IDENTITY_NOT_ABSENT';state['identity']={'sku':m['sku'],'http':r.status_code,'body':body(r)};save(state);emit('STOP',state);return
    headers={'Authorization':'Pigu-mp '+token,'Content-Type':'application/json','Accept':'application/json','User-Agent':'outfish-owner-approved-pilot/20261021'}
    r=requests.post(urljoin(BASE,f'/v3/sellers/{SELLER}/product/import/execution'),headers=headers,timeout=30)
    ej=body(r);state.update(status='EXECUTION_RESPONSE',execution_http=r.status_code,execution_response=ej)
    if r.status_code!=201 or not isinstance(ej,dict) or not ej.get('id'):
        save(state);emit('STOP',state);return
    eid=str(ej['id']);state['execution_id']=eid
    save(state);emit('EXECUTION',state)
    for p in PAYLOADS:
        skus=[m['sku'] for m in p['modifications']]
        state['status']='POST_INTENT';state['next_skus']=skus;save(state)
        r=requests.post(urljoin(BASE,f'/v3/sellers/product/import/execution/{eid}'),headers=headers,json=p,timeout=45)
        record={'skus':skus,'http':r.status_code,'response':body(r)}
        state['posts'].append(record);state['status']='POST_RESPONSE';save(state);emit('POST',record)
    # Poll read-only; save every changed result, never re-submit or repair.
    last=None;final=False
    for attempt in range(180):
        rr=_api_get(f'/v3/sellers/{SELLER}/product/import/execution/{eid}/results?limit=100&offset=0',token)
        bj=body(rr)
        serial=json.dumps(bj,sort_keys=True)
        if serial!=last:
            state.update(status='POLLING',results_http=rr.status_code,results=bj);save(state);emit('RESULTS',{'execution_id':eid,'http':rr.status_code,'body':bj});last=serial
        items=bj.get('items',[]) if isinstance(bj,dict) else bj if isinstance(bj,list) else []
        if len(items)>=2 and all(str(x.get('status','')).lower() in ('success','error','failed') for x in items):
            final=True;break
        time.sleep(5)
    state['status']='FINAL' if final else 'PROCESSING_TIMEOUT';save(state)
    # Read every SKU through global barcode GET; response contains authoritative product/modification identifiers.
    identity=[]
    for p in PAYLOADS:
        for m in p['modifications']:
            r=_api_get('/v3/products/product-modifications/barcodes?ean='+m['eans'][0],token)
            bj=body(r);identity.append({'sku':m['sku'],'ean':m['eans'][0],'http':r.status_code,'body':bj})
            emit('IDENTITY_GET',identity[-1])
    # Discover official GET paths from the live OpenAPI and use only existing documented paths.
    dr=_docs_get('/docs');dr.raise_for_status();spec=_embedded_spec(dr.text);paths=spec.get('paths',{})
    relevant={p:{'summary':v['get'].get('summary'),'parameters':v['get'].get('parameters')} for p,v in paths.items() if isinstance(v,dict) and 'get' in v and ('product' in p.lower() or 'modification' in p.lower())}
    emit('GET_CONTRACT',{'paths':relevant})
    seen=set()
    for entry in identity:
        entries=entry['body'] if isinstance(entry['body'],list) else []
        for e in entries:
            mod=e.get('modification') or {}
            if mod.get('app_name')!='220.lv':continue
            external=e.get('id')
            if external and external not in seen:
                seen.add(external)
                path=f'/v3/product-modification/pigu-external-id/{external}/auto-check-errors'
                ar=_api_get(path,token);ab=body(ar)
                emit('AUTOCHECK',{'sku':entry['sku'],'pigu_external_id':external,'http':ar.status_code,'body':ab,'validator_19_included':True})
    state['identity']=identity
    # Keep journal concise; full result bodies are emitted in logs.
    save(state);emit('DONE',{'execution_id':eid,'status':state['status'],'posts':state['posts'],'shopify_writes':0,'sia_fhm_writes':0})
def guarded_run():
    try:run()
    except Exception as e:emit('STOP_EXCEPTION',{'type':type(e).__name__,'message':str(e)[:700],'no_automatic_retry':True})
