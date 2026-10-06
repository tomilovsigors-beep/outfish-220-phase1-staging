"""Read-only no-duplicate/category discovery for Bushmen BUSHBED PRO/CAMO.

Strictly NO PHH, Shopify, or Master writes. All PHH lookup failures mark
existence UNKNOWN, NEVER absent. No POST import or PATCH code here.
"""
from __future__ import annotations
from pmp_api_probe import _api_login, _api_get
SKU='5902194520677'
EAN='5902194520677'
SELLER='9990696'
CATEGORY=11810

def _values(obj):
    if isinstance(obj,dict):
        for k,v in obj.items():
            if k in ('id','sku','ean','barcode','ean_codes','manufacturer_code','pigu_external_id'):
                if isinstance(v,(str,int)): yield str(v)
            yield from _values(v)
    elif isinstance(obj,list):
        for v in obj: yield from _values(v)
    elif isinstance(obj,(str,int)):
        yield str(obj)

def run():
    out={'sku':SKU,'ean':EAN,'category_id_candidate':CATEGORY,'writes':0,
         'duplicate_state':'UNKNOWN','ready_to_create':False}
    login=_api_login('v3')
    if login is None or not login.ok:
        out.update(status='BLOCKED_AUTH',auth_http_status=None if login is None else login.status_code)
        return out
    token=login.json().get('token')
    if not token:
        out['status']='BLOCKED_MISSING_TOKEN';return out
    try:
        me=_api_get('/v3/sellers/me',token,timeout=12)
        if not me.ok:
            out.update(status='BLOCKED_SELLER_LOOKUP',http_status=me.status_code);return out
        mj=me.json()
        seller=str(mj.get('id') or (mj.get('seller') or {}).get('id') or '')
        if seller!=SELLER:
            out.update(status='BLOCKED_WRONG_SELLER',seller=seller);return out
        barcode=_api_get(f'/v3/products/product-modifications/barcodes?ean={EAN}',token,timeout=14)
        if not barcode.ok:
            out.update(status='BLOCKED_BARCODE_LOOKUP',http_status=barcode.status_code);return out
        barcode_data=barcode.json()
        if not isinstance(barcode_data,(list,dict)):
            out['status']='BLOCKED_BARCODE_RESPONSE_SHAPE';return out
        out['barcode_match_count']=len(barcode_data) if isinstance(barcode_data,list) else None
        out['barcode_matches']=[{'id':row.get('id'),'category_id':((row.get('modification') or {}).get('category') or {}).get('id'),'app_name':(row.get('modification') or {}).get('app_name')} for row in barcode_data if isinstance(row,dict)][:8] if isinstance(barcode_data,list) else []
        if barcode_data:
            out['duplicate_state']='EXISTING_PRODUCT_BARCODE'
            out['status']='SKIP_EXISTING_PRODUCT'
            return out
        n=0
        while n<10000:
            offers=_api_get(f'/v2/sellers/{SELLER}/offers?app_name=220.lv&limit=100&offset={n}',token,timeout=14)
            if not offers.ok:
                out.update(status='BLOCKED_OFFERS_LOOKUP',http_status=offers.status_code,offers_scanned=n);return out
            d=offers.json()
            arr=(d.get('offers') if isinstance(d,dict) else None) or (d.get('items') if isinstance(d,dict) else None) or (d if isinstance(d,list) else None)
            if not isinstance(arr,list):
                out['status']='BLOCKED_OFFERS_SHAPE';return out
            for x in arr:
                if SKU in set(_values(x)) or EAN in set(_values(x)):
                    out.update(status='SKIP_EXISTING_SELLER_OFFER',duplicate_state='EXISTING_OFFER',offers_scanned=n+len(arr))
                    return out
            n+=len(arr)
            if len(arr)<100:break
        if n>=10000:
            out.update(status='BLOCKED_OFFERS_SCAN_LIMIT',offers_scanned=n);return out
        out['offers_scanned']=n
        category=_api_get(f'/v3/categories?id={CATEGORY}&limit=10&offset=0',token,timeout=16)
        if not category.ok:
            out.update(status='BLOCKED_CATEGORY_LOOKUP',http_status=category.status_code);return out
        categories=category.json()
        matches=[c for c in (categories.get('category_list') or []) if str(c.get('category_id'))==str(CATEGORY)]
        if len(matches)!=1:
            out['status']='BLOCKED_CATEGORY_NOT_PROVEN';return out
        fields=matches[0].get('attributes') or []
        out['required_fields']=[{'field_id':f.get('field_id'),'title_lt':f.get('title_lt'),'required':f.get('required'),
                'allowed_values_available':bool(f.get('allowed_values') or f.get('values') or f.get('options'))}
                for f in fields if f.get('required')]
        out['category_confirmed']=True
        out['duplicate_state']='NOT_FOUND_IN_CHECKED_ENDPOINTS'
        out['status']='CANDIDATE_NEEDS_DICTIONARY_AND_PACKAGE_VERIFICATION'
        return out
    except Exception as e:
        out.update(status='BLOCKED_QUERY_ERROR',error_type=type(e).__name__)
        return out

if __name__=='__main__':
    import json
    print(json.dumps(run(),ensure_ascii=False))
