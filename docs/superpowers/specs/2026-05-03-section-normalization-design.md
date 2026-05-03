# Section Normalization — Design Spec

**Date:** 2026-05-03
**Status:** Approved

---

## Overview

Ensure all products export to Programa with clean, consistent section names. Normalization runs at two points: during enrichment (so canonical values are stored on the row and visible in the review table), and again at export time as a safety pass. A new `src/section_normalizer.py` module owns all normalization logic and is shared by both.

---

## New Module: `src/section_normalizer.py`

### Public API

```python
CANONICAL_SECTIONS: list[str]
# 23 canonical section names matching intake_schema.CATEGORIES

def normalize_section(value: str) -> str
# Maps any raw string to a canonical section name.
# Returns "General" for unknown or blank input.

def validate_sections(rows: list[dict]) -> dict
# Accepts list of row dicts (using "Product Category" key).
# Returns: {"section_counts": dict[str, int], "warnings": list[str]}
```

### Normalization Logic (in order)

1. Blank / None input → `"General"`
2. Exact case-insensitive match against `CANONICAL_SECTIONS` → return canonical-case value
3. Lookup in `ALIAS_MAP` (all keys lowercase) → return mapped value
4. Title-case the input → if result is in `CANONICAL_SECTIONS`, use it
5. No match → `"General"`

### Alias Map

All keys lowercase. Single place to add new mappings. Covers at minimum:

```python
ALIAS_MAP: dict[str, str] = {
    # Appliances
    "appliance": "Appliances",
    "kitchen appliance": "Appliances",
    "kitchen appliances": "Appliances",
    "built-in appliance": "Appliances",
    "built in": "Appliances",
    # Lighting
    "light": "Lighting",
    "lights": "Lighting",
    "lighting fixture": "Lighting",
    "lighting fixtures": "Lighting",
    "lamp": "Lighting",
    "lamps": "Lighting",
    "ceiling light": "Lighting",
    "pendant": "Lighting",
    "pendant light": "Lighting",
    # Plumbing
    "plumbing fixture": "Plumbing",
    "plumbing fixtures": "Plumbing",
    "bath fixture": "Plumbing",
    "bath fixtures": "Plumbing",
    "bathroom fixture": "Plumbing",
    "faucet": "Plumbing",
    "faucets": "Plumbing",
    "toilet": "Plumbing",
    "sink": "Plumbing",
    "shower": "Plumbing",
    "bathtub": "Plumbing",
    # Cabinetry
    "cabinet": "Cabinetry",
    "cabinets": "Cabinetry",
    "millwork": "Cabinetry",
    # Flooring
    "floor": "Flooring",
    "floors": "Flooring",
    "floor covering": "Flooring",
    "hardwood": "Flooring",
    "tile flooring": "Flooring",
    # Furniture
    "furnishing": "Furniture",
    "furnishings": "Furniture",
    # Decor
    "decoration": "Decor",
    "decorations": "Decor",
    # Accessories
    "accessory": "Accessories",
    # Art
    "artwork": "Art",
    "art / artwork": "Art",
    # General fallbacks
    "misc": "General",
    "miscellaneous": "General",
    "other": "General",
    "unknown": "General",
    "n/a": "General",
    "": "General",
}
```

### Validation Logic

```python
SECTION_COUNT_WARNING_THRESHOLD = 10
SECTION_NAME_MAX_LENGTH = 40

def validate_sections(rows: list[dict]) -> dict:
    # 1. Normalize all Product Category values for counting
    # 2. Count per section (sorted descending by count)
    # 3. Collect warnings:
    #    - len(unique_sections) > SECTION_COUNT_WARNING_THRESHOLD
    #    - any section name > SECTION_NAME_MAX_LENGTH chars
    #    - any section name that exactly matches a Product Name in the same batch
    # 4. Also return: rows_by_section (dict[str, list[str]])
    #    → maps section name to list of product names in that section
    #    Used by UI for expandable "General" list and other drill-downs.
```

Return shape:
```python
{
    "section_counts": {"Appliances": 12, "Lighting": 6, ...},   # sorted desc
    "rows_by_section": {"General": ["Unknown Fixture", ...], ...},
    "warnings": [
        "Section count (16) exceeds 10 — check for one-off categories",
        "Section name too long: '...'",
        "Section name matches product name: 'Scotsman Icemaker'",
    ],
}
```

---

## Integration Points

### `product_enrichment.py` — intake-time normalization

In `_apply_enrichment()`, when writing `Product Category` from AI extraction back to the row:

```python
from src.section_normalizer import normalize_section

# Before:
row["Product Category"] = extracted.get("Product Category", "")

# After:
raw_category = extracted.get("Product Category", "")
row["Product Category"] = normalize_section(raw_category)
```

This covers the AI path (Claude Haiku returns "kitchen appliance" → stored as "Appliances"). User-entered values through the Streamlit dropdown are already constrained to `CATEGORIES`, so they do not need normalization.

### `programa_export.py` — export-time safety pass

In `_row_to_programa_dict()`, replace:

```python
"Section": _str_val(row.get("Product Category")) or "General",
```

with:

```python
"Section": normalize_section(_str_val(row.get("Product Category"))),
```

This is the final safety pass and runs unconditionally on every row exported, regardless of how the value was set.

### `intake_schema.py` — unchanged

`CATEGORIES` remains as-is. `section_normalizer.py` defines `CANONICAL_SECTIONS` independently. A test enforces they stay in sync (see Testing section).

---

## Streamlit UI

All changes are in `app.py`, within the existing "Export for Programa Import" section.

### Section Distribution (inserted before download buttons)

Runs `validate_sections(included)` (where `included` is the already-filtered DataFrame). Displayed between the existing warning lines and the download buttons:

```
Section Distribution
  Appliances      12
  Lighting         6
  Decor            4
  General          2    ▸ [expandable: product names]

⚠  16 unique sections — unusually high, check for one-off categories
⚠  Section name "Scotsman SCN60PA1SU" matches a product name
```

**Rules:**
- Sort counts descending.
- "General" row is always expandable, showing the product names assigned to it (so users can fix them).
- Any other section with a warning (too long, matches product name) is also highlighted.
- Section explosion warning fires when unique section count > 10.

### Bulk Section Edit (inserted immediately after Section Distribution, before download buttons)

```
Set section for all included rows:  [Appliances ▾]  [Apply to All Included]
```

**Behavior:**
- Dropdown populated with `CANONICAL_SECTIONS`.
- On click: update `Product Category` on all rows where `Include == True`.
- Immediately re-run `validate_sections()` and re-render the Section Distribution summary.
- Show a brief success message: `✓ Section updated to "Appliances" for N rows`.

Implementation note: use `st.session_state` to trigger re-render after bulk apply. The download buttons below automatically reflect the updated values on the same render pass.

### Per-Row Override

No change. The existing `st.data_editor` with `SelectboxColumn` for `Product Category` already constrains to `CATEGORIES` values. The export safety pass handles any edge cases.

---

## Testing

**`tests/test_section_normalizer.py`**

1. `CANONICAL_SECTIONS` equals `intake_schema.CATEGORIES` (sync guard)
2. `normalize_section` exact match (case-insensitive): `"appliances"` → `"Appliances"`
3. `normalize_section` alias: `"kitchen appliance"` → `"Appliances"`, `"lighting fixture"` → `"Lighting"`, `"plumbing fixture"` → `"Plumbing"`
4. `normalize_section` title-case fallback: `"art"` → `"Art"`
5. `normalize_section` unknown → `"General"`
6. `normalize_section` blank / None → `"General"`
7. `validate_sections` returns counts sorted descending
8. `validate_sections` warns when unique section count > 10
9. `validate_sections` warns on section name > 40 chars
10. `validate_sections` warns when section name matches a product name
11. `validate_sections` `rows_by_section["General"]` lists correct product names
12. `programa_export._row_to_programa_dict` normalizes via `normalize_section` (alias input → canonical output)
13. `programa_export._row_to_programa_dict` blank category → `"General"`

---

## Export Behavior

`PROGRAMA_COLUMNS` are unchanged. The `Section` column always contains a canonical value from `CANONICAL_SECTIONS` (or `"General"`). No new columns are added to the export.

---

## Acceptance Criteria

Given the same 24-product CSV:

- CSV export produces clean section groupings, e.g.:
  ```
  Appliances (12 items)
  Lighting (6 items)
  Decor (4 items)
  General (2 items)
  ```
- No raw AI labels (e.g. "kitchen appliance", "lighting fixture") appear as Section values in the export.
- Section Distribution UI shows counts sorted descending.
- "General" rows are expandable with product names.
- Bulk edit updates all included rows and re-renders validation immediately.
- `normalize_section` test suite passes with 13 tests.

---

## Out of Scope

- AI-assisted category inference for uncategorized products — separate spec.
- Section normalization in Next.js frontend — deferred until Streamlit flow is stable.
- Adding a `Normalized Category` column to the schema — not needed; canonical values are stored in place.
