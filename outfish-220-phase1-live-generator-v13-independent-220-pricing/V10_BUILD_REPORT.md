# V10 Production Schema Build Report

- Python compile: PASS
- Generator self-test: SELF_TEST PASS: 9/9
- Production-schema test: V10 TEST PASS
- Production writes: NONE
- FHM: excluded

## What V9 proved

The current production feed uses one and the same six-tag product schema for all **672/672** overlapping Phase 1 rows:

`sku → ean → price-before-discount → price-after-discount → stock → collectionhours`

So the V9 failure was not random XML ordering. V9 was emitting a different **market-specific price schema**.

## V10 correction

V10 now emits the exact current production schema and exact tag order.

The single production selling price follows the approved rule:

`max(current 220 selling price, live Shopify price)`

Zero-stock handling remains fail-closed:
- preserve legacy collectionhours;
- block the row if legacy collectionhours is absent.

## Existing V9 data quality signals retained

- candidate rows: 672
- candidate-only rows: 0
- EAN changes: 0
- duplicate candidate SKUs: 0
- price decreases: 0
- stock changes expected from live routing: 268
- collectionhours changes expected from live routing: 41

V10 remains read-only and does not replace the production feed.
