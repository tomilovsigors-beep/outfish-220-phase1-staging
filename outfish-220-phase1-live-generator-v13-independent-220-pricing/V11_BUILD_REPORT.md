# V11 Price Baseline Build Report

V10 proved the XML schema/order, but the final gate found 188 price decreases.

Analysis of the full V10 production diff:
- 672/672 rows overlap production.
- 0 EAN changes.
- 0 duplicate candidate SKUs.
- exact production tag order: PASS.
- 188 price decreases.
- all 188 decreases are **price-before-discount only**.
- there are no decreases of the actual selling price (`price-after-discount`).

V11 therefore preserves the current production `price-before-discount` anchor while keeping the Shopify floor on the selling price:

- `price-after-discount = max(current production selling price, live Shopify price)`
- `price-before-discount = max(current production price-before-discount, price-after-discount)`

This avoids lowering an existing reference/list price and also guarantees `before >= after`.

Safety:
- no Shopify writes
- no Google Sheet writes
- no PMP writes
- no production-feed replacement
- FHM remains out of scope
