from __future__ import annotations
import json, os
from collections import Counter, defaultdict
from phh_master_audit_v30 import _master_rows_full, _s

def _latest_blocked():
    db=os.getenv('DATABASE_URL')
    if not db: raise RuntimeError('DATABASE_URL missing')
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('select max(gate_ts) from phh_identity_automation_gate_v36')
            ts=cur.fetchone()[0]
            if not ts: raise RuntimeError('v36 snapshot missing')
            cur.execute("""select row_no from phh_identity_automation_gate_v36
                           where gate_ts=%s and identity_gate='BLOCKED_HUMAN_DECISION'""",(ts,))
            blocked={int(r[0]) for r in cur.fetchall()}
    return ts,blocked

def run():
    v36_ts,blocked=_latest_blocked()
    master=_master_rows_full()
    rows=[]; counts=Counter(); reasons=Counter()
    for row_no,m in enumerate(master,start=2):
        stock_flag=_s(m.get('220_stock_feed_enabled')).upper()=='TRUE'
        if not stock_flag:
            continue
        counts['trusted_phase1']+=1
        status=_s(m.get('220_status'))
        if status!='ACTIVE_220':
            counts['trusted_inactive']+=1
            continue
        counts['trusted_active']+=1
        row_reasons=[]
        if row_no in blocked:
            row_reasons.append('BLOCKED_IDENTITY_DECISION')
        if _s(m.get('vendor')).casefold()=='naturehike':
            row_reasons.append('NATUREHIKE_NO_PRICE_STOCK_AUTH')
        if _s(m.get('shopify_status')).upper()!='ACTIVE':
            row_reasons.append('SHOPIFY_NOT_ACTIVE')
        try:
            price=float(_s(m.get('shopify_price_reference')) or 0)
        except Exception:
            price=0
        if price<10:
            row_reasons.append('PRICE_BELOW_10')
        if not _s(m.get('shopify_product_id')) or not _s(m.get('shopify_variant_id')):
            row_reasons.append('MISSING_SHOPIFY_VARIANT_ID')
        if not _s(m.get('220_sku')) or not _s(m.get('220_ean')):
            row_reasons.append('INCOMPLETE_MARKETPLACE_IDENTITY')
        for r in set(row_reasons): reasons[r]+=1
        readiness='READY_FOR_LIVE_INVENTORY_JOIN' if not row_reasons else 'EXCLUDED_FROM_STOCK_DRY_RUN'
        if readiness.startswith('READY'):
            counts['ready_for_live_inventory_join']+=1
        else:
            counts['excluded_active']+=1
        rows.append({
            'row_no':row_no,'readiness':readiness,'exclusion_reasons':'|'.join(dict.fromkeys(row_reasons)),
            'vendor':_s(m.get('vendor')),'title':_s(m.get('shopify_title')),
            'shopify_product_id':_s(m.get('shopify_product_id')),'shopify_variant_id':_s(m.get('shopify_variant_id')),
            'shopify_sku':_s(m.get('shopify_sku')),'shopify_status':_s(m.get('shopify_status')),
            'shopify_price_reference':price,'220_sku':_s(m.get('220_sku')),'220_ean':_s(m.get('220_ean')),
            '220_status':status,'220_stock_feed_enabled':_s(m.get('220_stock_feed_enabled'))
        })
    summary={
        'status':'PASS','source_v36_ts':v36_ts.isoformat() if hasattr(v36_ts,'isoformat') else str(v36_ts),
        'counts':dict(counts),'exclusion_reason_counts':dict(reasons),
        'publish_status':'DRY_RUN_ONLY',
        'phh_stock_writes_authorized':False,
        'safety':{'master_writes':0,'phh_writes':0,'shopify_writes':0}
    }
    print('PHH_STOCK_FEED_READINESS_V37_SUMMARY '+json.dumps(summary,sort_keys=True),flush=True)
    excluded=[x for x in rows if x['readiness'].startswith('EXCLUDED')]
    print('PHH_STOCK_FEED_READINESS_V37_EXCLUDED '+json.dumps(excluded,ensure_ascii=False,separators=(',',':')),flush=True)
    return summary
