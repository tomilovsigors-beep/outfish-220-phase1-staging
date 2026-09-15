from pathlib import Path
import tempfile
import production_compare as pc

XML1=b'''<?xml version="1.0"?><products><product><sku>A</sku><ean>1234567890123</ean><price-before-discount-lt>10</price-before-discount-lt><price-after-discount-lt>10</price-after-discount-lt><price-before-discount-lv>10</price-before-discount-lv><price-after-discount-lv>10</price-after-discount-lv><price-before-discount-ee>10</price-before-discount-ee><price-after-discount-ee>10</price-after-discount-ee><price-before-discount-fi>10</price-before-discount-fi><price-after-discount-fi>10</price-after-discount-fi><stock>3</stock><collectionhours>24</collectionhours></product></products>'''
XML2=XML1.replace(b'<stock>3</stock>',b'<stock>5</stock>')
XML_DOWN=XML1.replace(b'<price-after-discount-lv>10</price-after-discount-lv>',b'<price-after-discount-lv>9</price-after-discount-lv>')

def run():
    with tempfile.TemporaryDirectory() as d:
        p=Path(d)/'candidate.xml'; p.write_bytes(XML2)
        old=pc.fetch_bytes
        pc.fetch_bytes=lambda url,timeout=30: XML1
        try:
            r=pc.compare(p,'mock://production',expected_export_rows=1)
            assert r['pass'] and r['stock_change_count']==1 and r['price_decrease_count']==0
            p.write_bytes(XML_DOWN)
            r=pc.compare(p,'mock://production',expected_export_rows=1)
            assert not r['pass'] and r['price_decrease_count']==1
        finally:
            pc.fetch_bytes=old
    print('V8_TEST PASS: production diff + price-decrease gate')

if __name__=='__main__': run()
