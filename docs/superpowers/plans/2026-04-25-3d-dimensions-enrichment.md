# 3D Dimensions Enrichment Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich rows that are missing full W×H×D dimensions by searching official spec sheets; handle partial dimensions gracefully; surface "Verify full W×H×D" in Suggested Action wherever dimensions are incomplete.

**Architecture:** A new `has_complete_3d_dimensions` helper lives in `product_enrichment.py` and is the single source of truth for whether a dimension string is complete. `_qualifies` uses it to include rows with partial dimensions. `_build_search_query` adds dimension-specific terms when dimensions are needed. `_build_extraction_prompt` explicitly asks Claude for W, H, D separately plus a formatted combined string. `_apply_enrichment` is updated to overwrite partial dims with complete 3D dims, and to append a partial-dim note when only partial is found. `confidence.py`'s `_suggested_action` is updated to emit "Verify full W×H×D dimensions from official spec sheet" whenever Dimensions is blank or incomplete.

> **Note:** `confidence.py` Task 4 of this plan supersedes Task 3 from `2026-04-25-location-dimension-extraction.md`. If that Task 3 was already executed, apply this plan's Task 4 as a replacement edit, not an append.

**Tech Stack:** Python stdlib `re`, existing `product_enrichment.py`, existing `confidence.py`.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `src/product_enrichment.py` | `has_complete_3d_dimensions`, `_qualifies`, `_build_search_query`, `_build_extraction_prompt`, `_apply_enrichment` |
| Modify | `tests/test_product_enrichment.py` | Tests for all of the above |
| Modify | `src/confidence.py` | Update `_suggested_action` tail to check incomplete dims |
| Modify | `tests/test_confidence.py` | Tests for updated `_suggested_action` (create file if absent) |

---

## Task 1: `has_complete_3d_dimensions` helper

**Files:**
- Modify: `src/product_enrichment.py` (add function after `_str_val`)
- Modify: `tests/test_product_enrichment.py` (append tests)

- [ ] **Step 1: Append the failing tests to `tests/test_product_enrichment.py`**

```python
# ── has_complete_3d_dimensions ─────────────────────────────────────────────────

from src.product_enrichment import has_complete_3d_dimensions


def test_3d_complete_standard_format():
    assert has_complete_3d_dimensions('36"W x 34.5"H x 24"D') is True


def test_3d_complete_unicode_times():
    assert has_complete_3d_dimensions('36"W × 34.5"H × 24"D') is True


def test_3d_complete_space_before_letter():
    assert has_complete_3d_dimensions('29 7/8" W × 23 1/2" D × 11 7/8" H') is True


def test_3d_complete_full_words():
    assert has_complete_3d_dimensions('Width: 30", Height: 84", Depth: 24"') is True


def test_3d_incomplete_one_dim():
    assert has_complete_3d_dimensions('36 inch') is False


def test_3d_incomplete_one_label():
    assert has_complete_3d_dimensions('30"W') is False


def test_3d_incomplete_two_dims():
    assert has_complete_3d_dimensions('36"W x 34.5"H') is False


def test_3d_incomplete_missing_height():
    assert has_complete_3d_dimensions('36"W x 24"D') is False


def test_3d_empty_string():
    assert has_complete_3d_dimensions('') is False


def test_3d_none_safe():
    # The function must handle any falsy input without raising
    assert has_complete_3d_dimensions(None) is False  # type: ignore[arg-type]
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py -k "3d" -v 2>&1 | head -15
```

Expected: `ImportError` — `has_complete_3d_dimensions` not yet defined.

- [ ] **Step 3: Add `has_complete_3d_dimensions` to `src/product_enrichment.py`**

Insert immediately after the `_str_val` function (after line 57):

```python
# Compiled patterns for detecting W, H, D dimension labels.
# Matches both abbreviated (W, H, D) and full-word (width, height, depth) forms,
# preceded by a digit, quote, or space — preventing false matches on word fragments.
_DIM_W = re.compile(r'[\d"\'\s]W\b|\bwidth\b', re.IGNORECASE)
_DIM_H = re.compile(r'[\d"\'\s]H\b|\bheight\b', re.IGNORECASE)
_DIM_D = re.compile(r'[\d"\'\s]D\b|\bdepth\b', re.IGNORECASE)


def has_complete_3d_dimensions(dimensions) -> bool:
    """Return True only if dimensions contains explicit W, H, and D measurements."""
    s = str(dimensions or "").strip()
    if not s:
        return False
    return (
        bool(_DIM_W.search(s))
        and bool(_DIM_H.search(s))
        and bool(_DIM_D.search(s))
    )
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py -k "3d" -v
```

Expected: 10 tests pass.

- [ ] **Step 5: Run full suite**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/product_enrichment.py tests/test_product_enrichment.py && git commit -m "feat: has_complete_3d_dimensions — detect W×H×D completeness"
```

---

## Task 2: Update `_qualifies` and `_build_search_query`

**Files:**
- Modify: `src/product_enrichment.py` (`_qualifies`, `_build_search_query`)
- Modify: `tests/test_product_enrichment.py` (append tests)

`_qualifies` must now qualify rows where Dimensions is **present but not complete 3D** (not just blank). `_build_search_query` must add dimension-specific search terms when dimensions are missing or incomplete, so Brave Search surfaces spec sheets.

- [ ] **Step 1: Append the failing tests to `tests/test_product_enrichment.py`**

```python
# ── _qualifies with incomplete dimensions ──────────────────────────────────────

def test_qualifies_partial_dimension_qualifies():
    """Row with a partial dimension string (not full 3D) should qualify."""
    row = {
        **_base_qualifying_row(),
        "Product Name": "Fridge Drawers",
        "Dimensions": "36 inch",           # partial — not 3D
        "Finish / Color": "Stainless",
        "Product Category": "Appliance",
        "Product URL": "https://example.com",
    }
    assert _qualifies(row)


def test_qualifies_complete_3d_dimension_does_not_qualify():
    """Row with full W×H×D dimensions should NOT qualify (nothing to enrich)."""
    row = {
        **_base_qualifying_row(),
        "Product Name": "Fridge",
        "Dimensions": '36"W x 84"H x 24"D',
        "Finish / Color": "Stainless",
        "Product Category": "Appliance",
        "Product URL": "https://example.com",
    }
    assert not _qualifies(row)


# ── _build_search_query with dimension intent ──────────────────────────────────

def test_build_query_blank_dimensions_adds_dim_terms():
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "", "Dimensions": ""}
    query = _build_search_query(row)
    assert "dimensions" in query.lower()
    assert "width" in query.lower()
    assert "depth" in query.lower()


def test_build_query_partial_dimensions_adds_dim_terms():
    row = {"Brand": "Sub-Zero", "Model/SKU": "ID36R", "Product Name": "", "Dimensions": "36 inch"}
    query = _build_search_query(row)
    assert "dimensions" in query.lower()


def test_build_query_complete_3d_dimensions_uses_general_terms():
    """When dimensions are already complete 3D, use the general suffix, not dim-specific."""
    row = {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "Microwave",
        "Dimensions": '30"W x 15"H x 17"D',
    }
    query = _build_search_query(row)
    assert "specifications" in query.lower() or "official" in query.lower()
    assert "width height depth" not in query.lower()
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py -k "partial_dimension or dim_terms or 3d_dimension_does_not" -v 2>&1 | head -20
```

Expected: failures — `_qualifies` and `_build_search_query` not yet updated.

- [ ] **Step 3: Replace `_qualifies` in `src/product_enrichment.py`**

Find and replace the current `_qualifies` function (lines 60–75):

```python
def _qualifies(row: dict) -> bool:
    """True if this row should be sent through enrichment."""
    source = _str_val(row.get("Source Type", ""))
    if source == "URL":
        return False
    if source.endswith("_Enriched"):
        return False
    if not _str_val(row.get("Brand")):
        return False
    if not _str_val(row.get("Model/SKU")):
        return False
    # Qualify if any enrichable field is blank, OR if Dimensions exists but is
    # not full W×H×D (a partial dimension still needs enrichment).
    blank_or_incomplete = [
        f for f in _ENRICHABLE_FIELDS
        if not _str_val(row.get(f))
        or (f == "Dimensions" and not has_complete_3d_dimensions(_str_val(row.get(f))))
    ]
    return bool(blank_or_incomplete)
```

- [ ] **Step 4: Replace `_build_search_query` in `src/product_enrichment.py`**

Find and replace the current `_build_search_query` function (lines 78–86):

```python
def _build_search_query(row: dict) -> str:
    """Build a Brave Search query; prioritise spec sheets when dimensions are needed."""
    dims = _str_val(row.get("Dimensions"))
    needs_dims = not dims or not has_complete_3d_dimensions(dims)

    parts = [
        _str_val(row.get("Brand")),
        _str_val(row.get("Model/SKU")),
        _str_val(row.get("Product Name")),
    ]
    suffix = (
        "dimensions width height depth spec sheet official"
        if needs_dims
        else "specifications official"
    )
    return " ".join(p for p in parts if p) + " " + suffix
```

- [ ] **Step 5: Run the new tests — expect PASS**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py -k "partial_dimension or dim_terms or 3d_dimension_does_not" -v
```

Expected: all pass.

- [ ] **Step 6: Run full suite**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/product_enrichment.py tests/test_product_enrichment.py && git commit -m "feat: qualify rows with incomplete dims; dimension-focused search query"
```

---

## Task 3: Update `_build_extraction_prompt` and `_apply_enrichment`

**Files:**
- Modify: `src/product_enrichment.py` (`_build_extraction_prompt`, `_apply_enrichment`)
- Modify: `tests/test_product_enrichment.py` (append tests)

`_build_extraction_prompt` must explicitly ask Claude to return W, H, D separately when dimensions are needed, and only return the combined string when all three are found. `_apply_enrichment` must handle four Dimensions outcomes: complete 3D extracted (overwrite, even if partial exists), partial extracted (note, don't fill), nothing extracted + currently blank (leave), nothing extracted + has partial (leave as-is).

- [ ] **Step 1: Append the failing tests to `tests/test_product_enrichment.py`**

```python
# ── _apply_enrichment with 3D dimensions ──────────────────────────────────────

def test_apply_enrichment_fills_complete_3d_dims():
    """Complete 3D extracted → fill Dimensions."""
    row = _base_row_for_apply()
    extracted = {
        "Product Name": "",
        "Dimensions": '36"W x 84"H x 24"D',
        "Finish / Color": "",
        "Product Category": "",
        "materials": "",
    }
    updated = _apply_enrichment(row, extracted, "https://example.com", 85)
    assert updated["Dimensions"] == '36"W x 84"H x 24"D'


def test_apply_enrichment_overwrites_partial_with_complete_3d():
    """Complete 3D extracted → overwrite even if row already had partial dims."""
    row = {**_base_row_for_apply(), "Dimensions": "36 inch"}
    extracted = {
        "Product Name": "",
        "Dimensions": '36"W x 84"H x 24"D',
        "Finish / Color": "",
        "Product Category": "",
        "materials": "",
    }
    updated = _apply_enrichment(row, extracted, "https://example.com", 85)
    assert updated["Dimensions"] == '36"W x 84"H x 24"D'


def test_apply_enrichment_partial_extracted_adds_note():
    """Partial extracted → no fill, note appended to Notes."""
    row = _base_row_for_apply()
    extracted = {
        "Product Name": "",
        "Dimensions": "36 inch",   # partial
        "Finish / Color": "",
        "Product Category": "",
        "materials": "",
    }
    updated = _apply_enrichment(row, extracted, "https://example.com", 85)
    assert updated["Dimensions"] == ""   # not filled
    assert "[Partial dimension found:" in updated["Notes"]
    assert "36 inch" in updated["Notes"]
    assert "full W x H x D still needed" in updated["Notes"]


def test_apply_enrichment_partial_note_not_duplicated():
    """Partial dim note is not appended twice if already present."""
    row = _base_row_for_apply()
    row["Notes"] = "[Partial dimension found: 36 inch; full W x H x D still needed]"
    extracted = {"Product Name": "", "Dimensions": "36 inch", "Finish / Color": "", "Product Category": "", "materials": ""}
    updated = _apply_enrichment(row, extracted, "https://example.com", 85)
    assert updated["Notes"].count("[Partial dimension found:") == 1


def test_apply_enrichment_no_dim_extracted_blank_row_unchanged():
    """No dimensions extracted and row has blank dims → Dimensions stays blank."""
    row = _base_row_for_apply()
    extracted = {"Product Name": "", "Dimensions": "", "Finish / Color": "", "Product Category": "", "materials": ""}
    updated = _apply_enrichment(row, extracted, "https://example.com", 85)
    assert updated["Dimensions"] == ""


# ── _build_extraction_prompt with dimensions ───────────────────────────────────

from src.product_enrichment import _build_extraction_prompt


def test_extraction_prompt_requests_3d_when_dims_blank():
    """When Dimensions is blank, prompt must ask for W, H, D explicitly."""
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "Microwave",
           "Dimensions": "", "Finish / Color": "", "Product Category": ""}
    prompt = _build_extraction_prompt("page text", row)
    assert "width" in prompt.lower()
    assert "height" in prompt.lower()
    assert "depth" in prompt.lower()


def test_extraction_prompt_requests_3d_when_dims_partial():
    """When Dimensions is partial, prompt must still ask for full 3D."""
    row = {"Brand": "Sub-Zero", "Model/SKU": "ID36R", "Product Name": "Fridge",
           "Dimensions": "36 inch", "Finish / Color": "", "Product Category": ""}
    prompt = _build_extraction_prompt("page text", row)
    assert "width" in prompt.lower()
    assert "depth" in prompt.lower()


def test_extraction_prompt_no_dim_request_when_3d_complete():
    """When 3D dimensions are already complete, prompt should NOT ask for dimensions."""
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "",
           "Dimensions": '36"W x 84"H x 24"D', "Finish / Color": "", "Product Category": ""}
    prompt = _build_extraction_prompt("page text", row)
    # "Dimensions" must not be in the blank-fields list in the prompt
    assert '"Dimensions"' not in prompt or "already complete" in prompt.lower()
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py -k "3d_dims or partial_extracted or no_dim or extraction_prompt" -v 2>&1 | head -20
```

Expected: assertion failures.

- [ ] **Step 3: Replace `_build_extraction_prompt` in `src/product_enrichment.py`**

Find and replace the current `_build_extraction_prompt` function (lines 169–197):

```python
def _build_extraction_prompt(page_text: str, row: dict) -> str:
    """Build the Claude Haiku prompt listing which fields are blank and need filling."""
    dims = _str_val(row.get("Dimensions"))
    needs_dims = not dims or not has_complete_3d_dimensions(dims)

    # Non-dimension fields that are blank
    blank = [
        f for f in ["Product Name", "Finish / Color", "Product Category"]
        if not _str_val(row.get(f))
    ]
    if needs_dims:
        blank.append("Dimensions")

    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))

    dim_instruction = ""
    if needs_dims:
        dim_instruction = (
            "\n\nFor Dimensions: look for the exact product specification "
            "listing width, height, and depth. "
            'Format your answer as: 36"W x 34.5"H x 24"D '
            "(always include the W, H, and D labels). "
            "Return the combined string ONLY if all three of width, height, "
            "and depth are explicitly stated on the page. "
            'If any one of them is missing, return "".'
        )

    return (
        f"You are extracting product specification data for {brand} model {model}.\n\n"
        f"The following fields are currently blank or incomplete and need to be filled:\n"
        f"{', '.join(blank)}\n\n"
        "Also extract: materials (short description of primary construction materials, "
        "e.g. 'Solid Oak', 'Stainless Steel')"
        + dim_instruction + "\n\n"
        "Return ONLY a JSON object. No prose. No markdown fences. Example:\n"
        '{"Product Name": "Wolf 30\\" Drawer Microwave Oven", '
        '"Dimensions": "29 7/8\\" W x 23 1/2\\" D x 11 7/8\\" H", '
        '"Finish / Color": "Stainless Steel", '
        '"Product Category": "Appliance", '
        '"materials": "Stainless steel exterior"}\n\n'
        "Rules:\n"
        "- Only include the fields listed above as blank/incomplete, plus 'materials'.\n"
        "- If a field is not clearly stated in the page, return \"\" for that field.\n"
        "- Never invent values not present in the page.\n"
        "- For Dimensions: only return a value when width AND height AND depth "
        "are all explicitly stated. Never infer from product name alone.\n"
        "- Product Category must be one of: Chair, Sofa, Paint, Fabric, Table, "
        "Lighting, Plumbing, Hardware, Rug, Artwork, Mirror, Appliance, Accessories, Other.\n\n"
        f"PAGE TEXT:\n---\n{page_text}\n---"
    )
```

- [ ] **Step 4: Update the Dimensions branch inside `_apply_enrichment`**

In `_apply_enrichment`, the current loop body is:

```python
    for field in _ENRICHABLE_FIELDS:
        if field == "Product URL":
            if not _str_val(updated.get("Product URL")):
                updated["Product URL"] = source_url
            continue

        # Never overwrite non-empty fields
        if _str_val(updated.get(field)):
            continue

        value = _str_val(extracted.get(field))
        if not value:
            continue

        if field == "Product Category":
            value = _normalise_category(value)

        if value:
            updated[field] = value
```

Replace with:

```python
    for field in _ENRICHABLE_FIELDS:
        if field == "Product URL":
            if not _str_val(updated.get("Product URL")):
                updated["Product URL"] = source_url
            continue

        if field == "Dimensions":
            dim_extracted = _str_val(extracted.get("Dimensions"))
            if dim_extracted:
                if has_complete_3d_dimensions(dim_extracted):
                    # Always accept complete 3D, even if row already had partial dims
                    updated["Dimensions"] = dim_extracted
                else:
                    # Partial found — note it, but do not fill
                    existing_notes = _str_val(updated.get("Notes"))
                    partial_note = (
                        f"[Partial dimension found: {dim_extracted}; "
                        "full W x H x D still needed]"
                    )
                    if partial_note not in existing_notes:
                        updated["Notes"] = (
                            f"{existing_notes} {partial_note}".strip()
                            if existing_notes else partial_note
                        )
            continue

        # Never overwrite non-empty fields for all other enrichable fields
        if _str_val(updated.get(field)):
            continue

        value = _str_val(extracted.get(field))
        if not value:
            continue

        if field == "Product Category":
            value = _normalise_category(value)

        if value:
            updated[field] = value
```

- [ ] **Step 5: Run the new tests — expect PASS**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py -k "3d_dims or partial_extracted or no_dim or extraction_prompt" -v
```

Expected: all pass.

- [ ] **Step 6: Run full suite**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/product_enrichment.py tests/test_product_enrichment.py && git commit -m "feat: extraction prompt asks for full W×H×D; apply_enrichment handles partial dims"
```

---

## Task 4: Update `_suggested_action` in `confidence.py`

**Files:**
- Modify: `src/confidence.py` (import `has_complete_3d_dimensions`; update `_suggested_action`)
- Create/Modify: `tests/test_confidence.py`

> **If Task 3 from `2026-04-25-location-dimension-extraction.md` was already executed,** the tail of `_suggested_action` will already have been changed to use an `action` variable instead of early returns. In that case only replace the final `if … dimension …` block. If it was NOT executed, replace the entire tail as shown below.

This update checks both blank AND incomplete dimensions (via `has_complete_3d_dimensions`), and uses the final message "Verify full W×H×D dimensions from official spec sheet".

- [ ] **Step 1: Write or extend `tests/test_confidence.py`**

If `tests/test_confidence.py` does not exist, create it. If it exists, append the new tests.

```python
import pytest
from src.confidence import _suggested_action


def _pdf_ai_row(**overrides):
    base = {
        "Source Type": "PDF_AI",
        "Product Name": "Wolf Microwave",
        "Brand": "Wolf",
        "Dimensions": "",
        "Room": "Kitchen",
        "Quantity": 1,
        "Supplier": "AEG",
        "Product Category": "Appliance",
        "Model/SKU": "MDD30TS",
        "Product URL": "",
        "Price": "$1,200",
    }
    base.update(overrides)
    return base


def test_pdf_ai_blank_dims_gets_3d_verify_note():
    """PDF_AI row with blank Dimensions → 'Verify full W×H×D' note."""
    action = _suggested_action(_pdf_ai_row(), missing=[])
    assert "W" in action and "H" in action and "D" in action
    assert "spec sheet" in action.lower()


def test_pdf_ai_partial_dims_gets_3d_verify_note():
    """PDF_AI row with partial dims ('36 inch') → 'Verify full W×H×D' note."""
    action = _suggested_action(_pdf_ai_row(Dimensions="36 inch"), missing=[])
    assert "W" in action and "H" in action and "D" in action
    assert "spec sheet" in action.lower()


def test_pdf_ai_complete_3d_dims_no_verify_note():
    """PDF_AI row with full 3D dims → no dimension verification note."""
    action = _suggested_action(_pdf_ai_row(**{"Dimensions": '36"W x 84"H x 24"D'}), missing=[])
    assert "spec sheet" not in action.lower()


def test_non_pdf_ai_blank_dims_no_verify_note():
    """Non-PDF_AI rows never get the dimension verification note."""
    for source in ("PDF", "Manual", "URL"):
        row = {**_pdf_ai_row(), "Source Type": source}
        action = _suggested_action(row, missing=[])
        assert "spec sheet" not in action.lower(), f"Source '{source}' should not get dim note"


def test_dim_note_not_duplicated():
    """The note appears exactly once even if _suggested_action is called twice."""
    action = _suggested_action(_pdf_ai_row(), missing=[])
    assert action.lower().count("spec sheet") == 1


def test_missing_fields_and_blank_dims_both_surfaced():
    """Missing fields AND blank dims → both appear in the action string."""
    action = _suggested_action(_pdf_ai_row(), missing=["Room"])
    assert "Room" in action
    assert "spec sheet" in action.lower()
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_confidence.py -v 2>&1 | head -20
```

Expected: assertion failures (message doesn't say "full W×H×D" / no partial-dims check).

- [ ] **Step 3: Add import to `src/confidence.py`**

At the top of `src/confidence.py`, after the existing imports, add:

```python
from src.product_enrichment import has_complete_3d_dimensions
```

- [ ] **Step 4: Replace the tail of `_suggested_action` in `src/confidence.py`**

Find this block (currently the last section of `_suggested_action`, lines 235–245):

```python
    if not missing:
        if source == SOURCE_URL:
            return "Ready for Programa URL import"
        return "Ready for review"

    # Format missing field list in natural language
    if len(missing) == 1:
        return f"Missing {missing[0]}"
    if len(missing) == 2:
        return f"Missing {missing[0]} and {missing[1]}"
    return f"Missing {', '.join(missing[:-1])}, and {missing[-1]}"
```

Replace with:

```python
    if not missing:
        action = "Ready for Programa URL import" if source == SOURCE_URL else "Ready for review"
    elif len(missing) == 1:
        action = f"Missing {missing[0]}"
    elif len(missing) == 2:
        action = f"Missing {missing[0]} and {missing[1]}"
    else:
        action = f"Missing {', '.join(missing[:-1])}, and {missing[-1]}"

    # For PDF_AI rows: flag missing or incomplete W×H×D dimensions so reviewers
    # know to source them from the official spec sheet before sending to Programa.
    dims = _str(row.get("Dimensions"))
    needs_dim_verify = source == SOURCE_PDF_AI and (
        not dims or not has_complete_3d_dimensions(dims)
    )
    if needs_dim_verify and "spec sheet" not in action.lower():
        dim_note = "Verify full W×H×D dimensions from official spec sheet"
        action = dim_note if action == "Ready for review" else f"{action}; {dim_note}"

    return action
```

- [ ] **Step 5: Run confidence tests — expect PASS**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_confidence.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full suite**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/confidence.py tests/test_confidence.py && git commit -m "feat: flag incomplete W×H×D dims in suggested action for PDF_AI rows"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task |
|---|---|
| `has_complete_3d_dimensions(dimensions) -> bool` | Task 1 |
| Returns True only if W, H, D all present | Task 1 |
| Qualify rows if Dimensions blank OR not complete 3D | Task 2 (`_qualifies`) |
| Search query prioritises spec sheets when dims needed | Task 2 (`_build_search_query`) |
| Extraction prompt asks for W, H, D + formatted string | Task 3 (`_build_extraction_prompt`) |
| Fill Dimensions if full 3D found | Task 3 (`_apply_enrichment`) |
| Partial dims found → append note, don't fill | Task 3 (`_apply_enrichment`) |
| `"[Partial dimension found: X; full W x H x D still needed]"` note format | Task 3 |
| `"Verify full W×H×D dimensions from official spec sheet"` in Suggested Action | Task 4 (`_suggested_action`) |
| Do not infer from product name alone | Task 3 (prompt rule) |
| Prefer official manufacturer spec PDFs | Task 2 (search suffix) + existing domain scoring |
| Low source confidence → Review Required = True | Existing `_apply_enrichment` confidence flagging |
| Preserve existing UI and workflow | No app.py or styling changes |

### Placeholder scan
No TBD, TODO, or "similar to Task N" patterns. All code blocks are complete.

### Type consistency
`has_complete_3d_dimensions` defined in Task 1 and imported by both `_qualifies` / `_build_extraction_prompt` (same file, Task 2/3) and `_suggested_action` (Task 4) — signature is identical in all uses.
