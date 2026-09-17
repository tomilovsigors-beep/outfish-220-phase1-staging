from __future__ import annotations

import json, os
import requests

import current_product_category_audit as v3
import current_product_category_audit_v7 as v7


def enrich_live_variant_identity(shopify, token, shop_domain, product_ids):
    query='''query V7VariantIdentity($ids:[ID!]!){nodes(ids:$ids){... on Product{id variants(first:100){nodes{id title sku barcode}}}}}'''
    r=requests.post(
        f'https://{shop_domain}/admin/api/2026-07/graphql.json',
        headers={'X-Shopify-Access-Token':token,'Content-Type':'application/json'},
        json={'query':query,'variables':{'ids':product_ids}},timeout=60)
    r.raise_for_status(); payload=r.json()
    if payload.get('errors'):
        raise RuntimeError('Shopify V7 identity query errors: '+json.dumps(payload['errors'])[:1500])
    for node in (payload.get('data') or {}).get('nodes') or []:
        if not node: continue
        p=shopify.get(node.get('id'))
        if p is None: continue
        by_id=p.setdefault('variants_by_id',{})
        for var in (node.get('variants') or {}).get('nodes') or []:
            rec=by_id.setdefault(var['id'],{'id':var['id']})
            rec['sku']=v3._norm(var.get('sku'))
            rec['barcode']=v3._norm(var.get('barcode'))
            if not rec.get('title'): rec['title']=v3._norm(var.get('title'))
    return shopify


def _ean_diag(master_ean, live_barcode, vendor):
    me=v3._norm(master_ean); lb=v3._norm(live_barcode)
    if not lb:
        return 'LIVE_BARCODE_BLANK'
    if me==lb:
        return 'EAN_EXACT'
    if vendor.casefold()=='fhm' and me.startswith('46') and lb.startswith('475'):
        return 'FHM_EAN_MIGRATION_46_TO_475_OBSERVED'
    return 'EAN_DIFF_AUXILIARY_ONLY'


def _validate_rule_sku(spec,groups,by_cat,shopify):
    members=groups.get(spec['family_key']) or []; errors=[]
    if len(members)!=spec['member_count']:
        errors.append(f'member_count={len(members)} expected={spec["member_count"]}')
    pids={v3._norm(x.get('shopify_product_id')) for x in members if v3._norm(x.get('shopify_product_id'))}
    titles={v3._norm(x.get('shopify_title') or x.get('220_title')) for x in members if v3._norm(x.get('shopify_title') or x.get('220_title'))}
    vendors={v3._norm(x.get('vendor')) for x in members if v3._norm(x.get('vendor'))}
    types={v3._norm(x.get('product_type')) for x in members if v3._norm(x.get('product_type'))}
    grouping={v3._norm(x.get('220_grouping_status')) for x in members if v3._norm(x.get('220_grouping_status'))}
    identities={(v3._norm(x.get('220_sku')),v3._norm(x.get('220_ean'))) for x in members}
    if pids!={spec['shopify_product_id']}: errors.append('shopify_product_id mismatch')
    if titles!={spec['exact_title']}: errors.append('family title mismatch')
    if vendors!={spec['vendor']}: errors.append('vendor mismatch')
    if types!={spec['product_type']}: errors.append('product_type mismatch')
    if grouping and grouping!={'PASS'}: errors.append('grouping_status not uniformly PASS')
    if len(identities)!=len(members): errors.append('canonical identity duplicate')

    product=shopify.get(spec['shopify_product_id']) or {}
    if v3._norm(product.get('title'))!=spec['exact_title']: errors.append('live Shopify title mismatch')
    if v3._norm(product.get('vendor'))!=spec['vendor']: errors.append('live Shopify vendor mismatch')
    if v3._norm(product.get('product_type'))!=spec['product_type']: errors.append('live Shopify product_type mismatch')
    desc=v3._norm(product.get('description')).casefold()
    if not ('men' in desc or 'mens' in desc or "men's" in desc): errors.append('live Shopify description lacks explicit male audience proof')

    live_variants=list((product.get('variants_by_id') or {}).values())
    live_by_sku={v3._norm(x.get('sku')):x for x in live_variants if v3._norm(x.get('sku'))}
    master_skus={v3._norm(x.get('220_sku')) for x in members if v3._norm(x.get('220_sku'))}
    live_skus=set(live_by_sku)
    if master_skus!=live_skus:
        missing=sorted(master_skus-live_skus); extra=sorted(live_skus-master_skus)
        errors.append(f'live SKU set drift master={len(master_skus)} live={len(live_skus)} missing={missing[:5]} extra={extra[:5]}')

    ean_trail=[]
    for m in members:
        sku=v3._norm(m.get('220_sku')); lv=live_by_sku.get(sku) or {}
        ean_trail.append({'sku':sku,'master_220_ean':v3._norm(m.get('220_ean')),'live_shopify_barcode':v3._norm(lv.get('barcode')),
                          'status':_ean_diag(m.get('220_ean'),lv.get('barcode'),spec['vendor'])})

    cat=by_cat.get(spec['phh_category_id'])
    if not cat: errors.append('PHH category missing')
    elif str(cat.get('allow_add_products')).casefold() not in {'true','1','yes'}: errors.append('PHH category not addable')
    if spec['phh_category_id']=='9050':
        c9050=by_cat.get('9050') or {}; c9053=by_cat.get('9053') or {}
        lv0=v3._norm(c9050.get('title_lv')).casefold(); lv3=v3._norm(c9053.get('title_lv')).casefold()
        ru0=v3._norm(c9050.get('title_ru')).casefold(); ru3=v3._norm(c9053.get('title_ru')).casefold()
        if 'virsjak' not in lv0 and 'куртк' not in ru0: errors.append('9050 outerwear localization proof missing')
        if 'žaket' not in lv3 and 'пидж' not in ru3: errors.append('9053 blazer localization proof missing')
    product['_v7_ean_trail']=ean_trail
    return members,product,cat,errors


def run_audit(master_rows,shopify,db,token=None,shop_domain=None):
    product_ids=[x['shopify_product_id'] for x in v7.RULE_SPECS]
    if token and shop_domain:
        enrich_live_variant_identity(shopify,token,shop_domain,product_ids)
    old=v7._validate_rule
    v7._validate_rule=_validate_rule_sku
    try:
        summary,artifacts=v7.run_audit(master_rows,shopify,db)
    finally:
        v7._validate_rule=old
    trails={}
    for spec in v7.RULE_SPECS:
        trails[spec['rule_id']]=(shopify.get(spec['shopify_product_id']) or {}).get('_v7_ean_trail') or []
    print('V7_FHM_EAN_MIGRATION_DIAG '+json.dumps(trails,ensure_ascii=False,sort_keys=True),flush=True)
    return summary,artifacts
