"""Conservative Master↔Shopify variant resolver.

Evidence is strictly equality-based. No fuzzy titles, manufacturer codes,
lookalike UPCs, or inferred replacement EANs. Does not write to any service.
"""
from collections import defaultdict


def clean(value):
    return "" if value is None else str(value).strip()


def gtin13(raw):
    code=clean(raw)
    if len(code)==12 and code.isdigit():
        code="0"+code
    if len(code)!=13 or not code.isdigit():
        return ""
    ds=[int(x) for x in code]
    check=(10-sum(x*(1 if i%2==0 else 3) for i,x in enumerate(ds[:12]))%10)%10
    return code if ds[-1]==check else ""


def variant_id(value):
    token=clean(value)
    if token.startswith("gid://shopify/ProductVariant/"):
        token=token.rsplit("/",1)[-1]
    return token if token.isdigit() else ""


def master_variant_index(rows):
    by_vid=defaultdict(list)
    by_pair=defaultdict(list)
    for idx,row in enumerate(rows,2):
        vid=variant_id(row.get("shopify_variant_id"))
        if vid: by_vid[vid].append((idx,row))
        for sku_name,gtin_name in (("shopify_sku","shopify_barcode"),("220_sku","220_ean")):
            sku=clean(row.get(sku_name))
            ean=gtin13(row.get(gtin_name))
            if sku and ean and (idx,row) not in by_pair[(sku,ean)]:
                by_pair[(sku,ean)].append((idx,row))
    return by_vid,by_pair


def resolve_variant(variant,index):
    """An exact Shopify variant ID wins; conflicting duplicates fail closed.

    Dual SKU+GTIN fallback is allowed only when both match and the Master
    row's own source identity does not contradict live Shopify.
    """
    by_vid,by_pair=index
    vid=variant_id(variant.get("shopify_variant_id"))
    sku=clean(variant.get("shopify_sku"))
    barcode=gtin13(variant.get("shopify_barcode"))
    linked=by_vid.get(vid,[]) if vid else []
    if linked:
        if len(linked)!=1: return None,"CONFLICT_DUPLICATE_VARIANT_ID"
        row=linked[0][1]
        src_sku=clean(row.get("shopify_sku"))
        src_barcode=gtin13(row.get("shopify_barcode"))
        if src_sku and sku and src_sku!=sku:
            return None,"CONFLICT_SHOPIFY_SKU"
        if src_barcode and barcode and src_barcode!=barcode:
            return None,"CONFLICT_SHOPIFY_GTIN"
        return row,"EXACT_VARIANT_ID"
    if not sku or not barcode: return None,"INSUFFICIENT_SKU_GTIN"
    matches=by_pair.get((sku,barcode),[])
    if len(matches)>1: return None,"CONFLICT_DUPLICATE_SKU_GTIN"
    if not matches: return None,"NOT_IN_MASTER"
    row=matches[0][1]
    recorded_id=variant_id(row.get("shopify_variant_id"))
    if recorded_id and recorded_id!=vid:
        return None,"CONFLICT_DIFFERENT_VARIANT_ID"
    # If a different canonical Shopify barcode is present on the matched
    # Master row, do not reuse locale or image evidence.
    source_ean=gtin13(row.get("shopify_barcode"))
    if source_ean and source_ean!=barcode:
        return None,"CONFLICT_SHOPIFY_GTIN"
    source_sku=clean(row.get("shopify_sku"))
    if source_sku and source_sku!=sku:
        return None,"CONFLICT_SHOPIFY_SKU"
    return row,"EXACT_SKU_GTIN"


def selftest():
    example={"shopify_sku":"10592","shopify_barcode":"021563105926",
             "220_sku":"10592","220_ean":"0021563105926",
             "shopify_variant_id":""}
    index=master_variant_index([example])
    live={"shopify_variant_id":"gid://shopify/ProductVariant/123",
          "shopify_sku":"10592","shopify_barcode":"0021563105926"}
    assert resolve_variant(live,index)[1]=="EXACT_SKU_GTIN"
    assert resolve_variant({**live,"shopify_sku":"OTHER"},index)[0] is None
    assert resolve_variant(live,master_variant_index([example,example]))[0] is None
    assert gtin13("021563105926")=="0021563105926"
    assert not gtin13("021563105927")
    same_id={**example,"shopify_variant_id":"123"}
    assert resolve_variant(live,master_variant_index([same_id]))[1]=="EXACT_VARIANT_ID"
    assert resolve_variant({**live,"shopify_sku":"UNRELATED"},master_variant_index([same_id]))[1]=="CONFLICT_SHOPIFY_SKU"
    return True
