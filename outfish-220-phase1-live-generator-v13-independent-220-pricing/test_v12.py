from pathlib import Path
import tempfile, threading, os
from http.server import HTTPServer, SimpleHTTPRequestHandler
import xml.etree.ElementTree as ET
from production_merge import merge_candidate_into_production

prod = """<?xml version="1.0"?><products>
<product><sku>A</sku><ean>1111111111111</ean><price-before-discount>30</price-before-discount><price-after-discount>10</price-after-discount><stock>1</stock><collectionhours>72</collectionhours></product>
<product><sku>B</sku><ean>2222222222222</ean><price-before-discount>20</price-before-discount><price-after-discount>20</price-after-discount><stock>0</stock><collectionhours>48</collectionhours></product>
<product><sku>C</sku><ean>3333333333333</ean><price-before-discount>9</price-before-discount><price-after-discount>9</price-after-discount><stock>2</stock><collectionhours>72</collectionhours></product>
</products>"""
cand = """<?xml version="1.0"?><products>
<product><sku>A</sku><ean>1111111111111</ean><price-before-discount>30</price-before-discount><price-after-discount>20</price-after-discount><stock>3</stock><collectionhours>24</collectionhours></product>
<product><sku>B</sku><ean>2222222222222</ean><price-before-discount>20</price-before-discount><price-after-discount>20</price-after-discount><stock>4</stock><collectionhours>48</collectionhours></product>
</products>"""

with tempfile.TemporaryDirectory() as td:
    d=Path(td)
    (d/"prod.xml").write_text(prod, encoding="utf-8")
    (d/"cand.xml").write_text(cand, encoding="utf-8")
    class H(SimpleHTTPRequestHandler):
        def log_message(self,*args): pass
    old=os.getcwd(); os.chdir(td)
    srv=HTTPServer(("127.0.0.1",0),H)
    threading.Thread(target=srv.serve_forever,daemon=True).start()
    try:
        r=merge_candidate_into_production(
            d/"cand.xml",
            f"http://127.0.0.1:{srv.server_port}/prod.xml",
            d/"merged.xml")
        assert r["pass"], r
        assert r["production_rows"] == 3
        assert r["candidate_rows"] == 2
        assert r["merged_rows"] == 3
        assert r["replaced_rows"] == 2
        assert r["untouched_rows"] == 1
        root=ET.parse(d/"merged.xml").getroot()
        rows={p.findtext("sku"):p for p in root.findall("product")}
        assert rows["A"].findtext("stock") == "3"
        assert rows["B"].findtext("stock") == "4"
        assert rows["C"].findtext("stock") == "2"
        assert rows["C"].findtext("collectionhours") == "72"
    finally:
        srv.shutdown(); os.chdir(old)
print("V12 TEST PASS")
