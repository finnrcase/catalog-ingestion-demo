# Dimension Enrichment Pipeline — Design Spec

**Date:** 2026-05-03
**Status:** Approved

---

## Overview

After regular product enrichment, rows that still lack complete 3D dimensions and have Brand + Model/SKU are passed through a dedicated dimension lookup pipeline. The pipeline searches manufacturer websites first, falls back to trusted retailers, parses both HTML and PDF sources, assigns confidence tiers, and applies high/medium-confidence results automatically. Low-confidence results are recorded but not applied. All diagnostic data is returned in the API response for UI display; four status/source columns are persisted on the row for review and debug export.

Goal: substantially improve dimension coverage from the current 5/24 baseline before Programa export.

---

## Integration Point

**No new endpoint.** Dimension enrichment runs inside the existing `POST /intake/enrich` flow:

1. `product_enrichment.py` runs existing per-row enrichment (Brave Search → page fetch → Claude extraction → apply to blanks).
2. After each row's regular enrichment, check `has_complete_3d_dimensions(row["Dimensions"])`.
3. If False **and** `Brand` and `Model/SKU` are both non-blank → call `dimension_enrichment.find_dimensions(row)`.
4. Apply result according to confidence rules (see below).
5. Never overwrite a row that already has complete 3D dimensions.

A future "Find Missing Dimensions" manual button will call `find_dimensions()` directly via a new endpoint — that is out of scope for this spec.

---

## New Module: `src/dimension_enrichment.py`

### Public API

```python
def find_dimensions(row: dict) -> DimensionResult
```

### Return Type

```python
@dataclass
class DimensionResult:
    # Persisted to row
    dimensions: str           # raw human-readable string, fractions preserved
    width: str                # decimal-normalized, e.g. "14.875"
    height: str
    depth: str
    length: str               # blank if not found
    source_url: str
    confidence: str           # "high" | "medium" | "low" | "none"
    source_type: str          # "manufacturer_page" | "manufacturer_pdf"
                              # | "retailer_page" | "retailer_pdf" | "none"
    status: str               # "found" | "not_found" | "low_confidence_skipped"
    # Diagnostics — returned in API response only, not persisted to row
    queries_tried: list[str]
    urls_checked: list[str]
    evidence_text: str        # raw text snippet where dimensions were found
    failure_reason: str       # human-readable explanation if status != "found"
```

---

## Model Normalization

For each `Model/SKU` value, generate up to 4 variants tried in order:

1. **Exact** — stripped of leading/trailing whitespace
2. **No spaces** — `re.sub(r'\s+', '', model)`
3. **Dashes for spaces** — `re.sub(r'\s+', '-', model)`
4. **Suffix-stripped** — if the last dash/space-delimited token is 1–3 characters (e.g. a color code like `SS`, `W`, `BK`), try the model without that suffix

Try all queries with the primary (exact) model first before falling back to variants.

---

## Brand → Domain Lookup

Case-insensitive dict lookup. Easy to extend — add entries to the top-level constant.

```python
BRAND_DOMAIN_TABLE: dict[str, str] = {
    "scotsman": "scotsman-ice.com",
    "kohler": "kohler.com",
    "kallista": "kallista.com",
    "miele": "mieleusa.com",
    "wolf": "subzero-wolf.com",
    "sub-zero": "subzero-wolf.com",
    "thermador": "thermador.com",
    "dacor": "dacor.com",
    "samsung": "samsung.com",
    "ge": "geappliances.com",
    "ge appliances": "geappliances.com",
    "bosch": "bosch-home.com",
    "fisher & paykel": "fisherpaykel.com",
    "frigidaire": "frigidaire.com",
    "lg": "lg.com",
    "whirlpool": "whirlpool.com",
    "kitchenaid": "kitchenaid.com",
    "viking": "vikingrange.com",
}
```

**For unknown brands:**
1. Run `"{brand}" official website product specifications` via Brave Search.
2. Extract the domain from the top result URL.
3. Cache in a module-level session dict `_discovered_domains: dict[str, str]` for the process lifetime.
4. If domain discovery fails, proceed with broad queries (no `site:` restriction).

---

## Search Strategy

Queries are tried in order. For each query, fetch the top 3–5 result URLs and check each for dimensions before moving to the next query. Stop as soon as a high or medium confidence result is found.

### Phase 1 — Manufacturer targeted (known or discovered domain)

1. `site:{domain} "{model}" dimensions`
2. `site:{domain} "{model}" specifications`
3. `site:{domain} "{model}" spec sheet`
4. `site:{domain} "{model}" installation guide`

### Phase 2 — General brand queries

5. `"{brand}" "{model}" "dimensions"`
6. `"{brand}" "{model}" "specifications"`

### Phase 3 — Retailer fallback (only if phases 1–2 fail)

Trusted retailer domains tried in order:
- build.com
- ajmadison.com
- bestbuy.com
- homedepot.com
- lowes.com
- wayfair.com
- ferguson.com
- appliancesconnection.com

Query format: `site:{retailer} "{brand}" "{model}" dimensions`

Retailer results cap at **medium** confidence regardless of source quality.

### Phase 4 — Final fallbacks (only if phases 1–3 fail)

7. `"{brand}" "{product_name}" dimensions`
8. `"{model_sku}" dimensions specifications`
9. `"{brand}" "{model}" dimensions` (broad, no site restriction)

### Per-URL behavior

- Fetch URL with `httpx` (existing dependency).
- If URL ends in `.pdf` or `Content-Type: application/pdf` → PDF parser.
- Otherwise → HTML parser.
- Check parsed result with `has_complete_3d_dimensions()`. If True → evaluate confidence and accept or reject.
- If False → continue to next URL.

---

## Content Parsing

### PDF Parsing (PyMuPDF / `fitz`)

- Open with `fitz.open(stream=bytes, filetype="pdf")`.
- Scan first 10 pages only.
- Search page text for dimension labels in priority order:
  1. `Product Dimensions` / `Overall Dimensions`
  2. `Dimensions`
  3. `Width` + `Height` + `Depth` (individually, then combined)
  4. Inline pattern `W × H × D` or `14 7/8"W x 22"D x 33 3/8"H`
  5. `Cutout Dimensions` (appliances only — see below)
  6. `Shipping Dimensions` (low confidence only — see below)

### HTML Parsing (three passes in order)

**Pass 1 — JSON-LD:**
Parse `<script type="application/ld+json">` blocks. Look for `Product` schema with `width`, `height`, `depth`, `additionalProperty` array, or `description` containing dimension patterns.

**Pass 2 — Spec tables:**
Find `<table>`, `<dl>`, and `<ul>` elements with label/value pairs. Match label text (case-insensitive) against: `width`, `height`, `depth`, `dimensions`, `overall dimensions`, `product dimensions`, `W×H×D`.

**Pass 3 — Visible text regex:**
Run regex patterns against `soup.get_text()`:
- `[\d. /]+"?\s*[Ww]\s*[×xX]\s*[\d. /]+"?\s*[Hh]\s*[×xX]\s*[\d. /]+"?\s*[Dd]`
- `(?:product|overall)?\s*dimensions?\s*[:\-]\s*([^\n]+)`
- `width\s*[:\-]\s*([\d. /]+)`  (and height, depth equivalents)

---

## Appliance Dimension Disambiguation

When `Product Category` is `Appliances` (or similar built-in category) and multiple dimension blocks are found:

| Found type | Action |
|---|---|
| Product / Overall dimensions | Use as primary `Dimensions` value |
| Cutout dimensions | Append `[Cutout Dimensions: {raw_string}]` to `Notes` |
| Shipping dimensions | Ignore unless no other dimensions exist; if used, confidence = low |

For non-appliance categories, use the first complete W/H/D block found.

---

## Confidence Rules

| Condition | Confidence |
|---|---|
| Exact model match + official manufacturer source + complete W/H/D | **high** |
| Exact model match + trusted retailer + complete W/H/D | **medium** |
| Official manufacturer source + variant/suffix-stripped model match + complete W/H/D | **medium** |
| Partial model match + any source | **low** |
| Shipping dimensions only (no product/overall found) | **low** |
| Dimensions found but `has_complete_3d_dimensions()` returns False | rejected — not a result |

"Exact model match" = primary or space/dash-normalized variant found verbatim on source page.
"Partial match" = only a suffix-stripped variant matched.

---

## Result Application Rules

**High or medium confidence:**
- Fill `Dimensions` with raw human-readable string (fractions preserved, e.g. `14 7/8 in W x 22 in D x 33 3/8 in H`).
- Fill `Width (in)`, `Height (in)`, `Depth (in)`, `Length (in)` with decimal-normalized values (e.g. `14.875`). Leave blank if not present.
- Set `Dimension Source URL`, `Dimension Confidence`, `Dimension Source Type`.
- Set `Dimension Lookup Status = "found"`.
- For appliances with cutout dimensions: append `[Cutout Dimensions: {raw}]` to `Notes`.

**Low confidence:**
- Do **not** fill `Dimensions` or W/H/D fields.
- Set `Dimension Lookup Status = "low_confidence_skipped"`.
- Set `Dimension Confidence = "low"`, `Dimension Source URL` to the URL where they were found.

**No result:**
- Set `Dimension Lookup Status = "not_found"`.
- Leave `Dimensions` and W/H/D fields unchanged.
- Leave `Dimension Source URL` and `Dimension Source Type` blank.

**Never overwrite** an existing complete dimension value.

---

## New Schema Columns

Add to `intake_schema.py` `ALL_COLUMNS` (internal-only, excluded from `PROGRAMA_COLUMNS`):

| Column | Values |
|---|---|
| `Dimension Source URL` | URL string or blank |
| `Dimension Confidence` | `"high"` / `"medium"` / `"low"` / blank |
| `Dimension Source Type` | `"manufacturer_page"` / `"manufacturer_pdf"` / `"retailer_page"` / `"retailer_pdf"` / blank |
| `Dimension Lookup Status` | `"found"` / `"not_found"` / `"low_confidence_skipped"` / blank |

Add all four to `_DEBUG_EXTRA_COLUMNS` in `src/programa_export.py`.

---

## API Response Changes

`POST /intake/enrich` response gains a `dimension_diagnostics` field:

```json
{
  "rows": [...],
  "errors": [...],
  "dimension_diagnostics": [
    {
      "row_index": 2,
      "product_name": "Scotsman Icemaker Built-In Pump",
      "model_searched": "SCN60PA1SU",
      "domain_used": "scotsman-ice.com",
      "queries_tried": ["site:scotsman-ice.com \"SCN60PA1SU\" dimensions", "..."],
      "urls_checked": ["https://scotsman-ice.com/product/scn60pa1su", "..."],
      "evidence_text": "Product Dimensions: 14 7/8\"W x 22\"D x 33 3/8\"H",
      "confidence": "high",
      "status": "found",
      "source_url": "https://scotsman-ice.com/product/scn60pa1su",
      "failure_reason": ""
    }
  ]
}
```

Only rows where dimension lookup ran are included (rows that already had complete dimensions are omitted).

---

## UI Diagnostics

After enrichment completes, a collapsible **"Dimension Lookup Results"** section appears:

```
Dimension Lookup Results
N dimensions found   M rows still missing

▸ Scotsman Icemaker (SCN60PA1SU)   ✓ high   scotsman-ice.com
▸ Kohler Toilet (K-3999)            ✓ medium  build.com
▸ Unknown Fixture (XYZ-123)         ✗ not_found — no results after 9 queries
▸ Custom Lamp (ABC)                 ○ low_confidence_skipped — partial match only
```

Each row expands to show: model searched, domain used, source URL (linked), all queries tried, all URLs checked, raw evidence text, and failure reason.

---

## Export Behavior

| Export type | Dimension diagnostic columns |
|---|---|
| Main Programa CSV/XLSX (`PROGRAMA_COLUMNS`) | Excluded |
| Debug CSV (`_DEBUG_EXTRA_COLUMNS`) | Included: `Dimension Source URL`, `Dimension Confidence`, `Dimension Source Type`, `Dimension Lookup Status` |

The existing `validate_for_export()` `missing_dimensions` count and `⚠ N rows missing Dimensions` UI warning already cover export-time flagging. The new `Dimension Lookup Status` column lets the UI distinguish:
- `not_found` — lookup ran, nothing found
- `low_confidence_skipped` — candidate dimensions exist but below confidence threshold
- blank — lookup never ran (row already had complete dimensions, or lacked Brand/Model/SKU)

---

## Acceptance Test

Run the same 24-product CSV used in the original upload test.

**Pass criteria:**
- Dimension coverage improves substantially above 5/24.
- Every successful fill includes a non-blank `Dimension Source URL` and `Dimension Confidence` of `"high"` or `"medium"`.
- Every row where lookup ran but failed has `Dimension Lookup Status = "not_found"` and a non-blank `failure_reason` in the diagnostic response.
- No shipping dimensions appear as primary `Dimensions` values unless `Dimension Confidence = "low"` and `Dimension Lookup Status = "low_confidence_skipped"`.
- Existing rows with complete dimensions are not modified.

---

## Out of Scope

- Manual "Find Missing Dimensions" button and dedicated endpoint — future spec.
- Image URL discovery — separate spec.
- Caching dimension results to disk across sessions.
- Batch parallel fetching (currently sequential per row, consistent with existing enrichment).
