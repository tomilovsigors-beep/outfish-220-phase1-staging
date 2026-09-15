from pathlib import Path
import base64,csv,gzip,io,json
ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'config'
MAP=CONFIG/'inventory_source_map.csv'
BASE=CONFIG/'production_equivalent_baseline.csv'

def ensure_config():
    if MAP.exists() and BASE.exists():
        return
    parts=sorted(CONFIG.glob('bundle.part*'))
    if not parts:
        return
    enc=''.join(p.read_text() for p in parts)
    obj=json.loads(gzip.decompress(base64.b64decode(enc)).decode())
    MAP.parent.mkdir(parents=True,exist_ok=True)
    with MAP.open('w',encoding='utf-8',newline='') as f:
        fields=['220_sku','220_ean','inventory_source_type','inventory_source_key']
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for r in obj['m']:
            typ='SHOPIFY_MAPPED' if r['t']=='S' else 'LEGACY_EXTERNAL_SOURCE'
            key=('gid://shopify/ProductVariant/'+r['g']) if r['t']=='S' else ('SIA_FHM:Sheet1:sku='+r['s'])
            w.writerow({'220_sku':r['s'],'220_ean':r['e'],'inventory_source_type':typ,'inventory_source_key':key})
    with BASE.open('w',encoding='utf-8',newline='') as f:
        fields=['220_sku','baseline_stock','baseline_collectionhours']
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for r in obj['b']:
            w.writerow({'220_sku':r['s'],'baseline_stock':r['k'],'baseline_collectionhours':r['h']})
ensure_config()
