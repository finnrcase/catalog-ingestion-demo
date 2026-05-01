# Data Ingestion & Enrichment Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve product data quality through notes cleaning, 6 new Programa-ready schema fields, and a new manufacturer website lookup module that runs before the Brave Search fallback.

**Architecture:** New `src/manufacturer_lookup.py` (Approach B — isolated module, no pipeline refactor). `product_enrichment.py` calls it as Phase 1; existing Brave Search is Phase 2. Schema extended in-place. Notes cleaning applied across all ingestion paths.

**Tech Stack:** Python 3.11+, anthropic SDK, httpx, PyMuPDF (fitz), html2text, pytest

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `src/notes.py` | Modify | Add `clean_notes_text()`, keep alias |
| `src/intake_schema.py` | Modify | Add 6 new columns + make_base_row defaults |
| `src/dimensions.py` | Modify | Add `parse_dimensions()`, `format_dimensions_string()`, `_parse_fraction()` |
| `src/ai_extraction.py` | Modify | New field map entries, prompt additions, post-process in `_item_to_row()` |
| `src/document_parser.py` | Modify | Apply notes cleaning + dim parsing after row build |
| `src/manufacturer_lookup.py` | Create | Brand-domain map, site: searches, PDF fetch, confidence scoring, Claude extraction |
| `src/product_enrichment.py` | Modify | Phase 1 wiring, updated `_qualifies()`, `_apply_enrichment()` signature, `_sync_dimensions()` |
| `src/enrichment_debug.py` | Modify | Add manufacturer lookup fields to trace |
| `tests/test_notes.py` | Modify | Add `clean_notes_text()` tests |
| `tests/test_dimensions.py` | Modify | Add `parse_dimensions()` + `format_dimensions_string()` tests |
| `tests/test_manufacturer_lookup.py` | Create | Unit tests for new module |
| `tests/test_product_enrichment.py` | Modify | Tests for `_MfrLookup` suffix, merge rules, new fields |

---

### Task 1: Expand notes cleaning in `src/notes.py`

**Files:**
- Modify: `src/notes.py`
- Test: `tests/test_notes.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_notes.py`:

```python
from src.notes import clean_notes_text, remove_notes_row_prefix


def test_clean_notes_strips_line_prefix():
    assert clean_notes_text("Line 1. Salesperson: Tom Gross, tomgross@optonline.net") == \
        "Salesperson: Tom Gross, tomgross@optonline.net"


def test_clean_notes_strips_line_colon():
    assert clean_notes_text("Line 2: Stainless steel finish") == "Stainless steel finish"


def test_clean_notes_strips_line_dash():
    assert clean_notes_text("Line 3 - Confirm room") == "Confirm room"


def test_clean_notes_strips_item_prefix():
    assert clean_notes_text("Item 1. Verify dimensions") == "Verify dimensions"


def test_clean_notes_strips_item_paren():
    assert clean_notes_text("Item 2) Check color") == "Check color"


def test_clean_notes_strips_bare_hash_number():
    assert clean_notes_text("#1. Verify dimensions") == "Verify dimensions"


def test_clean_notes_preserves_price():
    assert clean_notes_text("$1,200 quoted price") == "$1,200 quoted price"


def test_clean_notes_preserves_lead_time_range():
    # "12 - 14" — the number is followed by another number, not a letter
    assert clean_notes_text("12 - 14 week lead time") == "12 - 14 week lead time"


def test_clean_notes_preserves_time():
    assert clean_notes_text("3:00 PM delivery") == "3:00 PM delivery"


def test_clean_notes_preserves_dimension_number():
    assert clean_notes_text("36 inch clearance required") == "36 inch clearance required"


def test_clean_notes_preserves_model_number():
    assert clean_notes_text("SCN60PA1SU spec requested") == "SCN60PA1SU spec requested"


def test_remove_notes_row_prefix_still_works():
    # backward-compat alias
    assert remove_notes_row_prefix("Row 7. Confirm finish") == "Confirm finish"
    assert remove_notes_row_prefix("12 - Verify dimensions") == "Verify dimensions"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_notes.py -v 2>&1 | head -40
```

Expected: several FAIL with `ImportError: cannot import name 'clean_notes_text'`

- [ ] **Step 3: Implement `clean_notes_text()` in `src/notes.py`**

Replace the entire file:

```python
from __future__ import annotations

import re

# Matches: "Line 1." / "Line 2:" / "Line 3 -" / "Item 1." / "Item 2)"
#          "Row #1 -" / "#1." / bare "1." only when followed by a letter or "["
# The lookahead (?=[A-Za-z\[\(\"']) prevents stripping lead-time ranges like "12 - 14"
_ROW_PREFIX_RE = re.compile(
    r"^\s*(?:(?:line|item|row)\s+)?#?\d{1,3}\s*[-–—:.)]\s+(?=[A-Za-z\[\(\"'])",
    re.IGNORECASE,
)


def clean_notes_text(text: object) -> str:
    """Strip leading line/item/row number prefixes from Notes strings."""
    s = str(text or "").strip()
    return _ROW_PREFIX_RE.sub("", s, count=1).strip()


def remove_notes_row_prefix(value: object) -> str:
    """Backward-compatible alias for clean_notes_text."""
    return clean_notes_text(value)
```

- [ ] **Step 4: Run tests — all must pass**

```bash
pytest tests/test_notes.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/notes.py tests/test_notes.py
git commit -m "feat: add clean_notes_text() covering Line/Item/Row/bare-number prefixes"
```

---

### Task 2: Add 6 new columns to `src/intake_schema.py`

**Files:**
- Modify: `src/intake_schema.py`

- [ ] **Step 1: Add to `ALL_COLUMNS` (after `"Dimensions"`)**

Open `src/intake_schema.py`. In `ALL_COLUMNS`, after `"Dimensions"` insert:

```python
    "Width (in)",
    "Height (in)",
    "Depth (in)",
    "Length (in)",
    "Material",
    "Lead Time",
```

- [ ] **Step 2: Add defaults to `make_base_row()`**

In `make_base_row()`, after `"Dimensions": ""` add:

```python
        "Width (in)":       None,
        "Height (in)":      None,
        "Depth (in)":       None,
        "Length (in)":      None,
        "Material":         "",
        "Lead Time":        "",
```

- [ ] **Step 3: Verify schema smoke test**

```bash
python -c "
from src.intake_schema import make_base_row, ALL_COLUMNS
r = make_base_row()
for col in ['Width (in)', 'Height (in)', 'Depth (in)', 'Length (in)', 'Material', 'Lead Time']:
    assert col in r, f'Missing: {col}'
    assert col in ALL_COLUMNS, f'Not in ALL_COLUMNS: {col}'
print('OK — all 6 new columns present')
"
```

Expected: `OK — all 6 new columns present`

- [ ] **Step 4: Commit**

```bash
git add src/intake_schema.py
git commit -m "feat: add Width/Height/Depth/Length/Material/Lead Time to schema"
```

---

### Task 3: Add `parse_dimensions()` and `format_dimensions_string()` to `src/dimensions.py`

**Files:**
- Modify: `src/dimensions.py`
- Test: `tests/test_dimensions.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_dimensions.py`:

```python
from src.dimensions import parse_dimensions, format_dimensions_string


def test_parse_dimensions_whd_labels():
    r = parse_dimensions('24"W x 23"D x 34"H')
    assert r == {"width_in": 24.0, "height_in": 34.0, "depth_in": 23.0, "length_in": None}


def test_parse_dimensions_with_fraction():
    r = parse_dimensions('29 7/8"W x 23 1/2"D x 11 7/8"H')
    assert abs(r["width_in"] - 29.875) < 0.001
    assert abs(r["depth_in"] - 23.5) < 0.001
    assert abs(r["height_in"] - 11.875) < 0.001
    assert r["length_in"] is None


def test_parse_dimensions_with_length():
    r = parse_dimensions('36"W x 34"H x 24"D x 72"L')
    assert r["width_in"] == 36.0
    assert r["height_in"] == 34.0
    assert r["depth_in"] == 24.0
    assert r["length_in"] == 72.0


def test_parse_dimensions_word_labels():
    r = parse_dimensions("Width 36, Height 34.5, Depth 24")
    assert r["width_in"] == 36.0
    assert r["height_in"] == 34.5
    assert r["depth_in"] == 24.0


def test_parse_dimensions_bare_numbers_returns_none():
    # Bare numbers without axis labels are too ambiguous — never infer
    r = parse_dimensions("36 x 24 x 34")
    assert all(v is None for v in r.values())


def test_parse_dimensions_empty():
    r = parse_dimensions("")
    assert all(v is None for v in r.values())


def test_format_dimensions_three_axes():
    s = format_dimensions_string(24.0, 34.0, 23.0, None)
    assert s == "24 in W x 34 in H x 23 in D"


def test_format_dimensions_omits_none():
    s = format_dimensions_string(24.0, None, None, None)
    assert s == "24 in W"


def test_format_dimensions_with_length():
    s = format_dimensions_string(36.0, 34.0, 24.0, 72.0)
    assert s == "36 in W x 34 in H x 24 in D x 72 in L"


def test_format_dimensions_fractional():
    s = format_dimensions_string(29.875, 11.875, 23.5, None)
    assert "29.875" in s and "11.875" in s and "23.5" in s


def test_roundtrip_parse_format():
    original = '24"W x 34"H x 23"D'
    parsed = parse_dimensions(original)
    rebuilt = format_dimensions_string(
        parsed["width_in"], parsed["height_in"], parsed["depth_in"], parsed["length_in"]
    )
    reparsed = parse_dimensions(rebuilt)
    assert reparsed["width_in"] == 24.0
    assert reparsed["height_in"] == 34.0
    assert reparsed["depth_in"] == 23.0
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_dimensions.py -v 2>&1 | head -20
```

Expected: FAIL with `ImportError: cannot import name 'parse_dimensions'`

- [ ] **Step 3: Add `_parse_fraction()`, `parse_dimensions()`, `format_dimensions_string()` to `src/dimensions.py`**

Append after the existing `has_complete_3d_dimensions()` function:

```python
# ── Per-axis parsing and formatting ───────────────────────────────────────────


def _parse_fraction(s: str) -> float | None:
    """Convert a dimension value string (possibly mixed fraction) to float."""
    s = s.strip()
    m = re.match(r"^(\d+)\s+(\d+)/(\d+)$", s)
    if m:
        return float(m.group(1)) + float(m.group(2)) / float(m.group(3))
    m = re.match(r"^(\d+)/(\d+)$", s)
    if m:
        return float(m.group(1)) / float(m.group(2))
    try:
        return float(s)
    except ValueError:
        return None


def parse_dimensions(s: object) -> dict:
    """
    Parse a dimension string into per-axis float values.

    Returns dict with keys width_in, height_in, depth_in, length_in.
    Each value is float or None. Never infers from bare numbers without axis labels.
    """
    labeled = extract_labeled_dimensions(s)
    return {
        "width_in":  _parse_fraction(labeled["width"])  if labeled["width"]  else None,
        "height_in": _parse_fraction(labeled["height"]) if labeled["height"] else None,
        "depth_in":  _parse_fraction(labeled["depth"])  if labeled["depth"]  else None,
        "length_in": _parse_fraction(labeled["length"]) if labeled["length"] else None,
    }


def _fmt_float(v: float) -> str:
    """Format a float: integer representation when whole, else strip trailing zeros."""
    if v == int(v):
        return str(int(v))
    return f"{v:.3f}".rstrip("0")


def format_dimensions_string(
    width_in: "float | None",
    height_in: "float | None",
    depth_in: "float | None",
    length_in: "float | None",
) -> str:
    """Build human-readable dimension string from per-axis floats. Omits None axes."""
    parts = []
    if width_in is not None:
        parts.append(f"{_fmt_float(width_in)} in W")
    if height_in is not None:
        parts.append(f"{_fmt_float(height_in)} in H")
    if depth_in is not None:
        parts.append(f"{_fmt_float(depth_in)} in D")
    if length_in is not None:
        parts.append(f"{_fmt_float(length_in)} in L")
    return " x ".join(parts)
```

- [ ] **Step 4: Run tests — all must pass**

```bash
pytest tests/test_dimensions.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/dimensions.py tests/test_dimensions.py
git commit -m "feat: add parse_dimensions() and format_dimensions_string() to dimensions.py"
```

---

### Task 4: Update `src/ai_extraction.py` — new fields, prompt, post-processing

**Files:**
- Modify: `src/ai_extraction.py`

- [ ] **Step 1: Add new entries to `_FIELD_MAP`**

In `src/ai_extraction.py`, in the `_FIELD_MAP` dict, add after `"notes": "Notes"`:

```python
    "material":   "Material",
    "lead_time":  "Lead Time",
```

- [ ] **Step 2: Add new columns to `_OUTPUT_COLUMNS`**

In `_OUTPUT_COLUMNS`, add after `"Notes"`:

```python
    "Width (in)",
    "Height (in)",
    "Depth (in)",
    "Length (in)",
    "Material",
    "Lead Time",
```

- [ ] **Step 3: Update `_build_prompt()` — add material and lead_time extraction rules**

In `_build_prompt()`, in the `EXTRACTION RULES` numbered list, add after rule 9 (Room/Location):

```
17. Material: primary construction materials if explicitly stated in the document (e.g. "Stainless Steel", "Solid Oak", "Tempered Glass"). Leave empty string "" if not stated — do not infer from product name.
18. Lead Time: extract only when the document explicitly states a lead time (e.g. "8–10 weeks", "In stock", "Ships in 4 weeks"). Leave empty string "" if not stated.
```

In the `RESPONSE FORMAT` JSON example, add after `"notes"`:

```
    "material": "<primary construction materials or empty string>",
    "lead_time": "<lead time if explicitly stated or empty string>",
```

- [ ] **Step 4: Post-process in `_item_to_row()` — add imports, notes cleaning, dimension sync**

At the top of `src/ai_extraction.py`, add imports after existing imports:

```python
from src.notes import clean_notes_text
from src.dimensions import parse_dimensions, format_dimensions_string
```

In `_item_to_row()`, after the line `row["Room"] = _cleaned_room or default_room` and before the Status derivation, add:

```python
    # Clean notes text
    row["Notes"] = clean_notes_text(row.get("Notes", ""))

    # Initialize per-axis dimension fields
    for col in ["Width (in)", "Height (in)", "Depth (in)", "Length (in)"]:
        row.setdefault(col, None)

    # Sync: Dimensions string → per-axis fields
    dims = str(row.get("Dimensions", "") or "").strip()
    if dims:
        parsed = parse_dimensions(dims)
        for col, key in [
            ("Width (in)", "width_in"),
            ("Height (in)", "height_in"),
            ("Depth (in)", "depth_in"),
            ("Length (in)", "length_in"),
        ]:
            if row.get(col) is None and parsed.get(key) is not None:
                row[col] = parsed[key]
    elif any(row.get(c) is not None for c in ["Width (in)", "Height (in)", "Depth (in)", "Length (in)"]):
        row["Dimensions"] = format_dimensions_string(
            row.get("Width (in)"), row.get("Height (in)"),
            row.get("Depth (in)"), row.get("Length (in)"),
        )
```

- [ ] **Step 5: Add new column defaults in `extract_products_from_pdf_with_ai()`**

In the `_defaults` dict near the end of that function, add:

```python
        "Width (in)": None,
        "Height (in)": None,
        "Depth (in)": None,
        "Length (in)": None,
        "Material": "",
        "Lead Time": "",
```

- [ ] **Step 6: Smoke test — import check**

```bash
python -c "from src.ai_extraction import _FIELD_MAP, _OUTPUT_COLUMNS; assert 'material' in _FIELD_MAP; assert 'Width (in)' in _OUTPUT_COLUMNS; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add src/ai_extraction.py
git commit -m "feat: add Material/Lead Time to AI extraction prompt and dimension sync in _item_to_row"
```

---

### Task 5: Update `src/document_parser.py` — notes cleaning and dimension sync

**Files:**
- Modify: `src/document_parser.py`

- [ ] **Step 1: Add imports**

At the top of `src/document_parser.py`, add after existing imports:

```python
from src.notes import clean_notes_text
from src.dimensions import parse_dimensions
```

- [ ] **Step 2: Clean notes at entry point of `parse_pdf_rows()`**

In `parse_pdf_rows()`, as the first line of the function body before `raw = pdf_file.read()`, add:

```python
    notes = clean_notes_text(notes)
```

- [ ] **Step 3: Add `material` and `lead time` column recognition in `_parse_table_rows()`**

In `_parse_table_rows()`, after the `for key in ("brand", "manufacturer", "mfr"):` block, add:

```python
                for key in ("material", "materials"):
                    if key in col_map and col_map[key] < len(cells):
                        row["Material"] = cells[col_map[key]]
                        break

                for key in ("lead time", "lead_time", "leadtime", "delivery"):
                    if key in col_map and col_map[key] < len(cells):
                        row["Lead Time"] = cells[col_map[key]]
                        break
```

- [ ] **Step 4: Add dimension sync helper and call it after row build**

Add a module-level helper after `_compute_status()`:

```python
def _sync_dimensions_to_axes(row: dict) -> None:
    """Populate per-axis dimension fields from the Dimensions string in-place."""
    from src.dimensions import parse_dimensions
    dims = str(row.get("Dimensions", "") or "").strip()
    if not dims:
        return
    parsed = parse_dimensions(dims)
    for col, key in [
        ("Width (in)", "width_in"),
        ("Height (in)", "height_in"),
        ("Depth (in)", "depth_in"),
        ("Length (in)", "length_in"),
    ]:
        if row.get(col) is None and parsed.get(key) is not None:
            row[col] = parsed[key]
```

Then in `_row_from_line()`, before `return row`, add:

```python
    _sync_dimensions_to_axes(row)
```

And in `_parse_table_rows()`, before `rows.append(row)`, add:

```python
            _sync_dimensions_to_axes(row)
```

- [ ] **Step 5: Smoke test**

```bash
python -c "
from src.document_parser import parse_pdf_rows
import io, fitz
# Just confirm imports work cleanly
print('document_parser imports OK')
"
```

Expected: `document_parser imports OK`

- [ ] **Step 6: Commit**

```bash
git add src/document_parser.py
git commit -m "feat: apply notes cleaning and dimension sync in document_parser"
```

---

### Task 6: Create `src/manufacturer_lookup.py`

**Files:**
- Create: `src/manufacturer_lookup.py`

- [ ] **Step 1: Write the full module**

Create `src/manufacturer_lookup.py`:

```python
"""
Manufacturer website lookup for SCH DesignOps Intake.

Performs targeted site: searches for a product by brand + model number,
fetches the manufacturer page or spec sheet PDF, scores match confidence,
and extracts product fields via Claude Haiku.

Public API
----------
try_manufacturer_lookup(row: dict) -> ManufacturerLookupResult
    Attempts manufacturer lookup. Returns result with match details and
    extracted fields. Diagnostic-only — callers decide how to apply results.

ManufacturerLookupResult : dataclass
    attempted, lookup_url, match_found, match_confidence, source_type,
    fields_found, extracted
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

from src.brave_search import BRAVE_API_KEY, search_product_candidates

try:
    import html2text as _html2text
except ImportError:
    _html2text = None

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None

load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# Known luxury/residential design brands → official domain
_BRAND_DOMAINS: dict[str, str] = {
    "scotsman": "scotsman-ice.com",
    "wolf": "subzero-wolf.com",
    "sub-zero": "subzero-wolf.com",
    "subzero": "subzero-wolf.com",
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
    "article": "article.com",
    "cb2": "cb2.com",
    "crate and barrel": "crateandbarrel.com",
    "west elm": "westelm.com",
    "rejuvenation": "rejuvenation.com",
}

_SKIP_DOMAINS: frozenset = frozenset({
    "amazon.com", "amazon.ca", "ebay.com", "walmart.com", "target.com",
    "reddit.com", "pinterest.com", "yelp.com", "houzz.com", "trustpilot.com",
})

_DIM_TERMS_RE = re.compile(
    r"\b(width|height|depth|length|w\s*[×x]\s*[hd]"
    r"|\d+\s*[\"']\s*[whdWHD]|\d+\s+[whdWHD]\b)\b",
    re.IGNORECASE,
)


@dataclass
class ManufacturerLookupResult:
    attempted: bool = False
    lookup_url: str | None = None
    match_found: bool = False
    match_confidence: str = "none"   # "high" | "medium" | "low" | "none"
    source_type: str = "none"        # "product_page" | "spec_sheet" | "search_result" | "none"
    fields_found: list[str] = field(default_factory=list)
    extracted: dict = field(default_factory=dict)


# ── Domain utilities ───────────────────────────────────────────────────────────


def _extract_domain(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def _domain_matches(domain: str, candidate: str) -> bool:
    return domain == candidate or domain.endswith("." + candidate)


# ── Brand → domain resolution ──────────────────────────────────────────────────


def _resolve_manufacturer_domain(brand: str) -> str | None:
    """Return official domain for brand, or None if not resolvable."""
    slug = brand.lower().strip()

    if slug in _BRAND_DOMAINS:
        return _BRAND_DOMAINS[slug]

    # Partial match (e.g. "Scotsman Ice" contains "scotsman")
    for key, domain in _BRAND_DOMAINS.items():
        if key in slug or slug in key:
            return domain

    # Brave fallback for unknown brands
    if not BRAVE_API_KEY:
        return None

    results = search_product_candidates(f"{brand} official website", brand)
    for r in results[:2]:
        domain = _extract_domain(r.url)
        if domain and not any(_domain_matches(domain, s) for s in _SKIP_DOMAINS):
            return domain

    return None


# ── Search query builder ───────────────────────────────────────────────────────


def _build_search_queries(brand: str, model: str, domain: str | None) -> list[str]:
    """Return ordered list of search queries to try, stopping on first confident hit."""
    queries = []
    if domain:
        queries.append(f"site:{domain} {model}")
        queries.append(f"site:{domain} {model} spec sheet")
    queries.append(f"{brand} {model} specifications")
    return queries


# ── Page fetching ──────────────────────────────────────────────────────────────


def _fetch_page_content(url: str) -> tuple[str, str]:
    """
    Fetch URL and return (text_content, source_type).
    source_type is "spec_sheet" for PDFs, "product_page" for HTML.
    Returns ("", "none") on any failure.
    """
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SCH-Intake/1.0)"},
            timeout=12,
            follow_redirects=True,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "").lower()
        is_pdf = url.lower().rstrip("?#").endswith(".pdf") or "application/pdf" in content_type

        if is_pdf:
            try:
                import fitz
                doc = fitz.open(stream=resp.content, filetype="pdf")
                text = "\n".join(page.get_text("text") for page in doc)
                doc.close()
                return text[:8000].strip(), "spec_sheet"
            except Exception:
                return "", "none"

        if _html2text is not None:
            h = _html2text.HTML2Text()
            h.ignore_links = True
            h.ignore_images = True
            text = h.handle(resp.text)
        else:
            text = re.sub(r"<[^>]+>", " ", resp.text)
            text = re.sub(r"\s{2,}", " ", text)

        return text[:6000].strip(), "product_page"
    except Exception:
        return "", "none"


# ── Match confidence scoring ───────────────────────────────────────────────────


def _score_match(
    page_text: str,
    model: str,
    url: str,
    mfr_domain: str | None,
) -> tuple[str, str]:
    """
    Return (match_confidence, source_type_hint).
    match_confidence: "high" | "medium" | "low" | "none"
    """
    if not page_text or not model:
        return "none", "none"

    text_upper = page_text.upper()
    model_upper = model.upper()
    exact_match = model_upper in text_upper

    if not exact_match:
        # Partial: first 6 chars match (minimum meaningful prefix)
        if len(model) >= 4 and model_upper[:6] in text_upper:
            return "low", "search_result"
        return "none", "none"

    domain = _extract_domain(url)
    is_mfr = bool(mfr_domain and _domain_matches(domain, mfr_domain))
    has_dims = bool(_DIM_TERMS_RE.search(page_text))
    is_pdf_url = url.lower().rstrip("?#").endswith(".pdf")
    source_hint = "spec_sheet" if is_pdf_url else ("product_page" if is_mfr else "search_result")

    # Manufacturer source is strongly preferred
    if is_mfr and has_dims:
        return "high", source_hint
    if is_mfr:
        return "medium", source_hint
    # Trusted non-manufacturer with dimensions
    if has_dims:
        return "medium", "search_result"
    return "low", source_hint


# ── Claude field extraction ────────────────────────────────────────────────────


def _extract_fields_with_claude(page_text: str, brand: str, model: str) -> dict:
    """Extract product fields from page text via Claude Haiku. Returns {} on failure."""
    if not ANTHROPIC_API_KEY or _anthropic is None:
        return {}
    try:
        client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = (
            f"Extract product specification data for {brand} model {model} "
            "from the page text below.\n\n"
            "Return ONLY a JSON object with these keys (empty string \"\" if not found):\n"
            '{"Product Name": "", "Dimensions": "", "Finish / Color": "", '
            '"Product Category": "", "Material": "", "Lead Time": ""}\n\n'
            "Rules:\n"
            "- Dimensions: include W/H/D labels (e.g. '24\"W x 34\"H x 23\"D'). "
            "Return \"\" if any axis is missing.\n"
            "- Material: primary construction materials only (e.g. 'Stainless Steel'). "
            "\"\" if not stated.\n"
            "- Lead Time: only from explicit text. \"\" if not stated.\n"
            "- Product Category must be one of: Paint/Wallpaper, Stone/Tile, Seating, "
            "Hardware, Flooring, Tables, Gym Equipment, Fabrics/Pillows, Lighting, Rugs, "
            "Mirrors, Beds/Mattresses, Dressers/Drawers/Storage, Appliances, Accessories, "
            "Artwork, Bedding/Linens/Bath Linens.\n"
            "- Never invent values not present in the text.\n"
            f"\nPAGE TEXT:\n---\n{page_text[:6000]}\n---"
        )
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        text = re.sub(r"```(?:json)?\s*|```", "", text).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return {}
        return json.loads(text[start : end + 1])
    except Exception:
        return {}


# ── Main public entry point ────────────────────────────────────────────────────


def try_manufacturer_lookup(row: dict) -> ManufacturerLookupResult:
    """
    Attempt manufacturer site lookup for a product row.

    Tries up to 3 ordered searches (site: manufacturer searches first,
    then open search). Stops early on high or medium confidence.
    Returns ManufacturerLookupResult with diagnostic metadata and extracted fields.
    """
    brand = str(row.get("Brand") or "").strip()
    model = str(row.get("Model/SKU") or "").strip()

    if not brand or not model:
        return ManufacturerLookupResult(attempted=False)

    mfr_domain = _resolve_manufacturer_domain(brand)
    queries = _build_search_queries(brand, model, mfr_domain)

    best_low: ManufacturerLookupResult | None = None

    for query in queries:
        results = search_product_candidates(query, brand)
        if not results:
            continue

        # Prefer manufacturer domain when sorting candidates
        if mfr_domain:
            results = sorted(
                results,
                key=lambda r: _domain_matches(_extract_domain(r.url), mfr_domain),
                reverse=True,
            )

        for candidate in results[:2]:
            page_text, fetch_source_type = _fetch_page_content(candidate.url)
            if not page_text:
                continue

            confidence, score_source_type = _score_match(
                page_text, model, candidate.url, mfr_domain
            )
            source_type = (
                fetch_source_type if fetch_source_type != "none" else score_source_type
            )

            if confidence == "none":
                continue

            extracted = _extract_fields_with_claude(page_text, brand, model)
            result = ManufacturerLookupResult(
                attempted=True,
                lookup_url=candidate.url,
                match_found=confidence in ("high", "medium"),
                match_confidence=confidence,
                source_type=source_type,
                fields_found=[k for k, v in extracted.items() if v],
                extracted=extracted,
            )

            if confidence in ("high", "medium"):
                return result

            if best_low is None:
                best_low = result

    return best_low or ManufacturerLookupResult(attempted=True)
```

- [ ] **Step 2: Smoke test import**

```bash
python -c "
from src.manufacturer_lookup import (
    ManufacturerLookupResult, try_manufacturer_lookup,
    _resolve_manufacturer_domain, _score_match, _fetch_page_content,
)
print('manufacturer_lookup imports OK')
r = ManufacturerLookupResult()
assert r.attempted is False
print('dataclass OK')
"
```

Expected: both OK lines printed.

- [ ] **Step 3: Commit**

```bash
git add src/manufacturer_lookup.py
git commit -m "feat: add src/manufacturer_lookup.py — brand-domain lookup, site: search, confidence scoring"
```

---

### Task 7: Tests for `src/manufacturer_lookup.py`

**Files:**
- Create: `tests/test_manufacturer_lookup.py`

- [ ] **Step 1: Write tests**

Create `tests/test_manufacturer_lookup.py`:

```python
"""
Tests for manufacturer_lookup.py.
Network calls (Brave Search, httpx, Claude) are fully mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.manufacturer_lookup import (
    ManufacturerLookupResult,
    _build_search_queries,
    _extract_domain,
    _resolve_manufacturer_domain,
    _score_match,
    try_manufacturer_lookup,
)


# ── _extract_domain ────────────────────────────────────────────────────────────


def test_extract_domain_strips_www():
    assert _extract_domain("https://www.scotsman-ice.com/products/p1") == "scotsman-ice.com"


def test_extract_domain_no_www():
    assert _extract_domain("https://scotsman-ice.com/p") == "scotsman-ice.com"


# ── _resolve_manufacturer_domain ───────────────────────────────────────────────


def test_resolve_known_brand_exact():
    assert _resolve_manufacturer_domain("Scotsman") == "scotsman-ice.com"


def test_resolve_known_brand_case_insensitive():
    assert _resolve_manufacturer_domain("WOLF") == "subzero-wolf.com"
    assert _resolve_manufacturer_domain("sub-zero") == "subzero-wolf.com"


def test_resolve_known_brand_partial():
    # "Scotsman Ice" contains "scotsman"
    assert _resolve_manufacturer_domain("Scotsman Ice") == "scotsman-ice.com"


def test_resolve_unknown_brand_no_api_key():
    with patch("src.manufacturer_lookup.BRAVE_API_KEY", ""):
        result = _resolve_manufacturer_domain("Completely Unknown Brand XYZ")
    assert result is None


# ── _build_search_queries ──────────────────────────────────────────────────────


def test_build_queries_with_domain():
    qs = _build_search_queries("Scotsman", "SCN60PA1SU", "scotsman-ice.com")
    assert qs[0] == "site:scotsman-ice.com SCN60PA1SU"
    assert qs[1] == "site:scotsman-ice.com SCN60PA1SU spec sheet"
    assert any("specifications" in q for q in qs)


def test_build_queries_without_domain():
    qs = _build_search_queries("Unknown", "XYZ123", None)
    assert not any("site:" in q for q in qs)
    assert any("specifications" in q for q in qs)


# ── _score_match ───────────────────────────────────────────────────────────────


def test_score_match_high_mfr_with_dims():
    confidence, src = _score_match(
        page_text="SCN60PA1SU undercounter ice machine Width 15 Height 34 Depth 24",
        model="SCN60PA1SU",
        url="https://scotsman-ice.com/products/SCN60PA1SU",
        mfr_domain="scotsman-ice.com",
    )
    assert confidence == "high"


def test_score_match_medium_mfr_no_dims():
    confidence, src = _score_match(
        page_text="SCN60PA1SU undercounter ice machine product details",
        model="SCN60PA1SU",
        url="https://scotsman-ice.com/products/SCN60PA1SU",
        mfr_domain="scotsman-ice.com",
    )
    assert confidence == "medium"


def test_score_match_medium_non_mfr_with_dims():
    confidence, src = _score_match(
        page_text="SCN60PA1SU ice maker Width 15 Height 34 Depth 24",
        model="SCN60PA1SU",
        url="https://build.com/scotsman/SCN60PA1SU",
        mfr_domain="scotsman-ice.com",
    )
    assert confidence == "medium"
    assert src == "search_result"


def test_score_match_low_partial_model():
    confidence, src = _score_match(
        page_text="SCN60 series overview page",
        model="SCN60PA1SU",
        url="https://scotsman-ice.com/series/scn60",
        mfr_domain="scotsman-ice.com",
    )
    assert confidence == "low"


def test_score_match_none_model_absent():
    confidence, src = _score_match(
        page_text="Some unrelated appliance page",
        model="SCN60PA1SU",
        url="https://scotsman-ice.com/",
        mfr_domain="scotsman-ice.com",
    )
    assert confidence == "none"


def test_score_match_empty_page():
    confidence, src = _score_match("", "SCN60PA1SU", "https://scotsman-ice.com/", "scotsman-ice.com")
    assert confidence == "none"


# ── try_manufacturer_lookup ────────────────────────────────────────────────────


def test_try_lookup_no_brand():
    row = {"Brand": "", "Model/SKU": "SCN60PA1SU"}
    result = try_manufacturer_lookup(row)
    assert result.attempted is False
    assert result.match_confidence == "none"


def test_try_lookup_no_model():
    row = {"Brand": "Scotsman", "Model/SKU": ""}
    result = try_manufacturer_lookup(row)
    assert result.attempted is False


def test_try_lookup_no_api_key_returns_attempted():
    """Without BRAVE_API_KEY, search returns [] so result is attempted but no match."""
    with patch("src.manufacturer_lookup.search_product_candidates", return_value=[]):
        result = try_manufacturer_lookup({"Brand": "Scotsman", "Model/SKU": "SCN60PA1SU"})
    assert result.attempted is True
    assert result.match_found is False
    assert result.match_confidence == "none"


def test_try_lookup_high_confidence_returns_early():
    """Simulate a successful high-confidence manufacturer hit."""
    from src.brave_search import SearchResult
    fake_result = SearchResult(
        title="SCN60PA1SU Spec Sheet",
        url="https://scotsman-ice.com/products/SCN60PA1SU",
        description="Official product page",
        domain_score=90,
    )
    page_text = "SCN60PA1SU Built-In Pump Ice Machine Width 15 in Height 34 in Depth 24 in"

    with (
        patch("src.manufacturer_lookup.search_product_candidates", return_value=[fake_result]),
        patch("src.manufacturer_lookup._fetch_page_content", return_value=(page_text, "product_page")),
        patch("src.manufacturer_lookup._extract_fields_with_claude", return_value={
            "Product Name": "Scotsman SCN60PA1SU Built-In Pump Ice Machine",
            "Dimensions": '15"W x 34"H x 24"D',
            "Material": "Stainless Steel",
            "Lead Time": "",
            "Finish / Color": "",
            "Product Category": "Appliances",
        }),
    ):
        result = try_manufacturer_lookup({"Brand": "Scotsman", "Model/SKU": "SCN60PA1SU"})

    assert result.attempted is True
    assert result.match_found is True
    assert result.match_confidence == "high"
    assert result.lookup_url == "https://scotsman-ice.com/products/SCN60PA1SU"
    assert "Product Name" in result.fields_found
    assert result.extracted["Material"] == "Stainless Steel"
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_manufacturer_lookup.py -v
```

Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_manufacturer_lookup.py
git commit -m "test: add manufacturer_lookup unit tests with mocked network calls"
```

---

### Task 8: Wire manufacturer lookup into `src/product_enrichment.py`

**Files:**
- Modify: `src/product_enrichment.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_product_enrichment.py`:

```python
from src.product_enrichment import _qualifies, _apply_enrichment, _sync_dimensions


def test_qualifies_mfr_lookup_row_skipped():
    row = {**_base_qualifying_row(), "Source Type": "PDF_MfrLookup"}
    assert not _qualifies(row)


def test_apply_enrichment_uses_mfr_suffix():
    row = {**_base_qualifying_row(), "Source Type": "PDF"}
    extracted = {"Product Name": "Scotsman Ice Machine"}
    updated = _apply_enrichment(row, extracted, "https://scotsman-ice.com/p", 100,
                                source_suffix="_MfrLookup", match_confidence="high")
    assert updated["Source Type"] == "PDF_MfrLookup"


def test_apply_enrichment_never_overwrites_manual():
    row = {**_base_qualifying_row(), "Source Type": "Manual", "Product Name": "User Name"}
    extracted = {"Product Name": "AI Name"}
    updated = _apply_enrichment(row, extracted, "https://example.com", 90,
                                source_suffix="_MfrLookup", match_confidence="high")
    assert updated["Product Name"] == "User Name"


def test_apply_enrichment_overwrites_parser_extracted_at_medium_confidence():
    row = {**_base_qualifying_row(), "Source Type": "PDF", "Product Name": "Rough Parser Name"}
    extracted = {"Product Name": "Scotsman SCN60PA1SU Ice Machine"}
    updated = _apply_enrichment(row, extracted, "https://scotsman-ice.com/p", 100,
                                source_suffix="_MfrLookup", match_confidence="medium")
    assert updated["Product Name"] == "Scotsman SCN60PA1SU Ice Machine"
    assert "[Original Product Name: Rough Parser Name]" in updated.get("Notes", "")


def test_apply_enrichment_does_not_overwrite_with_low_confidence():
    row = {**_base_qualifying_row(), "Source Type": "PDF", "Product Name": "Existing Name"}
    extracted = {"Product Name": "Low Conf Name"}
    updated = _apply_enrichment(row, extracted, "https://example.com", 50,
                                source_suffix="_MfrLookup", match_confidence="low")
    assert updated["Product Name"] == "Existing Name"


def test_sync_dimensions_populates_axes_from_string():
    row = {"Dimensions": '24"W x 34"H x 23"D', "Width (in)": None, "Height (in)": None,
           "Depth (in)": None, "Length (in)": None}
    result = _sync_dimensions(row)
    assert result["Width (in)"] == 24.0
    assert result["Height (in)"] == 34.0
    assert result["Depth (in)"] == 23.0
    assert result["Length (in)"] is None


def test_sync_dimensions_builds_string_from_axes():
    row = {"Dimensions": "", "Width (in)": 24.0, "Height (in)": 34.0,
           "Depth (in)": 23.0, "Length (in)": None}
    result = _sync_dimensions(row)
    assert "24" in result["Dimensions"]
    assert "34" in result["Dimensions"]
    assert "23" in result["Dimensions"]
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_product_enrichment.py::test_qualifies_mfr_lookup_row_skipped \
       tests/test_product_enrichment.py::test_apply_enrichment_uses_mfr_suffix \
       tests/test_product_enrichment.py::test_sync_dimensions_populates_axes_from_string -v
```

Expected: FAIL (name not found or assertion error)

- [ ] **Step 3: Update `_ENRICHABLE_FIELDS` and `_qualifies()`**

In `src/product_enrichment.py`, replace `_ENRICHABLE_FIELDS`:

```python
_ENRICHABLE_FIELDS: list = [
    "Product Name",
    "Dimensions",
    "Finish / Color",
    "Product Category",
    "Product URL",
    "Material",
    "Lead Time",
]
```

Replace the `_qualifies()` function:

```python
def _qualifies(row: dict) -> bool:
    source = _str_val(row.get("Source Type", ""))
    if source == "URL":
        return False
    if source.endswith("_Enriched") or source.endswith("_MfrLookup"):
        return False
    if not _str_val(row.get("Brand")):
        return False
    if not _str_val(row.get("Model/SKU")):
        return False
    blank_or_incomplete = [
        f for f in _ENRICHABLE_FIELDS
        if not _str_val(row.get(f))
        or (f == "Dimensions" and not has_complete_3d_dimensions(_str_val(row.get(f))))
    ]
    return bool(blank_or_incomplete)
```

- [ ] **Step 4: Update `_apply_enrichment()` signature and merge logic**

Replace the entire `_apply_enrichment()` function:

```python
def _apply_enrichment(
    row: dict,
    extracted: dict,
    source_url: str,
    domain_score: int,
    source_suffix: str = "_Enriched",
    match_confidence: str = "medium",
) -> dict:
    """
    Apply extracted fields to a row copy. Fills blank fields; for manufacturer
    lookup (source_suffix="_MfrLookup") at medium+ confidence, also overwrites
    parser-extracted values. Never overwrites Manual/URL user-entered data.
    """
    updated = row.copy()
    is_mfr_lookup = source_suffix == "_MfrLookup"
    src_base = _str_val(updated.get("Source Type", ""))
    is_user_source = src_base.startswith(("Manual", "URL"))

    for field in _ENRICHABLE_FIELDS:
        if field == "Product URL":
            if not _str_val(updated.get("Product URL")):
                updated["Product URL"] = source_url
            continue

        if field == "Dimensions":
            dim_extracted = _str_val(extracted.get("Dimensions"))
            if dim_extracted:
                if has_complete_3d_dimensions(dim_extracted):
                    updated["Dimensions"] = dim_extracted
                else:
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

        value = _str_val(extracted.get(field))
        if not value:
            continue

        if field == "Product Category":
            value = _normalise_category(value)
        if not value:
            continue

        existing_val = _str_val(updated.get(field))

        if existing_val:
            if is_user_source:
                continue  # never overwrite Manual/URL data
            if is_mfr_lookup and match_confidence in ("high", "medium"):
                # Overwrite parser-extracted with manufacturer value; log conflict
                if existing_val != value:
                    n = _str_val(updated.get("Notes"))
                    conflict = f"[Original {field}: {existing_val}]"
                    if conflict not in n:
                        updated["Notes"] = f"{n} {conflict}".strip() if n else conflict
                updated[field] = value
            # else: don't overwrite with low-confidence or Brave results
        else:
            updated[field] = value

    # Legacy "materials" key → Material column if not already filled
    materials = _str_val(extracted.get("materials"))
    if materials and not _str_val(updated.get("Material")):
        updated["Material"] = materials

    # Source Type suffix
    original = _str_val(updated.get("Source Type"))
    already_done = original.endswith("_Enriched") or original.endswith("_MfrLookup")
    if original and not already_done:
        updated["Source Type"] = f"{original}{source_suffix}"
    elif not original:
        updated["Source Type"] = source_suffix.lstrip("_")

    # Confidence flagging
    if domain_score < MIN_CONF_SCORE:
        updated["Review Required"] = True
        updated["Suggested Action"] = "Enriched from low-confidence source — verify fields"

    return updated
```

- [ ] **Step 5: Add `_sync_dimensions()` helper**

Add after `_apply_enrichment()`:

```python
def _sync_dimensions(row: dict) -> dict:
    """Sync Dimensions string and per-axis columns after enrichment merge."""
    from src.dimensions import parse_dimensions, format_dimensions_string

    dims = _str_val(row.get("Dimensions"))
    per_axis_cols = ["Width (in)", "Height (in)", "Depth (in)", "Length (in)"]
    axis_keys = ["width_in", "height_in", "depth_in", "length_in"]

    if dims:
        parsed = parse_dimensions(dims)
        for col, key in zip(per_axis_cols, axis_keys):
            if row.get(col) is None and parsed.get(key) is not None:
                row[col] = parsed[key]
    elif any(row.get(c) is not None for c in per_axis_cols):
        row["Dimensions"] = format_dimensions_string(
            row.get("Width (in)"),
            row.get("Height (in)"),
            row.get("Depth (in)"),
            row.get("Length (in)"),
        )

    return row
```

- [ ] **Step 6: Wire Phase 1 into `enrich_row()`**

Replace the existing `enrich_row()` function:

```python
def enrich_row(row: dict) -> tuple[dict, str | None]:
    """
    Enrich a single row. Phase 1: manufacturer lookup. Phase 2: Brave Search fallback.

    Returns (updated_row, None) on success or graceful no-result.
    Returns (row_unchanged, error_string) only on unexpected exceptions.
    """
    try:
        brand = _str_val(row.get("Brand"))
        model = _str_val(row.get("Model/SKU"))

        # Phase 1 — Manufacturer lookup
        if brand and model:
            from src.manufacturer_lookup import try_manufacturer_lookup
            mfr_result = try_manufacturer_lookup(row)

            if mfr_result.match_confidence in ("high", "medium"):
                updated = _apply_enrichment(
                    row,
                    mfr_result.extracted,
                    mfr_result.lookup_url or "",
                    100,  # manufacturer source treated as top-score
                    source_suffix="_MfrLookup",
                    match_confidence=mfr_result.match_confidence,
                )
                updated = _sync_dimensions(updated)
                return updated, None

        # Phase 2 — Brave Search fallback
        query = _build_search_query(row)
        results = search_product_candidates(query, brand)

        if not results or results[0].domain_score < MIN_USE_SCORE:
            updated = row.copy()
            existing = _str_val(updated.get("Notes"))
            note = "[Enrichment: no confident source found]"
            if note not in existing:
                updated["Notes"] = f"{existing} {note}".strip() if existing else note
            return updated, None

        best = results[0]
        page_text = _fetch_page_text(best.url)

        if not page_text:
            updated = row.copy()
            existing = _str_val(updated.get("Notes"))
            domain = urllib.parse.urlparse(best.url).netloc or best.url[:50]
            note = f"[Enrichment: could not fetch {domain}]"
            if note not in existing:
                updated["Notes"] = f"{existing} {note}".strip() if existing else note
            return updated, None

        extracted = _extract_with_claude(page_text, row)
        updated = _apply_enrichment(row, extracted, best.url, best.domain_score)
        updated = _sync_dimensions(updated)
        return updated, None
    except Exception as exc:
        return row, str(exc)
```

- [ ] **Step 7: Run all new product enrichment tests**

```bash
pytest tests/test_product_enrichment.py -v
```

Expected: all PASS (new tests pass, existing tests still pass)

- [ ] **Step 8: Commit**

```bash
git add src/product_enrichment.py tests/test_product_enrichment.py
git commit -m "feat: wire manufacturer lookup as Phase 1 in product_enrichment, add _sync_dimensions"
```

---

### Task 9: Extend `src/enrichment_debug.py` with manufacturer lookup diagnostics

**Files:**
- Modify: `src/enrichment_debug.py`

- [ ] **Step 1: Add manufacturer lookup fields to `debug_enrich_row()`**

In `debug_enrich_row()`, in the `trace` dict initialisation, add:

```python
        "manufacturer_lookup_attempted": False,
        "manufacturer_lookup_url":       None,
        "manufacturer_match_found":      False,
        "manufacturer_match_confidence": "none",
        "manufacturer_source_type":      "none",
        "manufacturer_fields_found":     [],
```

After `# Gate 1: qualifies?` block (i.e. right before `# Gate 2: API keys`), add a Phase 1 block:

```python
    # ── Phase 1: Manufacturer lookup ──────────────────────────────────────────
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))
    if brand and model:
        from src.manufacturer_lookup import try_manufacturer_lookup
        mfr = try_manufacturer_lookup(row)
        trace["manufacturer_lookup_attempted"] = mfr.attempted
        trace["manufacturer_lookup_url"]       = mfr.lookup_url
        trace["manufacturer_match_found"]      = mfr.match_found
        trace["manufacturer_match_confidence"] = mfr.match_confidence
        trace["manufacturer_source_type"]      = mfr.source_type
        trace["manufacturer_fields_found"]     = mfr.fields_found
```

- [ ] **Step 2: Update `_disqualify_reason()` to handle `_MfrLookup`**

In `_disqualify_reason()`, replace:

```python
    if source.endswith("_Enriched"):
        return f"Source Type is '{source}' — already enriched"
```

with:

```python
    if source.endswith("_Enriched") or source.endswith("_MfrLookup"):
        return f"Source Type is '{source}' — already enriched"
```

- [ ] **Step 3: Smoke test**

```bash
python -c "
from src.enrichment_debug import debug_enrich_row
row = {'Brand': 'Scotsman', 'Model/SKU': 'SCN60PA1SU', 'Source Type': 'PDF',
       'Product Name': '', 'Dimensions': '', 'Finish / Color': '', 'Product Category': '',
       'Product URL': '', 'Notes': '', 'Review Required': False, 'Suggested Action': ''}
trace = debug_enrich_row(row)
assert 'manufacturer_lookup_attempted' in trace
print('enrichment_debug manufacturer fields OK')
"
```

Expected: prints OK line

- [ ] **Step 4: Commit**

```bash
git add src/enrichment_debug.py
git commit -m "feat: add manufacturer lookup diagnostics to enrichment_debug trace"
```

---

### Task 10: Full test suite verification

- [ ] **Step 1: Run the full test suite**

```bash
pytest tests/ -v 2>&1 | tail -30
```

Expected: no regressions; all previously passing tests still pass.

- [ ] **Step 2: Fix any failures before proceeding**

If any test fails due to the schema change (new columns now in make_base_row), update test fixtures to include the new columns with their defaults (`None` for per-axis, `""` for Material/Lead Time).

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "chore: verify all tests pass after data ingestion enrichment improvements"
```

---

## Self-Review Notes

**Spec coverage check:**

| Spec requirement | Covered by task |
|---|---|
| `clean_notes_text()` stripping Line/Item/Row/bare-number prefixes | Task 1 |
| Applied at document_parser, ai_extraction, product_enrichment | Tasks 4, 5 (applied at parser entry); enrichment uses clean fields |
| 6 new schema columns in ALL_COLUMNS + make_base_row | Task 2 |
| `parse_dimensions()` + `format_dimensions_string()` | Task 3 |
| AI extraction: Material + Lead Time fields | Task 4 |
| Document parser: table header recognition for material/lead time | Task 5 |
| New `src/manufacturer_lookup.py` with brand-domain map | Task 6 |
| Ordered site: searches, stop on high/medium | Task 6 |
| PDF spec sheet detection via Content-Type / URL | Task 6 |
| Match confidence: high/medium/low/none rules | Task 6 |
| `ManufacturerLookupResult` dataclass, diagnostic-only | Task 6 |
| Tests for manufacturer_lookup | Task 7 |
| Phase 1 in `enrich_row()`, Phase 2 unchanged | Task 8 |
| `_qualifies()` skips `_MfrLookup` suffix | Task 8 |
| `_apply_enrichment()` signature + merge rules | Task 8 |
| `_sync_dimensions()` after every merge | Task 8 |
| Source type provenance: `_MfrLookup` vs `_Enriched` | Task 8 |
| enrichment_debug.py manufacturer lookup diagnostics | Task 9 |
| Full test suite passing | Task 10 |

**Type consistency:** `ManufacturerLookupResult` defined in Task 6 and used in Tasks 7, 8, 9 consistently. `_sync_dimensions()` defined in Task 8 and called within same module. `parse_dimensions()`/`format_dimensions_string()` defined in Task 3, imported in Tasks 4, 5, 8.

**No placeholders:** all steps contain complete code.
