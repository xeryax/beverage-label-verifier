# Regulatory Basis

This prototype implements checks derived from official TTB labeling requirements. **Authoritative source:** [TTB Labeling Resources](https://www.ttb.gov/regulated-commodities/labeling/labeling-resources).

## Government warning (27 CFR Part 16)

Required on all alcohol beverages ≥0.5% ABV. Exact text per 27 CFR § 16.21:

> GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems.

**We verify via OCR (27 CFR § 16.21 — benchmarked):**

- Text presence and fuzzy body match (≥45% token_set_ratio)
- `GOVERNMENT WARNING` header in ALL CAPS
- Title-case header → **fail** (16.21 violation)

**Experimental flat-artwork notes (not benchmarked; § 16.22):**

On flat COLA scans only, OpenCV stroke metrics on Tesseract word boxes may append advisory notes to the Government Warning field (header stroke weight vs body, type height vs net-contents text). These are **not** validated compliance checks and do not drive pass/fail except in rare match→review cases. Skipped on bottle photos.

**Agent visual review required (§ 16.22 — not automated):**

- Bold header, minimum type size, contrasting background, separate-and-apart placement
- All warning formatting on curved or glare-heavy photos
- Absolute millimeter type size without print metadata

## Mandatory fields by beverage type

| Field | Spirits (27 CFR 5) | Wine (27 CFR 4) | Beer (27 CFR 7) |
|---|---|---|---|
| Brand name | Required | Required (brand label) | Required |
| Class/type | Required | Required | Required |
| Alcohol content | Required | Required if >14% ABV; table wine 7–14% may omit % | Optional unless from flavors/additives |
| Net contents | Required (metric) | Required (metric) | Required (US measures primary per 7.70) |
| Producer/bottler | Required | Required | Required |
| Country of origin | Imports | Imports | Imports |
| Government warning | Required | Required | Required |

## Matching approach

| Field | Strategy |
|---|---|
| Brand, class, producer, country | Fuzzy (RapidFuzz); case-insensitive |
| Alcohol content | Numeric ± tolerance (spirits ±0.3%, wine ±1.0%) |
| Net contents | Value + unit exact match |
| Government warning | Exact text + ALL CAPS header |

## Out of scope

- Sulfite declaration (27 CFR 4.32a) — optional future field
- Appellation of origin, age statement, FD&C disclosures
- Same field of vision on multi-panel bottle photos
- COLA system integration
