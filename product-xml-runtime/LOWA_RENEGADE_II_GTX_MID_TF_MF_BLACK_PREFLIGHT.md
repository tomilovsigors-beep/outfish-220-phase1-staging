# LOWA RENEGADE II GTX MID TF MF Black — PHH preparation / BLOCKED

Last source verification: 2026-10-06.
Canon policy: [PROJECT_RULES.md](../PROJECT_RULES.md).

**Status: PREPARED / BLOCKED.** This file is a read-only handoff document, **not** an approved PHH CREATE or stock-feed payload. Do not upload or create this model/its sizes until every variant is identified against existing PHH seller offers by SKU+EAN and model/color, plus category/required-feature/packaging validation. Exclude FHM globally.

## Source of truth and identity

- Shopify Product GID: `gid://shopify/Product/9329307025746`
- Shopify title: LOWA RENEGADE II GTX MID TF MF Tactical Boots — GORE-TEX — Black
- Vendor: Lowa (not excluded brand FHM)
- Model: LOWA RENEGADE II GTX MID TF MF; color Black; manufacturer family code 310921/9999.
- Shopify active, 9 product images. Source has leather upper, GORE-TEX Professional lining, metal-free, MONOWRAP support and LOWA PATROL outsole.
- Existing 220 Master `MASTER` rows 1425–1434 show `NOT_IN_220`, `SHOPIFY_ONLY`, with blank 220 SKU/EAN. **This is historical staging data, NOT live PHH proof.**
- Live PHH seller-offer export endpoint timed out. Duplicate/existence status **UNKNOWN** for every variant.
- Shopify Admin GraphQL product variants: 8 barcodes null; 2 contain eight-digit strings, **not acceptable as EAN-13**. No EAN-13 independently verified for exact black men's variants.
- Internet lookup returned EANs for *dark brown* or women's/low cut variants; these are different SKUs and are deliberately NOT reused here.

## Per-size actual Shopify stock and PHH stock target (dry-run ONLY)

| EU | Exact Shopify SKU | Shopify barcode | Actual Shopify stock | PHH target (dry-run) | Identity gate |
|---|---|---|---:|---:|---|
| 40 | `310921 999940` | absent | 4 | 4 | BLOCKED |
| 41 | `310921 999941` | absent | 3 | 3 | BLOCKED |
| 41.5 | `310921 9999415` | absent | 1 | 3 | BLOCKED |
| 42 | `310921 999942` | absent | 2 | 3 | BLOCKED |
| 42.5 | `310921 9999425` | absent | 0 | 0 | BLOCKED |
| 43.5 | `310921 9999435` | absent | 1 | 3 | BLOCKED |
| 44 | `310921 999944` | `25418578` (8 digits; invalid EAN-13) | 3 | 3 | BLOCKED |
| 44.5 | `310921 9999445` | absent | 2 | 3 | BLOCKED |
| 45 | `310921 999945` | absent | 3 | 3 | BLOCKED |
| 46 | `310921 999946` | `74336338` (8 digits; invalid EAN-13) | 2 | 3 | BLOCKED |

Totals: Shopify 21; proposed PHH 28 **only if separately approved**. Formula: `0 if shopify_stock == 0 else max(3, shopify_stock)`. Never modify Shopify real stock.

## Localized PHH title drafts (model only; SKU per modification)

- LT: `Taktiniai batai LOWA RENEGADE II GTX MID TF MF Black`
- LV: `Taktiskie zābaki LOWA RENEGADE II GTX MID TF MF Black`
- EE: `Taktikalised saapad LOWA RENEGADE II GTX MID TF MF Black`
- RU: `Тактические ботинки LOWA RENEGADE II GTX MID TF MF Black`
- FI: `Taktiset varsikengät LOWA RENEGADE II GTX MID TF MF Black`

No commas, type first, model and color. Drafts require PHH-category title-rule confirmation.

## Localized description drafts

Each description MUST be emitted as `<h2>{identical locale title}</h2><p><br></p><p>...manufacturer-approved description...</p>` with manufacturer logo and verified short manufacturer block appended as supported by the marketplace. Exact manufacturer logo/source pending.

- LT: `Neperšlampami taktiniai batai su natūralios odos viršumi ir GORE-TEX Professional pamušalu. Konstrukcija be metalinių dalių. LOWA MONOWRAP atrama ir LOWA PATROL guminis padas. Suvarstomi batai darbui ir aktyviam judėjimui. Spalva juoda. Dydis parenkamas pagal EU variantą.`
- LV: `Ūdensnecaurlaidīgi taktiskie zābaki ar dabīgās ādas virsmu un GORE-TEX Professional oderi. Konstrukcija bez metāla detaļām. LOWA MONOWRAP atbalsts un LOWA PATROL gumijas zole. Šņorējami zābaki darbam un aktīvai kustībai. Melna krāsa. EU izmērs atbilstoši variantam.`
- EE: `Veekindlad taktikalised saapad ehtsast nahast pealse ja GORE-TEX Professional voodriga. Metallivaba konstruktsioon. LOWA MONOWRAP tugi ja LOWA PATROL kummist välistald. Paeltega saapad tööks ja aktiivseks liikumiseks. Värv must. EL suurus vastavalt variandile.`
- RU: `Водонепроницаемые тактические ботинки с верхом из натуральной кожи и подкладкой GORE-TEX Professional. Конструкция без металлических деталей. Поддержка LOWA MONOWRAP и резиновая подошва LOWA PATROL. Шнуровка, чёрный цвет. Размер EU выбирается для каждого варианта.`
- FI: `Vedenpitävät taktiset varsikengät, joissa on täysnahkainen päällinen ja GORE-TEX Professional -vuori. Metallivapaa rakenne. LOWA MONOWRAP -tuki ja LOWA PATROL -kumipohja. Nauhallinen malli työ- ja ulkokäyttöön. Väri musta. EU-koko on valittava tuotevariantille.`

## Open gates (ALL required before submitting)

1. Live seller offers/PHH product catalog read: ensure no duplicate for each size by SKU, barcode/UPC, manufacturer part and existing offer; marketplace API must respond successfully and exhaustively.
2. Manufacturer-verified EAN for each of ten **exact** black EU variants; never synthesize or copy another color's barcode. Alternatively clarify officially supported no-EAN exception with PHH before import.
3. Correct PHH product category ID and all mandatory product attributes; exact dictionary selections for brand, gender, waterproof membrane, material, color, size.
4. Verified shipping package weight/dimensions (manufacturer shoe weight is **not** parcel gross weight).
5. Check all selected Shopify image pixel dimensions and PHH rule compliance; minimum 2 images, 600×600 pixels, white background if required and no watermarks. Use exact images, canonical direct paths without tracking-query.
6. Fully formed HTML per locale, official manufacturer block/logo, matching H2 and title, supplier code identical in all five languages.
7. User approval to create ONLY genuinely missing product(s). No stock, price, Master or Shopify writes from this preparation.

Source: Shopify Admin GraphQL read-only query on product `9329307025746`, plus Google Sheets 220 Master snapshots, and previously user-approved 220_RULES.

**Writes performed by preparation: PHH 0; Shopify 0; Master 0; stock feed 0; price feed 0.**
