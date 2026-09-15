# V8 Cutover Rehearsal Build Report

- Generator self-test: PASS (9/9)
- Python compile: PASS
- Production-diff test: PASS
- Price-decrease fail-closed test: PASS
- Expected Phase 1 export rows: 672
- FHM in scope: false
- Production writes: none
- Shopify writes: none
- Google Sheet writes: none
- PMP writes: none

## What changed from V7
V8 adds a second gate that compares the freshly generated live candidate against the existing production feed before the candidate XML can be served. Stock/hour changes are informational; EAN changes, missing production SKUs, duplicate candidate SKUs, unexpected row count, and any price decrease block the cutover rehearsal.
