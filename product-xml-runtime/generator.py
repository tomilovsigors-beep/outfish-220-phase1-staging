from __future__ import annotations
import csv, io, json
from collections import Counter
from xml.etree.ElementTree import Element, tostring

SPECIAL_CANONICAL_BLOCKED={'NH21MSD08L','NH21MSD08R'}
TRUE_VALUES={'1','true','TRUE','yes','YES'}


def norm(v):
    return '' if v is None else str(v).strip()


def canonical_master(rows):
    out={}
    for r in rows:
        sku=norm(r.get('220_sku')); status=norm(r.get('220_status'))
        if sku and (status=='ACTIVE_220' or sku in SPECIAL_CANONICAL_BLOCKED):
            if sku in out:
                raise RuntimeError(f'duplicate canonical Master SKU {sku}')
            out[sku]=r
    return out


def conflict_skus(rows):
    return {norm(r.get('220_sku')) for r in rows if norm(r.get('match_status'))=='DUPLICATE_MASTER_220_SKU_CONFLICT'}


def _csv_bytes(rows, fields):
    s=io.StringIO(newline='')
    w=csv.DictWriter(s,fieldnames=fields)
    w.writeheader(); w.writerows(rows)
    return s.getvalue().encode('utf-8-sig')


def build(master_rows, shopify_by_product_id, phh_spec=None):
    phh_spec=phh_spec or {}
    canonical=canonical_master(master_rows); conflicts=conflict_skus(master_rows)
    spec_ready=bool(
        phh_spec.get('authoritative')
        and phh_spec.get('root_element')
        and phh_spec.get('product_mapping')
        and phh_spec.get('category_mapping') is not None
        and phh_spec.get('required_attributes') is not None
    )
    readiness=[]; blockers=[]
    for sku in sorted(canonical):
        r=canonical[sku]
        pid=norm(r.get('shopify_product_id')); vid=norm(r.get('shopify_variant_id'))
        src=shopify_by_product_id.get(pid) if pid and vid else None
        ean=norm(r.get('220_ean')); title=norm(r.get('220_title'))
        override_img=norm(r.get('220_main_image_url'))
        shop_img=norm(r.get('shopify_main_image_url')) or norm((src or {}).get('main_image_url'))
        resolved_img=override_img or shop_img
        reasons=[]
        if not sku: reasons.append('MISSING_220_SKU')
        if not ean: reasons.append('MISSING_220_EAN')
        if norm(r.get('220_status'))!='ACTIVE_220': reasons.append('LIFECYCLE_NOT_PRODUCT_XML_ALLOWED')
        if norm(r.get('220_product_xml_enabled')) not in TRUE_VALUES: reasons.append('PRODUCT_XML_NOT_ENABLED')
        if not title: reasons.append('MISSING_220_TITLE')
        if not resolved_img: reasons.append('MISSING_RESOLVED_MAIN_IMAGE')
        elif not resolved_img.lower().startswith('https://'): reasons.append('MAIN_IMAGE_NOT_HTTPS')
        if not src: reasons.append('NO_SAFE_SHOPIFY_MAPPING')
        if sku in conflicts: reasons.append('DUPLICATE_MASTER_220_SKU_CONFLICT')
        if not norm((src or {}).get('description')): reasons.append('MISSING_DESCRIPTION_CONTENT')
        category_status='SOURCE_PRESENT_MAPPING_REQUIRED' if norm((src or {}).get('category_id')) else 'MISSING_SOURCE_CATEGORY'
        if category_status=='MISSING_SOURCE_CATEGORY': reasons.append('MISSING_CATEGORY_SOURCE')
        if not spec_ready:
            reasons.extend(['PHH_CATEGORY_MAPPING_REQUIRED','PHH_REQUIRED_ATTRIBUTES_SPEC_UNAVAILABLE','PHH_AUTHORITATIVE_XML_SPEC_UNAVAILABLE'])
        reasons=list(dict.fromkeys(reasons))
        eligibility='READY_PRODUCT_XML' if not reasons else 'BLOCKED_PRODUCT_XML'
        row={
            '220_sku':sku,
            '220_ean':ean,
            'shopify_title':norm(r.get('shopify_title')) or norm((src or {}).get('title')),
            '220_title':title,
            'shopify_main_image_url':shop_img,
            '220_main_image_url':override_img,
            'resolved_main_image':resolved_img,
            'vendor':norm(r.get('vendor')) or norm((src or {}).get('vendor')),
            'product_type':norm(r.get('product_type')) or norm((src or {}).get('product_type')),
            'category_status':category_status,
            'attributes_status':'VALIDATE_FROM_PHH_SPEC' if spec_ready else 'PHH_SPEC_REQUIRED',
            'description_status':'PRESENT' if norm((src or {}).get('description')) else 'MISSING',
            'image_status':'OK_HTTPS' if resolved_img.lower().startswith('https://') else ('MISSING' if not resolved_img else 'INVALID'),
            'lifecycle_status':norm(r.get('220_status')),
            'product_xml_eligibility':eligibility,
            'blocker_reasons':'|'.join(reasons),
        }
        readiness.append(row)
        if reasons:
            blockers.append({'220_sku':sku,'220_ean':ean,'blocker_reasons':row['blocker_reasons']})

    ready=[r for r in readiness if r['product_xml_eligibility']=='READY_PRODUCT_XML']
    if spec_ready:
        if ready:
            raise RuntimeError('PHH product element renderer must be implemented from the authoritative spec before publication')
        root=Element(phh_spec['root_element'])
        xml=b'<?xml version="1.0" encoding="UTF-8"?>\n'+tostring(root,encoding='utf-8')
    else:
        # Internal staging marker, explicitly NOT a PHH schema document.
        root=Element('dry-run-product-xml',status='BLOCKED',reason='PHH_AUTHORITATIVE_XML_SPEC_UNAVAILABLE')
        xml=b'<?xml version="1.0" encoding="UTF-8"?>\n'+tostring(root,encoding='utf-8')+b'\n'

    counts=Counter(x for r in readiness for x in filter(None,r['blocker_reasons'].split('|')))
    validation={
        'canonical_identities':len(readiness),
        'ready_product_xml':len(ready),
        'blocked_product_xml':len(blockers),
        'publish_gate':'PASS' if spec_ready and not blockers else 'BLOCKED',
        'authoritative_phh_spec_loaded':spec_ready,
        'blocker_breakdown':dict(sorted(counts.items())),
    }
    fields=list(readiness[0]) if readiness else []
    return {
        'product-xml-readiness.csv':_csv_bytes(readiness,fields),
        'product-xml-blockers.csv':_csv_bytes(blockers,['220_sku','220_ean','blocker_reasons']),
        'product-xml-dry-run.xml':xml,
        'product-xml-validation.json':json.dumps(validation,indent=2,sort_keys=True).encode(),
    }
