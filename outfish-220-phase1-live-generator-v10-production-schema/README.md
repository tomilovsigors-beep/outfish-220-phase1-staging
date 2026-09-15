# Outfish 220 Phase 1 — V8 Cutover Rehearsal

Read-only staging service. It does **not** publish to 220, Shopify, Google Sheets or PMP.
FHM is not in scope.

## Pipeline

`live Shopify -> locked Phase 1 identity -> stock/price candidate -> generator gate -> compare with current production feed -> cutover gate`

Expected Phase 1 export: **672 rows** (674 approved pilot rows minus 2 explicit exclusions).

## Fail-closed cutover checks

The candidate XML is served only if all of the following are true:

- generator `publish_allowed=true`;
- candidate contains exactly 672 rows;
- candidate SKU is present in the current production feed;
- no duplicate candidate SKU;
- no EAN changes for the same marketplace SKU;
- no price field decreases vs current production.

Stock and `collectionhours` differences are reported but are not blockers because updating them is the purpose of the feed.
Price increases are allowed; price decreases are blocked.

## Endpoints

- `/health` — process liveness (Render health check)
- `/ready` — 200 only when full cutover gate is green
- `/audit` — current staging status
- `/generator-publish-gate.json` — internal generator gate
- `/production-diff.json` — candidate vs current production diff
- `/cutover-gate.json` — final rehearsal gate
- `/220-stock-candidate.xml` — XML only when final cutover gate is green; otherwise HTTP 503

## Production safety

The production URL is fetched **read-only** for comparison. V8 contains no code to replace or publish the existing production feed.
