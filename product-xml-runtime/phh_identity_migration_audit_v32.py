from __future__ import annotations
import json, time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from phh_master_audit_v30 import _master_rows_full, lookup_ean, _s, _offer_eans, _offer_skus
from phh_orphan_offer_export_v31 import scan_offers
from pmp_api_probe import _api_login

def _prefix(e):
    e=_s(e)
    return e[:3] if len(e)>=3 else e

def run():
    print('PHH_IDENTITY_MIGRATION_AUDIT_V32_STAGE '+json.dumps({'stage':'start'}),flush=True)
    master=_master_rows_full()
    print('PHH_IDENTITY_MIGRATION_AUDIT_V32_STAGE '+json.dumps({'stage':'master_loaded','rows':len(master)}),flush=True)
    lr=_api_login('v3'); lr.raise_for_status(); token=lr.json()['token']
    offers=scan_offers(token)
    print('PHH_IDENTITY_MIGRATION_AUDIT_V32_STAGE '+json.dumps({'stage':'offers_loaded','offers':len(offers)}),flush=True)
    by_ean=defaultdict(list); by_sku=defaultdict(list)
    for o in offers:
        for e in _offer_eans(o): by_ean[e].append(o)
        for s in _offer_skus(o): by_sku[s].append(o)

    targets=[]
    for row_no,row in enumerate(master,start=2):
        ss=_s(row.get('shopify_sku')); sb=_s(row.get('shopify_barcode'))
        s220=_s(row.get('220_sku')); e220=_s(row.get('220_ean'))
        vendor=_s(row.get('vendor')); title=_s(row.get('shopify_title'))
        mst=_s(row.get('220_status'))
        matches=[]
        for e in [x for x in (e220,sb) if x]:
            matches.extend(by_ean.get(e,[]))
        if not matches:
            for s in [x for x in (s220,ss) if x]:
                matches.extend(by_sku.get(s,[]))
        # dedupe
        uniq=[]; seen=set()
        for o in matches:
            oid=_s(o.get('id')) or str(id(o))
            if oid not in seen: seen.add(oid); uniq.append(o)
        matches=uniq
        offer=None
        if matches:
            active=[o for o in matches if _s(o.get('status')).lower()=='active']
            offer=active[0] if active else matches[0]
        live_state='NOT_IN_220'
        if offer:
            live_state='OFFER_ACTIVE' if _s(offer.get('status')).lower()=='active' else 'OFFER_INACTIVE'
        preliminary_mismatch = (
            (mst=='NOT_IN_220' and live_state!='NOT_IN_220') or
            (mst=='ACTIVE_220' and live_state=='NOT_IN_220')
        )
        live_eans=sorted(_offer_eans(offer)) if offer else []
        primary_live_ean=_s(((offer or {}).get('modification') or {}).get('ean'))
        identity_mismatch=bool(offer and e220 and primary_live_ean and e220!=primary_live_ean and sb!=primary_live_ean)
        is_fhm=(vendor.upper()=='FHM' or 'FHM' in title.upper())
        ru_lv_pattern=bool(sb.startswith('462') and (e220.startswith('475') or any(e.startswith('475') for e in live_eans)))
        if is_fhm or preliminary_mismatch or identity_mismatch:
            targets.append({
                'row_no':row_no,'row':row,'offer':offer,'matches':matches,
                'is_fhm':is_fhm,'preliminary_mismatch':preliminary_mismatch,
                'identity_mismatch':identity_mismatch,'ru_lv_pattern':ru_lv_pattern
            })

    lookup_eans=set()
    for t in targets:
        r=t['row']; o=t['offer']
        for e in (_s(r.get('shopify_barcode')),_s(r.get('220_ean'))):
            if e and e.isdigit(): lookup_eans.add(e)
        if o:
            for e in _offer_eans(o):
                if e and e.isdigit(): lookup_eans.add(e)

    print('PHH_IDENTITY_MIGRATION_AUDIT_V32_STAGE '+json.dumps({'stage':'targets_built','targets':len(targets),'lookup_eans':len(lookup_eans)}),flush=True)
    lookup={}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs={ex.submit(lookup_ean,token,e):e for e in sorted(lookup_eans)}
        done=0
        for f in as_completed(futs):
            e=futs[f]
            try: lookup[e]=f.result()
            except Exception as er: lookup[e]={'exists_220':False,'http':0,'error':f'{type(er).__name__}: {er}','items':[]}
            done+=1
            if done%100==0 or done==len(futs): print('PHH_IDENTITY_MIGRATION_AUDIT_V32_PROGRESS '+json.dumps({'done':done,'total':len(futs)}),flush=True)

    rows=[]; classes=Counter(); fhm_classes=Counter()
    for t in targets:
        r=t['row']; o=t['offer']; row_no=t['row_no']
        ss=_s(r.get('shopify_sku')); sb=_s(r.get('shopify_barcode'))
        s220=_s(r.get('220_sku')); e220=_s(r.get('220_ean')); mst=_s(r.get('220_status'))
        live_sku=_s(((o or {}).get('modification') or {}).get('sku'))
        live_eans=sorted(_offer_eans(o)) if o else []
        offer_status=_s((o or {}).get('status')).lower()
        shop_card=bool(sb and (lookup.get(sb) or {}).get('exists_220'))
        master_card=bool(e220 and (lookup.get(e220) or {}).get('exists_220'))
        live_card_eans=[e for e in live_eans if (lookup.get(e) or {}).get('exists_220')]

        cls='REVIEW'
        reason=[]
        if t['is_fhm'] and sb.startswith('462') and e220.startswith('475'):
            if master_card:
                cls='FHM_RU_TO_LV_MASTER_EAN_CONFIRMED'
                reason.append('master_220_ean_exists_as_220_card')
            elif offer_status=='reset' and live_sku and live_sku in (ss,s220) and any(e.startswith('475') for e in live_eans):
                cls='FHM_RU_TO_LV_LEGACY_RESET_OFFER'
                reason.append('same_sku_reset_offer_with_legacy_475_ean')
            else:
                cls='FHM_RU_TO_LV_REVIEW'
        elif t['is_fhm'] and t['identity_mismatch']:
            if master_card:
                cls='FHM_MASTER_EAN_CONFIRMED_LIVE_OFFER_LEGACY'
                reason.append('master_220_ean_exists_as_220_card')
            elif offer_status=='reset':
                cls='FHM_LEGACY_RESET_EAN_REVIEW'
            else:
                cls='FHM_ACTIVE_EAN_MISMATCH_REVIEW'
        elif t['identity_mismatch']:
            if master_card and offer_status=='reset':
                cls='MASTER_EAN_CONFIRMED_RESET_OFFER_LEGACY'
            elif master_card:
                cls='MASTER_EAN_CONFIRMED_OFFER_EAN_DIFF'
            else:
                cls='EAN_IDENTITY_MISMATCH_REVIEW'
        elif mst=='ACTIVE_220' and not o:
            if master_card or shop_card:
                cls='ACTIVE_CARD_ONLY'
            else:
                cls='FALSE_ACTIVE_NO_CARD_NO_OFFER'
        elif mst=='NOT_IN_220' and o:
            cls='FALSE_NOT_IN_220_ACTIVE' if offer_status=='active' else 'FALSE_NOT_IN_220_INACTIVE'

        rec={
            'row_no':row_no,'vendor':_s(r.get('vendor')),'title':_s(r.get('shopify_title')),
            'shopify_sku':ss,'shopify_barcode':sb,'master_220_sku':s220,'master_220_ean':e220,
            'master_220_status':mst,'match_status':_s(r.get('match_status')),
            'offer_id':_s((o or {}).get('id')),'offer_status':offer_status,'live_sku':live_sku,
            'live_eans':live_eans,'shopify_barcode_card_220':shop_card,'master_ean_card_220':master_card,
            'live_eans_card_220':live_card_eans,'ru_lv_pattern':t['ru_lv_pattern'],
            'classification':cls,'reason':'|'.join(reason)
        }
        rows.append(rec); classes[cls]+=1
        if t['is_fhm']: fhm_classes[cls]+=1

    fhm=[x for x in rows if x['vendor'].upper()=='FHM' or 'FHM' in x['title'].upper()]
    fhm_ru=[x for x in fhm if x['ru_lv_pattern']]
    summary={
        'status':'PASS','master_rows':len(master),'seller_offers':len(offers),'target_rows':len(rows),
        'lookup_eans':len(lookup_eans),'lookup_errors':sum(1 for x in lookup.values() if x.get('http') not in (200,404)),
        'class_counts':dict(classes),'fhm_target_rows':len(fhm),'fhm_class_counts':dict(fhm_classes),
        'fhm_ru_lv_rows':len(fhm_ru),
        'safety':{'phh_writes':0,'master_writes':0,'shopify_writes':0}
    }
    print('PHH_IDENTITY_MIGRATION_AUDIT_V32_SUMMARY '+json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
    chunk=60; total=(len(rows)+chunk-1)//chunk
    for i in range(total):
        print(f'PHH_IDENTITY_MIGRATION_AUDIT_V32 {i+1}/{total} '+json.dumps(rows[i*chunk:(i+1)*chunk],ensure_ascii=False,separators=(',',':')),flush=True)
    print('PHH_IDENTITY_MIGRATION_AUDIT_V32_FHM '+json.dumps(fhm,ensure_ascii=False,separators=(',',':')),flush=True)
    return summary
