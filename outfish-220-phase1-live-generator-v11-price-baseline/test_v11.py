from pathlib import Path
import tempfile, threading, os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from production_price_baseline import apply_production_before_discount
import xml.etree.ElementTree as ET

prod = """<?xml version="1.0"?><products>
<product><sku>A</sku><ean>1234567890123</ean><price-before-discount>30</price-before-discount><price-after-discount>10</price-after-discount><stock>1</stock><collectionhours>24</collectionhours></product>
<product><sku>B</sku><ean>1234567890124</ean><price-before-discount>13</price-before-discount><price-after-discount>5</price-after-discount><stock>1</stock><collectionhours>24</collectionhours></product>
</products>"""
cand = """<?xml version="1.0"?><products>
<product><sku>A</sku><ean>1234567890123</ean><price-before-discount>20</price-before-discount><price-after-discount>20</price-after-discount><stock>3</stock><collectionhours>24</collectionhours></product>
<product><sku>B</sku><ean>1234567890124</ean><price-before-discount>20</price-before-discount><price-after-discount>20</price-after-discount><stock>3</stock><collectionhours>24</collectionhours></product>
</products>"""

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    (d/"prod.xml").write_text(prod, encoding="utf-8")
    (d/"cand.xml").write_text(cand, encoding="utf-8")
    class H(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
    old = os.getcwd()
    os.chdir(td)
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = apply_production_before_discount(d/"cand.xml", f"http://127.0.0.1:{srv.server_port}/prod.xml")
        assert r["changed_count"] == 1, r
        root = ET.parse(d/"cand.xml").getroot()
        rows = {p.findtext("sku"): p for p in root.findall("product")}
        # A: preserve higher old before-discount 30 while selling price becomes 20.
        assert rows["A"].findtext("price-before-discount") == "30"
        assert rows["A"].findtext("price-after-discount") == "20"
        # B: selling price 20 is above old before-discount 13, so lift before to 20.
        assert rows["B"].findtext("price-before-discount") == "20"
        assert rows["B"].findtext("price-after-discount") == "20"
    finally:
        srv.shutdown()
        os.chdir(old)
print("V11 TEST PASS")
