from __future__ import annotations
import json
import pmp_api_probe as p

def run():
    lr=p._api_login('v3'); token=None
    if lr is not None and lr.ok:
        b=lr.json(); token=b.get('token') if isinstance(b,dict) else None
    if not token: raise RuntimeError('PHH token unavailable')
    me=p._api_get('/v3/sellers/me',token)
    me_body=me.json() if me.ok else {}
    # support common shapes without guessing beyond IDs already returned
    sid=None
    if isinstance(me_body,dict):
        sid=me_body.get('id') or (me_body.get('seller') or {}).get('id') if isinstance(me_body.get('seller'),dict) else me_body.get('id')
    out={'status':'PASS','me_status':me.status_code,'seller_id_found':bool(sid),'executions_status':None,'executions':None,'results':[],'safety':{'writes':0,'marketplace_mutations':0}}
    if sid:
        er=p._api_get(f'/v3/sellers/{sid}/product/import/executions?limit=20&offset=0',token)
        out['executions_status']=er.status_code
        eb=er.json() if er.ok else None; out['executions']=eb
        items=[]
        if isinstance(eb,dict):
            for key in ('items','executions','data'):
                if isinstance(eb.get(key),list): items=eb[key]; break
        elif isinstance(eb,list): items=eb
        for x in items[:10]:
            if not isinstance(x,dict) or not x.get('id'): continue
            eid=x['id']; rr=p._api_get(f'/v3/sellers/{sid}/product/import/execution/{eid}/results?limit=100&offset=0',token)
            rb=rr.json() if rr.ok else None
            out['results'].append({'execution_id':eid,'status':rr.status_code,'body':rb})
    print('PMP_IMPORT_HISTORY_MINING_V11K '+json.dumps(out,ensure_ascii=False,sort_keys=True)[:180000],flush=True)
    return out
