import json, os
try:
    raw=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON') or ''
    email=(json.loads(raw).get('client_email') if raw else None)
    if email:
        print('GOOGLE_SERVICE_ACCOUNT_PRINCIPAL', email, flush=True)
except Exception:
    pass
