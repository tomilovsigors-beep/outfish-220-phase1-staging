from __future__ import annotations
import json,re,requests
from urllib.parse import urljoin

BASE='https://pmp.pigugroup.eu'

def run():
    r=requests.get(BASE+'/login',timeout=25,allow_redirects=True,headers={'User-Agent':'outfish-phh-readonly-login-structure/11e'})
    text=r.text
    scripts=re.findall(r'<script[^>]+src=["\']([^"\']+)["\']',text,re.I)
    forms=[]
    for f in re.findall(r'<form\b[^>]*>.*?</form>',text,re.I|re.S):
        action=(re.search(r'action=["\']([^"\']*)["\']',f,re.I) or [None,''])[1]
        method=(re.search(r'method=["\']([^"\']*)["\']',f,re.I) or [None,''])[1]
        inputs=[]
        for tag in re.findall(r'<input\b[^>]*>',f,re.I):
            nm=(re.search(r'name=["\']([^"\']+)["\']',tag,re.I) or [None,''])[1]
            tp=(re.search(r'type=["\']([^"\']+)["\']',tag,re.I) or [None,''])[1]
            if nm or tp: inputs.append({'name':nm,'type':tp})
        forms.append({'action':action,'method':method,'inputs':inputs})
    # Extract only endpoint-like strings and auth-related markers, no page text dump.
    endpoint_candidates=sorted(set(re.findall(r'["\'](\/[^"\']{1,180}(?:login|auth|session|token)[^"\']{0,180})["\']',text,re.I)))[:200]
    markers={k:(k.lower() in text.lower()) for k in ['csrf','_token','recaptcha','two-factor','2fa','email','password','username','login','sign in','vue','react','axios']}
    out={'status':'PASS','http_status':r.status_code,'url':r.url,'bytes':len(r.content),'forms':forms,'script_count':len(scripts),'scripts':[urljoin(r.url,s) for s in scripts[:100]],'endpoint_candidates':endpoint_candidates,'markers':markers,'safety':{'credentials_used':False,'secrets_printed':0,'writes':0}}
    print('PMP_PORTAL_LOGIN_STRUCTURE_V11E_RESULT '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
