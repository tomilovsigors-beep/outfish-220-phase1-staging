"""One owner-approved batch, 2026-10-23. Reads exact JSON from private Master; no payload copy in repository."""
import json,os,time,hashlib,threading,requests
# One-shot owner authorization: eight exact payloads approved 2026-10-23.
# Persistent DB journal prevents repeats across workers, restarts and deploys.
OWNER_BATCH23 = [{"model":"ss9235","sha256":"beaf4d34058f0a5b11fa31b0365c56695259cac598b1bbbc5bf2a649c69ff1f9"},{"model":"ss9196","sha256":"054ab0d0e1c98ecbbaf44b243b1786bfc19bb4460bc00f81f98ff2fcfdf7dd1c"},{"model":"kj0529XS","sha256":"a9550795e560160f47f37a2c0efbed58eff68896a77b5010fb7348f14064abb0"},{"model":"ss3437sm","sha256":"5e8fc864396c3fccea8e2712d9e3d188106e62e15639a513e56167a4e20602db"},{"model":"ss3944","sha256":"bdf8d48835f813e04bb33ed5d5cd659eab1c23cde4aa86aea43dad71215196d7"},{"model":"14BAND520-480","sha256":"4de0c16e95dd444dddd6e06d5b8052b80e56b865b2f30ca8f996754ea0c65db1"},{"model":"14TUBM530-990","sha256":"8ee5883da3acecac5e241409480ee2c39e21ac4d46c27b4f267074966f347248"},{"model":"32051-S/M","sha256":"fbd0cdd3d01ffb9d7f92140d9b0d9e23f3d2ae9e35552a02c9d75b2c7cf1e1ea"}]
OWNER_BATCH23_KEY = 'owner_9142_create_20261023_v1'
OWNER_BATCH23_TABLE = 'outfish_owner_create_journal'
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

def _owner23_run():
    import psycopg
    from urllib.parse import urljoin,quote
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    from pmp_api_probe import BASE,_api_login,_api_get
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True) as db:
        if not db.execute('select pg_try_advisory_lock(2310202608)').fetchone()[0]: return
        db.execute('create table if not exists outfish_owner_create_journal (job_key text primary key, state jsonb not null, updated_at timestamptz not null default now())')
        if db.execute('select 1 from outfish_owner_create_journal where job_key=%s',(OWNER_BATCH23_KEY,)).fetchone(): return
        state={'job':OWNER_BATCH23_KEY,'status':'CLAIMED','started_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'execution_id':None,'posts':[],'models':{},'shopify_writes':0,'sia_fhm_writes':0,'automatic_retry':False}
        def save():
            db.execute('insert into outfish_owner_create_journal(job_key,state) values(%s,%s::jsonb) on conflict(job_key) do update set state=excluded.state,updated_at=now()',(OWNER_BATCH23_KEY,json.dumps(state,ensure_ascii=False)))
        save()
        try:
            creds=service_account.Credentials.from_service_account_info(json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']),scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
            gs=AuthorizedSession(creds)
            ur='https://sheets.googleapis.com/v4/spreadsheets/1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I/values/'+quote('CREATE_BATCH_02!D207:D215',safe='')
            rr=gs.get(ur,timeout=30);rr.raise_for_status()
            live=[json.loads(r[0]) for r in rr.json()['values'] if r and r[0]]
            batch=[]
            for approved in OWNER_BATCH23:
                matches=[v for v in live if v.get('model')==approved['model']]
                if len(matches)!=1:raise ValueError('APPROVED_MODEL_NOT_UNIQUE')
                x={'model':approved['model'],'sha256':approved['sha256'],'payload':matches[0]['phh_payload']}
                batch.append(x)
                payload=x['payload']; digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                if digest!=x['sha256'] or not any(v.get('model')==x['model'] and v.get('phh_payload')==payload for v in live): raise ValueError('APPROVED_PAYLOAD_MISMATCH:'+x['model'])
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
            lr=_api_login('v3',allow_network_login=False)
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
                model['status']='POST_INTENT';model['post_attempted']=True;save()
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
            state['size_encoding_observation']='NOT_PROVEN_BY_IMPORT_SUCCESS; needs portal Features read'
            save()
        except Exception as ex:
            state.update(status='STOP_EXCEPTION',error_type=type(ex).__name__,error=str(ex)[:700]);save()

def read_result():
    import psycopg
    try:
        with psycopg.connect(os.environ['DATABASE_URL']) as c:
            exists=c.execute("select to_regclass('outfish_owner_create_journal')").fetchone()[0]
            if not exists:return ({'status':'NOT_STARTED'})
            row=c.execute('select state from outfish_owner_create_journal where job_key=%s',(OWNER_BATCH23_KEY,)).fetchone()
            return (row[0] if row else {'status':'NOT_STARTED'})
    except Exception as ex:return ({'status':'READ_ERROR','error_type':type(ex).__name__},503)

def _owner23_guarded():
    try:_owner23_run()
    except Exception as ex:print('OWNER_BATCH23_BOOT_ERROR',type(ex).__name__,flush=True)
def start_once():
    threading.Thread(target=_owner23_guarded,daemon=True,name='owner-approved-batch23').start()
