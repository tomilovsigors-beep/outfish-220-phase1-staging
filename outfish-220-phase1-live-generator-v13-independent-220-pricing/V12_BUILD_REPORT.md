# V12 Full Feed Merge

## Why V12 is required

V11 is a valid **Phase 1 candidate**, but it contains only 672 rows while the current production feed contains 1671 rows.

Replacing the production feed directly with V11 would therefore remove 999 existing production rows. V12 prevents that.

## V12 behavior

At runtime V12:
1. builds the approved 672-row V11 candidate;
2. fetches the current production feed;
3. replaces only those 672 matching SKUs;
4. preserves the other 999 production rows;
5. requires the merged output to contain the same complete SKU set and row count as current production;
6. serves the result only when all comparison and merge gates pass.

Expected full output: **1671 rows**.

No production switch is performed by this package.
