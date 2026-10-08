from __future__ import annotations
import csv, io, json, os
from app import _master_rows

def run():
    import content_staging_app as c
    with c.FULL_CATALOG_LOCK:
        raw=(c.FULL_CATALOG.get('artifacts') or {}).get('all-existing-offers.csv')
    if not raw:
        raise RuntimeError('all-existing-offers.csv unavailable')
    rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    master={str(r.get('220_sku') or r.get('shopify_sku') or '').strip():r for r in _master_rows()}
    out=[]
    for r in rows:
        sku=str(r.get('sku') or '').strip()
        m=master.get(sku) or {}
        status=str(m.get('shopify_status') or '').strip().upper()
        title=str(m.get('shopify_title') or m.get('220_title') or '').strip()
        ptype=str(m.get('product_type') or '').strip()
        vendor=str(r.get('vendor') or m.get('vendor') or '').strip()
        cat=str(r.get('shopify_category_name') or '')
        try: phh=float(r.get('offer_price'))
        except: continue
        try: shp=float(r.get('shopify_price_live'))
        except: continue
        if abs(phh-shp)<0.005: continue
        hay=(' '.join([title,ptype,cat])).casefold()
        excluded=False; reason=''
        if status and status!='ACTIVE': excluded=True; reason='SHOPIFY_NOT_ACTIVE'
        elif shp<10: excluded=True; reason='PRICE_LT_10'
        elif vendor.casefold()=='fhm': excluded=True; reason='FHM'
        elif any(x in hay for x in ('gift card','giftcard','dāvanu karte','davanu karte','gift certificate','voucher')): excluded=True; reason='GIFT_CARD'
        elif any(x in (' '+hay+' ') for x in (' rental','rental ',' rent ',' noma','nomā',' īre','īre ','hire ')): excluded=True; reason='RENTAL'
        else:
            ptype_cf=ptype.casefold()
            title_cf=title.casefold()
            accessory_terms=('holder','mount','rack','bracket','paddle','bag','crate','seat','cover','trolley','cart','anchor','accessory','accessories','rail','carrier','roof','storage','motor mount','rod holder')
            if ('kayak' in ptype_cf or 'kajak' in ptype_cf or 'kayak' in title_cf or 'kajak' in title_cf) and not any(x in title_cf for x in accessory_terms):
                excluded=True; reason='KAYAK'
        if not excluded:
            out.append({'sku':sku,'phh_price':phh,'shopify_price':shp,'difference':round(shp-phh,2),'offer_id':r.get('offer_id')})
    print('OWNER_PRICE_MISMATCH_REPORT_COUNT '+json.dumps({'count':len(out),'writes':0},separators=(',',':')),flush=True)
    for i in range(0,len(out),40):
        print('OWNER_PRICE_MISMATCH_REPORT '+json.dumps(out[i:i+40],ensure_ascii=False,separators=(',',':')),flush=True)
    return {'count':len(out),'writes':0}

if __name__=='__main__': run()
