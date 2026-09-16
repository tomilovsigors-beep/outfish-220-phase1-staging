import json, os

def on_starting(server):
    try:
        raw=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON') or ''
        email=(json.loads(raw).get('client_email') if raw else None)
        if email:
            server.log.info('GOOGLE_SERVICE_ACCOUNT_PRINCIPAL %s', email)
    except Exception as exc:
        server.log.warning('GOOGLE_SERVICE_ACCOUNT_PRINCIPAL_ERROR %s', type(exc).__name__)
