# Data Ingestion & Enrichment Quality — Design Spec

**Date:** 2026-04-30
**Scope:** Notes cleaning, PDF/receipt parsing improvements, manufacturer website lookup enrichment
**Out of scope:** Retell calling, Programa browser automation, image upload, UI redesign

---

## Overview

Three targeted improvements to the data ingestion and enrichment pipeline:

1. **Notes cleaning** — strip line/item/row prefixes before storing Notes
2. **PDF/receipt parsing** — add `Material`, `Lead Time`, and per-axis dimension columns; improve AI extraction prompt
3. **Manufacturer lookup** — new `src/manufacturer_lookup.py` module; `product_enrichment.py` calls it first, Brave Search is the fallback

Approach B: manufacturer lookup is a new isolated module. No pipeline refactor.

---

## Issue 1: Notes Cleaning

### `src/notes.py`

Add public function `clean_notes_text(text: str) -> str`.

Strips leading line/item/row number prefixes from Notes strings:
- `Line 1.` / `Line 2:` / `Line 3 -`
- `Item 1.` / `Item 2)`
- `Row 1.` / `Row #1 -`
- `#1.` / bare `1.` — **only at the very start of the string and only when followed by a letter or `[`**, never inside model numbers, prices, or product descriptions

The existing `remove_notes_row_prefix()` is kept as an alias for backward compatibility.

### Applied at

- `document_parser.py` — before storing `Notes` on any row
- `ai_extraction.py` — applied to `notes` field from AI JSON before storing
- `product_enrichment.py` — before appending note tags so they don't get double-prefixed
- Programa send path — cleanup gate before send

---

## Issue 2: Schema, Dimensions, and Parsing

### New schema columns (`src/intake_schema.py`)

Six columns added to `ALL_COLUMNS` and `make_base_row()`, placed after `Dimensions`:

| Column (display) | Internal key | Type | Default | Programa field |
|---|---|---|---|---|
| `Width (in)` | `width_in` | `float \| None` | `None` | Width |
| `Height (in)` | `height_in` | `float \| None` | `None` | Height |
| `Depth (in)` | `depth_in` | `float \| None` | `None` | Depth |
| `Length (in)` | `length_in` | `float \| None` | `None` | Length |
| `Material` | `material` | `str` | `""` | Material |
| `Lead Time` | `lead_time` | `str` | `""` | Lead time |

`Dimensions` string is kept as the human-readable combined field. Per-axis columns are the structured Programa-ready truth.

### Dimension sync (`src/dimensions.py`)

Two new functions:

**`parse_dimensions(s: str) -> dict`**
- Parses `"24"W x 23"D x 34"H"` → `{"width_in": 24.0, "depth_in": 23.0, "height_in": 34.0, "length_in": None}`
- Handles: `"W"/"H"/"D"` labels, `in`/`"` units, fractions like `23 1/2"`, label-before or label-after
- Returns `None` for any axis not explicitly found — never infers
- Ignores bare 3-number strings without axis labels (too ambiguous)

**`format_dimensions_string(width_in, height_in, depth_in, length_in) -> str`**
- Builds `"24 in W x 34 in H x 23 in D"` from per-axis floats
- Omits `None` axes

**Sync rule (applied after any enrichment merge):**
1. `Dimensions` just filled → parse → populate per-axis columns if still empty
2. Per-axis columns just filled → generate `Dimensions` string if it's empty

### AI extraction prompt (`src/ai_extraction.py`)

New fields added to `_FIELD_MAP`:
- `"material"` → `"Material"`
- `"lead_time"` → `"Lead Time"`

Prompt additions:
- `material`: primary construction materials (e.g. `"Stainless Steel"`, `"Solid Oak"`). Never infer from product name; leave `""` if not stated.
- `lead_time`: only from explicit document text (e.g. `"8–10 weeks"`, `"In stock"`). Leave `""` if not stated.

After AI response is parsed in `_item_to_row()`:
1. Apply `clean_notes_text()` to the `notes` field
2. Call `parse_dimensions()` on `Dimensions` → populate per-axis columns
3. If per-axis set but `Dimensions` empty → call `format_dimensions_string()`

`_OUTPUT_COLUMNS` updated to include the six new columns.

### Rule-based parser (`src/document_parser.py`)

Conservative changes only:
- Apply `clean_notes_text()` to user-supplied `notes` before storing
- Table header map extended to look for `"material"` and `"lead time"` column variants
- After any row is built, call `parse_dimensions()` on whatever `Dimensions` value exists and populate per-axis fields

---

## Issue 3: Manufacturer Lookup (`src/manufacturer_lookup.py`)

New standalone module. Called by `product_enrichment.py` before Brave Search.

### Brand → domain resolution

Hardcoded `_BRAND_DOMAINS` dict for known luxury/residential design brands (expandable):

```python
_BRAND_DOMAINS = {
    "scotsman": "scotsman-ice.com",
    "wolf": "subzero-wolf.com",
    "sub-zero": "subzero-wolf.com",
    "miele": "mieleusa.com",
    "kohler": "kohler.com",
    "kallista": "kallista.com",
    "thermador": "thermador.com",
    "dacor": "dacor.com",
    "monogram": "monogram.com",
    "bosch": "bosch-home.com",
    "gaggenau": "gaggenau.com",
    "bertazzoni": "bertazzoni.com",
    "viking": "vikingrange.com",
    "brizo": "brizo.com",
    "dornbracht": "dornbracht.com",
    "waterworks": "waterworks.com",
    "visual comfort": "visualcomfort.com",
    "circa lighting": "circalighting.com",
    "restoration hardware": "rh.com",
    "rh": "rh.com",
}
```

Unknown brand: call Brave with `{brand} official website`, extract domain from top result. Used for that call only, not persisted.

### Search strategy (ordered, stop on first high/medium confidence hit)

Given `brand=Scotsman`, `model=SCN60PA1SU`, resolved domain `scotsman-ice.com`:

1. `site:scotsman-ice.com SCN60PA1SU`
2. `site:scotsman-ice.com SCN60PA1SU spec sheet`
3. `Scotsman SCN60PA1SU specifications` (open, no site: constraint)

Steps 1–2 skipped if no domain resolved. Step 3 is always available as final fallback.

### PDF spec sheet detection

If the fetched page URL ends in `.pdf` or `Content-Type` is `application/pdf`, parse with PyMuPDF (same path as `ai_extraction.py`) before sending to Claude. Spec sheet PDFs are preferred source type when available.

### Match confidence

| Condition | Confidence |
|---|---|
| Exact model in page text + manufacturer domain + dimensions listed | `high` |
| Exact model in page text + manufacturer domain | `medium` |
| Exact model on trusted retailer + dimensions listed | `medium` |
| Partial model match or model not in page text | `low` |
| Page fetch failed / model not mentioned | `none` |

Manufacturer source is strongly preferred — a manufacturer page at `medium` beats a retailer page at `medium`.

### Return type

```python
@dataclass
class ManufacturerLookupResult:
    attempted: bool
    lookup_url: str | None
    match_found: bool
    match_confidence: str          # "high" | "medium" | "low" | "none"
    source_type: str               # "product_page" | "spec_sheet" | "search_result" | "none"
    fields_found: list[str]
    extracted: dict
```

Diagnostic only — not written to row columns.

### Diagnostics (`src/enrichment_debug.py`)

Extended to display per-row manufacturer lookup results: lookup attempted, URL checked, match confidence, fields extracted, conflicts.

---

## Integration & Merge Strategy

### `product_enrichment.py` — updated `enrich_row()` flow

**Phase 1 — Manufacturer lookup** (new, runs first when Brand + Model/SKU present):
- Calls `manufacturer_lookup.try_manufacturer_lookup(row)`
- `match_confidence` is `"high"` or `"medium"` → apply fields, set `Source Type` suffix `_MfrLookup`, skip Phase 2
- `"low"` or `"none"` → log to diagnostics, run Phase 2

**Phase 2 — Brave Search fallback** (existing, unchanged):
- `_ENRICHABLE_FIELDS` extended to include `"Material"` and `"Lead Time"` only; per-axis dimension fields (`Width (in)` etc.) are not in this list — they're populated exclusively via the dimension sync step after merge
- Source Type suffix for this path remains `_Enriched`

### Source type provenance

Base types never changed. Suffixes appended:

| Original | Manufacturer hit | Brave fallback |
|---|---|---|
| `PDF` | `PDF_MfrLookup` | `PDF_Enriched` |
| `PDF_AI` | `PDF_AI_MfrLookup` | `PDF_AI_Enriched` |
| `Manual` | never enriched | never enriched |
| `URL` | never enriched | never enriched |

`_qualifies()` updated to skip rows already ending in `_MfrLookup` or `_Enriched`.

### Field merge rules

| Existing field state | Enrichment confidence | Action |
|---|---|---|
| Empty | Any | Fill |
| Parser-extracted (`PDF`/`PDF_AI`) | ≥ medium | Overwrite; note original value in Notes if it differed |
| Parser-extracted (`PDF`/`PDF_AI`) | low | Fill only if blank; flag Review Required |
| User-entered (`Manual`/`URL`) | Any | Never overwrite |
| Already high-confidence manufacturer value | Any lower | Skip |

### Dimension sync at merge time

After every field application:
1. If `Dimensions` was filled → `parse_dimensions()` → populate empty per-axis columns
2. If per-axis columns were filled → `format_dimensions_string()` → populate empty `Dimensions`

---

## Acceptance Test

Product: **Scotsman SCN60PA1SU** (Icemaker Built-In Pump)

Expected outcomes:
- Notes do not contain `"Line 1."` or similar prefixes
- `Product Name` is clean (e.g. `"Scotsman Undercounter Ice Machine"`)
- `Brand` = `"Scotsman"`
- `Model/SKU` = `"SCN60PA1SU"`
- Manufacturer lookup attempted against `scotsman-ice.com`
- Official Scotsman source/spec sheet checked
- `Dimensions` and/or per-axis fields extracted if available on the page
- `Material` filled if stated on manufacturer page
- Source type ends in `_MfrLookup` on success
- No messy contact/source text in Product Name or Product Description
- Extra source/contact info goes into cleaned Notes
