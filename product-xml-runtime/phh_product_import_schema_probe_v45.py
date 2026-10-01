from __future__ import annotations
from pmp_api_probe import _docs_get,_embedded_spec

TARGETS=('ProductImportRequestPost','ProductImportRequestPatch','ModificationImportRequest','ModificationImportRequestPatch')

def run():
    r=_docs_get('/docs'); r.raise_for_status()
    spec=_embedded_spec(r.text)
    schemas=((spec.get('components') or {}).get('schemas') or {})
    out={}
    for name in TARGETS:
        if name in schemas:
            out[name]=schemas[name]
    return {'status':'PASS','schemas':out,'writes':0}
