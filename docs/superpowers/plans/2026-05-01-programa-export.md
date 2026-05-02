# Programa Export Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `src/programa_export.py` that transforms the intake DataFrame into a clean Programa-compatible CSV/XLSX, plus a Streamlit export section in `app.py`.

**Architecture:** A new self-contained module handles all column mapping and data transformation. Three public functions — `validate_for_export`, `build_programa_import_dataframe`, `build_programa_debug_dataframe` — keep logic testable independently of the UI. The Streamlit section reads from the already-edited `edited_df`, builds the export DataFrame once, and offers CSV/XLSX downloads with an optional debug toggle.

**Tech Stack:** Python 3.11, pandas, openpyxl (XLSX), Streamlit, existing `src/dimensions.py` and `src/notes.py` helpers.

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Create | `src/programa_export.py` | All export logic: helpers, mapping, validation, writers |
| Create | `tests/test_programa_export.py` | Unit tests for the new module |
| Modify | `app.py:1297–1312` | Add "Export for Programa Import" section after Export CSV button |
| Modify | `app.py:722–725` | Label "Programa Automation" section as legacy |
| Modify | `requirements.txt` | Add `openpyxl>=3.1.0` |

---

## Task 1: Add `openpyxl` dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1.1: Add openpyxl to requirements.txt**

Open `requirements.txt` and add this line:

```
openpyxl>=3.1.0
```

- [ ] **Step 1.2: Install it**

```bash
pip install openpyxl
```

Expected: installs without errors.

- [ ] **Step 1.3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add openpyxl for Programa XLSX export"
```

---

## Task 2: Notes/material helper tests (failing)

**Files:**
- Create: `tests/test_programa_export.py`

- [ ] **Step 2.1: Create the test file with failing tests**

Create `tests/test_programa_export.py`:

```python
import io

import pandas as pd
import pytest

from src.programa_export import (
    _clean_notes,
    _extract_material_from_notes,
)


# ── Shared test fixtures ──────────────────────────────────────────────────────

def _scotsman_row() -> dict:
    """Acceptance test fixture — Scotsman icemaker."""
    return {
        "Include": True,
        "Product Category": "Appliances",
        "Product Name": "Scotsman Icemaker Built-In Pump",
        "Brand": "Scotsman",
        "Model/SKU": "SCN60PA1SU",
        "Dimensions": "14.875 in W x 22 in D x 33.375 in H",
        "Finish / Color": "Stainless Steel",
        "Quantity": 1,
        "Price": "",
        "Supplier": "Scotsman",
        "Product URL": "https://scotsman-ice.com/product/scn60pa1su",
        "Image URL": "https://scotsman-ice.com/images/scn60pa1su.jpg",
        "Notes": "Verify delivery date. [Materials: Stainless Steel] [Enrichment: matched]",
        "Room": "Kitchen",
    }


def _make_rows(overrides_list: list[dict]) -> list[dict]:
    """Build a list of minimal valid rows, applying per-row overrides."""
    base = {
        "Include": True,
        "Product Name": "Test Product",
        "Product Category": "Lighting",
        "Dimensions": "12 in W x 10 in H x 8 in D",
        "Product URL": "https://example.com/product",
        "Image URL": "https://example.com/image.jpg",
    }
    return [{**base, **o} for o in overrides_list]


# ── _extract_material_from_notes ──────────────────────────────────────────────

def test_extract_material_from_tag():
    assert _extract_material_from_notes("[Materials: Stainless Steel]") == "Stainless Steel"

def test_extract_material_case_insensitive():
    assert _extract_material_from_notes("[materials: oak veneer]") == "oak veneer"

def test_extract_material_trims_whitespace():
    assert _extract_material_from_notes("[Materials:  solid oak  ]") == "solid oak"

def test_extract_material_with_surrounding_text():
    result = _extract_material_from_notes("Some note. [Materials: Brass] More text.")
    assert result == "Brass"

def test_extract_material_missing_tag_returns_empty():
    assert _extract_material_from_notes("Just a regular note.") == ""

def test_extract_material_empty_string():
    assert _extract_material_from_notes("") == ""


# ── _clean_notes ──────────────────────────────────────────────────────────────

def test_clean_notes_strips_materials_tag():
    result = _clean_notes("Good note. [Materials: Steel]")
    assert "[Materials:" not in result
    assert "Good note." in result

def test_clean_notes_strips_enrichment_tag():
    result = _clean_notes("[Enrichment: no confident source found] Some note.")
    assert "[Enrichment:" not in result
    assert "Some note." in result

def test_clean_notes_strips_partial_dimension_tag():
    raw = "Check with vendor. [Partial dimension found: 14 W; full W x H x D still needed]"
    result = _clean_notes(raw)
    assert "[Partial dimension" not in result
    assert "Check with vendor." in result

def test_clean_notes_removes_row_prefix():
    assert _clean_notes("3 - Verify finish") == "Verify finish"

def test_clean_notes_keeps_human_text():
    assert _clean_notes("Stainless steel finish, confirm with supplier.") == \
        "Stainless steel finish, confirm with supplier."

def test_clean_notes_empty_string():
    assert _clean_notes("") == ""

def test_clean_notes_only_system_tag_returns_empty():
    result = _clean_notes("[Enrichment: no confident source found]")
    assert result == ""
```

- [ ] **Step 2.2: Run to verify they fail**

```bash
pytest tests/test_programa_export.py -v
```

Expected: `ImportError` — module not yet created.

---

## Task 3: Implement notes/material helpers

**Files:**
- Create: `src/programa_export.py`

- [ ] **Step 3.1: Create `src/programa_export.py` with helpers**

```python
"""
Programa import-file export for SCH DesignOps Intake.

Transforms the internal intake DataFrame into a clean CSV/XLSX compatible
with Programa's built-in "Import Products" feature.

Public API
----------
PROGRAMA_COLUMNS : list[str]
    Fixed 21-column output order for the Programa import file.

validate_for_export(rows) -> dict
    Returns a validation summary without modifying data.

build_programa_import_dataframe(rows) -> pd.DataFrame
    Returns a clean export-ready DataFrame (Include=True, Product Name required).

build_programa_debug_dataframe(rows) -> pd.DataFrame
    Same as above plus debug/confidence columns for internal review.

export_programa_csv(df) -> bytes
    Serialize DataFrame to UTF-8 CSV bytes.

export_programa_xlsx(df) -> bytes
    Serialize DataFrame to XLSX bytes (single sheet, no merged cells).
"""

from __future__ import annotations

import io
import re

import pandas as pd

from src.dimensions import extract_labeled_dimensions, has_complete_3d_dimensions
from src.notes import remove_notes_row_prefix

# ── Constants ─────────────────────────────────────────────────────────────────

PROGRAMA_COLUMNS: list[str] = [
    "Section",
    "Product Name",
    "Brand",
    "SKU",
    "Model",
    "Dimensions",
    "Width (in)",
    "Height (in)",
    "Depth (in)",
    "Length (in)",
    "Quantity",
    "Price",
    "Supplier",
    "Product URL",
    "Image URL",
    "Finish",
    "Color",
    "Material",
    "Lead Time",
    "Notes",
    "Location",
]

_DEBUG_EXTRA_COLUMNS: list[str] = [
    "Confidence Score",
    "Source Type",
    "AI Category Confidence",
    "Category Source",
    "Local Image Path",
]

_MATERIAL_TAG_RE = re.compile(r"\[Materials:\s*([^\]]+)\]", re.IGNORECASE)
_SYSTEM_TAG_RE = re.compile(r"\[[^\]]*\]")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _str_val(v) -> str:
    """Safely coerce a cell value to a stripped string, treating None/NaN as blank."""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"nan", "none", "null"} else s


def _extract_material_from_notes(notes: str) -> str:
    """Return the value from the first [Materials: ...] tag, or empty string."""
    m = _MATERIAL_TAG_RE.search(notes)
    return m.group(1).strip() if m else ""


def _clean_notes(notes: str) -> str:
    """Strip all [...] system tags and leading row-number prefixes from notes."""
    text = _SYSTEM_TAG_RE.sub("", notes)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return remove_notes_row_prefix(text)
```

- [ ] **Step 3.2: Run tests**

```bash
pytest tests/test_programa_export.py -v
```

Expected: all 13 tests PASS.

- [ ] **Step 3.3: Commit**

```bash
git add src/programa_export.py tests/test_programa_export.py
git commit -m "feat: add programa_export module with notes/material helpers"
```

---

## Task 4: Row transformation tests (failing)

**Files:**
- Modify: `tests/test_programa_export.py`

- [ ] **Step 4.1: Add failing tests for `_row_to_programa_dict`**

Append to `tests/test_programa_export.py`:

```python
from src.programa_export import _row_to_programa_dict


# ── _row_to_programa_dict ─────────────────────────────────────────────────────

def test_scotsman_section_maps_to_product_category():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Section"] == "Appliances"

def test_scotsman_sku_mapped():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["SKU"] == "SCN60PA1SU"

def test_scotsman_model_blank():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Model"] == ""

def test_scotsman_dimensions_direct():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Dimensions"] == "14.875 in W x 22 in D x 33.375 in H"

def test_scotsman_width_parsed():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Width (in)"] == "14.875"

def test_scotsman_height_parsed():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Height (in)"] == "33.375"

def test_scotsman_depth_parsed():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Depth (in)"] == "22"

def test_scotsman_length_blank_when_absent():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Length (in)"] == ""

def test_scotsman_finish_from_finish_color():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Finish"] == "Stainless Steel"

def test_scotsman_color_blank():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Color"] == ""

def test_scotsman_material_extracted_from_notes():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Material"] == "Stainless Steel"

def test_scotsman_notes_cleaned():
    result = _row_to_programa_dict(_scotsman_row())
    assert "[Materials:" not in result["Notes"]
    assert "[Enrichment:" not in result["Notes"]
    assert "Verify delivery date." in result["Notes"]

def test_scotsman_location_from_room():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Location"] == "Kitchen"

def test_section_fallback_to_general_when_category_blank():
    row = _scotsman_row()
    row["Product Category"] = ""
    result = _row_to_programa_dict(row)
    assert result["Section"] == "General"

def test_material_explicit_field_takes_priority():
    row = _scotsman_row()
    row["Material"] = "Cast Iron"
    result = _row_to_programa_dict(row)
    assert result["Material"] == "Cast Iron"

def test_lead_time_blank_when_no_field():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Lead Time"] == ""

def test_lead_time_explicit_field_used():
    row = _scotsman_row()
    row["Lead Time"] = "8-10 weeks"
    result = _row_to_programa_dict(row)
    assert result["Lead Time"] == "8-10 weeks"

def test_quantity_coerced_to_int():
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Quantity"] == 1

def test_quantity_string_input_coerced():
    row = _scotsman_row()
    row["Quantity"] = "3"
    result = _row_to_programa_dict(row)
    assert result["Quantity"] == 3

def test_output_has_all_programa_columns():
    from src.programa_export import PROGRAMA_COLUMNS
    result = _row_to_programa_dict(_scotsman_row())
    for col in PROGRAMA_COLUMNS:
        assert col in result, f"Missing column: {col}"
```

- [ ] **Step 4.2: Run to verify they fail**

```bash
pytest tests/test_programa_export.py::test_scotsman_section_maps_to_product_category -v
```

Expected: `ImportError` — `_row_to_programa_dict` not yet defined.

---

## Task 5: Implement row transformation

**Files:**
- Modify: `src/programa_export.py`

- [ ] **Step 5.1: Add `_row_to_programa_dict` to `src/programa_export.py`**

Append after the `_clean_notes` function:

```python
def _row_to_programa_dict(row: dict) -> dict:
    """
    Map one internal intake row to a Programa import column dict.
    Applies all transformations: material extraction, notes cleanup,
    section fallback, dimension parsing, type coercion.
    """
    notes_raw = _str_val(row.get("Notes"))

    # Material: explicit field → Notes tag → blank
    material = (
        _str_val(row.get("Material"))
        or _extract_material_from_notes(notes_raw)
    )

    # Notes: extract material tag first (so it's stripped), then clean
    cleaned_notes = _clean_notes(notes_raw)

    # Section: Product Category → "General" fallback
    section = _str_val(row.get("Product Category")) or "General"

    # Dimensions: direct copy + parsed structured dims
    dims = _str_val(row.get("Dimensions"))
    parsed = (
        extract_labeled_dimensions(dims)
        if dims
        else {"width": "", "height": "", "depth": "", "length": ""}
    )

    # Quantity: coerce to int if possible
    qty_raw = row.get("Quantity")
    try:
        quantity = int(qty_raw) if qty_raw not in (None, "", "nan") else ""
    except (ValueError, TypeError):
        quantity = _str_val(qty_raw)

    return {
        "Section":      section,
        "Product Name": _str_val(row.get("Product Name")),
        "Brand":        _str_val(row.get("Brand")),
        "SKU":          _str_val(row.get("Model/SKU")),
        "Model":        _str_val(row.get("Model")),
        "Dimensions":   dims,
        "Width (in)":   parsed["width"],
        "Height (in)":  parsed["height"],
        "Depth (in)":   parsed["depth"],
        "Length (in)":  parsed["length"],
        "Quantity":     quantity,
        "Price":        _str_val(row.get("Price")),
        "Supplier":     _str_val(row.get("Supplier")),
        "Product URL":  _str_val(row.get("Product URL")),
        "Image URL":    _str_val(row.get("Image URL")),
        "Finish":       _str_val(row.get("Finish / Color")),
        "Color":        _str_val(row.get("Color")),
        "Material":     material,
        "Lead Time":    _str_val(row.get("Lead Time")),
        "Notes":        cleaned_notes,
        "Location":     _str_val(row.get("Room")),
    }
```

- [ ] **Step 5.2: Run tests**

```bash
pytest tests/test_programa_export.py -v
```

Expected: all tests PASS.

- [ ] **Step 5.3: Commit**

```bash
git add src/programa_export.py tests/test_programa_export.py
git commit -m "feat: add _row_to_programa_dict with full column mapping"
```

---

## Task 6: Validation function tests + implementation

**Files:**
- Modify: `tests/test_programa_export.py`
- Modify: `src/programa_export.py`

- [ ] **Step 6.1: Add failing tests for `validate_for_export`**

Append to `tests/test_programa_export.py`:

```python
from src.programa_export import validate_for_export


# ── validate_for_export ───────────────────────────────────────────────────────

def test_validate_counts_export_rows():
    rows = _make_rows([{}, {}, {}])
    result = validate_for_export(rows)
    assert result["export_count"] == 3


def test_validate_skips_rows_missing_product_name():
    rows = _make_rows([{"Product Name": ""}, {"Product Name": "Real Product"}])
    result = validate_for_export(rows)
    assert result["export_count"] == 1
    assert len(result["skipped"]) == 1


def test_validate_skips_excluded_rows():
    rows = _make_rows([{"Include": False}, {}])
    result = validate_for_export(rows)
    assert result["export_count"] == 1


def test_validate_flags_missing_section():
    rows = _make_rows([{"Product Category": ""}, {}])
    result = validate_for_export(rows)
    assert len(result["missing_section"]) == 1
    assert result["missing_section"][0]["product_name"] == "Test Product"


def test_validate_flags_missing_dimensions():
    rows = _make_rows([{"Dimensions": ""}, {}])
    result = validate_for_export(rows)
    assert result["missing_dimensions"] == 1


def test_validate_flags_partial_dimensions():
    rows = _make_rows([{"Dimensions": "12 in W x 10 in H"}, {}])
    result = validate_for_export(rows)
    # Partial dims (no depth) should be flagged
    assert result["missing_dimensions"] == 1


def test_validate_flags_missing_product_url():
    rows = _make_rows([{"Product URL": ""}, {}])
    result = validate_for_export(rows)
    assert result["missing_product_url"] == 1


def test_validate_flags_missing_image_url():
    rows = _make_rows([{"Image URL": ""}, {}])
    result = validate_for_export(rows)
    assert result["missing_image_url"] == 1


def test_validate_accepts_dataframe_input():
    df = pd.DataFrame(_make_rows([{}, {}]))
    result = validate_for_export(df)
    assert result["export_count"] == 2


def test_validate_result_keys():
    result = validate_for_export([])
    assert set(result.keys()) == {
        "skipped", "missing_section", "missing_dimensions",
        "missing_product_url", "missing_image_url", "export_count",
    }
```

- [ ] **Step 6.2: Run to verify they fail**

```bash
pytest tests/test_programa_export.py -k "validate" -v
```

Expected: `ImportError` — `validate_for_export` not yet defined.

- [ ] **Step 6.3: Add `validate_for_export` to `src/programa_export.py`**

Append after `_row_to_programa_dict`:

```python
def _to_row_list(rows) -> list[dict]:
    """Normalize list[dict] or DataFrame to list[dict]."""
    if isinstance(rows, pd.DataFrame):
        return [r.to_dict() for _, r in rows.iterrows()]
    return list(rows)


def _is_included(row: dict) -> bool:
    """True when Include is truthy (True, 1, 'True', or absent)."""
    v = row.get("Include", True)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() not in {"false", "0", "no"}


def validate_for_export(rows) -> dict:
    """
    Return a validation summary dict without modifying any data.

    Keys:
        skipped          list[{index, product_name}]  — rows missing Product Name
        missing_section  list[{index, product_name}]  — rows that will use "General"
        missing_dimensions  int
        missing_product_url int
        missing_image_url   int
        export_count        int  — rows that will appear in the export
    """
    row_list = _to_row_list(rows)
    included = [r for r in row_list if _is_included(r)]

    skipped: list[dict] = []
    missing_section: list[dict] = []
    missing_dimensions = 0
    missing_product_url = 0
    missing_image_url = 0
    export_count = 0

    for i, row in enumerate(included):
        name = _str_val(row.get("Product Name"))
        if not name:
            skipped.append({"index": i, "product_name": "(no name)"})
            continue

        export_count += 1

        if not _str_val(row.get("Product Category")):
            missing_section.append({"index": i, "product_name": name})

        if not has_complete_3d_dimensions(_str_val(row.get("Dimensions"))):
            missing_dimensions += 1

        if not _str_val(row.get("Product URL")):
            missing_product_url += 1

        if not _str_val(row.get("Image URL")):
            missing_image_url += 1

    return {
        "skipped": skipped,
        "missing_section": missing_section,
        "missing_dimensions": missing_dimensions,
        "missing_product_url": missing_product_url,
        "missing_image_url": missing_image_url,
        "export_count": export_count,
    }
```

- [ ] **Step 6.4: Run tests**

```bash
pytest tests/test_programa_export.py -v
```

Expected: all tests PASS.

- [ ] **Step 6.5: Commit**

```bash
git add src/programa_export.py tests/test_programa_export.py
git commit -m "feat: add validate_for_export to programa_export"
```

---

## Task 7: DataFrame builder and export writer tests + implementation

**Files:**
- Modify: `tests/test_programa_export.py`
- Modify: `src/programa_export.py`

- [ ] **Step 7.1: Add failing tests**

Append to `tests/test_programa_export.py`:

```python
from src.programa_export import (
    build_programa_import_dataframe,
    build_programa_debug_dataframe,
    export_programa_csv,
    export_programa_xlsx,
    PROGRAMA_COLUMNS,
    _DEBUG_EXTRA_COLUMNS,
)


# ── build_programa_import_dataframe ───────────────────────────────────────────

def test_build_returns_dataframe():
    rows = _make_rows([{}])
    df = build_programa_import_dataframe(rows)
    assert isinstance(df, pd.DataFrame)


def test_build_has_exactly_programa_columns():
    rows = _make_rows([{}])
    df = build_programa_import_dataframe(rows)
    assert list(df.columns) == PROGRAMA_COLUMNS


def test_build_excludes_rows_missing_product_name():
    rows = _make_rows([{"Product Name": ""}, {}])
    df = build_programa_import_dataframe(rows)
    assert len(df) == 1


def test_build_excludes_not_included_rows():
    rows = _make_rows([{"Include": False}, {}])
    df = build_programa_import_dataframe(rows)
    assert len(df) == 1


def test_build_scotsman_acceptance_case():
    row = _scotsman_row()
    df = build_programa_import_dataframe([row])
    assert len(df) == 1
    assert df.iloc[0]["Section"] == "Appliances"
    assert df.iloc[0]["Width (in)"] == "14.875"
    assert df.iloc[0]["Height (in)"] == "33.375"
    assert df.iloc[0]["Depth (in)"] == "22"
    assert "[Materials:" not in df.iloc[0]["Notes"]
    assert df.iloc[0]["Material"] == "Stainless Steel"


def test_build_accepts_dataframe_input():
    df_in = pd.DataFrame(_make_rows([{}, {}]))
    df_out = build_programa_import_dataframe(df_in)
    assert len(df_out) == 2


def test_build_empty_input_returns_empty_dataframe():
    df = build_programa_import_dataframe([])
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == PROGRAMA_COLUMNS
    assert len(df) == 0


# ── build_programa_debug_dataframe ────────────────────────────────────────────

def test_debug_build_has_programa_plus_debug_columns():
    rows = _make_rows([{}])
    df = build_programa_debug_dataframe(rows)
    expected = PROGRAMA_COLUMNS + _DEBUG_EXTRA_COLUMNS
    assert list(df.columns) == expected


# ── export_programa_csv ───────────────────────────────────────────────────────

def test_export_csv_returns_bytes():
    df = build_programa_import_dataframe(_make_rows([{}]))
    result = export_programa_csv(df)
    assert isinstance(result, bytes)


def test_export_csv_contains_header_row():
    df = build_programa_import_dataframe(_make_rows([{}]))
    text = export_programa_csv(df).decode("utf-8")
    assert "Section" in text
    assert "Product Name" in text


def test_export_csv_one_row_per_product():
    rows = _make_rows([{}, {}])
    df = build_programa_import_dataframe(rows)
    text = export_programa_csv(df).decode("utf-8")
    lines = [l for l in text.splitlines() if l.strip()]
    assert len(lines) == 3  # header + 2 rows


# ── export_programa_xlsx ──────────────────────────────────────────────────────

def test_export_xlsx_returns_bytes():
    df = build_programa_import_dataframe(_make_rows([{}]))
    result = export_programa_xlsx(df)
    assert isinstance(result, bytes)


def test_export_xlsx_is_valid_xlsx():
    import openpyxl
    df = build_programa_import_dataframe(_make_rows([{}]))
    xlsx_bytes = export_programa_xlsx(df)
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
    assert len(wb.sheetnames) == 1
    ws = wb.active
    # Row 1 is header, row 2 is data
    headers = [ws.cell(1, c).value for c in range(1, len(PROGRAMA_COLUMNS) + 1)]
    assert headers[0] == "Section"
    assert headers[1] == "Product Name"


def test_export_xlsx_no_merged_cells():
    import openpyxl
    df = build_programa_import_dataframe(_make_rows([{}]))
    wb = openpyxl.load_workbook(io.BytesIO(export_programa_xlsx(df)))
    assert len(wb.active.merged_cells.ranges) == 0


# ── Golden test ───────────────────────────────────────────────────────────────

def test_golden_csv_exact_columns_and_no_nan():
    """
    Golden test: one clean product → CSV has exact column order,
    no extra columns, no NaN strings, and correct values in data row.
    """
    import csv as _csv

    row = _scotsman_row()
    df = build_programa_import_dataframe([row])
    csv_bytes = export_programa_csv(df)
    csv_text = csv_bytes.decode("utf-8")

    # Exact column count and order
    assert len(df.columns) == len(PROGRAMA_COLUMNS)
    assert list(df.columns) == PROGRAMA_COLUMNS

    # No NaN strings anywhere in output
    assert "nan" not in csv_text.lower()

    # Parse and verify structure
    parsed_rows = list(_csv.reader(csv_text.splitlines()))
    assert len(parsed_rows) == 2  # header + 1 data row
    assert parsed_rows[0] == PROGRAMA_COLUMNS

    data = parsed_rows[1]
    assert data[PROGRAMA_COLUMNS.index("Section")] == "Appliances"
    assert data[PROGRAMA_COLUMNS.index("Product Name")] == "Scotsman Icemaker Built-In Pump"
    assert data[PROGRAMA_COLUMNS.index("SKU")] == "SCN60PA1SU"
    assert data[PROGRAMA_COLUMNS.index("Width (in)")] == "14.875"
    assert data[PROGRAMA_COLUMNS.index("Height (in)")] == "33.375"
    assert data[PROGRAMA_COLUMNS.index("Depth (in)")] == "22"
    assert data[PROGRAMA_COLUMNS.index("Material")] == "Stainless Steel"
    assert "[Materials:" not in data[PROGRAMA_COLUMNS.index("Notes")]
```

- [ ] **Step 7.2: Run to verify they fail**

```bash
pytest tests/test_programa_export.py -k "build or export" -v
```

Expected: `ImportError` — functions not yet defined.

- [ ] **Step 7.3: Add public functions to `src/programa_export.py`**

Append after `validate_for_export`:

```python
def build_programa_import_dataframe(rows) -> pd.DataFrame:
    """
    Transform intake rows into a clean Programa import DataFrame.

    Filters to Include=True rows with a non-empty Product Name.
    Returns an empty DataFrame (with correct columns) if no rows qualify.
    """
    row_list = _to_row_list(rows)
    included = [
        r for r in row_list
        if _is_included(r) and _str_val(r.get("Product Name"))
    ]

    records = [_row_to_programa_dict(r) for r in included]

    if not records:
        return pd.DataFrame(columns=PROGRAMA_COLUMNS)

    return pd.DataFrame(records, columns=PROGRAMA_COLUMNS)


def build_programa_debug_dataframe(rows) -> pd.DataFrame:
    """
    Same as build_programa_import_dataframe but appends debug/confidence columns.
    Debug columns are taken directly from the source rows (not transformed).
    """
    row_list = _to_row_list(rows)
    included = [
        r for r in row_list
        if _is_included(r) and _str_val(r.get("Product Name"))
    ]

    records = []
    for r in included:
        d = _row_to_programa_dict(r)
        for col in _DEBUG_EXTRA_COLUMNS:
            d[col] = _str_val(r.get(col))
        records.append(d)

    if not records:
        return pd.DataFrame(columns=PROGRAMA_COLUMNS + _DEBUG_EXTRA_COLUMNS)

    return pd.DataFrame(records, columns=PROGRAMA_COLUMNS + _DEBUG_EXTRA_COLUMNS)


def export_programa_csv(df: pd.DataFrame) -> bytes:
    """Serialize a Programa import DataFrame to UTF-8 CSV bytes."""
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def export_programa_xlsx(df: pd.DataFrame) -> bytes:
    """Serialize a Programa import DataFrame to XLSX bytes (single sheet, no merged cells)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Programa Import")
    return buf.getvalue()
```

- [ ] **Step 7.4: Run all tests**

```bash
pytest tests/test_programa_export.py -v
```

Expected: all tests PASS.

- [ ] **Step 7.5: Commit**

```bash
git add src/programa_export.py tests/test_programa_export.py
git commit -m "feat: add build/export functions to programa_export module"
```

---

## Task 8: Streamlit UI — Export for Programa Import section

**Files:**
- Modify: `app.py`

The new section goes after the existing "Export CSV" block (around line 1311) and before the "Needs Review" section (around line 1313). The "Programa Automation" section label (line 725) gets a legacy note.

- [ ] **Step 8.1: Add legacy label to Programa Automation section**

In `app.py`, find this block (around line 722–725):

```python
    # ── Programa Automation ────────────────────────────────────────────────────
    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
    st.divider()
    section_label("Programa Automation")
```

Replace with:

```python
    # ── Programa Automation ────────────────────────────────────────────────────
    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
    st.divider()
    section_label("Programa Automation")
    st.caption("Legacy path — use the CSV/XLSX export below for most imports.")
```

- [ ] **Step 8.2: Add the new export section after the existing Export CSV button**

In `app.py`, find the end of the "Export CSV" block (after the `st.download_button` for `Export Review CSV`, around line 1311):

```python
        st.download_button(
            label="Export Review CSV",
            data=get_csv_bytes(included),
            file_name=f"{safe_name}_intake.csv",
            mime="text/csv",
            use_container_width=True,
        )
```

Insert the following block immediately after that `download_button` call and before the `# ── Needs Review` comment:

```python
    # ── Export for Programa Import ─────────────────────────────────────────────
    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
    st.divider()
    section_label("Export for Programa Import")

    from src.programa_export import (
        build_programa_import_dataframe,
        build_programa_debug_dataframe,
        export_programa_csv,
        export_programa_xlsx,
        validate_for_export,
    )
    import datetime as _dt

    _val = validate_for_export(edited_df)
    _today = _dt.date.today().strftime("%Y-%m-%d")

    if _val["export_count"] == 0 and not _val["skipped"]:
        st.info("No rows available for export. Add products to the intake table first.")
    else:
        if _val["export_count"] > 0:
            st.success(
                f"✓  {_val['export_count']} row{'s' if _val['export_count'] != 1 else ''} ready for export"
            )

        if _val["missing_section"]:
            _ms_label = (
                f"⚠  {len(_val['missing_section'])} row{'s' if len(_val['missing_section']) != 1 else ''} "
                "missing Section — will use \"General\""
            )
            with st.expander(_ms_label):
                for item in _val["missing_section"]:
                    st.caption(f"• {item['product_name']}")

        if _val["missing_dimensions"]:
            st.warning(
                f"⚠  {_val['missing_dimensions']} row{'s' if _val['missing_dimensions'] != 1 else ''} "
                "missing complete dimensions",
                icon="⚠️",
            )

        if _val["missing_product_url"]:
            st.warning(
                f"⚠  {_val['missing_product_url']} row{'s' if _val['missing_product_url'] != 1 else ''} "
                "missing Product URL",
                icon="⚠️",
            )

        if _val["missing_image_url"]:
            st.warning(
                f"⚠  {_val['missing_image_url']} row{'s' if _val['missing_image_url'] != 1 else ''} "
                "missing Image URL",
                icon="⚠️",
            )

        if _val["skipped"]:
            _sk_label = (
                f"✕  {len(_val['skipped'])} row{'s' if len(_val['skipped']) != 1 else ''} "
                "skipped — no Product Name"
            )
            with st.expander(_sk_label):
                for item in _val["skipped"]:
                    st.caption(f"• Row {item['index'] + 1}")

        if _val["export_count"] > 0:
            _export_df = build_programa_import_dataframe(edited_df)

            _dl_col1, _dl_col2 = st.columns(2)
            with _dl_col1:
                st.download_button(
                    "Download CSV",
                    data=export_programa_csv(_export_df),
                    file_name=f"programa_import_{_today}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            with _dl_col2:
                st.download_button(
                    "Download XLSX",
                    data=export_programa_xlsx(_export_df),
                    file_name=f"programa_import_{_today}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

            _debug_mode = st.checkbox("Include debug columns", key="programa_export_debug")
            if _debug_mode:
                _debug_df = build_programa_debug_dataframe(edited_df)
                st.download_button(
                    "Download Debug CSV",
                    data=export_programa_csv(_debug_df),
                    file_name=f"programa_import_{_today}_debug.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
```

- [ ] **Step 8.3: Run all tests to confirm no regressions**

```bash
pytest -v
```

Expected: all existing tests plus all new `test_programa_export.py` tests PASS.

- [ ] **Step 8.4: Start the Streamlit app and verify the new section renders**

```bash
streamlit run app.py
```

Navigate to the bottom of the intake table. Verify:
- "Export for Programa Import" section appears with the section label
- "Programa Automation" section shows the legacy caption below it
- With no rows loaded: info message is shown
- With rows loaded: validation summary reflects the current table state
- CSV download produces a UTF-8 file with 21 columns and one header row
- XLSX download produces a single-sheet file with no merged cells
- Debug checkbox reveals the debug CSV download button

- [ ] **Step 8.5: Commit**

```bash
git add app.py
git commit -m "feat: add Programa import export section to Streamlit UI"
```

---

## Task 9: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 9.1: Add programa_export module to the Architecture section**

In `CLAUDE.md`, in the `src/` module list, add:

```
    programa_export.py ← Programa import CSV/XLSX export (primary output path)
```

And add a new Key Pattern entry:

```
**Primary output is CSV/XLSX export (`src/programa_export.py`):** The preferred Programa import path generates a clean 21-column file for Programa's "Import Products" feature. Browser automation (`programa_automation.py`) is preserved as a labeled legacy path.
```

- [ ] **Step 9.2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document programa_export as primary output path in CLAUDE.md"
```

---

## Spec Coverage Check

| Spec requirement | Task |
|---|---|
| `build_programa_import_dataframe` public function | Task 7 |
| `export_programa_csv` public function | Task 7 |
| `export_programa_xlsx` public function | Task 7 |
| 21 Programa columns in fixed order | Tasks 3–7 |
| `Product Category` → `Section`, fallback `"General"` | Tasks 4–6 |
| Material priority: explicit → Notes tag → blank | Tasks 4–5 |
| Notes cleanup: strip system tags + row prefix | Tasks 2–3 |
| Dimension parsing to structured W/H/D/L columns | Tasks 4–5 |
| `Finish / Color` → `Finish`; `Color` blank | Tasks 4–5 |
| `Lead Time` explicit field only | Tasks 4–5 |
| Skip rows missing Product Name | Tasks 6–7 |
| Validation warnings for Section/Dims/URL/Image | Task 6 |
| Streamlit "Export for Programa Import" section | Task 8 |
| Expandable list for missing-Section rows | Task 8 |
| CSV + XLSX download buttons with dated filenames | Task 8 |
| Debug CSV with confidence + source columns | Tasks 7–8 |
| Legacy label on Programa Automation section | Task 8 |
| openpyxl dependency | Task 1 |
| CLAUDE.md updated | Task 9 |
| Acceptance test (Scotsman SCN60PA1SU) | Task 5 |
