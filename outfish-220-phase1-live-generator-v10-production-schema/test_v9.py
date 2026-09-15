
from pathlib import Path
import tempfile, threading, os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from production_compare import compare, EXPECTED_TAG_ORDER

def make_xml(order=None, lv="12"):
    order = order or EXPECTED_TAG_ORDER
    vals = {t:"12" for t in EXPECTED_TAG_ORDER}
    vals.update({"sku":"A","ean":"1234567890123","stock":"3","collectionhours":"24",
                 "price-before-discount-lv":lv,"price-after-discount-lv":lv})
    body = "".join(f"<{t}>{vals[t]}</{t}>" for t in order)
    return f'<?xml version="1.0"?><products><product>{body}</product></products>'

with tempfile.TemporaryDirectory() as td:
    d=Path(td)
    (d/"prod.xml").write_text(make_xml(), encoding="utf-8")
    (d/"cand.xml").write_text(make_xml(lv="13"), encoding="utf-8")
    class H(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
    old=os.getcwd(); os.chdir(td)
    srv=HTTPServer(("127.0.0.1",0), H)
    threading.Thread(target=srv.serve_forever,daemon=True).start()
    try:
        r=compare(d/"cand.xml", f"http://127.0.0.1:{srv.server_port}/prod.xml", 1)
        assert r["pass"], r
        bad=EXPECTED_TAG_ORDER[:]
        bad[0], bad[1] = bad[1], bad[0]
        (d/"cand.xml").write_text(make_xml(order=bad), encoding="utf-8")
        r2=compare(d/"cand.xml", f"http://127.0.0.1:{srv.server_port}/prod.xml", 1)
        assert not r2["pass"]
        assert any(c["check"]=="CANDIDATE_EXACT_TAG_ORDER" and not c["pass"] for c in r2["checks"])
    finally:
        srv.shutdown(); os.chdir(old)
print("V9 TEST PASS")
