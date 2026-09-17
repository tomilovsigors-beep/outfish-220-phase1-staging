from __future__ import annotations
import json, os
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pmp_api_probe import _api_login,_api_get

def D(x):
    try: return Decimal(str(x))
    except Exception: return None

def run():
    lr=_api_login('v3')
    if lr is None or not lr.ok: raise RuntimeError('login failed')
    token=lr.json().get('token')
    me=_api_get('/v3/sellers/me',token); me.raise_for_status(); md=me.json()
    seller_id=md.get('id') or (md.get('seller') or {}).get('id')

    invoice_rows=[]
    for inv in (md.get('invoices') or []):
        if not isinstance(inv,dict): continue
        invoice_rows.append({k:inv.get(k) for k in ('id','invoiceDate','documentNo','documentType','description','amount','remainingAmount','paymentDate','createDate','externalId','appName')})

    rows=[]
    for offset in range(0,500,100):
        rr=_api_get(f'/v4/sellers/{seller_id}/orders?limit=100&offset={offset}',token); rr.raise_for_status()
        d=rr.json(); items=(d.get('orders') or [])
        if not items: break
        for o in items:
            for p in (o.get('products') or []):
                price=D(p.get('price')); comm=D(p.get('commission')); comm_nv=D(p.get('commission_without_vat'))
                row={
                  'order_id':o.get('id'),'app_name':o.get('app_name'),'created_at':o.get('created_at'),
                  'order_status':o.get('order_status'),'payment_status':o.get('payment_status'),
                  'seller_delivery_type':o.get('seller_delivery_type'),'delivery_type':o.get('delivery_type'),
                  'delivery_price':str(o.get('delivery_price')),'serve_price':str(o.get('serve_price')),
                  'sku':((p.get('modification') or {}).get('sku')),'ean':((p.get('modification') or {}).get('ean')),
                  'price':str(p.get('price')),'amount':p.get('amount'),
                  'commission':str(p.get('commission')),'commission_without_vat':str(p.get('commission_without_vat')),
                  'commission_rate_pct':float((comm/price*100).quantize(Decimal('0.01'))) if price and comm is not None and price!=0 else None,
                  'commission_rate_ex_vat_pct':float((comm_nv/price*100).quantize(Decimal('0.01'))) if price and comm_nv is not None and price!=0 else None,
                }
                rows.append(row)
        if len(items)<100: break

    rate_counts=Counter(r['commission_rate_pct'] for r in rows if r['commission_rate_pct'] is not None)
    exvat_counts=Counter(r['commission_rate_ex_vat_pct'] for r in rows if r['commission_rate_ex_vat_pct'] is not None)
    delivery_counts=Counter((r['seller_delivery_type'],r['delivery_type'],r['delivery_price'],r['serve_price']) for r in rows)

    # Recent returns; economics fields only.
    ret=_api_get(f'/v4/returns/{seller_id}/returns-list?limit=100&offset=0',token); ret_info={'status':ret.status_code,'rows':[]}
    if ret.ok:
        rd=ret.json()
        ritems=(rd.get('order_returns') if isinstance(rd,dict) else rd) or []
        for x in ritems[:100]:
            o=x.get('order') or {}
            ps=[]
            for rp in (x.get('return_products') or []):
                op=rp.get('order_product') or {}
                ps.append({
                  'sell_price':rp.get('sell_price'),'amount':rp.get('amount'),'reason':rp.get('reason'),'status':rp.get('status'),
                  'order_product_commission':op.get('commission'),'order_product_commission_without_vat':op.get('commission_without_vat'),
                  'order_product_price':op.get('price'),
                  'sku':((op.get('modification') or {}).get('sku')),
                })
            ret_info['rows'].append({'status':x.get('status'),'is_confirmed_by_marketplace':x.get('is_confirmed_by_marketplace'),
                                     'order_id':o.get('id'),'delivery_price':o.get('delivery_price'),'serve_price':o.get('serve_price'),
                                     'seller_delivery_type':o.get('seller_delivery_type'),'delivery_type':o.get('delivery_type'),'products':ps})
        ret_info['count']=len(ritems)

    out={
      'status':'PASS','seller_id':seller_id,'orders_products_scanned':len(rows),
      'commission_rate_counts':{str(k):v for k,v in sorted(rate_counts.items())},
      'commission_rate_ex_vat_counts':{str(k):v for k,v in sorted(exvat_counts.items())},
      'delivery_tuple_counts':[{ 'seller_delivery_type':k[0],'delivery_type':k[1],'delivery_price':k[2],'serve_price':k[3],'count':v} for k,v in delivery_counts.most_common()],
      'sample_rows':rows[:40],
      'seller_invoices_count':len(invoice_rows),'seller_invoices':invoice_rows[-40:],
      'returns':ret_info,
      'safety':{'PHH_writes':0,'stock_changes':0,'price_changes':0,'Master_writes':0,'Shopify_writes':0}
    }
    print('PHH_ECONOMICS_AGGREGATE_V14C '+json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return out
