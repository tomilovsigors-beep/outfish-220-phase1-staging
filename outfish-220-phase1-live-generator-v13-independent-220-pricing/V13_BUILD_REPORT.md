# V13 Independent 220 Pricing

V13 fully decouples Shopify pricing from 220 pricing.

- Shopify price changes do not change 220.
- By default, each Phase 1 SKU preserves its current 220 production `price-before-discount` and `price-after-discount`.
- Explicit marketplace repricing is done only through `220_price_overrides.csv`.
- Stock and collectionhours still update from approved live inventory logic.
- Full-feed merge remains 1671 rows: 672 approved rows updated, 999 untouched rows preserved.
- No production switch is performed by this package.
