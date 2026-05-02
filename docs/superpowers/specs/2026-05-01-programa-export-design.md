# Programa Export Module — Design Spec

**Date:** 2026-05-01  
**Scope:** Spec #1 of 3 — Export module + Streamlit UI  
**Status:** Approved

---

## Overview

Replace Programa browser automation as the primary output path with a clean CSV/XLSX file formatted for Programa's built-in "Import Products" feature. Browser automation is preserved as a labeled legacy path.

---

## Column Mapping

Internal schema → Programa import columns (fixed output order):

| # | Programa Column | Internal Source | Transformation |
|---|---|---|---|
| 1 | `Section` | `Product Category` | Direct; fallback `"General"` with warning if blank (AI inference is Spec #2) |
| 2 | `Product Name` | `Product Name` | Direct; rows missing this are skipped from export |
| 3 | `Brand` | `Brand` | Direct |
| 4 | `SKU` | `Model/SKU` | Direct |
| 5 | `Model` | *(blank)* | Reserved for future split from `Model/SKU` |
| 6 | `Dimensions` | `Dimensions` | Direct |
| 7 | `Width (in)` | parsed from `Dimensions` | `extract_labeled_dimensions()` from `src/dimensions.py` |
| 8 | `Height (in)` | parsed from `Dimensions` | `extract_labeled_dimensions()` |
| 9 | `Depth (in)` | parsed from `Dimensions` | `extract_labeled_dimensions()` |
| 10 | `Length (in)` | parsed from `Dimensions` | `extract_labeled_dimensions()` |
| 11 | `Quantity` | `Quantity` | Direct |
| 12 | `Price` | `Price` | Direct |
| 13 | `Supplier` | `Supplier` | Direct |
| 14 | `Product URL` | `Product URL` | Direct |
| 15 | `Image URL` | `Image URL` | Direct |
| 16 | `Finish` | `Finish / Color` | Direct; no Color/Finish split |
| 17 | `Color` | *(blank)* | Reserved; only populated if source explicitly separates |
| 18 | `Material` | extracted from `Notes` | Strip `[Materials: ...]` tag; `Material` is not in current internal schema so Notes is always the source |
| 19 | `Lead Time` | `Lead Time` field if present; else blank | No extraction fallback |
| 20 | `Notes` | `Notes` | Strip all `[...]` system tags + row-number prefix |
| 21 | `Location` | `Room` | Direct; NOT used as Section |

Internal-only columns excluded from export: `Source Type`, `Status`, `Import Type`, `photo_only`, `AI Category Confidence`, `Category Source`, `Image Filename`, `Image Upload Status`, `Local Image Path`, `Confidence Score`, `Review Required`, `Suggested Action`, `Missing Fields`, `Include`, `Project`.

---

## Module: `src/programa_export.py`

### Public API

```python
build_programa_import_dataframe(rows: list[dict] | pd.DataFrame) -> pd.DataFrame
```
Applies all transformations. Returns export-ready DataFrame with exactly the 21 Programa columns above.

```python
export_programa_csv(df: pd.DataFrame) -> bytes
```
UTF-8 CSV. Thin writer around `df.to_csv()`.

```python
export_programa_xlsx(df: pd.DataFrame) -> bytes
```
Single worksheet, no merged cells. Uses `openpyxl` engine. Returns bytes.

### Transformation Pipeline (inside `build_programa_import_dataframe`)

Applied in order:

1. **Filter**: keep only `Include == True` rows (same as existing CSV export)
2. **Material extraction**: parse `[Materials: <value>]` tag from `Notes` → populate `Material` column (`Material` is not in the current internal schema, so Notes is always the source)
3. **Notes cleanup**: strip all `[...]` bracket-enclosed system tags from `Notes`, then `remove_notes_row_prefix()`
4. **Section fallback**: if `Product Category` is blank, use `"General"` and mark row for warning
5. **Dimension parsing**: call `extract_labeled_dimensions(dimensions_str)` → populate `Width (in)`, `Height (in)`, `Depth (in)`, `Length (in)` as raw parsed strings (fractions like `"14 7/8"` are preserved as written; empty string if not found)
6. **Column rename + reorder**: apply mapping table, output fixed 21-column order
7. **Type coercion**: `Quantity` → int or blank; `Price` → string (preserve original format)

---

## Validation

Runs before export. Warnings displayed in UI; export is never blocked by warnings.

| Issue | Severity | Behavior |
|---|---|---|
| Missing `Product Name` | Error | Row excluded from export; shown in UI with product index |
| Missing `Product Category` (→ Section) | Warning | Fallback to `"General"`; affected product names listed in expandable UI element |
| Missing `Dimensions` and no parsed dims | Warning | Count shown |
| Missing `Product URL` | Warning | Count shown |
| Missing `Image URL` | Warning | Count shown |

---

## Streamlit UI

### Placement

New `st.expander("Export for Programa Import", expanded=True)` in `app.py`, inserted after the review table. Existing Programa browser automation expander gets subtitle: *"Legacy path — use CSV/XLSX export above for most imports."*

### Validation Summary Layout

```
✓  N rows ready for export
⚠  N rows missing Section — using "General"
     ▸ [expandable list of product names]
⚠  N rows missing Dimensions
⚠  N rows missing Product URL
⚠  N rows missing Image URL
✕  N rows skipped (no Product Name)
     ▸ [list of row indices]
```

Validation re-runs on every render (no button required — always reflects current table state).

### Download Buttons

```
[Download CSV]    [Download XLSX]
```

Filenames: `programa_import_YYYY-MM-DD.csv` / `programa_import_YYYY-MM-DD.xlsx`

```
[ ] Include debug columns
[Download Debug CSV]    ← shown only when checkbox checked
```

Debug CSV includes all 21 Programa columns plus: `Confidence Score`, `Source Type`, `AI Category Confidence`, `Category Source`, `Local Image Path`.

---

## Programa Import File Requirements (constraints)

- File types: `.xlsx`, `.xls`, `.csv`
- No merged cells
- Clear column headers (row 1)
- One product per row
- Single worksheet only
- File size limit: 50 MB
- Image URL on same row as product

All constraints are met by the design above.

---

## Out of Scope for This Spec

- Image/URL discovery improvements → Spec #2
- Testing/validation pass → Spec #3
- Embedded images in XLSX (Programa does not require this)
- `Color` column population (no reliable source yet)
- `Model` column split from `Model/SKU` (no reliable delimiter)
- `Lead Time` extraction from unstructured text
