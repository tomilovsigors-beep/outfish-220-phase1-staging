from __future__ import annotations
import json
from pmp_api_probe import _docs_get,_embedded_spec
def run():
    r=_docs_get('/docs'); r.raise_for_status()
    spec=_embedded_spec(r.text)
    schemas=(spec.get('components') or {}).get('schemas') or {}
    names=[n for n in schemas if any(k in n.lower() for k in ('productimportrequest','productimportexecution','offerimport','importrequest'))]
    out={n:schemas[n] for n in names}
    print('PHH_IMPORT_SCHEMA_PROBE '+json.dumps(out,ensure_ascii=False,separators=(',',':')),flush=True)
    return {'status':'PASS','schemas':names}
if __name__=='__main__': run()
