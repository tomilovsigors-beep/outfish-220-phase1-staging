"""Read-only live taxonomy and reference lookup for CATEGORY_REFS. No PHH mutations."""
import json,re,os
from pmp_api_probe import _api_login,_api_get
from pmp_category_exporter import export_all
def run():
    try:
        lr=_api_login('v3')
        if lr is None or not lr.ok:raise RuntimeError('PHH auth unavailable')
        token=lr.json()['token']
        summary,arts=export_all('v3',100,persist=False,token=token)
        data=json.loads(arts['pmp-categories-full.json'])
        cats=data['category_list'];by={str(c['category_id']):c for c in cats}
        pat=re.compile(r'cap|hat|headband|glove|neck|balaclava|sleeping|hammock|tarp|tent|towel|knife|stove|lamp|cookware|pole|backpack|mug|bottle|repair|glue|adhes|sealan|saw|auger|drill|blanket|poncho|fire|life.?jacket|buoyancy|thermal|underwear|clip|clamp|kayak',re.I)
        selected=[]
        for c in cats:
            if not c.get('allow_add_products') or not pat.search(c.get('title_en') or ''):continue
            path=[];cur=c;seen=set()
            while cur and str(cur.get('category_id')) not in seen:
                seen.add(str(cur.get('category_id')));path.append(cur.get('title_en') or cur.get('title_lv') or str(cur['category_id']));cur=by.get(str(cur.get('parent_id')))
            out={k:c.get(k) for k in ('category_id','parent_id','title_en','title_lv','title_lt','title_ru','allow_add_products')}
            out['path']=' > '.join(reversed(path))
            out['required_fields']=[{k:a.get(k) for k in ('field_id','title_lt','title_lv','title_ru')} for a in c.get('attributes',[]) if a.get('required')]
            selected.append(out)
            print('CATEGORY_REFS_TAXONOMY '+json.dumps(out,ensure_ascii=False),flush=True)
        for ean in ('4751043570178','4751043571150'):
            r=_api_get('/v3/products/product-modifications/barcodes?ean='+ean,token)
            try:b=r.json()
            except Exception:b={'raw':r.text[:2000]}
            print('CATEGORY_REFS_REFERENCE '+json.dumps({'ean':ean,'http':r.status_code,'body':b},ensure_ascii=False),flush=True)
        print('CATEGORY_REFS_DONE '+json.dumps({'categories_fetched':len(cats),'selected':len(selected),'summary':summary,'phh_writes':0},ensure_ascii=False),flush=True)
    except Exception as e:print('CATEGORY_REFS_ERROR '+json.dumps({'error':type(e).__name__+': '+str(e)},ensure_ascii=False),flush=True)
