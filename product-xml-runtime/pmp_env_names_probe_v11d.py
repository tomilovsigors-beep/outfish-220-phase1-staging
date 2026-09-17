from __future__ import annotations
import json, os

def run():
    keys=sorted(k for k in os.environ if any(x in k.upper() for x in ('PMP','PIGU','PORTAL','SELLER')))
    # names only, never values
    summary={'status':'PASS','matching_env_names':keys,'matching_env_count':len(keys),'safety':{'secrets_printed':0,'writes':0}}
    print('PMP_ENV_NAMES_V11D_RESULT '+json.dumps(summary,sort_keys=True),flush=True)
    return summary
