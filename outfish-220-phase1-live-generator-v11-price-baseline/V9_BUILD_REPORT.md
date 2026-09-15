# V9 Final Gate Build Report

- Python compile: PASS
- Generator self-test: SELF_TEST PASS: 9/9
- Exact XML order test: V9 TEST PASS
- Production mutation: NONE
- FHM: excluded

## Changes
- Zero-stock rows preserve legacy `collectionhours`; missing legacy hours now hard-block.
- LV alone gets Shopify price floor.
- LT/EE/FI preserve existing 220-feed price.
- Exact XML child-tag order is a hard production-comparison gate.
- `/audit` now exposes the candidate XML SHA256.

V9 is still read-only. No production feed is replaced.
