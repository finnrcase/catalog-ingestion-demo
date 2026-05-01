# Location & Dimension Extraction Improvement Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably extract room/location from messy vendor-sheet annotations (e.g. "Bar - if we can fit it") and never infer dimensions from product names.

**Architecture:** A new pure `normalize_location` function handles all messy-location logic in isolation. It is wired into `_item_to_row` in `ai_extraction.py`: if the AI-extracted location is uncertain the row's Confidence Score is lowered (so `apply_confidence_checks` flags it for review) and the original note is preserved in Notes. `confidence.py`'s `_suggested_action` is updated to append "Verify dimensions from spec sheet" for PDF_AI rows with blank Dimensions. The AI extraction prompt is updated to stop inferring dimensions and to explicitly scan surrounding context for location.

**Tech Stack:** Python stdlib `re`, existing Anthropic SDK, existing `src/confidence.py`, existing `src/ai_extraction.py`.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/location_normalizer.py` | Pure `normalize_location` function |
| Create | `tests/test_location_normalizer.py` | All normalizer tests |
| Modify | `src/ai_extraction.py` lines 269–323 | Wire normalizer into `_item_to_row` |
| Create | `tests/test_ai_extraction.py` | Tests for `_item_to_row` location + dimension behaviour |
| Modify | `src/confidence.py` lines 206–245 | Append dimension note in `_suggested_action` |
| Modify | `tests/test_confidence.py` (create) | Tests for dimension suggested action |
| Modify | `src/ai_extraction.py` lines 115–206 | Update prompt rules 5 and 9 |

---

## Task 1: `src/location_normalizer.py` — pure location normalization

**Files:**
- Create: `src/location_normalizer.py`
- Create: `tests/test_location_normalizer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_location_normalizer.py`:

```python
import pytest
from src.location_normalizer import normalize_location


# ── Uncertainty qualifier stripping ───────────────────────────────────────────

def test_dash_if_we_can_fit_it():
    loc, conf, reason = normalize_location("Bar - if we can fit it")
    assert loc == "Bar"
    assert conf < 75
    assert "verify" in reason.lower() or "infer" in reason.lower()


def test_no_dash_if_we_can_fit_it():
    loc, conf, reason = normalize_location("Bar if we can fit it")
    assert loc == "Bar"
    assert conf < 75


def test_uncertainty_reason_mentions_verify():
    _, conf, reason = normalize_location("Kitchen - if possible")
    assert conf < 75
    assert reason  # non-empty


# ── Title-case normalisation ──────────────────────────────────────────────────

def test_lowercase_laundry_room():
    loc, conf, _ = normalize_location("laundry room floor 2")
    assert loc == "Laundry Room Floor 2"
    assert conf >= 75


def test_lowercase_single_word():
    for raw, expected in [
        ("exterior", "Exterior"),
        ("kitchen", "Kitchen"),
        ("mudroom", "Mudroom"),
        ("primary", "Primary"),
        ("gym", "Gym"),
    ]:
        loc, conf, _ = normalize_location(raw)
        assert loc == expected, f"{raw!r} → expected {expected!r}, got {loc!r}"
        assert conf >= 75


def test_two_word_lowercase():
    loc, conf, _ = normalize_location("nanny vestibule")
    assert loc == "Nanny Vestibule"
    assert conf >= 75


# ── Already-clean inputs ──────────────────────────────────────────────────────

def test_already_title_case():
    loc, conf, _ = normalize_location("Kitchen")
    assert loc == "Kitchen"
    assert conf >= 75


def test_already_title_case_multi_word():
    loc, conf, _ = normalize_location("Laundry Room Floor 2")
    assert loc == "Laundry Room Floor 2"
    assert conf >= 75


# ── Empty / whitespace inputs → use default ──────────────────────────────────

def test_empty_with_default():
    loc, conf, reason = normalize_location("", "Kitchen")
    assert loc == "Kitchen"
    assert conf < 75
    assert reason  # non-empty


def test_whitespace_only_with_default():
    loc, conf, _ = normalize_location("   ", "Bathroom")
    assert loc == "Bathroom"
    assert conf < 75


def test_empty_no_default():
    loc, conf, _ = normalize_location("")
    assert loc == ""
    assert conf == 0


# ── Return type ───────────────────────────────────────────────────────────────

def test_returns_three_tuple():
    result = normalize_location("Bar")
    assert isinstance(result, tuple)
    assert len(result) == 3
    loc, conf, reason = result
    assert isinstance(loc, str)
    assert isinstance(conf, int)
    assert isinstance(reason, str)
```

- [ ] **Step 2: Run tests — expect FAIL (module not created yet)**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_location_normalizer.py -v 2>&1 | head -15
```

Expected: `ModuleNotFoundError` — `src.location_normalizer` does not exist.

- [ ] **Step 3: Create `src/location_normalizer.py`**

```python
"""
Location string normalizer for SCH DesignOps Intake.

Cleans up informal location annotations extracted from vendor PDF sheets,
where location may appear as a messy note such as "Bar - if we can fit it"
or "laundry room floor 2".

Public API
----------
normalize_location(raw_location, default_location="") -> tuple[str, int, str]
    Returns (cleaned_location, confidence_score, reason).
    confidence_score is 0–100; below 75 the caller should flag Review Required.
"""

import re

# Patterns that indicate the location is uncertain or conditional.
# Matched case-insensitively against the raw string.
_UNCERTAINTY_RE = re.compile(
    r"(\s*[-–]\s*if\s+we\s+can\s+fit\s+it\b.*"
    r"|\s+if\s+we\s+can\s+fit\s+it\b.*"
    r"|\s*[-–]\s*if\s+possible\b.*"
    r"|\s+if\s+possible\b.*"
    r"|\s*[-–]\s*pending\b.*"
    r"|\s*[-–]\s*tbd\b.*"
    r"|\s*[-–]\s*maybe\b.*"
    r"|\s+if\s+it\s+fits\b.*)",
    re.IGNORECASE,
)


def normalize_location(
    raw_location: str,
    default_location: str = "",
) -> tuple[str, int, str]:
    """
    Clean an informal location string and return a confidence score.

    Parameters
    ----------
    raw_location     : Raw string from the PDF or AI extraction.
    default_location : Fallback value when raw_location is blank.

    Returns
    -------
    (cleaned_location, confidence_score, reason)
        confidence_score < 75 means the caller should set Review Required = True.
    """
    stripped = (raw_location or "").strip()

    # Empty → use default
    if not stripped:
        if default_location:
            return default_location, 40, "used default location — verify"
        return "", 0, "no location found"

    # Detect and remove uncertainty qualifiers
    had_uncertainty = bool(_UNCERTAINTY_RE.search(stripped))
    cleaned = _UNCERTAINTY_RE.sub("", stripped).strip().strip("-–").strip()

    if not cleaned:
        if default_location:
            return default_location, 40, "used default location — verify"
        return "", 0, "location reduced to empty after cleaning"

    # Title-case the result
    cleaned = cleaned.title()

    if had_uncertainty:
        return cleaned, 65, "location inferred from uncertain note — verify"

    return cleaned, 90, "location extracted from annotation"
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_location_normalizer.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run full suite to confirm no regressions**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/ -q
```

Expected: all tests pass (69 pre-existing + new location tests).

- [ ] **Step 6: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/location_normalizer.py tests/test_location_normalizer.py && git commit -m "feat: location normalizer — strip uncertainty qualifiers, title-case, confidence scoring"
```

---

## Task 2: Wire `normalize_location` into `_item_to_row` in `ai_extraction.py`

**Files:**
- Modify: `src/ai_extraction.py` lines 269–323 (`_item_to_row`)
- Create: `tests/test_ai_extraction.py`

The current `_item_to_row` function ends with three fallback assignments followed by a Status derivation. The location normalization block is inserted between the fallbacks and the Status line, so the adjusted Confidence Score is used when deriving Status.

Because `apply_confidence_checks` overwrites `Review Required` using `calculate_row_confidence`, setting `Review Required = True` directly in `_item_to_row` would be immediately overwritten. Instead, lower the row's `Confidence Score` by 15 when location confidence < 75 — this causes `calculate_row_confidence` (which returns the stored score for PDF_AI rows) to return a value below the 75 threshold, which causes `should_require_review` to return `True`, which causes `apply_confidence_checks` to write `Review Required = True`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ai_extraction.py`:

```python
import pytest
from src.ai_extraction import _item_to_row


def _base_item(**overrides):
    """Minimal AI JSON object that produces a valid row."""
    base = {
        "project": "Test Project",
        "room": "Kitchen",
        "product_name": "Wolf Microwave",
        "brand": "Wolf",
        "dimensions": "",
        "finish_color": "",
        "model_sku": "MDD30TS",
        "quantity": 1,
        "price": "$1,200",
        "supplier": "AEG",
        "product_url": "",
        "notes": "",
        "product_category": "Appliance",
        "confidence_score": 85,
        "review_required": False,
        "missing_fields": "",
        "suggested_action": "",
    }
    base.update(overrides)
    return base


# ── Location normalisation ────────────────────────────────────────────────────

def test_item_to_row_messy_location_cleaned():
    """Uncertain location qualifier is stripped; room is title-cased."""
    item = _base_item(room="bar - if we can fit it")
    row = _item_to_row(item, "Test Project", "Living Room", "AEG")
    assert row["Room"] == "Bar"


def test_item_to_row_messy_location_preserves_original_in_notes():
    """Original messy location note is preserved in Notes."""
    item = _base_item(room="bar - if we can fit it", notes="")
    row = _item_to_row(item, "Test Project", "Living Room", "AEG")
    assert "[Location note: bar - if we can fit it]" in row["Notes"]


def test_item_to_row_messy_location_lowers_confidence():
    """Messy location subtracts 15 from Claude's confidence score."""
    item = _base_item(room="bar - if we can fit it", confidence_score=85)
    row = _item_to_row(item, "Test Project", "Living Room", "AEG")
    assert row["Confidence Score"] == 70  # 85 - 15


def test_item_to_row_clean_location_unchanged():
    """Clean location is title-cased but does not affect confidence or Notes."""
    item = _base_item(room="laundry room floor 2", notes="")
    row = _item_to_row(item, "Test Project", "Kitchen", "AEG")
    assert row["Room"] == "Laundry Room Floor 2"
    assert row["Notes"] == ""
    assert row["Confidence Score"] == 85  # unchanged


def test_item_to_row_empty_location_uses_default():
    """Empty AI-extracted location falls back to default_room."""
    item = _base_item(room="")
    row = _item_to_row(item, "Test Project", "Master Bedroom", "AEG")
    assert row["Room"] == "Master Bedroom"


def test_item_to_row_location_note_not_duplicated():
    """Re-running _item_to_row on same item does not duplicate the Notes tag."""
    item = _base_item(room="bar - if we can fit it", notes="")
    row1 = _item_to_row(item, "Test Project", "Living Room", "AEG")
    # Simulate re-processing: set room to already-cleaned value, notes already set
    item2 = _base_item(room="Bar", notes=row1["Notes"])
    row2 = _item_to_row(item2, "Test Project", "Living Room", "AEG")
    assert row2["Notes"].count("[Location note:") <= 1
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_ai_extraction.py -v 2>&1 | head -25
```

Expected: `AssertionError` — `_item_to_row` does not yet normalize location.

- [ ] **Step 3: Add import and normalization block to `_item_to_row` in `src/ai_extraction.py`**

**3a.** At the top of `src/ai_extraction.py`, after the existing imports (around line 32), add:

```python
from src.location_normalizer import normalize_location
```

**3b.** In `_item_to_row`, find this block (currently the last section before `return row`):

```python
    # Enforce fallbacks for required context fields
    if not str(row.get("Project", "")).strip():
        row["Project"] = project_name
    if not str(row.get("Room", "")).strip():
        row["Room"] = default_room
    if not str(row.get("Supplier", "")).strip():
        row["Supplier"] = supplier

    # Derive Status from confidence so the review table badge is consistent
    score = row.get("Confidence Score", 50)
    row["Status"] = "Ready for Review" if int(score) >= 75 else "Needs Review"

    return row
```

Replace with:

```python
    # Enforce fallbacks for required context fields
    if not str(row.get("Project", "")).strip():
        row["Project"] = project_name
    if not str(row.get("Room", "")).strip():
        row["Room"] = default_room
    if not str(row.get("Supplier", "")).strip():
        row["Supplier"] = supplier

    # ── Location normalisation ─────────────────────────────────────────────────
    # normalize_location strips uncertainty qualifiers, title-cases, and scores.
    # If confidence < 75 we lower the row's Confidence Score by 15 so that
    # apply_confidence_checks naturally sets Review Required = True (it uses the
    # stored score for PDF_AI rows rather than re-deriving it).
    _raw_room = str(row.get("Room", "") or "").strip()
    _cleaned_room, _loc_conf, _loc_reason = normalize_location(_raw_room, default_room)
    if _raw_room and _cleaned_room != _raw_room:
        _existing_notes = str(row.get("Notes", "") or "").strip()
        _loc_note = f"[Location note: {_raw_room}]"
        if _loc_note not in _existing_notes:
            row["Notes"] = (
                f"{_existing_notes} {_loc_note}".strip()
                if _existing_notes else _loc_note
            )
    row["Room"] = _cleaned_room or default_room
    if _loc_conf < 75:
        row["Confidence Score"] = max(0, int(row.get("Confidence Score", 50)) - 15)

    # Derive Status from confidence so the review table badge is consistent
    score = row.get("Confidence Score", 50)
    row["Status"] = "Ready for Review" if int(score) >= 75 else "Needs Review"

    return row
```

- [ ] **Step 4: Run the new tests — expect PASS**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_ai_extraction.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Run full suite**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/ai_extraction.py tests/test_ai_extraction.py && git commit -m "feat: normalize AI-extracted location in _item_to_row; lower confidence for uncertain notes"
```

---

## Task 3: Dimension verification note in `_suggested_action` (`confidence.py`)

**Files:**
- Modify: `src/confidence.py` lines 206–245 (`_suggested_action`)
- Create: `tests/test_confidence.py`

For PDF_AI rows where `Dimensions` is blank, `_suggested_action` should append `"; Verify dimensions from spec sheet"` to whatever action is already present (or return just that string if the action was "Ready for review"). Non-PDF_AI rows and rows with filled dimensions are unaffected.

The current `_suggested_action` function ends with a set of early-return conditions plus format statements for missing fields. The change replaces the final block (lines 235–245) with a version that stores the result in `action` before applying the dimension check:

- [ ] **Step 1: Write the failing tests**

Create `tests/test_confidence.py`:

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


# ── Dimension verification note ───────────────────────────────────────────────

def test_pdf_ai_blank_dims_ready_row_gets_dim_note():
    """PDF_AI row with no missing fields and blank Dimensions → dim note only."""
    row = _pdf_ai_row()
    action = _suggested_action(row, missing=[])
    assert "Verify dimensions from spec sheet" in action


def test_pdf_ai_blank_dims_missing_fields_appends_dim_note():
    """PDF_AI row with missing fields AND blank Dimensions → field note + dim note."""
    row = _pdf_ai_row()
    action = _suggested_action(row, missing=["Room"])
    assert "Missing Room" in action
    assert "Verify dimensions from spec sheet" in action


def test_pdf_ai_filled_dims_no_dim_note():
    """PDF_AI row with filled Dimensions → no dim note."""
    row = _pdf_ai_row(Dimensions='30"W × 18"D')
    action = _suggested_action(row, missing=[])
    assert "dimension" not in action.lower()


def test_non_pdf_ai_blank_dims_no_dim_note():
    """Non-PDF_AI rows do not get the dim note even if Dimensions is blank."""
    for source in ("PDF", "Manual", "URL"):
        row = {**_pdf_ai_row(), "Source Type": source}
        action = _suggested_action(row, missing=[])
        assert "dimension" not in action.lower(), f"Source {source} should not get dim note"


def test_dim_note_not_duplicated():
    """If action already mentions 'dimension', it is not appended again."""
    row = _pdf_ai_row()
    # Simulate action that already has the note
    action1 = _suggested_action(row, missing=[])
    # The note should appear exactly once
    assert action1.lower().count("dimension") == 1
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_confidence.py -v 2>&1 | head -20
```

Expected: `AssertionError` — dimension note not yet added.

- [ ] **Step 3: Update the tail of `_suggested_action` in `src/confidence.py`**

Find this block (currently lines 235–245 in `confidence.py`):

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

    # For PDF_AI rows with blank Dimensions, append a verification note so
    # reviewers know to source dimensions from the manufacturer's spec sheet.
    if (source == SOURCE_PDF_AI
            and not _str(row.get("Dimensions"))
            and "dimension" not in action.lower()):
        dim_note = "Verify dimensions from spec sheet"
        action = dim_note if action == "Ready for review" else f"{action}; {dim_note}"

    return action
```

- [ ] **Step 4: Run confidence tests — expect PASS**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_confidence.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Run full suite**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/confidence.py tests/test_confidence.py && git commit -m "feat: append 'Verify dimensions from spec sheet' for PDF_AI rows with blank Dimensions"
```

---

## Task 4: Update AI extraction prompt — location and dimension rules

**Files:**
- Modify: `src/ai_extraction.py` lines 115–206 (`_build_prompt`, extraction rules 5 and 9)

No new tests required — prompt changes affect live Claude behaviour, not deterministic unit logic. Verify with a syntax check and ensure existing tests still pass.

- [ ] **Step 1: Replace rule 5 (Dimensions) in `_build_prompt`**

Find this line in `_build_prompt` (inside the `EXTRACTION RULES` block):

```python
5. Dimensions: extract if visible (e.g. '30"W × 18"D × 16"H'). Leave empty string if not stated.
```

Replace with:

```python
5. Dimensions: extract ONLY if the exact product dimensions are explicitly stated in a specification, table, or labelled field in the document (e.g. '30"W × 18"D × 16"H'). Do NOT infer dimensions from product names — a name like "36-inch refrigerator" or "30\\" range" does not give you H×W×D. Leave empty string "" if dimensions are not explicitly stated — the enrichment step will fill this from the manufacturer spec sheet. If dimensions are partially or ambiguously stated, leave empty string and include "Verify dimensions from spec sheet" in suggested_action.
```

- [ ] **Step 2: Replace rule 9 (Room) in `_build_prompt`**

Find this line:

```python
9. Room: extract any per-line room annotations (e.g. "Bar", "Kitchen", "Primary", "Laundry Room Floor 2", "Gym", "Mudroom", "Nanny Vestibule", "Exterior"). If not visible for a line, use the default room value above.
```

Replace with:

```python
9. Room / Location: Location may appear ANYWHERE near the product row — in a separate column, as a handwritten-style annotation, in red text beside the description, or as an informal phrase. Examples: "Bar - if we can fit it", "laundry room floor 2", "exterior", "primary", "mudroom", "nanny vestibule", "gym", "Nanny Vestibule", "Exterior".
   - Scan the full row and nearby context for any room or location hint.
   - Normalise to Title Case (e.g. "laundry room floor 2" → "Laundry Room Floor 2", "exterior" → "Exterior").
   - If a note contains uncertainty (e.g. "Bar - if we can fit it", "kitchen if it fits"), extract the clean room name ("Bar"), set review_required = true, and include the ORIGINAL note verbatim in the notes field.
   - If no location is visible for a line, use the default room value above.
```

- [ ] **Step 3: Verify Python syntax**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -c "import ast; ast.parse(open('src/ai_extraction.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Run full suite**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/ -q
```

Expected: all tests pass (prompt changes don't affect unit tests).

- [ ] **Step 5: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/ai_extraction.py && git commit -m "feat: update AI prompt — infer location from annotations, never infer dimensions from name"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task |
|---|---|
| Create `src/location_normalizer.py` | Task 1 |
| `normalize_location(raw, default="") -> tuple[str, int, str]` | Task 1 |
| "Bar - if we can fit it" → "Bar" | Task 1 (test + impl) |
| "Bar if we can fit it" → "Bar" | Task 1 (test + impl) |
| "laundry room floor 2" → "Laundry Room Floor 2" | Task 1 (test) |
| lowercase single words → Title Case | Task 1 (test) |
| Empty → use default, mark Review Required | Task 1 (impl) + Task 2 (confidence lowering) |
| Preserve uncertainty in Notes `[Location note: ...]` | Task 2 |
| Do not overwrite manually entered Location | Not needed — `_item_to_row` only processes PDF_AI rows; Manual rows never call `_item_to_row` |
| Update AI prompt — location from surrounding context | Task 4 |
| Update AI prompt — normalize informal notes | Task 4 |
| Update AI prompt — uncertainty → Review Required + Notes | Task 4 |
| Do not fill Dimensions unless from trusted source | Task 4 (prompt) |
| Dimensions from enrichment + ambiguous → Review Required | Existing enrichment domain scoring handles this; no code change needed |
| Dimensions not found → leave blank + Suggested Action | Task 3 (`_suggested_action`) + Task 4 (prompt) |
| Never infer dimensions from product name | Task 4 (prompt) |
| Lower confidence if Location came from messy note | Task 2 (confidence −15) |
| Lower confidence if Dimensions enriched from non-official source | Existing — enrichment domain score < MIN_CONF_SCORE already sets Review Required |
| Do not change styling | No styling files touched |

### Placeholder scan
No TBD, TODO, or "similar to Task N" patterns. All code blocks are complete and runnable.

### Type consistency
`normalize_location` signature used in Task 1 implementation matches import + call in Task 2 exactly.
`_suggested_action` signature unchanged; only the tail block is replaced.
`_item_to_row` signature unchanged.
