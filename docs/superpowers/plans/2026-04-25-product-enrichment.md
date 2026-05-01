# Product Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After intake creates rows, automatically search Brave Web Search for official manufacturer pages, fetch them with httpx, extract missing product fields with Claude Haiku, and fill blank-only fields before Programa review.

**Architecture:** `src/brave_search.py` handles the Brave API and domain scoring in isolation. `src/product_enrichment.py` orchestrates search → fetch → Claude extract → blank-only fill. `app.py` runs enrichment automatically after each intake path and exposes a manual re-run button.

**Tech Stack:** Brave Search API, httpx, html2text, Claude Haiku (`claude-haiku-4-5-20251001`), existing Anthropic SDK, python-dotenv.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/brave_search.py` | Brave API client, domain scoring, `SearchResult` |
| Create | `src/product_enrichment.py` | Orchestration: query → search → fetch → extract → fill |
| Create | `tests/test_brave_search.py` | Domain scoring + missing-key guard tests |
| Create | `tests/test_product_enrichment.py` | Qualifying, query, apply-enrichment, orchestration tests |
| Modify | `requirements.txt` | Add `httpx`, `html2text` |
| Modify | `.env.example` | Add `BRAVE_API_KEY` |
| Modify | `app.py` | Session state, auto-trigger, enrichment block, re-run button, caption |

---

## Task 1: Dependencies + `src/brave_search.py` — domain scoring

**Files:**
- Modify: `requirements.txt`
- Create: `src/brave_search.py`
- Create: `tests/test_brave_search.py`

- [ ] **Step 1: Add dependencies to requirements.txt**

Open `requirements.txt` and add two lines:

```
httpx>=0.27.0
html2text>=2024.2.26
```

Final file:
```
streamlit>=1.31.0
pandas>=2.0.0
playwright>=1.40.0
python-dotenv>=1.0.0
anthropic>=0.25.0
pymupdf>=1.23.0
httpx>=0.27.0
html2text>=2024.2.26
```

- [ ] **Step 2: Install the new dependencies**

```bash
pip3 install httpx html2text -q
```

Expected: installs cleanly, no errors.

- [ ] **Step 3: Write failing tests for domain scoring**

Create `tests/test_brave_search.py`:

```python
import pytest
import src.brave_search as bs


def test_score_domain_brand_in_domain():
    # "wolf" slug inside "wolfappliance.com" → ≥ 80
    score = bs._score_domain("https://www.wolfappliance.com/products/MDD30TS", "Wolf")
    assert score >= 80


def test_score_domain_preferred_domain_no_brand():
    # rh.com is preferred but brand "Herman Miller" is not in domain → 50+20=70
    score = bs._score_domain("https://www.rh.com/catalog/product/", "Herman Miller")
    assert 60 <= score <= 80


def test_score_domain_skip_domain():
    # amazon.com → penalised to < 20
    score = bs._score_domain("https://www.amazon.com/dp/B0001234", "Wolf")
    assert score < 20


def test_score_domain_neutral():
    # unknown retailer, not preferred, not skip → near base 50
    score = bs._score_domain("https://www.some-random-shop.com/product", "Wolf")
    assert 30 <= score <= 70


def test_score_domain_brand_and_preferred():
    # subzero-wolf.com contains "wolf" and is in preferred list → ≥ 90
    score = bs._score_domain("https://www.subzero-wolf.com/products/ID-36R", "Wolf")
    assert score >= 90


def test_search_product_candidates_missing_key(monkeypatch):
    monkeypatch.setattr(bs, "BRAVE_API_KEY", "")
    results = bs.search_product_candidates("Wolf MDD30TS specifications", "Wolf")
    assert results == []
```

- [ ] **Step 4: Run tests — expect FAIL (module not yet created)**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_brave_search.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError` or similar — module doesn't exist yet.

- [ ] **Step 5: Create `src/brave_search.py`**

```python
"""
Brave Web Search API client for SCH DesignOps product enrichment.

Public API
----------
BRAVE_API_KEY : str
    Loaded from env. Empty string if not configured.

SearchResult : dataclass
    title, url, description, domain_score (int 0-100).

search_product_candidates(query, brand="") -> list[SearchResult]
    Returns up to 5 results ranked by domain trustworthiness.
    Returns [] if BRAVE_API_KEY is missing or the API call fails.
"""

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

BRAVE_API_KEY: str = os.getenv("BRAVE_API_KEY", "")

_PREFERRED_DOMAINS: frozenset = frozenset({
    "subzero-wolf.com", "wolfappliance.com", "miele.com", "mieleusa.com",
    "kohler.com", "kallista.com", "brizo.com", "dornbracht.com",
    "waterworks.com", "rh.com", "restorationhardware.com", "article.com",
    "rejuvenation.com", "cb2.com", "crateandbarrel.com", "westelm.com",
    "visualcomfort.com", "circalighting.com", "hudsonvalleylighting.com",
    "scotsman-ice.com", "thermador.com", "jennair.com", "vikingrange.com",
    "bertazzoni.com", "ilve.com", "lacanche.com", "dacor.com",
    "monogram.com", "bosch-home.com", "gaggenau.com",
})

_SKIP_DOMAINS: frozenset = frozenset({
    "amazon.com", "amazon.ca", "amazon.co.uk", "ebay.com", "walmart.com",
    "target.com", "homedepot.com", "lowes.com", "reddit.com", "pinterest.com",
    "yelp.com", "houzz.com", "trustpilot.com", "sitejabber.com",
})


@dataclass
class SearchResult:
    title: str
    url: str
    description: str
    domain_score: int


def _extract_domain(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
        return netloc.lstrip("www.")
    except Exception:
        return ""


def _score_domain(url: str, brand: str) -> int:
    domain = _extract_domain(url)
    brand_slug = brand.lower().replace(" ", "").replace("-", "")

    if any(skip in domain for skip in _SKIP_DOMAINS):
        return max(0, 50 - 60)  # → 0 (clamped)

    score = 50
    if brand_slug and brand_slug in domain.replace("-", "").replace(".", ""):
        score += 40
    if any(pref in domain for pref in _PREFERRED_DOMAINS):
        score += 20
    return min(100, max(0, score))


def search_product_candidates(query: str, brand: str = "") -> list:
    """
    Search Brave Web Search and return results ranked by domain trustworthiness.
    Returns an empty list if BRAVE_API_KEY is not set or the request fails.
    """
    if not BRAVE_API_KEY:
        return []

    try:
        encoded = urllib.parse.quote(query)
        req = urllib.request.Request(
            f"https://api.search.brave.com/res/v1/web/search?q={encoded}&count=5",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": BRAVE_API_KEY,
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        raw = data.get("web", {}).get("results", [])
        results = [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                description=r.get("description", ""),
                domain_score=_score_domain(r.get("url", ""), brand),
            )
            for r in raw
            if r.get("url")
        ]
        results.sort(key=lambda r: r.domain_score, reverse=True)
        return results
    except Exception:
        return []
```

- [ ] **Step 6: Run tests — expect PASS**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_brave_search.py -v
```

Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/brave_search.py tests/test_brave_search.py requirements.txt && git commit -m "feat: add Brave Search client with domain scoring"
```

---

## Task 2: `src/product_enrichment.py` — qualifying rows and query building

**Files:**
- Create: `src/product_enrichment.py` (stub, grows over tasks 2–4)
- Modify: `tests/test_product_enrichment.py` (create in this task)

- [ ] **Step 1: Write failing tests for `_qualifies` and `_build_search_query`**

Create `tests/test_product_enrichment.py`:

```python
import pytest
import pandas as pd
from src.product_enrichment import (
    _qualifies,
    _build_search_query,
    _apply_enrichment,
    enrich_row,
    enrich_dataframe,
)


# ── _qualifies ─────────────────────────────────────────────────────────────────

def _base_qualifying_row():
    return {
        "Source Type": "PDF",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "",
        "Dimensions": "",
        "Finish / Color": "",
        "Product Category": "",
        "Product URL": "",
        "Notes": "",
        "Review Required": False,
        "Suggested Action": "",
    }


def test_qualifies_url_row_skipped():
    row = {**_base_qualifying_row(), "Source Type": "URL"}
    assert not _qualifies(row)


def test_qualifies_enriched_row_skipped():
    row = {**_base_qualifying_row(), "Source Type": "PDF_Enriched"}
    assert not _qualifies(row)


def test_qualifies_no_brand_skipped():
    row = {**_base_qualifying_row(), "Brand": ""}
    assert not _qualifies(row)


def test_qualifies_no_sku_skipped():
    row = {**_base_qualifying_row(), "Model/SKU": ""}
    assert not _qualifies(row)


def test_qualifies_all_enrichable_fields_present_skipped():
    row = {
        **_base_qualifying_row(),
        "Product Name": "Wolf Microwave",
        "Dimensions": '30"W',
        "Finish / Color": "Stainless",
        "Product Category": "Appliance",
    }
    assert not _qualifies(row)


def test_qualifies_missing_dimensions():
    row = {
        **_base_qualifying_row(),
        "Product Name": "Wolf Microwave",
        "Dimensions": "",
        "Finish / Color": "Stainless",
        "Product Category": "Appliance",
    }
    assert _qualifies(row)


def test_qualifies_all_blank_enrichable_fields():
    assert _qualifies(_base_qualifying_row())


# ── _build_search_query ────────────────────────────────────────────────────────

def test_build_search_query_full():
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "Drawer Microwave"}
    assert _build_search_query(row) == "Wolf MDD30TS Drawer Microwave specifications official"


def test_build_search_query_no_product_name():
    row = {"Brand": "Sub-Zero", "Model/SKU": "ID36R", "Product Name": ""}
    assert _build_search_query(row) == "Sub-Zero ID36R specifications official"


def test_build_search_query_strips_whitespace():
    row = {"Brand": "  Miele  ", "Model/SKU": " CVA7440 ", "Product Name": ""}
    result = _build_search_query(row)
    assert result == "Miele CVA7440 specifications official"
```

- [ ] **Step 2: Run — expect FAIL (module not created yet)**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py::test_qualifies_url_row_skipped -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/product_enrichment.py` with `_qualifies` and `_build_search_query`**

```python
"""
Product enrichment orchestrator for SCH DesignOps Intake.

Automatically fills blank fields (Product Name, Dimensions, Finish / Color,
Product Category, Product URL, Notes/materials) using Brave Search + httpx +
Claude Haiku. Never overwrites existing data.

Public API
----------
enrich_row(row: dict) -> tuple[dict, str | None]
enrich_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]
"""

import json
import os
import re
import time

import httpx
import pandas as pd
from dotenv import load_dotenv

from src.brave_search import BRAVE_API_KEY, search_product_candidates
from src.category_ai import _normalise_category

load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

_ENRICHABLE_FIELDS: list = [
    "Product Name",
    "Dimensions",
    "Finish / Color",
    "Product Category",
    "Product URL",
]

MIN_USE_SCORE = 40   # below this: skip entirely, note in Notes
MIN_CONF_SCORE = 60  # 40–59: fill fields but force Review Required = True


def _qualifies(row: dict) -> bool:
    """True if this row should be sent through enrichment."""
    source = str(row.get("Source Type", "") or "").strip()
    if source == "URL":
        return False
    if source.endswith("_Enriched"):
        return False
    if not str(row.get("Brand", "") or "").strip():
        return False
    if not str(row.get("Model/SKU", "") or "").strip():
        return False
    blank = [
        f for f in ["Product Name", "Dimensions", "Finish / Color", "Product Category"]
        if not str(row.get(f, "") or "").strip()
    ]
    return bool(blank)


def _build_search_query(row: dict) -> str:
    """Build a Brave Search query from the row's Brand, Model/SKU, and Product Name."""
    parts = [
        str(row.get("Brand", "") or "").strip(),
        str(row.get("Model/SKU", "") or "").strip(),
        str(row.get("Product Name", "") or "").strip(),
        "specifications official",
    ]
    return " ".join(p for p in parts if p)
```

- [ ] **Step 4: Run qualifying + query tests — expect PASS**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py -k "qualifies or build_search" -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/product_enrichment.py tests/test_product_enrichment.py && git commit -m "feat: enrichment qualifying logic and query builder"
```

---

## Task 3: `src/product_enrichment.py` — `_apply_enrichment`

**Files:**
- Modify: `src/product_enrichment.py` (add `_apply_enrichment`)
- Modify: `tests/test_product_enrichment.py` (add tests)

- [ ] **Step 1: Add `_apply_enrichment` tests to `tests/test_product_enrichment.py`**

Append to the existing test file (after the `_build_search_query` tests):

```python
# ── _apply_enrichment ──────────────────────────────────────────────────────────

def _base_row_for_apply():
    return {
        "Source Type": "PDF",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "",
        "Dimensions": "",
        "Finish / Color": "",
        "Product Category": "",
        "Product URL": "",
        "Notes": "",
        "Review Required": False,
        "Suggested Action": "",
    }


def test_apply_enrichment_fills_blank_fields():
    row = _base_row_for_apply()
    extracted = {
        "Product Name": "Wolf 30\" Drawer Microwave",
        "Dimensions": "29 7/8\" W × 23 1/2\" D",
        "Finish / Color": "",
        "Product Category": "Appliance",
        "materials": "",
    }
    updated = _apply_enrichment(row, extracted, "https://wolfappliance.com", 85)
    assert updated["Product Name"] == "Wolf 30\" Drawer Microwave"
    assert updated["Dimensions"] == "29 7/8\" W × 23 1/2\" D"
    assert updated["Product Category"] == "Appliance"
    assert updated["Product URL"] == "https://wolfappliance.com"
    assert updated["Source Type"] == "PDF_Enriched"
    assert updated["Review Required"] is False


def test_apply_enrichment_never_overwrites_existing():
    row = {
        **_base_row_for_apply(),
        "Product Name": "Existing Name",
        "Dimensions": "36\" W",
        "Product URL": "https://existing.com",
    }
    extracted = {
        "Product Name": "New Name",
        "Dimensions": "30\" W",
        "Finish / Color": "Black",
        "Product Category": "Appliance",
        "materials": "",
    }
    updated = _apply_enrichment(row, extracted, "https://other.com", 90)
    assert updated["Product Name"] == "Existing Name"
    assert updated["Dimensions"] == "36\" W"
    assert updated["Product URL"] == "https://existing.com"
    # Blank fields ARE filled
    assert updated["Finish / Color"] == "Black"


def test_apply_enrichment_low_confidence_flags_review():
    row = _base_row_for_apply()
    extracted = {"Product Name": "Some Product", "Dimensions": "", "Finish / Color": "", "Product Category": "", "materials": ""}
    updated = _apply_enrichment(row, extracted, "https://retailer.com", 50)
    assert updated["Review Required"] is True
    assert "low-confidence" in updated["Suggested Action"]


def test_apply_enrichment_high_confidence_no_review_flag():
    row = _base_row_for_apply()
    extracted = {"Product Name": "Wolf Microwave", "Dimensions": "", "Finish / Color": "", "Product Category": "", "materials": ""}
    updated = _apply_enrichment(row, extracted, "https://wolfappliance.com", 90)
    assert updated["Review Required"] is False


def test_apply_enrichment_source_type_suffix():
    row = {**_base_row_for_apply(), "Source Type": "Manual"}
    extracted = {"Product Name": "X", "Dimensions": "", "Finish / Color": "", "Product Category": "", "materials": ""}
    updated = _apply_enrichment(row, extracted, "https://example.com", 70)
    assert updated["Source Type"] == "Manual_Enriched"


def test_apply_enrichment_materials_go_to_notes():
    row = _base_row_for_apply()
    extracted = {
        "Product Name": "",
        "Dimensions": "",
        "Finish / Color": "",
        "Product Category": "",
        "materials": "Solid oak frame",
    }
    updated = _apply_enrichment(row, extracted, "https://example.com", 70)
    assert "[Materials: Solid oak frame]" in updated["Notes"]


def test_apply_enrichment_materials_not_duplicated_in_finish():
    row = {**_base_row_for_apply(), "Finish / Color": "Stainless Steel"}
    extracted = {
        "Product Name": "",
        "Dimensions": "",
        "Finish / Color": "",
        "Product Category": "",
        "materials": "stainless steel",  # already expressed in Finish / Color
    }
    updated = _apply_enrichment(row, extracted, "https://example.com", 70)
    assert "Materials" not in updated.get("Notes", "")


def test_apply_enrichment_normalises_category():
    row = _base_row_for_apply()
    extracted = {
        "Product Name": "",
        "Dimensions": "",
        "Finish / Color": "",
        "Product Category": "couch",  # alias → Sofa
        "materials": "",
    }
    updated = _apply_enrichment(row, extracted, "https://example.com", 70)
    assert updated["Product Category"] == "Sofa"
```

- [ ] **Step 2: Run — expect FAIL (function not yet implemented)**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py -k "apply_enrichment" -v 2>&1 | head -20
```

Expected: `ImportError` — `_apply_enrichment` not defined.

- [ ] **Step 3: Add `_apply_enrichment` to `src/product_enrichment.py`**

Append after `_build_search_query`:

```python
def _apply_enrichment(
    row: dict,
    extracted: dict,
    source_url: str,
    domain_score: int,
) -> dict:
    """
    Apply extracted fields to a row copy, filling blank fields only.
    Sets Source Type suffix and confidence flags.
    """
    updated = row.copy()

    for field in _ENRICHABLE_FIELDS:
        if field == "Product URL":
            if not str(updated.get("Product URL", "") or "").strip():
                updated["Product URL"] = source_url
            continue

        # Never overwrite non-empty fields
        if str(updated.get(field, "") or "").strip():
            continue

        value = str(extracted.get(field, "") or "").strip()
        if not value:
            continue

        if field == "Product Category":
            value = _normalise_category(value)

        if value:
            updated[field] = value

    # Materials → Notes (only if not already captured in Finish / Color)
    materials = str(extracted.get("materials", "") or "").strip()
    finish = str(updated.get("Finish / Color", "") or "").strip().lower()
    if materials and materials.lower() not in finish:
        existing_notes = str(updated.get("Notes", "") or "").strip()
        mat_tag = f"[Materials: {materials}]"
        updated["Notes"] = f"{existing_notes} {mat_tag}".strip() if existing_notes else mat_tag

    # Source Type suffix
    original = str(updated.get("Source Type", "") or "").strip()
    if original and not original.endswith("_Enriched"):
        updated["Source Type"] = f"{original}_Enriched"
    elif not original:
        updated["Source Type"] = "Enriched"

    # Confidence flagging
    if domain_score < MIN_CONF_SCORE:
        updated["Review Required"] = True
        updated["Suggested Action"] = "Enriched from low-confidence source — verify fields"

    return updated
```

- [ ] **Step 4: Run apply_enrichment tests — expect PASS**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py -k "apply_enrichment" -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/product_enrichment.py tests/test_product_enrichment.py && git commit -m "feat: blank-only enrichment apply logic with confidence flagging"
```

---

## Task 4: `src/product_enrichment.py` — page fetch and Claude extraction

**Files:**
- Modify: `src/product_enrichment.py` (add `_fetch_page_text`, `_build_extraction_prompt`, `_extract_with_claude`)
- Modify: `tests/test_product_enrichment.py` (add tests)

- [ ] **Step 1: Add fetch and extraction tests to `tests/test_product_enrichment.py`**

Append to the test file:

```python
# ── _fetch_page_text ───────────────────────────────────────────────────────────

from unittest.mock import patch, MagicMock
from src.product_enrichment import _fetch_page_text, _extract_with_claude


def test_fetch_page_text_returns_stripped_text():
    mock_resp = MagicMock()
    mock_resp.text = "<html><body><h1>Wolf Microwave</h1><p>Model MDD30TS</p></body></html>"
    mock_resp.raise_for_status = MagicMock()

    with patch("src.product_enrichment.httpx.get", return_value=mock_resp):
        result = _fetch_page_text("https://wolfappliance.com/product")

    assert "Wolf Microwave" in result
    assert "MDD30TS" in result
    assert "<html>" not in result


def test_fetch_page_text_returns_empty_on_error():
    with patch("src.product_enrichment.httpx.get", side_effect=Exception("timeout")):
        result = _fetch_page_text("https://example.com/bad")
    assert result == ""


def test_fetch_page_text_caps_at_6000_chars():
    mock_resp = MagicMock()
    mock_resp.text = "<p>" + ("x" * 20000) + "</p>"
    mock_resp.raise_for_status = MagicMock()

    with patch("src.product_enrichment.httpx.get", return_value=mock_resp):
        result = _fetch_page_text("https://example.com/long")

    assert len(result) <= 6000


# ── _extract_with_claude ───────────────────────────────────────────────────────

def test_extract_with_claude_parses_json():
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"Product Name": "Wolf Microwave", "Dimensions": "30\\" W", "Finish / Color": "", "Product Category": "Appliance", "materials": "Stainless steel"}')]

    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "", "Dimensions": "", "Finish / Color": "", "Product Category": ""}

    with patch("src.product_enrichment.ANTHROPIC_API_KEY", "fake_key"), \
         patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = mock_msg
        result = _extract_with_claude("some page text", row)

    assert result.get("Product Name") == "Wolf Microwave"
    assert result.get("Dimensions") == '30" W'
    assert result.get("Product Category") == "Appliance"


def test_extract_with_claude_returns_empty_on_missing_key():
    import src.product_enrichment as pe
    with patch.object(pe, "ANTHROPIC_API_KEY", ""):
        result = _extract_with_claude("some page text", {"Brand": "Wolf", "Model/SKU": "MDD30TS"})
    assert result == {}


def test_extract_with_claude_returns_empty_on_bad_response():
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="Sorry, I cannot help with that.")]

    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "", "Dimensions": ""}

    with patch("src.product_enrichment.ANTHROPIC_API_KEY", "fake_key"), \
         patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = mock_msg
        result = _extract_with_claude("some text", row)

    assert result == {}
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py -k "fetch or extract_with_claude" -v 2>&1 | head -20
```

Expected: `ImportError` — functions not yet defined.

- [ ] **Step 3: Add `_fetch_page_text`, `_build_extraction_prompt`, `_extract_with_claude` to `src/product_enrichment.py`**

Append after `_apply_enrichment`:

```python
def _fetch_page_text(url: str) -> str:
    """Fetch URL with httpx and return plain text (max 6 000 chars). Empty string on error."""
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SCH-Intake/1.0)"},
            timeout=10,
            follow_redirects=True,
        )
        resp.raise_for_status()

        try:
            import html2text as _ht
            h = _ht.HTML2Text()
            h.ignore_links = True
            h.ignore_images = True
            text = h.handle(resp.text)
        except ImportError:
            text = re.sub(r"<[^>]+>", " ", resp.text)
            text = re.sub(r"\s{2,}", " ", text)

        return text[:6000].strip()
    except Exception:
        return ""


def _build_extraction_prompt(page_text: str, row: dict) -> str:
    blank = [
        f for f in ["Product Name", "Dimensions", "Finish / Color", "Product Category"]
        if not str(row.get(f, "") or "").strip()
    ]
    brand = str(row.get("Brand", "") or "").strip()
    model = str(row.get("Model/SKU", "") or "").strip()

    return (
        f"You are extracting product specification data for {brand} model {model}.\n\n"
        f"The following fields are currently blank and need to be filled:\n"
        f"{', '.join(blank)}\n\n"
        "Also extract: materials (short description of primary construction materials, "
        "e.g. 'Solid Oak', 'Stainless Steel')\n\n"
        "Return ONLY a JSON object. No prose. No markdown fences. Example:\n"
        '{"Product Name": "Wolf 30\\" Drawer Microwave Oven", '
        '"Dimensions": "29 7/8\\" W x 23 1/2\\" D x 11 7/8\\" H", '
        '"Finish / Color": "Stainless Steel", '
        '"Product Category": "Appliance", '
        '"materials": "Stainless steel exterior"}\n\n'
        "Rules:\n"
        "- Only include the fields listed above as blank, plus 'materials'.\n"
        "- If a field is not clearly stated in the page, return \"\" for that field.\n"
        "- Never invent values not present in the page.\n"
        "- Product Category must be one of: Chair, Sofa, Paint, Fabric, Table, "
        "Lighting, Plumbing, Hardware, Rug, Artwork, Mirror, Appliance, Accessories, Other.\n\n"
        f"PAGE TEXT:\n---\n{page_text}\n---"
    )


def _extract_with_claude(page_text: str, row: dict) -> dict:
    """Call Claude Haiku to extract missing fields from page text. Returns {} on any failure."""
    if not ANTHROPIC_API_KEY:
        return {}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": _build_extraction_prompt(page_text, row)}],
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
```

- [ ] **Step 4: Run fetch and extraction tests — expect PASS**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py -k "fetch or extract_with_claude" -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/product_enrichment.py tests/test_product_enrichment.py && git commit -m "feat: page fetch and Claude Haiku field extraction"
```

---

## Task 5: `src/product_enrichment.py` — orchestration (`enrich_row`, `enrich_dataframe`)

**Files:**
- Modify: `src/product_enrichment.py` (add `enrich_row`, `enrich_dataframe`)
- Modify: `tests/test_product_enrichment.py` (add orchestration tests)

- [ ] **Step 1: Add orchestration tests to `tests/test_product_enrichment.py`**

Append:

```python
# ── enrich_row ─────────────────────────────────────────────────────────────────

from src.brave_search import SearchResult


def _qualifying_row():
    return {
        "Source Type": "PDF",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "",
        "Dimensions": "",
        "Finish / Color": "",
        "Product Category": "",
        "Product URL": "",
        "Notes": "",
        "Review Required": False,
        "Suggested Action": "",
    }


def test_enrich_row_no_search_results_leaves_note():
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        updated, error = enrich_row(_qualifying_row())
    assert error is None
    assert "[Enrichment: no confident source found]" in updated["Notes"]
    assert updated["Product Name"] == ""


def test_enrich_row_low_score_result_leaves_note():
    low_result = SearchResult("title", "https://amazon.com/dp/B001", "desc", 10)
    with patch("src.product_enrichment.search_product_candidates", return_value=[low_result]):
        updated, error = enrich_row(_qualifying_row())
    assert "[Enrichment: no confident source found]" in updated["Notes"]


def test_enrich_row_fetch_failure_leaves_note():
    good_result = SearchResult("Wolf Spec", "https://wolfappliance.com", "desc", 90)
    with patch("src.product_enrichment.search_product_candidates", return_value=[good_result]), \
         patch("src.product_enrichment._fetch_page_text", return_value=""):
        updated, error = enrich_row(_qualifying_row())
    assert "could not fetch" in updated["Notes"]


def test_enrich_row_fills_fields_on_success():
    good_result = SearchResult("Wolf Spec", "https://wolfappliance.com", "desc", 90)
    extracted = {
        "Product Name": "Wolf 30\" Drawer Microwave",
        "Dimensions": "29 7/8\" W",
        "Finish / Color": "",
        "Product Category": "Appliance",
        "materials": "",
    }
    with patch("src.product_enrichment.search_product_candidates", return_value=[good_result]), \
         patch("src.product_enrichment._fetch_page_text", return_value="page content"), \
         patch("src.product_enrichment._extract_with_claude", return_value=extracted):
        updated, error = enrich_row(_qualifying_row())

    assert error is None
    assert updated["Product Name"] == "Wolf 30\" Drawer Microwave"
    assert updated["Source Type"] == "PDF_Enriched"


# ── enrich_dataframe ───────────────────────────────────────────────────────────

def test_enrich_dataframe_skips_non_qualifying():
    df = pd.DataFrame([{
        "Source Type": "URL",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "",
        "Dimensions": "",
        "Finish / Color": "",
        "Product Category": "",
        "Product URL": "https://example.com",
        "Notes": "",
        "Review Required": False,
        "Suggested Action": "",
    }])
    with patch("src.product_enrichment.search_product_candidates", return_value=[]) as mock_search:
        updated_df, errors = enrich_dataframe(df)
    mock_search.assert_not_called()
    assert errors == []


def test_enrich_dataframe_isolates_exceptions():
    rows = [
        {**_qualifying_row(), "Brand": "Wolf"},
        {**_qualifying_row(), "Brand": "Miele", "Model/SKU": "CVA7440"},
    ]
    df = pd.DataFrame(rows)

    def bad_enrich_row(row):
        if row["Brand"] == "Wolf":
            raise RuntimeError("network error")
        return row, None

    with patch("src.product_enrichment.enrich_row", side_effect=bad_enrich_row), \
         patch("src.product_enrichment.time.sleep"):
        updated_df, errors = enrich_dataframe(df)

    assert len(errors) == 1
    assert "Wolf" in errors[0]
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py -k "enrich_row or enrich_dataframe" -v 2>&1 | head -20
```

Expected: `ImportError` — functions not yet defined.

- [ ] **Step 3: Add `enrich_row` and `enrich_dataframe` to `src/product_enrichment.py`**

Append after `_extract_with_claude`:

```python
def enrich_row(row: dict) -> tuple:
    """
    Enrich a single row using Brave Search + httpx + Claude.

    Returns (updated_row, None) on success or graceful no-result.
    Returns (row_unchanged, error_string) only on unexpected exceptions.
    """
    query = _build_search_query(row)
    brand = str(row.get("Brand", "") or "").strip()

    results = search_product_candidates(query, brand)

    if not results or results[0].domain_score < MIN_USE_SCORE:
        updated = row.copy()
        existing = str(updated.get("Notes", "") or "").strip()
        note = "[Enrichment: no confident source found]"
        updated["Notes"] = f"{existing} {note}".strip() if existing else note
        return updated, None

    best = results[0]
    page_text = _fetch_page_text(best.url)

    if not page_text:
        updated = row.copy()
        existing = str(updated.get("Notes", "") or "").strip()
        domain = best.url[:50]
        note = f"[Enrichment: could not fetch {domain}]"
        updated["Notes"] = f"{existing} {note}".strip() if existing else note
        return updated, None

    extracted = _extract_with_claude(page_text, row)
    updated = _apply_enrichment(row, extracted, best.url, best.domain_score)
    return updated, None


def enrich_dataframe(df: pd.DataFrame) -> tuple:
    """
    Enrich all qualifying rows in df. Returns (updated_df, error_list).
    Exceptions in individual rows are caught and logged; the row is left unchanged.
    """
    df = df.copy()
    errors: list = []

    for idx, row in df.iterrows():
        r = row.to_dict()
        if not _qualifies(r):
            continue

        try:
            updated, error = enrich_row(r)
            if error:
                errors.append(error)
            else:
                for col, val in updated.items():
                    if col in df.columns:
                        df.at[idx, col] = val
        except Exception as exc:
            label = str(r.get("Product Name") or r.get("Model/SKU") or idx)
            errors.append(f"Row '{label}': {exc}")

        time.sleep(0.5)

    return df, errors
```

- [ ] **Step 4: Run all enrichment tests — expect PASS**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/test_product_enrichment.py -v
```

Expected: all tests pass (target ≥ 24).

- [ ] **Step 5: Run full test suite to check nothing broke**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/ -v
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/product_enrichment.py tests/test_product_enrichment.py && git commit -m "feat: enrich_row and enrich_dataframe orchestration with error isolation"
```

---

## Task 6: `app.py` integration + `.env.example`

**Files:**
- Modify: `app.py`
- Modify: `.env.example`

No unit tests for Streamlit UI logic — manual smoke test described at end.

- [ ] **Step 1: Update `.env.example`**

Open `.env.example` and add the Brave key line. Final file should contain:

```
ANTHROPIC_API_KEY=your_anthropic_api_key_here
BRAVE_API_KEY=your_brave_api_key_here
```

- [ ] **Step 2: Add imports to `app.py`**

At the top of `app.py`, after the existing `from src.category_ai import suggest_categories_batch` line (around line 19), add:

```python
from src.product_enrichment import enrich_dataframe
from src.brave_search import BRAVE_API_KEY as _BRAVE_API_KEY
```

- [ ] **Step 3: Add session state keys to `app.py`**

After the existing `if "cat_ai_error" not in st.session_state:` block (around line 87), add:

```python
if "pending_enrichment" not in st.session_state:
    st.session_state.pending_enrichment = False
if "enrichment_errors" not in st.session_state:
    st.session_state.enrichment_errors = []
```

- [ ] **Step 4: Set `pending_enrichment = True` after each intake path**

There are three places where `st.session_state.intake_df` is set inside `if generate:`. After each assignment, add the flag. The three assignments are at approximately lines 284, 311, and inside the manual-entry form (~line 227).

**After line 284** (AI extraction path — inside `if not all_frames: ... else: ...`):
```python
                st.session_state.intake_df = apply_confidence_checks(combined)
                st.session_state.automation_results = None
                st.session_state.pending_enrichment = True   # ← add this line
```

**After line 311** (standard PDF/URL path):
```python
            st.session_state.intake_df = apply_confidence_checks(base_df)
            st.session_state.automation_results = None
            st.session_state.pending_enrichment = True   # ← add this line
```

**After line 227** (manual entry form — after `apply_confidence_checks`):

Find the block that starts with `if add_manual:` → `st.session_state.intake_df = apply_confidence_checks(combined)` and add:
```python
        st.session_state.intake_df = apply_confidence_checks(combined)
        st.session_state.pending_enrichment = True   # ← add this line
```

Also handle the `clear` button — reset the flags when clearing:
```python
if clear:
    st.session_state.intake_df = None
    st.session_state.automation_results = None
    st.session_state.ai_errors = []
    st.session_state.pending_enrichment = False   # ← add this line
    st.session_state.enrichment_errors = []       # ← add this line
    st.rerun()
```

- [ ] **Step 5: Add automatic enrichment block to `app.py`**

Find the line `# ── Review section ─────────` (around line 319). Just after `if st.session_state.intake_df is not None:` and `df: pd.DataFrame = st.session_state.intake_df`, insert the enrichment block:

```python
if st.session_state.intake_df is not None:
    df: pd.DataFrame = st.session_state.intake_df

    # ── Automatic enrichment pass ──────────────────────────────────────────────
    if st.session_state.pending_enrichment:
        if _BRAVE_API_KEY:
            with st.spinner("Searching manufacturer sources to fill missing product details…"):
                _enriched_df, _enrich_errors = enrich_dataframe(df)
                st.session_state.intake_df = apply_confidence_checks(_enriched_df)
                st.session_state.enrichment_errors = _enrich_errors
                st.session_state.pending_enrichment = False
                df = st.session_state.intake_df
        else:
            st.session_state.pending_enrichment = False

    # Enrichment error banner
    if st.session_state.enrichment_errors:
        st.warning(
            f"{len(st.session_state.enrichment_errors)} row(s) could not be enriched — "
            "details added to Notes for those rows.",
            icon="⚠️",
        )

    st.divider()
    # ... rest of review section continues unchanged ...
```

- [ ] **Step 6: Add missing-key caption when BRAVE_API_KEY is absent**

Find the existing `section_label("AI-Assisted Cleanup")` block (around line 477). After the section label, add the caption only when the key is missing:

```python
    section_label("AI-Assisted Cleanup")

    if not _BRAVE_API_KEY:
        st.caption(
            "Product enrichment requires BRAVE_API_KEY — add it to .env and restart the app."
        )
```

- [ ] **Step 7: Add "Re-run Enrichment" button to AI-Assisted Cleanup section**

In the AI-Assisted Cleanup section, after the existing category suggestion columns block (after the `cat_col` block, around line 517), add:

```python
    enrich_col, _, __ = st.columns([2, 6, 2])
    with enrich_col:
        enrich_rerun_clicked = st.button(
            "Re-run Enrichment for Needs Enrichment Rows",
            type="secondary",
            use_container_width=True,
            disabled=not _BRAVE_API_KEY,
            help="Re-search manufacturer sources for rows still marked Needs Enrichment.",
        )
    if enrich_rerun_clicked and _BRAVE_API_KEY:
        st.session_state.pending_enrichment = True
        st.rerun()
```

- [ ] **Step 8: Verify syntax**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -c "import ast; ast.parse(open('app.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 9: Run full test suite**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && python3 -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 10: Smoke test — manual**

Start the app:
```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && streamlit run app.py
```

Verify:
1. App loads without error.
2. Enter project name and upload a PDF with known model numbers.
3. Click "Generate Intake Table" — spinner "Searching manufacturer sources…" appears.
4. Table renders with enriched rows: `Source Type` shows `PDF_Enriched`, blank fields filled.
5. Low-confidence rows show `Review Required = True`.
6. "Re-run Enrichment" button appears in AI-Assisted Cleanup section.
7. Remove `BRAVE_API_KEY` from `.env`, restart — caption "Product enrichment requires BRAVE_API_KEY" appears; no crash.

- [ ] **Step 11: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add app.py .env.example && git commit -m "feat: automatic enrichment on import with re-run button and missing-key caption"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Brave Search + httpx + Claude Haiku — Task 1, 4
- ✅ Domain scoring (brand slug, preferred, skip) — Task 1
- ✅ `_qualifies` (URL skip, Enriched skip, no brand, no SKU, all-full skip) — Task 2
- ✅ `_build_search_query` — Task 2
- ✅ `_apply_enrichment` (blank-only, no-overwrite, suffix, confidence flags) — Task 3
- ✅ `_fetch_page_text` (httpx, html2text, 6000-char cap, error → "") — Task 4
- ✅ `_extract_with_claude` (Haiku, JSON parse, empty on failure) — Task 4
- ✅ `enrich_row` (no-result note, fetch-fail note, success path) — Task 5
- ✅ `enrich_dataframe` (skips non-qualifying, isolates exceptions, rate limit) — Task 5
- ✅ `app.py` auto-trigger after all intake paths — Task 6
- ✅ `app.py` missing-key caption — Task 6
- ✅ `app.py` re-run button — Task 6
- ✅ `app.py` enrichment error banner — Task 6
- ✅ `.env.example` updated — Task 6
- ✅ `requirements.txt` updated — Task 1

**Type consistency:** All function signatures referenced in tests match the implementations (verified above).

**No placeholders:** All code blocks are complete. No TBD, TODO, or "similar to Task N" patterns.
