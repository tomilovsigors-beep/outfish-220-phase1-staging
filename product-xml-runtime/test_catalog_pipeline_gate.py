"""Regression tests for publication gates (no network / no PHH writes)."""
import unittest
from catalog_pipeline_gate import classify, audit


def specimen():
    title = "Verified example"
    locales = {x: {"title": title, "description_html": "<h2>" + title + "</h2><p><br></p><p>Body</p>", "supplier_code": "SKU-1"} for x in ("lt","lv","ee","ru","fi")}
    return dict(sku="SKU-1", barcode="0021563105926", vendor="Gear Aid",
                sku_unique=True, ean_unique=True, shopify_active=True, price_eur=12.50,
                phh_identity={"status": "ABSENT_CONFIRMED", "exhaustive_check": True, "http_status": 200},
                category_confirmed=True, category_id="123", required_attributes_complete=True,
                required_attributes=[{"field_id":"12","value":"Confirmed","dictionary_required":True,"dictionary_confirmed":True}],
                locales=locales, images=[{"url":"https://example.org/a.jpg","width":800,"height":800},
                                         {"url":"https://example.org/b.jpg","width":800,"height":800}],
                main_image_neutral_verified=True, package_verified=True,
                manufacturer_verified=True, content_approved=True)


class ReadinessSafetyTests(unittest.TestCase):
    def test_ready_complete(self):
        self.assertEqual(classify(specimen())["state"], "READY")

    def test_fhm_always_excluded_even_if_phh_exists(self):
        s=specimen(); s["vendor"]="fHm"; s["phh_identity"]={"status":"EXISTING","identity_verified":True}
        self.assertEqual(classify(s)["state"], "EXCLUDED")

    def test_404_fails_closed(self):
        s=specimen(); s["phh_identity"]={"status":"ABSENT_CONFIRMED","exhaustive_check":True,"http_status":404}
        self.assertEqual(classify(s)["state"], "BLOCKED")

    def test_existing_never_recreated(self):
        s=specimen(); s["phh_identity"]={"status":"EXISTING","identity_verified":True,"product_id":"270344850"}
        self.assertEqual(classify(s)["state"], "EXISTING")

    def test_existing_verified_only_with_product_proof(self):
        s=specimen(); s["phh_identity"]={"status":"EXISTING","identity_verified":True,"listing_verified":True,"all_fields_verified":True}
        self.assertEqual(classify(s)["state"], "VERIFIED")

    def test_missing_dictionary_value(self):
        s=specimen(); s["required_attributes"][0]["dictionary_confirmed"]=False
        self.assertEqual(classify(s)["state"], "NEEDS_DATA")

    def test_invalid_ean(self):
        s=specimen(); s["barcode"]="12345678"
        self.assertEqual(classify(s)["state"], "BLOCKED")

    def test_below_minimum_price(self):
        s=specimen(); s["price_eur"]=9.99
        self.assertEqual(classify(s)["state"], "BLOCKED")

    def test_duplicate_identity(self):
        s=specimen()
        out=audit([s,s])
        self.assertEqual(out["counts"]["BLOCKED"],2)
        self.assertEqual(out["phh_writes"],0)


if __name__=="__main__":
    unittest.main()
