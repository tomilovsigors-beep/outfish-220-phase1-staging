# 220 Master-driven staging runtime

Separate staging-only runtime. It does not modify Shopify, Google Sheets, PHH, the old production feed, or the frozen V13 candidate. No cutover is performed.

Runtime sources:
- 220 Master: identity, lifecycle, 220 prices
- Shopify Admin API read-only: exact locked 970 ProductVariant GIDs, on_hand only
- SIA FHM: exact direct source for 701 external SKUs and production-equivalent stock/hours/price baseline

No SKU/barcode rematching is performed. Shopify price is never queried or used. The old `/220-stock.xml` is never queried or used.

Required secret environment: `SHOPIFY_ACCESS_TOKEN` and Google read access (`GOOGLE_SERVICE_ACCOUNT_JSON`, or direct accessible `MASTER_CSV_URL` + `SIA_CSV_URL`). Do not store secrets in the repository.

Endpoints: `/health`, `/audit`, `/source-snapshot-status`, `/220-stock-master-driven.xml`, `/runtime-dataset.csv`, `/blockers.csv`, `/production-equivalent-comparison.csv`, POST `/refresh`.

Refresh is fail-closed. A failed source fetch never publishes a partial success. If a prior validated snapshot exists, it may remain served while health reports degraded/stale.
