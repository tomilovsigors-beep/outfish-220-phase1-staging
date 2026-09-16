import json, os, threading

def on_starting(server):
    try:
        raw=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON') or ''
        info=json.loads(raw) if raw else {}
        email=info.get('client_email')
        if email:
            server.log.info('GOOGLE_SERVICE_ACCOUNT_PRINCIPAL %s', email)
        if os.getenv('RUN_TITLE_ACTIVATION','').strip()=='APPROVED':
            from title_activation import run_title_activation
            report,_=run_title_activation()
            server.log.info('TITLE_ACTIVATION_RESULT %s',json.dumps(report,sort_keys=True))
        if os.getenv('RUN_IDENTITY_DUP_DIAG','')!='1':
            return
        import psycopg
        from collections import defaultdict
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        db=os.getenv('DATABASE_URL') or ''
        dataset='a4465570311f9ae08e9733cd2fe9349ca0ece6f3bf6438f07de70fa4281ee3d7'
        with psycopg.connect(db) as c:
            with c.cursor() as cur:
                cur.execute('select distinct sku,ean from product_xml_master_proposal_rows where dataset_hash=%s',(dataset,))
                proposal={(str(a or '').strip(),str(b or '').strip()) for a,b in cur.fetchall()}
        creds=service_account.Credentials.from_service_account_info(info,scopes=['https://www.googleapis.com/auth/spreadsheets'])
        s=AuthorizedSession(creds)
        url='https://sheets.googleapis.com/v4/spreadsheets/1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I/values/MASTER!E1:F5000'
        r=s.get(url,params={'majorDimension':'ROWS','valueRenderOption':'UNFORMATTED_VALUE'},timeout=60)
        r.raise_for_status()
        vals=r.json().get('values') or []
        idmap=defaultdict(list)
        for rn,row in enumerate(vals[1:],start=2):
            sku=str(row[0]).strip() if len(row)>0 and row[0] is not None else ''
            ean=str(row[1]).strip() if len(row)>1 and row[1] is not None else ''
            if (sku,ean) in proposal:
                idmap[(sku,ean)].append(rn)
        dups=[{'220_sku':sku,'220_ean':ean,'rows':rows} for (sku,ean),rows in sorted(idmap.items()) if len(rows)>1]
        server.log.info('MASTER_IDENTITY_DUP_DIAG %s',json.dumps({'proposal_identities':len(proposal),'duplicate_identities':len(dups),'duplicates':dups},sort_keys=True))
    except Exception as exc:
        server.log.warning('STARTUP_DIAG_OR_TITLE_ERROR %s %s', type(exc).__name__, str(exc)[:2000])

def when_ready(server):
    def _audit():
        try:
            from noncategory_audit import run_noncategory_audit,emit_artifacts
            summary,arts=run_noncategory_audit(); emit_artifacts(summary,arts)
        except Exception as exc:
            server.log.warning('NONCATEGORY_AUDIT_FAILED %s %s',type(exc).__name__,str(exc)[:2000])
    threading.Thread(target=_audit,daemon=True,name='noncategory-audit').start()
