from __future__ import annotations
import json,re,requests
BASE='https://pmp.pigugroup.eu'
BUNDLE=BASE+'/build/4Vzwd4Bz.js'

def run():
    r=requests.get(BUNDLE,timeout=35,headers={'User-Agent':'outfish-phh-readonly-bundle/11f'})
    text=r.text
    # Extract bounded strings only; never execute JS.
    strings=re.findall(r'["\']([^"\']{1,240})["\']',text)
    interesting=[]
    needles=('login','auth','session','token','seller-academy','faq','integration','api','attribute','category','product','xml','field','value')
    for s in strings:
        low=s.lower()
        if any(n in low for n in needles) and (s.startswith('/') or 'http' in low or 'seller-academy' in low or 'faq' in low):
            interesting.append(s)
    uniq=[]; seen=set()
    for s in interesting:
        if s not in seen:
            seen.add(s); uniq.append(s)
    out={'status':'PASS','http_status':r.status_code,'bytes':len(r.content),'interesting_strings':uniq[:1000],'interesting_count':len(uniq),'markers':{k:(k in text.lower()) for k in needles},'safety':{'credentials_used':False,'js_executed':False,'writes':0}}
    print('PMP_PORTAL_BUNDLE_V11F_RESULT '+json.dumps(out,ensure_ascii=False,sort_keys=True)[:100000],flush=True)
    return out
