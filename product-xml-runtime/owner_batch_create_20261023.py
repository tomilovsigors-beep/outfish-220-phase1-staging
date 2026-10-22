"""One owner-approved batch, 2026-10-23. Reads exact JSON from private Master; no payload copy in repository."""
import json,os,time,hashlib,threading,requests
# One-shot owner authorization: two exact Thermowave payloads approved 2026-10-23.
# Google Sheets intent journal prevents repeats; one process lock serializes workers.
OWNER_BATCH23 = [{"model": "14BAND520-480", "sha256": "4de0c16e95dd444dddd6e06d5b8052b80e56b865b2f30ca8f996754ea0c65db1"}, {"model": "14TUBM530-990", "sha256": "8ee5883da3acecac5e241409480ee2c39e21ac4d46c27b4f267074966f347248"}]
OWNER_BATCH23_KEY = 'owner_9142_thermowave_resume_20261023_v2'
MASTER_ID = '1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I'
HEADERS=['SKU','SHA-256','execution_id','статус','product_id','время']
def _owner23_body(r):
    try: return r.json()
    except ValueError: return {'raw':r.text}

def _owner23_scalars(x):
    if isinstance(x,dict):
        for v in x.values(): yield from _owner23_scalars(v)
    elif isinstance(x,list):
        for v in x: yield from _owner23_scalars(v)
    elif isinstance(x,(str,int,float)) and not isinstance(x,bool): yield str(x)

def _owner23_jpeg_dims(b):
    import struct
    if b[:2]!=b'\xff\xd8': raise ValueError('Not JPEG bytes')
    p=2
    while p+4<len(b):
        if b[p]!=255: p+=1; continue
        while p<len(b) and b[p]==255: p+=1
        m=b[p];p+=1
        if m in (216,217) or 208<=m<=215: continue
        n=int.from_bytes(b[p:p+2],'big')
        if m in (192,193,194,195,197,198,199,201,202,203,205,206,207):
            h,w=struct.unpack('>HH',b[p+3:p+7]); return w,h
        if n<2: raise ValueError('Invalid JPEG')
        p+=n
    raise ValueError('No JPEG dimensions')


def _now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

_RESULT_CACHE = None

class SheetsJournal:
    def __init__(self):
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        creds=service_account.Credentials.from_service_account_info(json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']),scopes=['https://www.googleapis.com/auth/spreadsheets'])
        self.gs=AuthorizedSession(creds)
        self.base='https://sheets.googleapis.com/v4/spreadsheets/'+MASTER_ID
        self.log_rows=None
        self.last_request=None
    def pause(self):
        # At most20 Sheets calls/minute, including reads; monitoring uses memory.
        if self.last_request is not None:
            delay=3.0-(time.monotonic()-self.last_request)
            if delay>0:time.sleep(delay)
        self.last_request=time.monotonic()
    def read(self,rng):
        from urllib.parse import quote
        self.pause()
        r=self.gs.get(self.base+'/values/'+quote(rng,safe=''),timeout=40);r.raise_for_status()
        return r.json().get('values',[])
    def state(self):
        rows=self.read('CREATE_BATCH_02!D218')
        return json.loads(rows[0][0]) if rows and rows[0] else {}
    def load_log_once(self):
        if self.log_rows is not None:raise RuntimeError('CREATE_LOG_ALREADY_LOADED')
        self.log_rows=self.read('CREATE_LOG!A1:F200')
        if not self.log_rows or self.log_rows[0]!=HEADERS:raise RuntimeError('CREATE_LOG_HEADER_MISMATCH')
    def locate(self,sku):
        if self.log_rows is None:raise RuntimeError('CREATE_LOG_NOT_LOADED')
        matches=[(i+1,r) for i,r in enumerate(self.log_rows) if i>0 and r and str(r[0]).casefold()==sku.casefold()]
        if len(matches)!=1:raise RuntimeError('CREATE_LOG_SKU_NOT_UNIQUE:'+sku)
        return matches[0]
    def check_model(self,model,status):
        # Check the startup snapshot plus acknowledged atomic writes; no new GET.
        for sku in model['skus']:
            rn,row=self.locate(sku);r=row+['']*6
            if r[1]!=model['sha256'] or r[3]!=status or r[4]:raise RuntimeError('CREATE_LOG_REPEAT_OR_HASH_BLOCK:'+sku)
            if status=='POST_INTENT' and str(r[2])!=str(model['execution_id']):raise RuntimeError('CREATE_LOG_EXECUTION_MISMATCH:'+sku)
    def save(self,state):
        global _RESULT_CACHE
        state['updated_at']=_now()
        state['run_enabled']=False
        text=json.dumps(state,ensure_ascii=False)
        if len(text)>49000:raise RuntimeError('STATE_EXCEEDS_SHEETS_CELL')
        data=[{'range':'CREATE_BATCH_02!D218:F218','values':[[text,state['status'],'Thermowave only; run disabled; CREATE_LOG cached once, atomic throttled writes']]}]
        changed=[]
        if set(state['models'])!={a['model'] for a in OWNER_BATCH23}:raise RuntimeError('ONLY_TWO_THERMOWAVE_MODELS_ALLOWED')
        for a in OWNER_BATCH23:
            m=state['models'][a['model']]
            m['execution_id']=state.get('execution_id') or ''
            if m['skus']!=[a['model']]:raise RuntimeError('EXPECTED_ONE_VARIANT_PER_THERMOWAVE_MODEL')
            sku=m['skus'][0];rn,old=self.locate(sku)
            row=[sku,m['sha256'],m['execution_id'],m['status'],m.get('product_id',''),state['updated_at']]
            data.append({'range':f'CREATE_LOG!A{rn}:F{rn}','values':[row]});changed.append((rn,row))
        # Values echoed by the atomic write confirm persistence without another read.
        self.pause()
        r=self.gs.post(self.base+'/values:batchUpdate',json={'valueInputOption':'RAW','includeValuesInResponse':True,'responseValueRenderOption':'UNFORMATTED_VALUE','data':data},timeout=60)
        r.raise_for_status();responses=r.json().get('responses',[])
        if len(responses)!=len(data) or any(a.get('updatedData',{}).get('values')!=b['values'] for a,b in zip(responses,data)):
            raise RuntimeError('JOURNAL_WRITE_ACK_MISMATCH_NO_POST')
        for rn,row in changed:self.log_rows[rn-1]=row
        _RESULT_CACHE=json.loads(json.dumps(state))

def _owner23_run_locked():
    from urllib.parse import urljoin,quote
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    from pmp_api_probe import BASE,_api_login,_api_get
    journal=SheetsJournal()
    global _RESULT_CACHE
    gate=journal.state()
    _RESULT_CACHE=json.loads(json.dumps(gate))
    if gate.get('job')!=OWNER_BATCH23_KEY or gate.get('run_enabled') is not True or gate.get('status')!='ARMED':return
    # One startup read, then only the two explicitly reauthorized unposted models.
    if set(gate.get('models',{}))!={a['model'] for a in OWNER_BATCH23}:raise RuntimeError('UNAUTHORIZED_MODEL_IN_GATE')
    journal.load_log_once()
    for approved in OWNER_BATCH23:
        model=gate['models'][approved['model']]
        if model['sha256']!=approved['sha256']:raise RuntimeError('ARMED_HASH_MISMATCH')
        journal.check_model(model,'READY_THERMOWAVE_OWNER_OK')
    state={'job':OWNER_BATCH23_KEY,'status':'CLAIMED','started_at':_now(),'execution_id':None,'posts':[],'models':json.loads(json.dumps(gate['models'])),'shopify_writes':0,'sia_fhm_writes':0,'automatic_retry':False,'run_enabled':False}
    def save():journal.save(state)
    save()
    try:
        live=[json.loads(r[0]) for r in journal.read('CREATE_BATCH_02!D207:D215') if r and r[0]]
        registry=journal.read('PROJECT_LOG!A57:D64')
        approved_registry={r[2]:r[3] for r in registry if len(r)>=4 and r[1]=='OWNER_APPROVED_SHA256'}
        batch=[]
        for approved in OWNER_BATCH23:
            matches=[v for v in live if v.get('model')==approved['model']]
            if len(matches)!=1:raise ValueError('APPROVED_MODEL_NOT_UNIQUE')
            x={'model':approved['model'],'sha256':approved['sha256'],'payload':matches[0]['phh_payload']}
            batch.append(x)
            payload=x['payload']
            if payload.get('category_id')!=9142 or [f['value'] for f in payload['product_features'] if f['name']=='Prekės ženklas']!=['Thermowave']:raise RuntimeError('ONLY_DICTIONARY_CONFIRMED_THERMOWAVE_ALLOWED')
            payload=x['payload']; digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            if digest!=x['sha256'] or approved_registry.get(x['model'])!=digest or not any(v.get('model')==x['model'] and v.get('phh_payload')==payload for v in live): raise ValueError('APPROVED_PAYLOAD_MISMATCH:'+x['model'])
            state['models'][x['model']]={'sha256':digest,'skus':[m['sku'] for m in payload['modifications']],'status':'PRECHECK','images':[]}
        save()
        for x in batch:
            model=state['models'][x['model']]
            for url in x['payload']['images']:
                c={'url':url,'pass':False}
                try:
                    ir=requests.get(url,timeout=40);ct=ir.headers.get('Content-Type','').split(';')[0].lower().strip()
                    w,h=_owner23_jpeg_dims(ir.content) if ir.status_code==200 and ct=='image/jpeg' else (0,0)
                    c.update(http=ir.status_code,content_type=ct,width=w,height=h)
                    c['pass']=ir.status_code==200 and ct=='image/jpeg' and min(w,h)>=600 and '?' not in url
                except Exception as ex:c['error_type']=type(ex).__name__
                model['images'].append(c)
            if not all(c['pass'] for c in model['images']):model['status']='SKIP_IMAGE_PREFLIGHT_FAILED'
            save()
        if any(v['status']=='SKIP_IMAGE_PREFLIGHT_FAILED' for v in state['models'].values()):
            state['status']='STOP_IMAGE_PREFLIGHT_FAILED';save();return
        # SELECT_001: entire bounded stock sheet, read only, before PHH writes.
        stock_url='https://sheets.googleapis.com/v4/spreadsheets/1qWs8saSIlq1TLhcg-BLZ1dWHRojDQWRX2BlEpZTkDjE/values/'+quote('Sheet1!A1:B2544',safe='')
        journal.pause()
        sr=journal.gs.get(stock_url,timeout=40);sr.raise_for_status()
        stock=sr.json().get('values',[])
        existing={str(v).strip().casefold() for row in stock for v in row if str(v).strip()}
        existing_gtin={str(row[0]).strip().zfill(13) for row in stock if row and str(row[0]).strip().isdigit()}
        state['sia_rows_read']=len(stock)
        for x in batch:
            if any(m['sku'].casefold() in existing or any(e.zfill(13) in existing_gtin for e in m['eans']) for m in x['payload']['modifications']):
                state['models'][x['model']]['status']='SKIP_EXISTING_SIA'
        save()
        lr=_api_login('v3',allow_network_login=True)
        if lr is None or not lr.ok: raise RuntimeError('NO_VALID_SERVER_TOKEN')
        token=lr.json()['token']
        me=_api_get('/v3/sellers/me',token);me.raise_for_status();md=me.json()
        if str(md.get('id') or (md.get('seller') or {}).get('id'))!='9990696':raise RuntimeError('SELLER_MISMATCH')
        control=_api_get('/v3/products/product-modifications/barcodes?ean=0021563681505',token)
        state['positive_control']={'http':control.status_code,'body':_owner23_body(control)}
        if control.status_code!=200 or not isinstance(_owner23_body(control),list) or not _owner23_body(control):raise RuntimeError('BARCODE_CONTROL_FAILED')
        for x in batch:
            model=state['models'][x['model']];model['barcode_preflight']=[]
            for m in x['payload']['modifications']:
                br=_api_get('/v3/products/product-modifications/barcodes?ean='+m['eans'][0],token);bb=_owner23_body(br)
                model['barcode_preflight'].append({'sku':m['sku'],'ean':m['eans'][0],'http':br.status_code,'body':bb})
                if br.status_code==200 and isinstance(bb,list) and bb:model['status']='SKIP_EXISTING'
                elif not (br.status_code==404 and (bb=={'error':{'message':'Not Found'}} or isinstance(bb,dict) and bb.get('code')==404 and str(bb.get('message','')).lower()=='barcode not found')):
                    model['status']='SKIP_IDENTITY_UNKNOWN'
            save()
        offers=[]
        for off in range(0,30000,100):
            ar=_api_get('/v2/sellers/9990696/offers?app_name=220.lv&limit=100&offset='+str(off),token);ar.raise_for_status();ab=ar.json()
            rows=ab if isinstance(ab,list) else ab.get('offers',ab.get('items')) if isinstance(ab,dict) else None
            if not isinstance(rows,list):raise RuntimeError('OFFER_SCAN_SHAPE_UNKNOWN')
            offers.extend(rows)
            if len(rows)<100:break
        else:raise RuntimeError('OFFER_SCAN_INCOMPLETE')
        state['offers_scanned']=len(offers)
        offer_values=[set(_owner23_scalars(o)) for o in offers]
        for x in batch:
            keys={v for m in x['payload']['modifications'] for v in [m['sku'],*m['eans']]}
            if any(keys & v for v in offer_values):state['models'][x['model']]['status']='SKIP_EXISTING_OFFER'
        state['status']='PREFLIGHT_COMPLETE';save()
        headers={'Authorization':'Pigu-mp '+token,'Content-Type':'application/json','Accept':'application/json'}
        if not any(v['status']=='PRECHECK' for v in state['models'].values()):state['status']='STOP_NO_ELIGIBLE_MODELS';save();return
        state['status']='EXECUTION_INTENT';save()
        er=requests.post(urljoin(BASE,'/v3/sellers/9990696/product/import/execution'),headers=headers,timeout=40)
        eb=_owner23_body(er);state.update(execution_http=er.status_code,execution_response=eb);save()
        if er.status_code!=201 or not isinstance(eb,dict) or not eb.get('id'):state['status']='EXECUTION_REJECTED';save();return
        eid=str(eb['id']);state['execution_id']=eid;save()
        for x in batch:
            model=state['models'][x['model']]
            if model['status']!='PRECHECK':continue
            journal.check_model(model,'PRECHECK')
            model['status']='POST_INTENT';model['post_attempted']=True;save()
            journal.check_model(model,'POST_INTENT')
            try:
                pr=requests.post(urljoin(BASE,'/v3/sellers/product/import/execution/'+eid),headers=headers,json=x['payload'],timeout=60)
                model.update(post_http=pr.status_code,post_response=_owner23_body(pr),status='SUBMITTED' if pr.status_code==200 else 'POST_ERROR')
            except requests.RequestException as ex:model.update(status='POST_OUTCOME_UNKNOWN',error_type=type(ex).__name__)
            state['posts'].append(x['model']);save()
        submitted=[v for v in state['models'].values() if v['status']=='SUBMITTED']
        state['status']='POLLING';save()
        if not submitted:state['status']='FINAL_NO_ACCEPTED_POSTS';save();return
        for attempt in range(360):
            qr=_api_get('/v3/sellers/9990696/product/import/execution/'+eid+'/results?limit=100&offset=0',token)
            qb=_owner23_body(qr);state.update(results_http=qr.status_code,results=qb);save()
            if qr.status_code in (401,403,429) or qr.status_code>=500:state['status']='POLL_ACCESS_ERROR';save();return
            items=qb.get('items',[]) if isinstance(qb,dict) else []
            for model in submitted:
                model['import_results']=[it for it in items if it.get('sku') in model['skus']]
                its=model['import_results']
                if its and all(it.get('status') in ('success','error') for it in its):
                    model['import_status']='ERROR' if any(it.get('status')=='error' for it in its) else 'SUCCESS'
                    model['status']='IMPORT_'+model['import_status']
                    import re
                    ids={m.group(1) for it in its for m in [re.search(r'(?:New product created\.\s*ID|product(?:_id| id))\s*[:=]\s*(\d+)',str(it.get('message') or ''),re.I)] if m}
                    if len(ids)==1:model['product_id']=ids.pop()
            save()
            if all(v.get('import_status') in ('SUCCESS','ERROR') for v in submitted):
                state['status']='FINAL';save();break
            time.sleep(5)
        else:state['status']='PROCESSING_TIMEOUT';save()
        state['identity_after']=[]
        for x in batch:
            if not state['models'][x['model']].get('post_attempted'):continue
            for m in x['payload']['modifications']:
                qr=_api_get('/v3/products/product-modifications/barcodes?ean='+m['eans'][0],token)
                state['identity_after'].append({'model':x['model'],'sku':m['sku'],'ean':m['eans'][0],'http':qr.status_code,'body':_owner23_body(qr)})
        state['size_encoding_observation']='No multi-size glove in this run; separate Existing values proven previously on271198525/271198530'
        state['autocheck']=[]
        for entry in state['identity_after']:
            for item in entry['body'] if isinstance(entry['body'],list) else []:
                if (item.get('modification') or {}).get('app_name')!='220.lv':continue
                external=item.get('id')
                if external:
                    ar=_api_get('/v3/product-modification/pigu-external-id/'+str(external)+'/auto-check-errors',token)
                    state['autocheck'].append({'sku':entry['sku'],'pigu_external_id':external,'http':ar.status_code,'body':_owner23_body(ar),'validator_19_included':True})
        save()
    except Exception as ex:
        state.update(status='STOP_EXCEPTION',error_type=type(ex).__name__,error=str(ex)[:700]);save()

def _owner23_run():
    # Render has one instance. Linux flock additionally excludes all Gunicorn workers.
    import fcntl
    with open('/tmp/outfish-owner-batch23.lock','a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        _owner23_run_locked()

def read_result():
    if _RESULT_CACHE is not None:return json.loads(json.dumps(_RESULT_CACHE))
    try:return SheetsJournal().state()
    except Exception as ex:return ({'status':'READ_ERROR','error_type':type(ex).__name__},503)

def _owner23_guarded():
    try:_owner23_run()
    except Exception as ex:print('OWNER_BATCH23_BOOT_ERROR',type(ex).__name__,flush=True)

def start_once():
    threading.Thread(target=_owner23_guarded,daemon=True,name='owner-approved-batch23').start()
