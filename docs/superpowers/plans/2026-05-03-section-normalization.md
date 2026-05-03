# Section Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/section_normalizer.py` — a canonical section name enforcer — and wire it into `product_enrichment.py` (intake time) and `programa_export.py` (export safety pass), plus a Streamlit UI showing section distribution with bulk edit.

**Architecture:** A new standalone module owns `CANONICAL_SECTIONS`, `ALIAS_MAP`, `normalize_section()`, and `validate_sections()`. `product_enrichment.py` calls `normalize_section()` when writing AI-extracted `Product Category` values. `programa_export.py` calls it unconditionally as a safety pass. `app.py` calls `validate_sections()` in the "Export for Programa Import" section for validation UI and bulk edit.

**Tech Stack:** Python, Streamlit, pytest.

---

## File Map

| Action | File | Purpose |
|--------|------|---------|
| Create | `src/section_normalizer.py` | Canonical section list, alias map, normalize/validate functions |
| Create | `tests/test_section_normalizer.py` | Full test suite for the new module |
| Modify | `src/product_enrichment.py` | Normalize AI-extracted `Product Category` at intake time |
| Modify | `src/programa_export.py` | Safety pass: normalize Section in `_row_to_programa_dict` |
| Modify | `app.py` | Section Distribution UI + Bulk Section Edit in "Export for Programa Import" |

---

### Task 1: Module scaffold — CANONICAL_SECTIONS, ALIAS_MAP, normalize_section

**Files:**
- Create: `tests/test_section_normalizer.py`
- Create: `src/section_normalizer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_section_normalizer.py
from src.intake_schema import CATEGORIES
from src.section_normalizer import (
    CANONICAL_SECTIONS,
    normalize_section,
)


def test_canonical_sections_matches_intake_schema_categories():
    """CANONICAL_SECTIONS must stay in sync with intake_schema.CATEGORIES."""
    assert sorted(CANONICAL_SECTIONS) == sorted(CATEGORIES), (
        "CANONICAL_SECTIONS and intake_schema.CATEGORIES have drifted. "
        "Update one to match the other."
    )


def test_normalize_section_exact_match_canonical_case():
    assert normalize_section("Appliances") == "Appliances"
    assert normalize_section("Lighting") == "Lighting"


def test_normalize_section_exact_match_case_insensitive():
    assert normalize_section("appliances") == "Appliances"
    assert normalize_section("LIGHTING") == "Lighting"
    assert normalize_section("pLuMbInG") == "Plumbing"


def test_normalize_section_alias_appliance():
    assert normalize_section("appliance") == "Appliances"
    assert normalize_section("kitchen appliance") == "Appliances"
    assert normalize_section("Kitchen Appliances") == "Appliances"
    assert normalize_section("built-in appliance") == "Appliances"


def test_normalize_section_alias_lighting():
    assert normalize_section("light") == "Lighting"
    assert normalize_section("lighting fixture") == "Lighting"
    assert normalize_section("pendant") == "Lighting"
    assert normalize_section("lamp") == "Lighting"


def test_normalize_section_alias_plumbing():
    assert normalize_section("plumbing fixture") == "Plumbing"
    assert normalize_section("bath fixture") == "Plumbing"
    assert normalize_section("faucet") == "Plumbing"
    assert normalize_section("toilet") == "Plumbing"


def test_normalize_section_title_case_fallback():
    # "art" is not directly in CANONICAL_SECTIONS but title-cases to "Art"
    assert normalize_section("art") == "Art"
    assert normalize_section("furniture") == "Furniture"
    assert normalize_section("flooring") == "Flooring"


def test_normalize_section_unknown_returns_general():
    assert normalize_section("random string xyz") == "General"
    assert normalize_section("foobar") == "General"


def test_normalize_section_blank_returns_general():
    assert normalize_section("") == "General"
    assert normalize_section("   ") == "General"


def test_normalize_section_none_returns_general():
    assert normalize_section(None) == "General"


def test_normalize_section_alias_general_fallbacks():
    assert normalize_section("misc") == "General"
    assert normalize_section("other") == "General"
    assert normalize_section("n/a") == "General"
    assert normalize_section("unknown") == "General"
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_section_normalizer.py -v
```
Expected: ImportError — `src.section_normalizer` does not exist.

- [ ] **Step 3: Create `src/section_normalizer.py`**

```python
"""
Section name normalization for SCH DesignOps Intake.

Maps raw Product Category strings to canonical Programa section names.
Called at intake time (product_enrichment.py) and at export time (programa_export.py).

Public API
----------
CANONICAL_SECTIONS : list[str]
    23 canonical section names. Must match intake_schema.CATEGORIES.

ALIAS_MAP : dict[str, str]
    All-lowercase key → canonical value. Single place to add mappings.

normalize_section(value) -> str
    Map any string to a canonical section name. Unknown → "General".

validate_sections(rows) -> dict
    Count and validate sections across a batch of intake rows.
"""

from __future__ import annotations

from collections import Counter


# ── Canonical section list ─────────────────────────────────────────────────────
# Must stay in sync with intake_schema.CATEGORIES.
# Enforced by test_canonical_sections_matches_intake_schema_categories.

CANONICAL_SECTIONS: list[str] = [
    "Appliances",
    "Lighting",
    "Plumbing",
    "Cabinetry",
    "Flooring",
    "Furniture",
    "Decor",
    "Hardware",
    "Exterior",
    "General",
    "Paint/Wallpaper",
    "Stone/Tile",
    "Seating",
    "Tables",
    "Gym Equipment",
    "Fabrics/Pillows",
    "Rugs",
    "Mirrors",
    "Beds/Mattresses",
    "Dressers/Drawers/Storage",
    "Accessories",
    "Art",
    "Artwork",
    "Bedding/Linens/Bath Linens",
]

_CANONICAL_LOWER: dict[str, str] = {s.lower(): s for s in CANONICAL_SECTIONS}

# ── Alias map ─────────────────────────────────────────────────────────────────
# All keys lowercase. Add new synonyms here.

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


# ── Public functions ───────────────────────────────────────────────────────────

def normalize_section(value) -> str:
    """
    Map a raw Product Category / Section string to a canonical section name.
    Returns "General" for blank, None, or unrecognized input.
    """
    if value is None:
        return "General"
    text = str(value).strip()
    if not text:
        return "General"

    lower = text.lower()

    # 1. Exact case-insensitive match against canonical list
    if lower in _CANONICAL_LOWER:
        return _CANONICAL_LOWER[lower]

    # 2. Alias lookup
    if lower in ALIAS_MAP:
        return ALIAS_MAP[lower]

    # 3. Title-case fallback
    title = text.title()
    if title in _CANONICAL_LOWER.values():
        return title

    # 4. Unknown → General
    return "General"


SECTION_COUNT_WARNING_THRESHOLD: int = 10
SECTION_NAME_MAX_LENGTH: int = 40


def validate_sections(rows: list[dict]) -> dict:
    """
    Validate section assignments across a batch of intake rows.

    Returns:
        section_counts: dict[str, int] sorted by count descending
        rows_by_section: dict[str, list[str]] product names per section
        warnings: list[str] human-readable warning messages
    """
    product_names = [str(r.get("Product Name") or "").strip() for r in rows]
    product_name_set = {n.lower() for n in product_names if n}

    sections = [normalize_section(r.get("Product Category")) for r in rows]
    counter = Counter(sections)

    # Sort by count descending
    section_counts = dict(sorted(counter.items(), key=lambda x: x[1], reverse=True))

    # rows_by_section: map section → list of product names
    rows_by_section: dict[str, list[str]] = {s: [] for s in section_counts}
    for r, section in zip(rows, sections):
        name = str(r.get("Product Name") or "").strip()
        rows_by_section[section].append(name or "(no name)")

    # Warnings
    warnings: list[str] = []

    unique_count = len(section_counts)
    if unique_count > SECTION_COUNT_WARNING_THRESHOLD:
        warnings.append(
            f"Section count ({unique_count}) exceeds {SECTION_COUNT_WARNING_THRESHOLD}"
            " — check for one-off categories"
        )

    for section in section_counts:
        if len(section) > SECTION_NAME_MAX_LENGTH:
            warnings.append(f"Section name too long: '{section}'")
        if section.lower() in product_name_set:
            warnings.append(f"Section name matches a product name: '{section}'")

    return {
        "section_counts": section_counts,
        "rows_by_section": rows_by_section,
        "warnings": warnings,
    }
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_section_normalizer.py -v
```
Expected: all 11 tests pass.

- [ ] **Step 5: Commit**

```
git add src/section_normalizer.py tests/test_section_normalizer.py
git commit -m "feat: add section_normalizer with canonical sections, alias map, normalize/validate"
```

---

### Task 2: validate_sections tests

**Files:**
- Modify: `tests/test_section_normalizer.py`

- [ ] **Step 1: Write failing tests for `validate_sections`**

Append to `tests/test_section_normalizer.py`:

```python
from src.section_normalizer import validate_sections


def test_validate_sections_counts_are_correct():
    rows = [
        {"Product Category": "Appliances", "Product Name": "Fridge"},
        {"Product Category": "Appliances", "Product Name": "Icemaker"},
        {"Product Category": "Lighting", "Product Name": "Pendant"},
        {"Product Category": "kitchen appliance", "Product Name": "Dishwasher"},
    ]
    result = validate_sections(rows)
    assert result["section_counts"]["Appliances"] == 3
    assert result["section_counts"]["Lighting"] == 1


def test_validate_sections_counts_sorted_descending():
    rows = [
        {"Product Category": "Lighting", "Product Name": "A"},
        {"Product Category": "Appliances", "Product Name": "B"},
        {"Product Category": "Appliances", "Product Name": "C"},
        {"Product Category": "Appliances", "Product Name": "D"},
    ]
    result = validate_sections(rows)
    counts = list(result["section_counts"].values())
    assert counts == sorted(counts, reverse=True)


def test_validate_sections_warns_section_explosion():
    rows = [
        {"Product Category": cat, "Product Name": f"Item {i}"}
        for i, cat in enumerate([
            "Appliances", "Lighting", "Plumbing", "Cabinetry",
            "Flooring", "Furniture", "Decor", "Hardware",
            "Exterior", "General", "Seating", "Tables",
        ])
    ]
    result = validate_sections(rows)
    assert any("exceeds 10" in w for w in result["warnings"])


def test_validate_sections_no_warning_under_threshold():
    rows = [
        {"Product Category": "Appliances", "Product Name": "A"},
        {"Product Category": "Lighting", "Product Name": "B"},
    ]
    result = validate_sections(rows)
    assert not any("exceeds" in w for w in result["warnings"])


def test_validate_sections_warns_section_name_too_long():
    long_name = "A" * 50
    rows = [{"Product Category": long_name, "Product Name": "Thing"}]
    result = validate_sections(rows)
    assert any("too long" in w for w in result["warnings"])


def test_validate_sections_warns_section_matches_product_name():
    rows = [
        {"Product Category": "Scotsman Icemaker", "Product Name": "Scotsman Icemaker"},
    ]
    result = validate_sections(rows)
    assert any("matches a product name" in w for w in result["warnings"])


def test_validate_sections_rows_by_section_general():
    rows = [
        {"Product Category": "", "Product Name": "Mystery Item"},
        {"Product Category": "unknown", "Product Name": "Unnamed"},
        {"Product Category": "Appliances", "Product Name": "Fridge"},
    ]
    result = validate_sections(rows)
    general_names = result["rows_by_section"]["General"]
    assert "Mystery Item" in general_names
    assert "Unnamed" in general_names
    assert "Fridge" not in general_names
```

- [ ] **Step 2: Run to verify they pass** (validate_sections is already implemented in Task 1)

```
pytest tests/test_section_normalizer.py -v
```
Expected: all passing.

- [ ] **Step 3: Commit**

```
git add tests/test_section_normalizer.py
git commit -m "test: add validate_sections tests for section normalization"
```

---

### Task 3: Export safety pass — `programa_export.py`

**Files:**
- Modify: `src/programa_export.py:35-36` (import) and `src/programa_export.py:155-157` (`_row_to_programa_dict`)
- Modify: `tests/test_section_normalizer.py` — append export integration tests

- [ ] **Step 1: Write failing tests**

Append to `tests/test_section_normalizer.py`:

```python
from src.programa_export import build_programa_import_dataframe


def test_programa_export_normalizes_alias_section():
    rows = [
        {
            "Include": True,
            "Product Name": "Dishwasher",
            "Product Category": "kitchen appliance",
            "Product URL": "https://example.com",
            "Image URL": "https://example.com/img.jpg",
        }
    ]
    df = build_programa_import_dataframe(rows)
    assert df.iloc[0]["Section"] == "Appliances"


def test_programa_export_blank_category_becomes_general():
    rows = [
        {
            "Include": True,
            "Product Name": "Mystery Item",
            "Product Category": "",
            "Product URL": "https://example.com",
            "Image URL": "https://example.com/img.jpg",
        }
    ]
    df = build_programa_import_dataframe(rows)
    assert df.iloc[0]["Section"] == "General"


def test_programa_export_canonical_category_unchanged():
    rows = [
        {
            "Include": True,
            "Product Name": "Pendant Light",
            "Product Category": "Lighting",
            "Product URL": "https://example.com",
            "Image URL": "https://example.com/img.jpg",
        }
    ]
    df = build_programa_import_dataframe(rows)
    assert df.iloc[0]["Section"] == "Lighting"
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_section_normalizer.py::test_programa_export_normalizes_alias_section -v
```
Expected: FAIL — "kitchen appliance" is not normalized (current code: `_str_val(row.get("Product Category")) or "General"`).

- [ ] **Step 3: Add import to `src/programa_export.py`**

At the top of `src/programa_export.py`, add:

```python
from src.section_normalizer import normalize_section
```

- [ ] **Step 4: Replace Section assignment in `_row_to_programa_dict`**

Find in `src/programa_export.py`:

```python
        "Section": _str_val(row.get("Product Category")) or "General",
```

Replace with:

```python
        "Section": normalize_section(_str_val(row.get("Product Category"))),
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_section_normalizer.py tests/test_programa_export.py -v
```
Expected: all passing.

- [ ] **Step 6: Commit**

```
git add src/programa_export.py tests/test_section_normalizer.py
git commit -m "feat: normalize Section via normalize_section() in programa_export safety pass"
```

---

### Task 4: Intake-time normalization — `product_enrichment.py`

**Files:**
- Modify: `src/product_enrichment.py:146-149` (the `Product Category` branch in `_apply_enrichment`)
- Modify: `tests/test_section_normalizer.py` — append enrichment integration test

- [ ] **Step 1: Write failing test**

Append to `tests/test_section_normalizer.py`:

```python
from unittest.mock import patch, MagicMock
from src.product_enrichment import enrich_row


def test_enrich_row_normalizes_ai_extracted_category(monkeypatch):
    """AI extraction returns "kitchen appliance" → stored as "Appliances"."""
    row = {
        "Brand": "Miele",
        "Model/SKU": "G7156SCVi",
        "Product Name": "",
        "Product Category": "",
        "Dimensions": "",
        "Source Type": "Manual",
        "Notes": "",
    }
    mock_search_result = MagicMock()
    mock_search_result.domain_score = 80
    mock_search_result.url = "https://mieleusa.com/g7156scvi"

    with patch("src.product_enrichment.search_product_candidates", return_value=[mock_search_result]):
        with patch("src.product_enrichment._fetch_page_text", return_value="dishwasher specs"):
            with patch(
                "src.product_enrichment._extract_with_claude",
                return_value={"Product Category": "kitchen appliance", "Product Name": "Miele Dishwasher"},
            ):
                with patch("src.dimension_enrichment.find_dimensions", return_value=__import__("src.dimension_enrichment", fromlist=["DimensionResult"]).DimensionResult()):
                    updated, error = enrich_row(row)

    assert updated["Product Category"] == "Appliances"
    assert error is None
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_section_normalizer.py::test_enrich_row_normalizes_ai_extracted_category -v
```
Expected: FAIL — raw "kitchen appliance" stored as-is (current `_normalise_category` from `category_ai.py` may or may not normalize this).

- [ ] **Step 3: Add import to `src/product_enrichment.py`**

Near the top of `src/product_enrichment.py`, add:

```python
from src.section_normalizer import normalize_section as _normalize_section
```

- [ ] **Step 4: Update `_apply_enrichment` in `src/product_enrichment.py`**

Find the `Product Category` branch (currently uses `_normalise_category`):

```python
        if field == "Product Category":
            value = _normalise_category(value)
```

Replace with:

```python
        if field == "Product Category":
            value = _normalize_section(_normalise_category(value))
```

This chains: Claude's raw value → `_normalise_category` (existing AI category normalization) → `_normalize_section` (canonical section enforcement). Both steps run so neither is lost.

- [ ] **Step 5: Run tests**

```
pytest tests/test_section_normalizer.py -v
```
Expected: all passing.

- [ ] **Step 6: Run full test suite**

```
pytest tests/ -v --ignore=tests/test_programa_automation.py
```
Expected: all passing.

- [ ] **Step 7: Commit**

```
git add src/product_enrichment.py tests/test_section_normalizer.py
git commit -m "feat: normalize AI-extracted Product Category to canonical section at intake time"
```

---

### Task 5: Streamlit UI — Section Distribution + Bulk Edit

**Files:**
- Modify: `app.py` — inside the "Export for Programa Import" section (around line 1502–1565)

- [ ] **Step 1: Locate the insertion point in `app.py`**

The "Export for Programa Import" section currently looks like:

```python
    _export_summary = validate_for_export(included)
    _programa_df = build_programa_import_dataframe(included)
    _today = datetime.date.today().isoformat()

    _export_count = _export_summary["export_count"]
    ...

    if _export_count > 0:
        st.success(...)
    ...
    if _missing_img > 0:
        st.warning(...)
    if _skipped:
        with st.expander(...):
            ...

    _dl_col1, _dl_col2, _dl_spacer = st.columns([1, 1, 4])
    ...
    _include_debug = st.checkbox(...)
```

Insert the Section Distribution block and Bulk Edit block **between** the last warning/skipped expander and the `_dl_col1, _dl_col2` download buttons.

- [ ] **Step 2: Add import at the top of `app.py`**

In the imports block at the top of `app.py`, add:

```python
from src.section_normalizer import CANONICAL_SECTIONS, validate_sections
```

- [ ] **Step 3: Insert Section Distribution + Bulk Edit into `app.py`**

Find this line in the "Export for Programa Import" section:

```python
    _dl_col1, _dl_col2, _dl_spacer = st.columns([1, 1, 4])
```

Insert before it:

```python
    # ── Section Distribution ───────────────────────────────────────────────────
    st.markdown("**Section Distribution**")
    _section_validation = validate_sections(included.to_dict("records") if hasattr(included, "to_dict") else list(included))
    _section_counts = _section_validation["section_counts"]
    _section_warnings = _section_validation["warnings"]
    _rows_by_section = _section_validation["rows_by_section"]

    for _sec, _cnt in _section_counts.items():
        if _sec == "General" and _rows_by_section.get("General"):
            with st.expander(f"  General   {_cnt}"):
                for _pname in _rows_by_section["General"]:
                    st.markdown(f"- {_pname}")
        else:
            st.markdown(f"  {_sec}   {_cnt}")

    for _warn in _section_warnings:
        st.warning(_warn, icon=None)

    # ── Bulk Section Edit ──────────────────────────────────────────────────────
    _bulk_col1, _bulk_col2, _bulk_spacer = st.columns([2, 1, 3])
    with _bulk_col1:
        _bulk_section = st.selectbox(
            "Set section for all included rows",
            options=CANONICAL_SECTIONS,
            key="bulk_section_select",
            label_visibility="visible",
        )
    with _bulk_col2:
        st.markdown("<div style='margin-top:1.6rem'></div>", unsafe_allow_html=True)
        if st.button("Apply to All Included", key="bulk_section_apply"):
            _rows_updated = 0
            for _idx in edited_df.index:
                if edited_df.at[_idx, "Include"] if "Include" in edited_df.columns else True:
                    edited_df.at[_idx, "Product Category"] = _bulk_section
                    _rows_updated += 1
            st.session_state["edited_df"] = edited_df
            st.success(f'✓ Section updated to "{_bulk_section}" for {_rows_updated} row{"s" if _rows_updated != 1 else ""}')
            st.rerun()

    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

```

- [ ] **Step 4: Verify `edited_df` is accessible at the insertion point**

The `edited_df` variable is defined earlier in the same function scope and used throughout the page. It is accessible at this insertion point. The `st.session_state["edited_df"]` write + `st.rerun()` pattern re-renders the page with the updated DataFrame, which re-runs `validate_sections()` automatically — no extra trigger needed.

- [ ] **Step 5: Run all tests to confirm nothing broke**

```
pytest tests/ -v --ignore=tests/test_programa_automation.py
```
Expected: all passing.

- [ ] **Step 6: Commit**

```
git add app.py
git commit -m "feat: add Section Distribution and Bulk Section Edit to Export for Programa Import UI"
```

---

## Acceptance Criteria Checklist

Run the 24-product CSV through the full flow:

```
pytest tests/ -v --ignore=tests/test_programa_automation.py
```

Manual verification:
1. Import 24-product CSV with mixed category strings (some AI-labeled "kitchen appliance", "lighting fixture", etc.)
2. Run enrichment — check `Product Category` values are canonical after enrichment
3. Open "Export for Programa Import" section:
   - Section Distribution shows sorted counts
   - "General" section is expandable with product names
   - Section count > 10 shows warning
4. Use Bulk Edit to set all rows to "Appliances" — confirm success message + re-render
5. Download CSV — confirm `Section` column contains only canonical values, no raw AI labels
6. All 13 `test_section_normalizer.py` tests pass
