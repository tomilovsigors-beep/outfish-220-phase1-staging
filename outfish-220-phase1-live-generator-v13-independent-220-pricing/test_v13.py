from pathlib import Path
import tempfile, threading, os
from http.server import HTTPServer, SimpleHTTPRequestHandler
import xml.etree.ElementTree as ET
from marketplace_pricing import apply_independent_220_pricing
prod="""<?xml version="1.0"?><products><product><sku>A</sku><ean>1111111111111</ean><price-before-discount>30</price-before-discount><price-after-discount>10</price-after-discount><stock>1</stock><collectionhours>24</collectionhours></product><product><sku>B</sku><ean>2222222222222</ean><price-before-discount>20</price-before-discount><price-after-discount>20</price-after-discount><stock>1</stock><collectionhours>24</collectionhours></product></products>"""
cand="""<?xml version="1.0"?><products><product><sku>A</sku><ean>1111111111111</ean><price-before-discount>999</price-before-discount><price-after-discount>999</price-after-discount><stock>3</stock><collectionhours>24</collectionhours></product><product><sku>B</sku><ean>2222222222222</ean><price-before-discount>888</price-before-discount><price-after-discount>888</price-after-discount><stock>3</stock><collectionhours>24</collectionhours></product></products>"""
with tempfile.TemporaryDirectory() as td:
 d=Path(td); (d/'prod.xml').write_text(prod); (d/'cand.xml').write_text(cand); (d/'overrides.csv').write_text('sku,price-before-discount,price-after-discount,reason\nB,25,22,manual repricing\n')
 class H(SimpleHTTPRequestHandler):
  def log_message(self,*args): pass
 old=os.getcwd(); os.chdir(td); srv=HTTPServer(('127.0.0.1',0),H); threading.Thread(target=srv.serve_forever,daemon=True).start()
 try:
  r=apply_independent_220_pricing(d/'cand.xml',f'http://127.0.0.1:{srv.server_port}/prod.xml',d/'overrides.csv')
  assert r['shopify_price_used'] is False and r['production_prices_preserved']==1 and r['explicit_price_overrides']==1
  root=ET.parse(d/'cand.xml').getroot(); rows={p.findtext('sku'):p for p in root.findall('product')}
  assert rows['A'].findtext('price-before-discount')=='30' and rows['A'].findtext('price-after-discount')=='10'
  assert rows['B'].findtext('price-before-discount')=='25' and rows['B'].findtext('price-after-discount')=='22'
 finally:
  srv.shutdown(); os.chdir(old)
print('V13 TEST PASS')
