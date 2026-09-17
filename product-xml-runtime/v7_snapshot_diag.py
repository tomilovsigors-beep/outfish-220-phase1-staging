from __future__ import annotations
import json
from current_product_category_audit_v7 import load_latest_artifact

def run(db):
    b=load_latest_artifact(db,'v7-rule-audit.json')
    if not b: raise RuntimeError('v7 rule audit unavailable')
    obj=json.loads(b.decode('utf-8-sig'))
    print('V7_RULE_VALIDATION_DIAG '+json.dumps(obj,ensure_ascii=False,sort_keys=True),flush=True)
    return obj
