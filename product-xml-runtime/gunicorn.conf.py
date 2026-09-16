import json, os

def on_starting(server):
    try:
        raw=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON') or ''
        info=json.loads(raw) if raw else {}
        email=info.get('client_email')
        if email:
            server.log.info('GOOGLE_SERVICE_ACCOUNT_PRINCIPAL %s', email)
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        creds=service_account.Credentials.from_service_account_info(info,scopes=['https://www.googleapis.com/auth/drive'])
        s=AuthorizedSession(creds)
        u='https://www.googleapis.com/drive/v3/files/1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I?fields=id,name,mimeType,capabilities'
        r=s.get(u,timeout=30)
        server.log.info('GOOGLE_DRIVE_API_DIAG status=%s body=%s',r.status_code,(r.text or '')[:1400].replace('\n',' '))
    except Exception as exc:
        server.log.warning('GOOGLE_DRIVE_API_DIAG_ERROR %s %s', type(exc).__name__, str(exc)[:500])
