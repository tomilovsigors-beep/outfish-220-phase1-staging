from pathlib import Path
import base64,hashlib,lzma
ROOT=Path(__file__).resolve().parent
BUNDLE=ROOT/'config'/'locked_mapped_gids.b64'
EXPECTED_CANONICAL_SKU_SHA256='fc7a2e790ffb2423a5ba9bf025f6d35859cd88ac0058dc00b8d8bdc97379652f'

def _read_varint(data,pos):
    n=0; shift=0
    while True:
        if pos>=len(data): raise RuntimeError('truncated varint')
        b=data[pos]; pos+=1; n|=(b&127)<<shift
        if not b&128:return n,pos
        shift+=7

def _unzig(n): return -(n//2)-1 if n&1 else n//2

def load_locked_mapped(canonical_skus):
    canonical_skus=list(canonical_skus)
    actual_hash=hashlib.sha256('\n'.join(canonical_skus).encode()).hexdigest()
    if actual_hash!=EXPECTED_CANONICAL_SKU_SHA256:
        raise RuntimeError(f'canonical Master SKU set/order drift: {actual_hash} != locked {EXPECTED_CANONICAL_SKU_SHA256}')
    if not BUNDLE.exists(): raise FileNotFoundError(BUNDLE)
    raw=lzma.decompress(base64.b64decode(BUNDLE.read_bytes()))
    out={}; pos=0; idx=0; gid=0
    while pos<len(raw):
        di,pos=_read_varint(raw,pos); zg,pos=_read_varint(raw,pos)
        idx+=di; gid+=_unzig(zg)
        if idx>=len(canonical_skus): raise RuntimeError('locked mapped index outside canonical Master set')
        sku=canonical_skus[idx]
        if sku in out: raise RuntimeError('duplicate locked mapped SKU')
        out[sku]='gid://shopify/ProductVariant/'+str(gid)
    if len(out)!=970 or len(set(out.values()))!=970:
        raise RuntimeError(f'locked mapped gate failed {len(out)}/{len(set(out.values()))}')
    return out
