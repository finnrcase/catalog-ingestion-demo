# Category AI + Document Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PDF document parser that extracts structured product rows from any receipt/quote/invoice (no AI required), plus a single-call AI category suggester that fills blank `Product Category` fields across all source types.

**Architecture:** Parser (`document_parser.py`) always runs first and produces partial row dicts from PDF text/table heuristics. When the AI toggle is on, its output is passed as structured context to the existing Claude call in `ai_extraction.py` — making extraction faster and more accurate. A separate `category_ai.py` module handles batch category suggestion via one Claude call from the AI-Assisted Cleanup section of the UI.

**Tech Stack:** Python 3.11+, PyMuPDF 1.23+ (already installed), Anthropic SDK (already installed), Streamlit, pandas.

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `src/intake_schema.py` | Modify | Remove "Couch", add 2 new columns, update make_base_row |
| `src/category_ai.py` | Create | `_normalise_category()` + `suggest_categories_batch()` |
| `src/document_parser.py` | Create | `parse_pdf_rows()` — heuristic PDF extraction, no AI |
| `src/ai_extraction.py` | Modify | Accept parser output; update `_build_prompt()` |
| `app.py` | Modify | Wire parser; add category suggestion UI |
| `tests/test_category_ai.py` | Create | Unit tests for normalisation + batch response parsing |
| `tests/test_document_parser.py` | Create | Unit tests for skip-row detection + field extraction |

---

## Task 1: Update `src/intake_schema.py`

**Files:**
- Modify: `src/intake_schema.py`

- [ ] **Step 1: Open the file and plan the changes**

Read `src/intake_schema.py`. Changes needed:
1. Remove `"Couch"` from `CATEGORIES` — keep `"Sofa"`.
2. Append `"AI Category Confidence"` and `"Category Source"` to `ALL_COLUMNS`.
3. Add both fields to `make_base_row()` with defaults `0` and `"Unknown"`.

- [ ] **Step 2: Apply the changes**

Replace the `CATEGORIES` list:
```python
CATEGORIES: list[str] = [
    "Chair",
    "Sofa",
    "Paint",
    "Fabric",
    "Table",
    "Lighting",
    "Plumbing",
    "Hardware",
    "Rug",
    "Artwork",
    "Mirror",
    "Appliance",
    "Accessories",
    "Other",
]
```

Replace `ALL_COLUMNS` — append two new entries at the end:
```python
ALL_COLUMNS: list[str] = [
    "Include",
    "Project",
    "Room",
    "Product Name",
    "Brand",
    "Dimensions",
    "Finish / Color",
    "Model/SKU",
    "Product Category",
    "Quantity",
    "Price",
    "Supplier",
    "Product URL",
    "Notes",
    "Source Type",
    "Status",
    "AI Category Confidence",
    "Category Source",
]
```

Update `make_base_row()` — add two new keys to the returned dict:
```python
"AI Category Confidence": 0,
"Category Source":        "Unknown",
```

- [ ] **Step 3: Verify with a quick syntax + smoke test**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj"
python3 -c "
from src.intake_schema import ALL_COLUMNS, CATEGORIES, make_base_row
assert 'AI Category Confidence' in ALL_COLUMNS
assert 'Category Source' in ALL_COLUMNS
assert 'Couch' not in CATEGORIES
assert 'Sofa' in CATEGORIES
row = make_base_row()
assert row['AI Category Confidence'] == 0
assert row['Category Source'] == 'Unknown'
print('intake_schema OK')
"
```
Expected: `intake_schema OK`

- [ ] **Step 4: Commit**

```bash
git -C "/Users/finncase/Desktop/Dev/SCH data input proj" add src/intake_schema.py
git -C "/Users/finncase/Desktop/Dev/SCH data input proj" commit -m "feat: add AI Category Confidence + Category Source columns; remove Couch alias"
```

---

## Task 2: Create `src/category_ai.py`

**Files:**
- Create: `src/category_ai.py`
- Create: `tests/test_category_ai.py`

- [ ] **Step 1: Create the tests directory and write failing tests**

```bash
mkdir -p "/Users/finncase/Desktop/Dev/SCH data input proj/tests"
touch "/Users/finncase/Desktop/Dev/SCH data input proj/tests/__init__.py"
```

Write `tests/test_category_ai.py`:
```python
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── _normalise_category ────────────────────────────────────────────────────────

def test_normalise_exact_match():
    from src.category_ai import _normalise_category
    assert _normalise_category("Appliance") == "Appliance"

def test_normalise_case_insensitive():
    from src.category_ai import _normalise_category
    assert _normalise_category("appliance") == "Appliance"

def test_normalise_couch_maps_to_sofa():
    from src.category_ai import _normalise_category
    assert _normalise_category("Couch") == "Sofa"
    assert _normalise_category("couch") == "Sofa"

def test_normalise_unknown_maps_to_other():
    from src.category_ai import _normalise_category
    assert _normalise_category("Spaceship") == "Other"

def test_normalise_empty_maps_to_other():
    from src.category_ai import _normalise_category
    assert _normalise_category("") == "Other"


# ── _parse_batch_response ──────────────────────────────────────────────────────

def test_parse_batch_response_valid():
    from src.category_ai import _parse_batch_response
    raw = '[{"id": 0, "category": "Appliance", "confidence": 92, "reason": "Wolf line"}]'
    result = _parse_batch_response(raw)
    assert result[0]["category"] == "Appliance"
    assert result[0]["confidence"] == 92
    assert result[0]["id"] == 0

def test_parse_batch_response_strips_markdown():
    from src.category_ai import _parse_batch_response
    raw = '```json\n[{"id": 1, "category": "Chair", "confidence": 80, "reason": "seat"}]\n```'
    result = _parse_batch_response(raw)
    assert result[0]["category"] == "Chair"

def test_parse_batch_response_invalid_raises():
    from src.category_ai import _parse_batch_response
    with pytest.raises(ValueError):
        _parse_batch_response("not json at all")
```

- [ ] **Step 2: Run to verify tests fail**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj"
python3 -m pytest tests/test_category_ai.py -v 2>&1 | head -30
```
Expected: ImportError or ModuleNotFoundError — `src.category_ai` does not exist yet.

- [ ] **Step 3: Create `src/category_ai.py`**

```python
"""
AI-assisted batch category suggestion for SCH DesignOps Intake.

Public API
----------
suggest_categories_batch(rows, row_indices) -> tuple[dict[int, dict], str | None]
    Returns ({row_index: {category, confidence, reason}}, error_or_None).
    On any failure returns ({}, error_string) — caller leaves table unchanged.

_normalise_category(raw) -> str
    Maps any category string to a canonical CATEGORIES value.
    Imported by document_parser.py.
"""

import json
import os
import re

from dotenv import load_dotenv

from src.intake_schema import CATEGORIES

load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

_CATEGORY_ALIASES: dict[str, str] = {"couch": "sofa"}


def _normalise_category(raw: str) -> str:
    lower = raw.strip().lower()
    canonical = _CATEGORY_ALIASES.get(lower, lower)
    match = next((c for c in CATEGORIES if c.lower() == canonical), None)
    return match if match else "Other"


def _parse_batch_response(response_text: str) -> list[dict]:
    text = re.sub(r"```(?:json)?\s*|```", "", response_text).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON array in response. First 300 chars: {text[:300]}")
    items = json.loads(text[start : end + 1])
    if not isinstance(items, list):
        raise ValueError("Response root is not a JSON array.")
    return items


def _build_category_prompt(rows: list[dict], row_indices: list[int]) -> str:
    categories_str = ", ".join(CATEGORIES)
    items = []
    for idx, (row, row_idx) in enumerate(zip(rows, row_indices)):
        url = str(row.get("Product URL") or "")[:200]
        items.append({
            "id": row_idx,
            "product_name": str(row.get("Product Name") or "").strip(),
            "brand": str(row.get("Brand") or "").strip(),
            "finish_color": str(row.get("Finish / Color") or "").strip(),
            "model_sku": str(row.get("Model/SKU") or "").strip(),
            "notes": str(row.get("Notes") or "").strip(),
            "product_url": url,
        })

    return f"""You are a procurement assistant for Saffron Case Homes, an interior design firm.
For each product below, suggest the single best category from the allowed list.

ALLOWED CATEGORIES: {categories_str}

RULES
1. Return ONLY a valid JSON array — no explanation, no markdown, no code fences.
2. Each element must have exactly: "id" (integer), "category" (string), "confidence" (0-100), "reason" (short string).
3. "id" must match the input "id" exactly.
4. If uncertain, use "Other" with low confidence rather than guessing.
5. For appliances (Wolf, Sub-Zero, Miele, Fisher & Paykel, etc.) use "Appliance".
6. For seating that is a sofa/couch/sectional use "Sofa".

PRODUCTS
{json.dumps(items, indent=2)}"""


def suggest_categories_batch(
    rows: list[dict],
    row_indices: list[int],
) -> tuple[dict[int, dict], str | None]:
    if not rows:
        return {}, None

    if not ANTHROPIC_API_KEY:
        return {}, (
            "AI category suggestion requires ANTHROPIC_API_KEY. "
            "Add it to your .env file and restart the app."
        )

    try:
        import anthropic
    except ImportError:
        return {}, "The 'anthropic' package is not installed. Run: pip install anthropic"

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": _build_category_prompt(rows, row_indices)}],
        )
        response_text = message.content[0].text
    except Exception as exc:
        return {}, f"AI category API call failed: {exc}"

    try:
        items = _parse_batch_response(response_text)
    except (ValueError, json.JSONDecodeError) as exc:
        return {}, f"Could not parse AI category response: {exc}"

    suggestions: dict[int, dict] = {}
    for item in items:
        try:
            row_idx = int(item["id"])
            category = _normalise_category(str(item.get("category", "")))
            confidence = max(0, min(100, int(item.get("confidence", 0))))
            reason = str(item.get("reason", "")).strip()
            # Unrecognised category → confidence 0
            if category == "Other" and str(item.get("category", "")).strip().lower() not in ("other", ""):
                confidence = 0
            suggestions[row_idx] = {"category": category, "confidence": confidence, "reason": reason}
        except (KeyError, TypeError, ValueError):
            continue

    return suggestions, None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj"
python3 -m pytest tests/test_category_ai.py -v
```
Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git -C "/Users/finncase/Desktop/Dev/SCH data input proj" add src/category_ai.py tests/test_category_ai.py tests/__init__.py
git -C "/Users/finncase/Desktop/Dev/SCH data input proj" commit -m "feat: add category_ai module with batch suggestion and normalisation"
```

---

## Task 3: Create `src/document_parser.py`

**Files:**
- Create: `src/document_parser.py`
- Create: `tests/test_document_parser.py`

- [ ] **Step 1: Write failing tests**

Write `tests/test_document_parser.py`:
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.document_parser import (
    _is_skip_line,
    _extract_model_sku,
    _extract_quantity,
    _extract_price,
)


# ── Skip-line detection ────────────────────────────────────────────────────────

def test_skip_subtotal():
    assert _is_skip_line("Subtotal: $1,200.00") is True

def test_skip_tax():
    assert _is_skip_line("HST 13%: $156.00") is True

def test_skip_delivery():
    assert _is_skip_line("Delivery & Installation") is True

def test_skip_freight():
    assert _is_skip_line("Freight & Handling: $75.00") is True

def test_skip_deposit():
    assert _is_skip_line("Deposit paid") is True

def test_skip_total():
    assert _is_skip_line("TOTAL DUE: $2,500.00") is True

def test_not_skip_product():
    assert _is_skip_line("Wolf 30\" Microwave MDD30TS") is False

def test_not_skip_empty():
    assert _is_skip_line("") is False


# ── Model/SKU extraction ───────────────────────────────────────────────────────

def test_extract_model_sku_model_label():
    val, label = _extract_model_sku("Model #: MDD30TS  Wolf Microwave")
    assert val == "MDD30TS"
    assert label == "Model #"

def test_extract_model_sku_sku_label():
    val, label = _extract_model_sku("SKU: 00884844")
    assert val == "00884844"
    assert label == "SKU"

def test_extract_model_sku_item_number():
    val, label = _extract_model_sku("Item #: 1234-AB  Sofa")
    assert val == "1234-AB"
    assert label == "Item #"

def test_extract_model_sku_part_number():
    val, label = _extract_model_sku("Part Number: XYZ-99 cushion")
    assert val == "XYZ-99"
    assert label == "Part Number"

def test_extract_model_sku_none():
    val, label = _extract_model_sku("Just a description with no model")
    assert val == ""
    assert label == ""


# ── Quantity extraction ────────────────────────────────────────────────────────

def test_extract_quantity_qty():
    assert _extract_quantity("Wolf Microwave  qty 2") == 2

def test_extract_quantity_x():
    assert _extract_quantity("Chair x3") == 3

def test_extract_quantity_parentheses():
    assert _extract_quantity("Sofa (2)") == 2

def test_extract_quantity_default():
    assert _extract_quantity("Just a product") == 1


# ── Price extraction ───────────────────────────────────────────────────────────

def test_extract_price_dollar():
    assert _extract_price("Wolf Microwave $1,250.00") == "$1,250.00"

def test_extract_price_decimal():
    assert _extract_price("Chair 899.00") == "899.00"

def test_extract_price_none():
    assert _extract_price("Chair no price here") == ""
```

- [ ] **Step 2: Run to verify tests fail**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj"
python3 -m pytest tests/test_document_parser.py -v 2>&1 | head -20
```
Expected: ImportError — `src.document_parser` does not exist yet.

- [ ] **Step 3: Create `src/document_parser.py`**

```python
"""
Heuristic PDF parser for SCH DesignOps Intake.

Extracts structured product rows from receipts, quotes, invoices, and tear sheets
using PyMuPDF text/table extraction — no AI required.

Public API
----------
parse_pdf_rows(pdf_file, project, room, supplier, notes) -> list[dict]
    Returns partial row dicts using make_base_row() field names.
    Unknown fields are left at their default — never invented.
"""

import re

from src.intake_schema import IMPORTANT_FIELDS, SOURCE_PDF, make_base_row

# Recognised serial/model number label patterns (case-insensitive)
_SKU_LABEL_RE = re.compile(
    r"(?P<label>"
    r"serial\s*(?:number|#|no\.?)?|"
    r"s/?n|"
    r"model\s*(?:number|#|no\.?)?|"
    r"sku|"
    r"item\s*(?:number|#|no\.?)?|"
    r"product\s*(?:code|#)?|"
    r"part\s*(?:number|#|no\.?)?|"
    r"mfr\.?\s*(?:number|#|no\.?)?"
    r")\s*[:\-]?\s*(?P<value>\S+)",
    re.IGNORECASE,
)

# Lines that should be skipped (same logic as confidence.py _IGNORED_RE)
_SKIP_RE = re.compile(
    r"\b(subtotal|sub[- ]?total|tax|gst|hst|vat|pst|delivery|shipping|freight|"
    r"total|balance(\s+due)?|discount|credit|surcharge|handling|deposit|"
    r"freight\s*&\s*handling|f&h|service\s+plan)\b",
    re.IGNORECASE,
)

_QTY_RE = re.compile(
    r"(?:qty|quantity)\s*[:\-]?\s*(\d+)"
    r"|(?<!\w)x\s*(\d+)(?!\w)"
    r"|\((\d+)\)",
    re.IGNORECASE,
)

_PRICE_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?|\b\d{1,6}\.\d{2}\b")


def _is_skip_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(_SKIP_RE.search(stripped))


def _extract_model_sku(line: str) -> tuple[str, str]:
    """Return (value, original_label) or ('', '') if none found."""
    m = _SKU_LABEL_RE.search(line)
    if not m:
        return "", ""
    label = m.group("label").strip().rstrip(":")
    value = m.group("value").strip().rstrip(".,;")
    return value, label


def _extract_quantity(line: str) -> int:
    m = _QTY_RE.search(line)
    if not m:
        return 1
    val = next(g for g in m.groups() if g is not None)
    try:
        return max(1, int(val))
    except ValueError:
        return 1


def _extract_price(line: str) -> str:
    m = _PRICE_RE.search(line)
    return m.group(0) if m else ""


def _clean_product_name(line: str) -> str:
    """Strip labels, prices, and quantities from a line to get a product description."""
    text = _SKU_LABEL_RE.sub("", line)
    text = _PRICE_RE.sub("", text)
    text = _QTY_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" .,;:-")
    return text


def _row_from_line(
    line: str, project: str, room: str, supplier: str, notes: str
) -> dict | None:
    """Parse a single text line into a row dict, or return None if it should be skipped."""
    if _is_skip_line(line):
        return None

    model_sku, sku_label = _extract_model_sku(line)
    name = _clean_product_name(line)

    # Require at least a name or a model number to treat as a product row
    if not name and not model_sku:
        return None

    row = make_base_row(project=project, room=room, supplier=supplier, notes=notes)
    row["Product Name"] = name
    row["Model/SKU"] = model_sku
    row["Quantity"] = _extract_quantity(line)
    row["Price"] = _extract_price(line)
    row["Source Type"] = SOURCE_PDF

    # If the original label is informative (e.g. "Part #"), append to Notes
    if sku_label and sku_label.lower() not in ("model #", "model number", "sku", "serial number"):
        note_tag = f"[{sku_label}: {model_sku}]"
        row["Notes"] = f"{notes} {note_tag}".strip() if notes else note_tag

    # Status: Needs Enrichment if serial present + important fields missing
    has_serial = bool(model_sku)
    missing = [
        f for f in IMPORTANT_FIELDS
        if not str(row.get(f, "") or "").strip()
        and not (f == "Quantity" and int(row.get("Quantity", 0)) > 0)
    ]
    row["Status"] = "Needs Enrichment" if (has_serial and missing) else "Needs Review"

    return row


def _parse_table_rows(
    page, project: str, room: str, supplier: str, notes: str
) -> list[dict]:
    """Extract product rows from PyMuPDF table structures on a single page."""
    rows = []
    try:
        tables = page.find_tables()
    except Exception:
        return rows

    for table in tables:
        header = None
        for i, trow in enumerate(table.extract()):
            cells = [str(c or "").strip() for c in trow]
            joined = " | ".join(c for c in cells if c)

            if i == 0:
                header = [c.lower() for c in cells]
                continue

            if _is_skip_line(joined):
                continue

            # If table has a header, try to map columns
            if header:
                col_map = {name: idx for idx, name in enumerate(header)}
                row = make_base_row(project=project, room=room, supplier=supplier, notes=notes)
                row["Source Type"] = SOURCE_PDF

                for key in ("description", "product", "item", "name"):
                    if key in col_map:
                        row["Product Name"] = cells[col_map[key]]
                        break

                for key in ("model", "sku", "model #", "model no", "item #", "part #", "serial"):
                    if key in col_map:
                        row["Model/SKU"] = cells[col_map[key]]
                        break

                for key in ("qty", "quantity"):
                    if key in col_map:
                        try:
                            row["Quantity"] = max(1, int(cells[col_map[key]]))
                        except (ValueError, IndexError):
                            pass
                        break

                for key in ("price", "unit price", "total", "amount"):
                    if key in col_map:
                        row["Price"] = cells[col_map[key]]
                        break

                for key in ("brand", "manufacturer", "mfr"):
                    if key in col_map:
                        row["Brand"] = cells[col_map[key]]
                        break
            else:
                row = _row_from_line(joined, project, room, supplier, notes)
                if row is None:
                    continue

            # Skip if no useful content
            if not str(row.get("Product Name", "")).strip() and not str(row.get("Model/SKU", "")).strip():
                continue

            has_serial = bool(str(row.get("Model/SKU", "")).strip())
            missing = [
                f for f in IMPORTANT_FIELDS
                if not str(row.get(f, "") or "").strip()
                and not (f == "Quantity" and int(row.get("Quantity", 0)) > 0)
            ]
            row["Status"] = "Needs Enrichment" if (has_serial and missing) else "Needs Review"
            rows.append(row)

    return rows


def parse_pdf_rows(
    pdf_file,
    project: str = "",
    room: str = "",
    supplier: str = "",
    notes: str = "",
) -> list[dict]:
    """
    Extract structured product rows from a PDF using heuristic text/table parsing.
    No AI call is made. Unknown fields are left blank — never invented.

    Parameters
    ----------
    pdf_file : Streamlit UploadedFile or any object with .read() and .seek().
    project, room, supplier, notes : metadata applied to every row.

    Returns
    -------
    list[dict] of partial row dicts aligned to make_base_row() field names.
    Empty list if the PDF has no parseable text.
    """
    try:
        import fitz
    except ImportError:
        raise ImportError("PyMuPDF is required. Run: pip install pymupdf")

    raw = pdf_file.read()
    pdf_file.seek(0)

    doc = fitz.open(stream=raw, filetype="pdf")
    all_rows: list[dict] = []
    seen_names: set[str] = set()

    for page in doc:
        # 1. Try table extraction first
        table_rows = _parse_table_rows(page, project, room, supplier, notes)
        if table_rows:
            for r in table_rows:
                key = (str(r.get("Product Name", "")).lower(), str(r.get("Model/SKU", "")).lower())
                if key not in seen_names:
                    seen_names.add(key)
                    all_rows.append(r)
            continue

        # 2. Fall back to line-by-line text parsing
        text = page.get_text("text")
        for line in text.splitlines():
            line = line.strip()
            if len(line) < 3:
                continue
            row = _row_from_line(line, project, room, supplier, notes)
            if row is None:
                continue
            key = (str(row.get("Product Name", "")).lower(), str(row.get("Model/SKU", "")).lower())
            if key not in seen_names:
                seen_names.add(key)
                all_rows.append(row)

    doc.close()
    return all_rows
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj"
python3 -m pytest tests/test_document_parser.py -v
```
Expected: All 20 tests PASS.

- [ ] **Step 5: Commit**

```bash
git -C "/Users/finncase/Desktop/Dev/SCH data input proj" add src/document_parser.py tests/test_document_parser.py
git -C "/Users/finncase/Desktop/Dev/SCH data input proj" commit -m "feat: add document_parser with heuristic PDF extraction and serial/model recognition"
```

---

## Task 4: Update `src/ai_extraction.py`

**Files:**
- Modify: `src/ai_extraction.py`

Changes:
1. Import `parse_pdf_rows` from `document_parser`.
2. Update `_build_prompt()` to accept an optional `structured_rows` argument.
3. Update `extract_products_from_pdf_with_ai()` to run the parser first, then pass its output to `_build_prompt()`.
4. Add `"AI Category Confidence"` and `"Category Source"` to `_OUTPUT_COLUMNS` and `_FIELD_MAP` defaults.

- [ ] **Step 1: Add new output columns to `_OUTPUT_COLUMNS`**

In `src/ai_extraction.py`, find `_OUTPUT_COLUMNS` and append the two new columns:
```python
_OUTPUT_COLUMNS: list[str] = [
    "Include",
    "Confidence Score",
    "Review Required",
    "Suggested Action",
    "Project",
    "Room",
    "Product Name",
    "Brand",
    "Dimensions",
    "Finish / Color",
    "Model/SKU",
    "Product Category",
    "Quantity",
    "Price",
    "Supplier",
    "Product URL",
    "Notes",
    "Source Type",
    "Status",
    "Missing Fields",
    "AI Category Confidence",
    "Category Source",
]
```

- [ ] **Step 2: Update `_build_prompt()` to accept structured rows**

Replace the existing `_build_prompt` signature and add the structured pre-parse section:

```python
def _build_prompt(
    pdf_text: str,
    project_name: str,
    default_room: str,
    supplier: str,
    structured_rows: list[dict] | None = None,
) -> str:
    categories_str = ", ".join(ALLOWED_CATEGORIES)

    pre_parse_section = ""
    if structured_rows:
        import json as _json
        compact = [
            {
                "product_name": r.get("Product Name", ""),
                "brand": r.get("Brand", ""),
                "model_sku": r.get("Model/SKU", ""),
                "quantity": r.get("Quantity", 1),
                "price": r.get("Price", ""),
                "notes": r.get("Notes", ""),
            }
            for r in structured_rows
        ]
        pre_parse_section = f"""
STRUCTURED PRE-PARSE
The following rows were extracted by a rule-based parser before this call.
Use them as a starting point — correct errors, fill gaps where the document
supports it, and add any rows the parser missed.
Do NOT invent dimensions, finish, or brand if not present in the document.

{_json.dumps(compact, indent=2)}

"""

    return f"""You are a procurement assistant for Saffron Case Homes, an interior design firm.
A team member has uploaded a product quote sheet or tear sheet (text extracted below).
Your job is to extract every product row and return them as structured JSON for import into Programa, a design project management platform.
{pre_parse_section}
PROJECT CONTEXT
- Project name: {project_name}
- Default room (use if no per-line room annotation is visible): {default_room}
- Supplier (use if not extractable from the document): {supplier}

EXTRACTION RULES
1. Return ONLY product items — skip: subtotals, tax, delivery, freight, service plan-only rows, balance due, deposit lines, and blank rows.
2. Product Name = Manufacturer + " " + Description (e.g. "Wolf Microwave", "Sub-Zero 36\" Refrigerator"). Do NOT use a model number as the Product Name unless there is absolutely no usable description.
3. Brand = manufacturer name only (e.g. "Wolf", "Sub-Zero", "Miele").
4. Model/SKU = the model or part number from the quote line. Accept any of: serial number, model number, SKU, item number, product code, part number, manufacturer number.
5. Dimensions: extract if visible (e.g. '30"W × 18"D × 16"H'). Leave empty string if not stated.
6. Finish / Color: extract finish or colour if stated (e.g. "Matte Black", "Stainless Steel"). Leave empty string if not stated.
7. Quantity: if the line description says "qty 2" or similar, use 2. If no quantity is shown, default to 1.
8. Price: use the line price exactly as shown on the quote. If the price appears to be a total for multiple units, keep it as shown and add a note: "Price appears to reflect quoted line total."
9. Room: extract any per-line room annotations. If not visible for a line, use the default room value above.
10. Product Category must be exactly one of: {categories_str}. For appliance quotes most items are "Appliance". Leave blank if uncertain — it can be suggested separately.
11. Confidence scoring (start at 85, apply deductions):
    - Deduct 20 if Product Name cannot be reliably determined.
    - Deduct 20 if Model/SKU is missing.
    - Deduct 15 if room is unclear or missing.
    - Deduct 15 if quantity is ambiguous.
    - Deduct 10 if price is missing or unclear.
12. Set review_required = true when confidence_score < 75, room is unclear, or quantity is ambiguous.
13. missing_fields: comma-separated list of field names that are empty or uncertain (e.g. "Room, Quantity").
14. suggested_action: one short instruction for the reviewer (e.g. "Confirm room assignment", "Verify quantity — description may imply qty 2").

RESPONSE FORMAT
Return ONLY a valid JSON array. No explanation, no markdown, no code fences. Each element must include every key below:
[
  {{
    "project": "{project_name}",
    "room": "<room or default>",
    "product_name": "<Manufacturer Description>",
    "brand": "<manufacturer>",
    "dimensions": "<dimensions string or empty string>",
    "finish_color": "<finish or colour or empty string>",
    "model_sku": "<model number or empty string>",
    "quantity": <integer>,
    "price": "<price string as shown on quote>",
    "supplier": "<supplier name>",
    "product_url": "",
    "notes": "<any notes or empty string>",
    "product_category": "<one of the allowed categories or empty string>",
    "confidence_score": <integer 0-100>,
    "review_required": <true or false>,
    "missing_fields": "<comma-separated list or empty string>",
    "suggested_action": "<short instruction or empty string>"
  }}
]

DOCUMENT TEXT
---
{pdf_text}
---"""
```

- [ ] **Step 3: Update `_item_to_row()` to set Category Source defaults**

In `_item_to_row()`, after the existing `row` dict initialisation line `row: dict = {"Include": True, "Source Type": "PDF_AI"}`, add:
```python
row["AI Category Confidence"] = 0
row["Category Source"] = "Unknown"
```

- [ ] **Step 4: Update `extract_products_from_pdf_with_ai()` to run parser first**

In the `extract_products_from_pdf_with_ai` function, add the import and parser call between step 1 (PDF text extraction) and step 2 (Claude call):

At the top of the file, add import:
```python
from src.document_parser import parse_pdf_rows
```

Between step 1 and step 2 in the function body, insert:
```python
    # ── 1b. Run heuristic parser for structured context ───────────────────────
    try:
        structured_rows = parse_pdf_rows(pdf_file, project_name, default_room, supplier)
    except Exception:
        structured_rows = []
```

Then in the Claude call section (step 2), update `_build_prompt` call to pass `structured_rows`:
```python
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[
                {"role": "user", "content": _build_prompt(
                    pdf_text, project_name, default_room, supplier, structured_rows
                )}
            ],
        )
```

- [ ] **Step 5: Ensure new columns exist in output DataFrame defaults**

In the `_defaults` dict near the end of `extract_products_from_pdf_with_ai`, add:
```python
_defaults: dict = {
    "Include": True,
    "Review Required": False,
    "Quantity": 1,
    "Confidence Score": 0,
    "AI Category Confidence": 0,
    "Category Source": "Unknown",
}
```

- [ ] **Step 6: Smoke test**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj"
python3 -c "
import ast
with open('src/ai_extraction.py') as f: src = f.read()
ast.parse(src)
from src.ai_extraction import _build_prompt, _OUTPUT_COLUMNS
assert 'AI Category Confidence' in _OUTPUT_COLUMNS
assert 'Category Source' in _OUTPUT_COLUMNS
print('ai_extraction OK')
"
```
Expected: `ai_extraction OK`

- [ ] **Step 7: Commit**

```bash
git -C "/Users/finncase/Desktop/Dev/SCH data input proj" add src/ai_extraction.py
git -C "/Users/finncase/Desktop/Dev/SCH data input proj" commit -m "feat: wire document parser into ai_extraction as structured pre-parse context"
```

---

## Task 5: Update `app.py`

**Files:**
- Modify: `app.py`

Changes:
1. Import `parse_pdf_rows` from `document_parser` and `suggest_categories_batch` from `category_ai`.
2. Wire `parse_pdf_rows` into the standard (non-AI) PDF generate path.
3. Add inline note above review table when blank categories exist.
4. Add category suggestion toggle + button in AI-Assisted Cleanup section.
5. Add `AI Category Confidence` and `Category Source` to `data_editor` column config.
6. Update session state initialisation.
7. Bump version badge to v0.5.

- [ ] **Step 1: Add imports**

At the top of `app.py`, add after the existing src imports:
```python
from src.document_parser import parse_pdf_rows
from src.category_ai import suggest_categories_batch
```

- [ ] **Step 2: Add session state for category suggestion**

In the session state block, add:
```python
if "cat_ai_error" not in st.session_state:
    st.session_state.cat_ai_error = None
```

- [ ] **Step 3: Bump version badge**

Change `v0.4` to `v0.5` in the version badge HTML string.

- [ ] **Step 4: Wire parser into the standard PDF path**

Find the `# ── Standard path ──` block. Replace `create_pdf_rows(...)` with parser output:

```python
    else:
        # ── Standard path ──────────────────────────────────────────────────────
        raw_urls = [u.strip() for u in (url_input or "").splitlines() if u.strip()]
        url_rows = create_url_rows(raw_urls, project_name, room, supplier, notes)

        parsed_pdf_rows = []
        for pdf_file in (uploaded_files or []):
            try:
                rows = parse_pdf_rows(pdf_file, project_name, room, supplier, notes)
                parsed_pdf_rows.extend(rows)
            except Exception as exc:
                # Fallback to filename-only row
                fallback = create_pdf_rows([pdf_file], project_name, room, supplier, notes)
                parsed_pdf_rows.extend(fallback)
                st.warning(f"Could not parse '{pdf_file.name}': {exc}", icon="⚠️")

        if not url_rows and not parsed_pdf_rows:
            st.warning(
                "Nothing to process — please upload at least one PDF "
                "or paste at least one URL."
            )
        else:
            base_df = build_intake_dataframe(url_rows, [], parsed_pdf_rows)
            st.session_state.intake_df = apply_confidence_checks(base_df)
            st.session_state.automation_results = None
```

Note: `build_intake_dataframe` currently takes `(url_rows, pdf_rows, manual_rows=None)`. The parser output replaces `pdf_rows` here — pass it as `manual_rows` since it already has all fields populated, OR update `build_intake_dataframe` to accept a fourth `parsed_rows` argument. The cleanest approach: pass parsed rows as `pdf_rows` since they use `Source Type = "PDF"` and the column guarantee loop handles any missing columns.

Update the call to:
```python
base_df = build_intake_dataframe(url_rows, parsed_pdf_rows)
```

- [ ] **Step 5: Add inline note above review table**

After the line `section_label(f"Review Table · {row_count} item{'s' if row_count != 1 else ''}")`, add:

```python
    blank_cat_count = int(
        ((df["Product Category"].isna()) | (df["Product Category"].str.strip() == "")).sum()
        if "Product Category" in df.columns else 0
    )
    if blank_cat_count > 0:
        st.caption(
            f"ℹ️  {blank_cat_count} row{'s' if blank_cat_count != 1 else ''} with blank category — "
            "suggestions available in AI-Assisted Cleanup below."
        )
```

- [ ] **Step 6: Add new column configs to data_editor**

Inside the `column_config` dict in `st.data_editor(...)`, add after the `"Missing Fields"` entry:

```python
            "AI Category Confidence": st.column_config.NumberColumn(
                "Cat. AI Confidence", width="small", disabled=True,
                min_value=0, max_value=100, format="%d %%"
            ),
            "Category Source": st.column_config.TextColumn(
                "Category Source", width="small", disabled=True
            ),
```

- [ ] **Step 7: Replace AI-Assisted Cleanup section**

Find the existing `# ── AI-Assisted Cleanup placeholder ───` block and replace it entirely:

```python
    # ── AI-Assisted Cleanup ────────────────────────────────────────────────────
    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
    st.divider()
    section_label("AI-Assisted Cleanup")

    # Clear any previous category AI error on each render
    if st.session_state.get("cat_ai_error"):
        st.error(st.session_state.cat_ai_error, icon="❌")

    # ── Category suggestion ────────────────────────────────────────────────────
    use_cat_ai = st.checkbox(
        "Use AI to suggest categories",
        value=False,
        key="use_cat_ai",
        help="Sends rows with blank Category to Claude for a single batched suggestion call.",
    )

    included_mask = edited_df.get("Include", pd.Series([True] * len(edited_df))) == True
    if "Product Category" in edited_df.columns:
        blank_cat_mask = (
            included_mask
            & (
                edited_df["Product Category"].isna()
                | (edited_df["Product Category"].str.strip() == "")
            )
        )
    else:
        blank_cat_mask = pd.Series([False] * len(edited_df))

    blank_cat_indices = list(edited_df[blank_cat_mask].index)
    blank_cat_n = len(blank_cat_indices)

    cat_btn_label = (
        f"Suggest Missing Categories ({blank_cat_n} row{'s' if blank_cat_n != 1 else ''})"
        if blank_cat_n > 0 else "No blank categories"
    )
    cat_col, _, __ = st.columns([2, 6, 2])
    with cat_col:
        cat_clicked = st.button(
            cat_btn_label,
            type="secondary",
            use_container_width=True,
            disabled=(not use_cat_ai or blank_cat_n == 0),
        )

    if cat_clicked and use_cat_ai and blank_cat_n > 0:
        blank_rows = edited_df.loc[blank_cat_indices].to_dict("records")
        with st.spinner(f"Suggesting categories for {blank_cat_n} row{'s' if blank_cat_n != 1 else ''}…"):
            suggestions, error = suggest_categories_batch(blank_rows, blank_cat_indices)

        if error:
            st.session_state.cat_ai_error = error
            st.rerun()
        else:
            st.session_state.cat_ai_error = None
            updated_df = edited_df.copy()
            for row_idx, suggestion in suggestions.items():
                updated_df.at[row_idx, "Product Category"] = suggestion["category"]
                updated_df.at[row_idx, "AI Category Confidence"] = suggestion["confidence"]
                updated_df.at[row_idx, "Category Source"] = "AI Suggested"
                if suggestion["confidence"] < 75:
                    updated_df.at[row_idx, "Review Required"] = True
                    updated_df.at[row_idx, "Suggested Action"] = "Review AI category suggestion"
            st.session_state.intake_df = apply_confidence_checks(updated_df)
            st.rerun()

    # ── Uncertain rows cleanup (existing placeholder) ──────────────────────────
    uncertain_rows = (
        edited_df[(edited_df["Review Required"] == True) & (edited_df["Include"] == True)]
        if "Review Required" in edited_df.columns
        else pd.DataFrame()
    )
    uncertain_n = len(uncertain_rows)

    ai_label = (
        f"Use AI to clean {uncertain_n} uncertain row{'s' if uncertain_n != 1 else ''}"
        if uncertain_n > 0 else "No uncertain rows"
    )
    ai_col, _, __ = st.columns([2, 6, 2])
    with ai_col:
        ai_clicked = st.button(
            ai_label, type="secondary", use_container_width=True,
            disabled=(uncertain_n == 0),
        )
    if ai_clicked:
        st.info("AI cleanup will be added in a future version.", icon="ℹ️")
```

- [ ] **Step 8: Ensure `build_intake_dataframe` handles the new columns**

Open `src/intake.py`. In `build_intake_dataframe`, the column guarantee loop already fills missing columns with `""`. Since `ALL_COLUMNS` now includes `"AI Category Confidence"` and `"Category Source"`, they will be covered. No change needed — but verify:

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj"
python3 -c "
from src.intake import build_intake_dataframe
from src.intake_schema import ALL_COLUMNS
df = build_intake_dataframe([], [])
assert 'AI Category Confidence' in df.columns
assert 'Category Source' in df.columns
print('build_intake_dataframe columns OK')
"
```
Expected: `build_intake_dataframe columns OK`

- [ ] **Step 9: Syntax check app.py**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj"
python3 -c "
import ast
with open('app.py') as f: src = f.read()
ast.parse(src)
print('app.py syntax OK')
"
```
Expected: `app.py syntax OK`

- [ ] **Step 10: Smoke test all module imports**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj"
python3 -c "
from src.intake_schema import ALL_COLUMNS, CATEGORIES, make_base_row
from src.document_parser import parse_pdf_rows
from src.category_ai import suggest_categories_batch, _normalise_category
from src.ai_extraction import extract_products_from_pdf_with_ai
from src.confidence import apply_confidence_checks
from src.intake import build_intake_dataframe
print('All imports OK')
print('CATEGORIES:', CATEGORIES)
print('New columns:', [c for c in ALL_COLUMNS if c in ('AI Category Confidence', 'Category Source')])
"
```
Expected:
```
All imports OK
CATEGORIES: ['Chair', 'Sofa', 'Paint', 'Fabric', 'Table', 'Lighting', 'Plumbing', 'Hardware', 'Rug', 'Artwork', 'Mirror', 'Appliance', 'Accessories', 'Other']
New columns: ['AI Category Confidence', 'Category Source']
```

- [ ] **Step 11: Run full test suite**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj"
python3 -m pytest tests/ -v
```
Expected: All tests PASS.

- [ ] **Step 12: Commit**

```bash
git -C "/Users/finncase/Desktop/Dev/SCH data input proj" add app.py src/
git -C "/Users/finncase/Desktop/Dev/SCH data input proj" commit -m "feat: wire document parser and category AI into app — v0.5"
```

---

## Self-Review Checklist

**Spec coverage:**

| Spec section | Covered by task |
|---|---|
| `intake_schema.py`: remove Couch, add 2 columns | Task 1 |
| `category_ai.py`: `_normalise_category`, `suggest_categories_batch` | Task 2 |
| `document_parser.py`: skip-row, serial/model, table/text parse | Task 3 |
| `ai_extraction.py`: parser context, new columns, updated prompt | Task 4 |
| `app.py`: inline note, toggle, button, column config, v0.5 | Task 5 |
| Parser first always / AI only when toggle on | Task 4 + Task 5 Step 4 |
| Category Source = Manual / AI Suggested / Unknown | Task 2 (normalise) + Task 5 Step 7 |
| Couch alias → Sofa | Task 2 (`_CATEGORY_ALIASES`) |
| Broad serial/model label recognition | Task 3 (`_SKU_LABEL_RE`) |
| Table unchanged if AI fails | Task 5 Step 7 (error path sets `cat_ai_error`, no rerun with changed df) |
| No invented fields | Task 3 code + Task 4 prompt instruction |

**Placeholder scan:** None found.

**Type consistency:**
- `suggest_categories_batch(rows: list[dict], row_indices: list[int])` — called in Task 5 Step 7 as `suggest_categories_batch(blank_rows, blank_cat_indices)` ✓
- `parse_pdf_rows(pdf_file, project, room, supplier, notes)` — called in Task 5 Step 4 with same signature ✓
- `_build_prompt(..., structured_rows: list[dict] | None = None)` — called in Task 4 Step 4 with `structured_rows` ✓
- `_OUTPUT_COLUMNS` updated in Task 4 Step 1 before referenced in Step 5 ✓
