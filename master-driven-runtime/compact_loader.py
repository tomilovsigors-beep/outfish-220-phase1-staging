from pathlib import Path
import base64,csv,gzip
ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'config'
MAP=CONFIG/'inventory_source_map.csv'
BASE=CONFIG/'production_equivalent_baseline.csv'

def decode_b64_gz(path):
    return gzip.decompress(base64.b64decode(path.read_bytes())).decode('utf-8').splitlines()

def ensure_config():
    sm=CONFIG/'source_map_min.b64'; bm=CONFIG/'baseline_min.b64'
    if not sm.exists() or not bm.exists():
        return
    MAP.parent.mkdir(parents=True,exist_ok=True)
    with MAP.open('w',encoding='utf-8',newline='') as f:
        fields=['220_sku','220_ean','inventory_source_type','inventory_source_key']
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for line in decode_b64_gz(sm):
            p=line.split('|')
            if p[0]=='S' and len(p)==4:
                w.writerow({'220_sku':p[1],'220_ean':p[2],'inventory_source_type':'SHOPIFY_MAPPED','inventory_source_key':'gid://shopify/ProductVariant/'+p[3]})
            elif p[0]=='E' and len(p)==3:
                w.writerow({'220_sku':p[1],'220_ean':p[2],'inventory_source_type':'LEGACY_EXTERNAL_SOURCE','inventory_source_key':'SIA_FHM:Sheet1:sku='+p[1]})
            else: raise RuntimeError('invalid compact source map line')
    with BASE.open('w',encoding='utf-8',newline='') as f:
        fields=['220_sku','baseline_stock','baseline_collectionhours']
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for line in decode_b64_gz(bm):
            p=line.split('|',2)
            if len(p)!=3: raise RuntimeError('invalid compact baseline line')
            w.writerow({'220_sku':p[0],'baseline_stock':p[1],'baseline_collectionhours':p[2]})
ensure_config()
