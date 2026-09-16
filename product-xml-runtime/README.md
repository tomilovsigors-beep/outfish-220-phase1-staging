# 220 Product XML staging runtime

Isolated Product XML dry-run pipeline. It does not read from or publish to the stock/price feed and does not write to PHH, Shopify, Master, or SIA.

Flow:

`220 Master + Shopify product data (read-only) -> Product XML readiness validation -> dry-run artifacts`

Endpoints when deployed as its own staging service:

- `GET /health`
- `POST /refresh` (read-only source refresh; generates staging artifacts only)
- `GET /product-xml-validation.json`
- `GET /product-xml-readiness.csv`
- `GET /product-xml-blockers.csv`
- `GET /product-xml-dry-run.xml`

Identity is always `220_sku + 220_ean`. `220_title` is mandatory and never falls back to Shopify title. Main image resolution is `220_main_image_url` first, then `shopify_main_image_url`; source refresh never writes Master and therefore never overwrites the manual override.

`phh-spec.json` intentionally keeps the publish gate closed until an authoritative PHH Product XML structure, category mapping, and required-attribute specification is supplied. No XML element names from third-party examples are treated as authoritative.
