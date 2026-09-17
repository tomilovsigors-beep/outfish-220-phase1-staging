from __future__ import annotations
import json
from pmp_api_probe import discover

def run():
    result = discover()
    oa = result.get('openapi_summary') or {}
    gets = []
    for item in oa.get('semantic_paths') or []:
        for op in item.get('operations') or []:
            if op.get('method') == 'GET':
                gets.append({
                    'path': item.get('path'),
                    'summary': op.get('summary'),
                    'operationId': op.get('operationId'),
                    'parameters': op.get('parameters'),
                })
    out = {
        'status': 'PASS',
        'get_count': len(gets),
        'gets': gets,
        'schema_names': sorted((oa.get('semantic_schemas') or {}).keys()),
        'safety': {'writes': 0, 'marketplace_mutations': 0},
    }
    print('PMP_SEMANTIC_GET_INVENTORY_V11G ' + json.dumps(out, ensure_ascii=False, sort_keys=True), flush=True)
    return out
