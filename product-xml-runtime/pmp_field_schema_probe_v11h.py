from __future__ import annotations
import json
from pmp_api_probe import discover

def run():
    result=discover(); oa=result.get('openapi_summary') or {}; schemas=oa.get('semantic_schemas') or {}
    wanted={k:v for k,v in schemas.items() if k in {'Field','Category','Category2','CategoryListItem','CategoryResponse','CategoryListResponse'}}
    out={'status':'PASS','schemas':wanted,'safety':{'writes':0,'marketplace_mutations':0}}
    print('PMP_FIELD_SCHEMA_V11H '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
