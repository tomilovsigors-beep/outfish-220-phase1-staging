from __future__ import annotations
import json
from pmp_api_probe import _docs_get,_embedded_spec
PATHS=[
'/{version}/sellers/{sellerId}/barcodes/check/execution',
'/{version}/sellers/{sellerId}/barcodes/check/execution/{executionId}',
'/{version}/sellers/{sellerId}/barcodes/check/execution/{executionId}/results',
]
def slim_schema(s):
    if not isinstance(s,dict): return s
    out={}
    for k in ('$ref','type','required','properties','items','example','description'):
        if k in s: out[k]=s[k]
    return out
def run():
    r=_docs_get('/docs'); r.raise_for_status(); spec=_embedded_spec(r.text)
    out={}
    for p in PATHS:
        ops=(spec.get('paths') or {}).get(p) or {}
        po={}
        for m in ('get','post'):
            op=ops.get(m)
            if not isinstance(op,dict): continue
            rb=(op.get('requestBody') or {}).get('content') or {}
            po[m.upper()]={'parameters':op.get('parameters'),'requestBody':{ct:slim_schema(v.get('schema') or {}) for ct,v in rb.items()},'responses':op.get('responses')}
        out[p]=po
    print('PHH_BARCODE_CHECK_SCHEMA_V28 '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
