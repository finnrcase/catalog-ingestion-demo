# Phase 1 Image Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a confidence-gated image recovery pipeline with PDF-crop and product-page-screenshot fallbacks, never auto-exporting low-confidence images.

**Architecture:** New `src/image_recovery.py` module orchestrates three sources (existing URL fetch, PyMuPDF page-crop, Playwright element/full-page screenshot) and emits an `ImageRecoveryResult` carrying confidence and evidence. Local files live under `.tmp/uploads/{session_id}/images/`; uploaded PDFs live under `.tmp/uploads/{session_id}/pdfs/`. The standard Programa CSV/XLSX stays clean — diagnostics flow through the manifest CSV inside the ZIP and through the debug export.

**Tech Stack:** Python 3.14, PyMuPDF (`fitz`), Playwright (Chromium headless), Pillow, httpx, pandas, pytest, Streamlit, FastAPI.

**Source spec:** [`docs/superpowers/specs/2026-05-08-phase1-image-recovery-design.md`](../specs/2026-05-08-phase1-image-recovery-design.md)

**Conventions used in this plan:**
- Test commands assume working directory is repo root (`/Users/finncase/Desktop/Dev/SCH data input proj`)
- Use `python3` (the repo's interpreter) — `python` is not on PATH in this environment
- Every task ends with a commit; CI runs `pytest` so each commit must be green

---

## Task 1: Bootstrap — gitignore + schema fields

**Goal:** Reserve disk and schema space for the new pipeline so later tasks can write into stable shapes.

**Files:**
- Modify: `.gitignore`
- Modify: `src/intake_schema.py`
- Test: `tests/test_intake_schema.py` (extend if exists, create if not)

- [ ] **Step 1: Verify `.gitignore` doesn't already cover `.tmp/`**

Run: `grep -nE '^\\.tmp/?$' .gitignore`
Expected: no output (entry not present)

- [ ] **Step 2: Add `.tmp/` to `.gitignore`**

Append two lines to the bottom of `.gitignore`:

```
# Per-session image recovery scratch space (PDFs + recovered images)
.tmp/
```

- [ ] **Step 3: Write the schema test**

Create or append to `tests/test_intake_schema.py`:

```python
def test_internal_source_fields_present_in_base_row():
    from src.intake_schema import make_base_row
    row = make_base_row()
    assert "_source_pdf_id" in row
    assert "_source_page_number" in row
    assert "_source_filename" in row
    # All three default to empty / None
    assert row["_source_pdf_id"] == ""
    assert row["_source_page_number"] is None
    assert row["_source_filename"] == ""


def test_internal_source_fields_not_in_all_columns_export_list():
    """Internal fields are plumbing — they must NOT appear in ALL_COLUMNS,
    which feeds the user-facing column ordering."""
    from src.intake_schema import ALL_COLUMNS
    assert "_source_pdf_id" not in ALL_COLUMNS
    assert "_source_page_number" not in ALL_COLUMNS
    assert "_source_filename" not in ALL_COLUMNS
```

- [ ] **Step 4: Run the failing test**

Run: `python3 -m pytest tests/test_intake_schema.py -v`
Expected: FAIL — `KeyError: '_source_pdf_id'` in `test_internal_source_fields_present_in_base_row`.

- [ ] **Step 5: Add the three internal fields to `make_base_row` only**

Open `src/intake_schema.py`, find `make_base_row` (around line 121). Inside the returned dict, after the existing user-facing fields, add:

```python
def make_base_row(
    project: str = "",
    room: str = "",
    supplier: str = "",
    notes: str = "",
) -> dict:
    """
    Return a blank row dict with every column at its default value.
    Callers fill in the source-specific fields after calling this.

    Internal `_source_*` fields (added Phase 1 image recovery, 2026-05-08)
    record which uploaded PDF and page a row was parsed from. They are
    intentionally NOT in ALL_COLUMNS so the standard Programa CSV/XLSX
    export stays clean. They surface only in the debug export and in the
    manifest CSV inside the Programa ZIP.
    """
    base = {col: "" for col in ALL_COLUMNS}
    base["Include"] = True
    base["Project"] = project
    base["Room"] = room
    base["Supplier"] = supplier
    base["Notes"] = notes
    base["Quantity"] = 1
    base["Status"] = "Needs Review"
    # Internal source-tracking fields (not in ALL_COLUMNS by design)
    base["_source_pdf_id"] = ""
    base["_source_page_number"] = None
    base["_source_filename"] = ""
    return base
```

(If the existing `make_base_row` body differs, only ADD the three `base["_source_*"]` lines and the comment paragraph — do not rewrite working code.)

- [ ] **Step 6: Run schema tests to verify pass**

Run: `python3 -m pytest tests/test_intake_schema.py -v`
Expected: PASS for both new tests.

- [ ] **Step 7: Run full suite to confirm no regressions**

Run: `python3 -m pytest -q`
Expected: all 532 tests still pass plus the 2 new ones (534 passed).

- [ ] **Step 8: Commit**

```bash
git add .gitignore src/intake_schema.py tests/test_intake_schema.py
git commit -m "feat(schema): reserve internal _source_pdf_id, _source_page_number, _source_filename + ignore .tmp/

Phase 1 image recovery scaffolding. Fields are kept out of ALL_COLUMNS
so the user-facing Programa CSV/XLSX stays clean; they will surface only
in the debug export and the ZIP manifest."
```

---

## Task 2: Evidence helpers (`src/image_evidence.py`)

**Goal:** Provide pure-text matching helpers used by every recovery source for confidence scoring.

**Files:**
- Create: `src/image_evidence.py`
- Create: `tests/test_image_evidence.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_image_evidence.py` with this content:

```python
"""Tests for src/image_evidence.py — text matching helpers for confidence scoring."""
from __future__ import annotations

import pytest

from src.image_evidence import (
    sku_appears_in_text,
    product_name_appears_in_text,
    is_official_domain,
)


# ── sku_appears_in_text ───────────────────────────────────────────────────────

def test_sku_exact_match():
    assert sku_appears_in_text("MDD30TS", "Wolf MDD30TS warming drawer") is True


def test_sku_case_insensitive():
    assert sku_appears_in_text("MDD30TS", "wolf mdd30ts warming drawer") is True


def test_sku_with_dash_separator():
    assert sku_appears_in_text("MDD30TS", "Wolf MDD-30TS warming drawer") is True


def test_sku_with_space_separator():
    assert sku_appears_in_text("MDD30TS", "Wolf MDD 30 TS warming drawer") is True


def test_sku_not_substring_match():
    """SKU 'AB12' should not match inside 'CAB1234'."""
    assert sku_appears_in_text("AB12", "CAB1234 unrelated product") is False


def test_sku_with_trailing_punctuation():
    assert sku_appears_in_text("MDD30TS", "Model: MDD30TS. Built-in.") is True


def test_sku_blank_returns_false():
    assert sku_appears_in_text("", "anything") is False
    assert sku_appears_in_text("MDD30TS", "") is False


# ── product_name_appears_in_text ──────────────────────────────────────────────

def test_product_name_full_match():
    assert product_name_appears_in_text(
        "Wolf 30 Inch Warming Drawer",
        "Buy the Wolf 30 Inch Warming Drawer here.",
    ) is True


def test_product_name_partial_match_majority_tokens():
    """If 3 of 4 meaningful tokens match, that's a positive."""
    assert product_name_appears_in_text(
        "Wolf 30 Inch Warming Drawer",
        "Wolf Warming Drawer 30",
    ) is True


def test_product_name_too_few_tokens_is_false():
    """Only 1 of 4 tokens matching is too weak."""
    assert product_name_appears_in_text(
        "Wolf 30 Inch Warming Drawer",
        "completely unrelated text about teapots",
    ) is False


def test_product_name_blank_returns_false():
    assert product_name_appears_in_text("", "anything") is False


# ── is_official_domain ────────────────────────────────────────────────────────

def test_official_domain_exact():
    """Wolf appliances live on subzero-wolf.com per manufacturer_domains."""
    # We rely on get_domain_for_brand returning a canonical domain for known brands.
    assert is_official_domain("https://www.subzero-wolf.com/wolf/cooking/warming-drawer", "Wolf") is True


def test_official_domain_subdomain():
    assert is_official_domain("https://images.subzero-wolf.com/img/x.jpg", "Wolf") is True


def test_official_domain_mismatch():
    assert is_official_domain("https://amazon.com/x", "Wolf") is False


def test_official_domain_unknown_brand_returns_false():
    """Brands without a manufacturer_domains entry can't be confirmed official."""
    assert is_official_domain("https://example.com/x", "MadeUpBrand123") is False


def test_official_domain_blank_inputs():
    assert is_official_domain("", "Wolf") is False
    assert is_official_domain("https://example.com", "") is False
```

- [ ] **Step 2: Run the failing tests**

Run: `python3 -m pytest tests/test_image_evidence.py -v`
Expected: FAIL with `ImportError: cannot import name 'sku_appears_in_text' from 'src.image_evidence'` (module doesn't exist).

- [ ] **Step 3: Implement `src/image_evidence.py`**

Create `src/image_evidence.py`:

```python
"""
Text-matching helpers for image recovery confidence scoring.

Public API
----------
sku_appears_in_text(sku, text) -> bool
    Case-insensitive SKU match that tolerates separators (- _ space) inside the SKU.

product_name_appears_in_text(name, text) -> bool
    Returns True when ≥ 60% of meaningful tokens in `name` appear in `text`.

is_official_domain(url, brand) -> bool
    True when `url` is on (or is a subdomain of) the canonical domain for `brand`
    according to src/manufacturer_domains.py.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from src.manufacturer_domains import get_domain_for_brand


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
# Stop-words excluded from product-name token matching — they're too generic
# to count as evidence on their own.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "with",
    "in", "on", "to", "by", "inch", "inches", "in.", "cm", "mm",
}


def _normalize(text: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    return _NON_ALNUM_RE.sub(" ", (text or "").lower()).strip()


def sku_appears_in_text(sku: str, text: str) -> bool:
    """
    True if `sku` appears in `text` as a whole token, case-insensitive,
    tolerating common separators (- _ space) inserted into the SKU.

    Examples
    --------
    sku_appears_in_text("MDD30TS", "Wolf MDD-30TS")  → True
    sku_appears_in_text("MDD30TS", "Wolf mdd 30 ts") → True
    sku_appears_in_text("AB12",    "CAB1234")        → False  (not a whole token)
    """
    if not sku or not text:
        return False
    sku_norm = re.sub(r"[^a-z0-9]", "", sku.lower())
    if not sku_norm:
        return False
    text_norm = re.sub(r"[^a-z0-9]", "", text.lower())
    # Find all positions and require token-boundary in the original text
    # by also searching with the original separators preserved.
    if sku_norm in text_norm:
        # Re-anchor: walk the text token by token (split on non-alnum),
        # join consecutive tokens, see if `sku_norm` matches a contiguous
        # join boundary aligned with token starts/ends.
        tokens = [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]
        for i in range(len(tokens)):
            joined = ""
            for j in range(i, len(tokens)):
                joined += tokens[j]
                if joined == sku_norm:
                    return True
                if len(joined) > len(sku_norm):
                    break
    return False


def product_name_appears_in_text(name: str, text: str) -> bool:
    """
    True when ≥ 60% of the meaningful (non-stopword) tokens of `name`
    appear in `text` (case-insensitive, after stripping punctuation).
    """
    if not name or not text:
        return False
    name_tokens = [t for t in _normalize(name).split() if t and t not in _STOPWORDS and len(t) > 1]
    if not name_tokens:
        return False
    text_norm = _normalize(text)
    text_tokens = set(text_norm.split())
    matched = sum(1 for t in name_tokens if t in text_tokens)
    return matched / len(name_tokens) >= 0.6


def is_official_domain(url: str, brand: str) -> bool:
    """True when `url` is on the canonical manufacturer domain for `brand`."""
    if not url or not brand:
        return False
    canonical = get_domain_for_brand(brand)
    if not canonical:
        return False
    canonical_domain = canonical[1] if isinstance(canonical, tuple) else canonical
    canonical_domain = canonical_domain.lower().strip()
    if not canonical_domain:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    # Exact match or subdomain
    return host == canonical_domain or host.endswith("." + canonical_domain)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python3 -m pytest tests/test_image_evidence.py -v`
Expected: 13 tests PASS.

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/image_evidence.py tests/test_image_evidence.py
git commit -m "feat(image-recovery): add evidence helpers for confidence scoring

src/image_evidence.py provides sku_appears_in_text (token-boundary aware,
separator tolerant), product_name_appears_in_text (60% token match),
and is_official_domain (via manufacturer_domains.py)."
```

---

## Task 3: `ImageRecoveryResult` dataclass + `recover_from_url`

**Goal:** Stand up the new module skeleton and the simplest source — a wrapper around the existing URL pipeline that emits an `ImageRecoveryResult`.

**Files:**
- Create: `src/image_recovery.py`
- Create: `tests/test_image_recovery.py`

- [ ] **Step 1: Write failing tests for the dataclass and `recover_from_url`**

Create `tests/test_image_recovery.py`:

```python
"""Tests for src/image_recovery.py — Phase 1 confidence-gated image recovery."""
from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from PIL import Image

from src.image_recovery import (
    ImageRecoveryResult,
    recover_from_url,
)


def _jpeg_bytes(size: tuple[int, int] = (200, 200), color: str = "red") -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _mock_get(content: bytes, content_type: str = "image/jpeg"):
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.content = content
    resp.headers = {"content-type": content_type}
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    return resp


# ── Dataclass shape ───────────────────────────────────────────────────────────

def test_result_defaults_are_safe():
    r = ImageRecoveryResult()
    assert r.image_source == "none"
    assert r.confidence == "NONE"
    assert r.evidence == []
    assert r.needs_image_review is True  # only HIGH bypasses review
    assert r.image_url == ""
    assert r.local_image_filename == ""
    assert r.local_image_path == ""
    assert r.jpeg_bytes == b""
    assert r.error == ""


def test_result_high_confidence_does_not_need_review():
    r = ImageRecoveryResult(confidence="HIGH")
    assert r.needs_image_review is False


def test_result_medium_needs_review():
    r = ImageRecoveryResult(confidence="MEDIUM")
    assert r.needs_image_review is True


# ── recover_from_url: input gating ────────────────────────────────────────────

def test_url_recover_no_image_url_returns_none():
    row = {"Image URL": "", "Brand": "Wolf", "Model/SKU": "MDD30TS"}
    result = recover_from_url(row)
    assert result.confidence == "NONE"
    assert result.image_source == "none"


def test_url_recover_invalid_content_type_returns_none():
    row = {
        "Image URL": "https://example.com/x.jpg",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
    }
    with patch("src.image_recovery._check_image_content_type", return_value=False):
        result = recover_from_url(row)
    assert result.confidence == "NONE"


# ── recover_from_url: confidence ──────────────────────────────────────────────

def test_url_recover_high_when_sku_in_url_path():
    row = {
        "Image URL": "https://www.subzero-wolf.com/img/MDD30TS_hero.jpg",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "30 Inch Warming Drawer",
    }
    with patch("src.image_recovery._check_image_content_type", return_value=True), \
         patch("src.image_recovery.httpx.get", return_value=_mock_get(_jpeg_bytes())):
        result = recover_from_url(row)
    assert result.confidence == "HIGH"
    assert result.image_source == "url"
    assert "sku_in_image_url" in result.evidence
    assert result.image_url == "https://www.subzero-wolf.com/img/MDD30TS_hero.jpg"
    assert len(result.jpeg_bytes) > 0


def test_url_recover_medium_on_official_domain_no_sku():
    row = {
        "Image URL": "https://www.subzero-wolf.com/img/generic-hero.jpg",
        "Brand": "Wolf",
        "Model/SKU": "ZZZ999",  # not in URL or in body
        "Product Name": "Some Product",
    }
    with patch("src.image_recovery._check_image_content_type", return_value=True), \
         patch("src.image_recovery.httpx.get", return_value=_mock_get(_jpeg_bytes())):
        result = recover_from_url(row)
    assert result.confidence == "MEDIUM"
    assert "official_domain" in result.evidence


def test_url_recover_low_on_unknown_domain_no_sku():
    row = {
        "Image URL": "https://random-cdn.com/img/x.jpg",
        "Brand": "Wolf",
        "Model/SKU": "ZZZ999",
        "Product Name": "Some Product",
    }
    with patch("src.image_recovery._check_image_content_type", return_value=True), \
         patch("src.image_recovery.httpx.get", return_value=_mock_get(_jpeg_bytes())):
        result = recover_from_url(row)
    assert result.confidence == "LOW"
```

- [ ] **Step 2: Run failing tests**

Run: `python3 -m pytest tests/test_image_recovery.py -v`
Expected: FAIL — `ImportError: cannot import name 'ImageRecoveryResult'`.

- [ ] **Step 3: Create `src/image_recovery.py` with dataclass + `recover_from_url`**

Create `src/image_recovery.py`:

```python
"""
Phase 1 confidence-gated image recovery pipeline.

Public API
----------
ImageRecoveryResult : dataclass
    Confidence + evidence carrier returned by every recovery source.

recover_from_url(row)
    Validates an existing Image URL on the row, downloads bytes, scores
    confidence based on SKU presence in URL path and on official domain.

Sources for Phase 2 (image search) drop in alongside the existing three.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps

from src.image_evidence import (
    is_official_domain,
    product_name_appears_in_text,
    sku_appears_in_text,
)


_log = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (compatible; SCH-Intake/1.0)"


@dataclass
class ImageRecoveryResult:
    image_source: str = "none"   # "url" | "pdf_crop" | "page_screenshot" | "manual_upload" | "none"
    confidence: str = "NONE"     # "HIGH" | "MEDIUM" | "LOW" | "NONE"
    evidence: list[str] = field(default_factory=list)
    image_url: str = ""
    local_image_filename: str = ""
    local_image_path: str = ""
    jpeg_bytes: bytes = b""
    error: str = ""

    @property
    def needs_image_review(self) -> bool:
        return self.confidence != "HIGH"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _str_val(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"nan", "none", "null"} else s


def _check_image_content_type(url: str) -> bool:
    """HEAD then GET-Range fallback to confirm Content-Type starts with image/."""
    if not url:
        return False
    headers = {"User-Agent": _USER_AGENT}
    try:
        resp = httpx.head(url, headers=headers, timeout=5, follow_redirects=True)
        if 200 <= resp.status_code < 300:
            ct = resp.headers.get("content-type", "").lower()
            return ct.startswith("image/")
        # Some CDNs (Scene7, Akamai) reject HEAD; try a tiny GET.
        resp2 = httpx.get(
            url,
            headers={**headers, "Range": "bytes=0-1023"},
            timeout=8,
            follow_redirects=True,
        )
        if 200 <= resp2.status_code < 300 or resp2.status_code == 206:
            ct = resp2.headers.get("content-type", "").lower()
            return ct.startswith("image/")
        return False
    except Exception:
        return False


def _download_jpeg_bytes(url: str) -> bytes:
    """Download an image URL and return JPEG-encoded bytes (RGB), or b'' on failure."""
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as exc:
        _log.warning("[IMAGE RECOVERY] download failed url=%s err=%s", url[:80], exc)
        return b""
    try:
        with Image.open(io.BytesIO(resp.content)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue()
    except Exception as exc:
        _log.warning("[IMAGE RECOVERY] decode failed url=%s err=%s", url[:80], exc)
        return b""


# ── recover_from_url ──────────────────────────────────────────────────────────

def recover_from_url(row: dict) -> ImageRecoveryResult:
    """
    Validate the row's existing Image URL and download bytes.

    Confidence rules:
      HIGH   — SKU appears in image URL path OR in fetched page text
               (page text not yet fetched in Phase 1; we rely on URL path
                + product name match against the URL)
      MEDIUM — image URL is on the brand's official domain, no SKU evidence
      LOW    — URL valid but unrelated/unknown domain, no SKU evidence
    """
    url = _str_val(row.get("Image URL"))
    if not url:
        return ImageRecoveryResult()

    if not _check_image_content_type(url):
        return ImageRecoveryResult(
            image_source="url",
            confidence="NONE",
            error="invalid_content_type",
            image_url=url,
        )

    jpeg = _download_jpeg_bytes(url)
    if not jpeg:
        return ImageRecoveryResult(
            image_source="url",
            confidence="NONE",
            error="download_failed",
            image_url=url,
        )

    sku = _str_val(row.get("Model/SKU"))
    brand = _str_val(row.get("Brand"))
    product_name = _str_val(row.get("Product Name"))

    evidence: list[str] = []
    confidence = "LOW"

    # SKU in URL path is the strongest URL-side signal we have without a page fetch.
    parsed_path = urlparse(url).path
    if sku and sku_appears_in_text(sku, parsed_path):
        evidence.append("sku_in_image_url")
        confidence = "HIGH"
    elif sku and sku_appears_in_text(sku, url):
        evidence.append("sku_in_image_url")
        confidence = "HIGH"

    if confidence != "HIGH" and product_name and product_name_appears_in_text(product_name, parsed_path):
        evidence.append("product_name_in_image_url")
        confidence = "HIGH"

    if confidence != "HIGH" and is_official_domain(url, brand):
        evidence.append("official_domain")
        confidence = "MEDIUM"

    return ImageRecoveryResult(
        image_source="url",
        confidence=confidence,
        evidence=evidence,
        image_url=url,
        jpeg_bytes=jpeg,
    )
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_image_recovery.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/image_recovery.py tests/test_image_recovery.py
git commit -m "feat(image-recovery): add ImageRecoveryResult and recover_from_url

Dataclass carries confidence (HIGH/MEDIUM/LOW/NONE), evidence list,
and either jpeg_bytes (recovered) or an error string. recover_from_url
wraps the existing HEAD+GET-range content-type check and adds confidence
scoring based on SKU/product-name presence in the URL path and official
domain match via manufacturer_domains.py."
```

---

## Task 4: `recover_from_pdf_crop`

**Goal:** Render a PDF page with PyMuPDF, find the largest non-icon image region, crop it, and score confidence based on SKU/name presence in the page text.

**Files:**
- Modify: `src/image_recovery.py`
- Modify: `tests/test_image_recovery.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_image_recovery.py`:

```python
# ── recover_from_pdf_crop ─────────────────────────────────────────────────────

import fitz  # PyMuPDF
from src.image_recovery import recover_from_pdf_crop


def _make_pdf_with_image(
    tmp_path,
    text: str,
    image_size: tuple[int, int] = (400, 400),
    pages_text: list[str] | None = None,
) -> str:
    """Build a synthetic PDF: page 1 has `text` and an embedded image of `image_size`.
    Optional pages_text adds extra pages with given text only (no images)."""
    pdf_path = tmp_path / "test.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_text((50, 50), text)

    img = Image.new("RGB", image_size, "blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    rect = fitz.Rect(100, 100, 100 + image_size[0] / 2, 100 + image_size[1] / 2)
    page.insert_image(rect, stream=buf.getvalue())

    for extra in pages_text or []:
        p = doc.new_page(width=595, height=842)
        p.insert_text((50, 50), extra)

    doc.save(str(pdf_path))
    doc.close()
    return str(pdf_path)


def test_pdf_crop_high_when_sku_on_same_page(tmp_path):
    pdf_path = _make_pdf_with_image(tmp_path, "Wolf MDD30TS Warming Drawer Specifications")
    row = {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "Warming Drawer",
        "_source_page_number": 1,
    }
    result = recover_from_pdf_crop(row, pdf_path)
    assert result.image_source == "pdf_crop"
    assert result.confidence == "HIGH"
    assert "sku_on_pdf_page" in result.evidence
    assert len(result.jpeg_bytes) > 0


def test_pdf_crop_medium_when_no_sku_evidence(tmp_path):
    pdf_path = _make_pdf_with_image(tmp_path, "Generic catalog page with no SKU")
    row = {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "Warming Drawer",
        "_source_page_number": 1,
    }
    result = recover_from_pdf_crop(row, pdf_path)
    assert result.confidence == "MEDIUM"


def test_pdf_crop_falls_back_to_adjacent_page_capped_at_medium(tmp_path):
    # Page 1 has no image; page 2 has the image. Row says it came from page 1.
    pdf_path = tmp_path / "test.pdf"
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 50), "Wolf MDD30TS")  # SKU is here, not where the image is
    p2 = doc.new_page(width=595, height=842)
    img = Image.new("RGB", (400, 400), "blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    p2.insert_image(fitz.Rect(100, 100, 300, 300), stream=buf.getvalue())
    doc.save(str(pdf_path))
    doc.close()

    row = {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "Warming Drawer",
        "_source_page_number": 1,
    }
    result = recover_from_pdf_crop(row, str(pdf_path))
    # Even if SKU appears on the original page, the crop is from an adjacent
    # page so confidence is capped at MEDIUM.
    assert result.confidence == "MEDIUM"
    assert "adjacent_page_crop" in result.evidence


def test_pdf_crop_none_when_no_images_anywhere(tmp_path):
    pdf_path = tmp_path / "test.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "Wolf MDD30TS no images here")
    doc.save(str(pdf_path))
    doc.close()
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "_source_page_number": 1}
    result = recover_from_pdf_crop(row, str(pdf_path))
    assert result.confidence == "NONE"


def test_pdf_crop_none_when_pdf_unreadable(tmp_path):
    pdf_path = tmp_path / "missing.pdf"
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "_source_page_number": 1}
    result = recover_from_pdf_crop(row, str(pdf_path))
    assert result.confidence == "NONE"
    assert result.error == "pdf_unreadable"


def test_pdf_crop_filters_tiny_icons(tmp_path):
    pdf_path = tmp_path / "test.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 50), "Wolf MDD30TS")
    # Insert a tiny 50x50 image only — should be filtered as an icon.
    img = Image.new("RGB", (50, 50), "blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    page.insert_image(fitz.Rect(100, 100, 110, 110), stream=buf.getvalue())  # ~10x10pt rect
    doc.save(str(pdf_path))
    doc.close()
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "_source_page_number": 1}
    result = recover_from_pdf_crop(row, str(pdf_path))
    assert result.confidence == "NONE"
```

- [ ] **Step 2: Run failing tests**

Run: `python3 -m pytest tests/test_image_recovery.py::test_pdf_crop_high_when_sku_on_same_page -v`
Expected: FAIL — `ImportError: cannot import name 'recover_from_pdf_crop'`.

- [ ] **Step 3: Append `recover_from_pdf_crop` to `src/image_recovery.py`**

Append to `src/image_recovery.py`:

```python
# ── recover_from_pdf_crop ─────────────────────────────────────────────────────

# Filtering thresholds for PDF image candidates.
_PDF_MIN_PIXEL_AREA = 100 * 100        # discard < 100×100 px
_PDF_MIN_PAGE_AREA_FRACTION = 0.01     # discard < 1% of page area
_PDF_ASPECT_RATIO_MIN = 0.25           # 1:4
_PDF_ASPECT_RATIO_MAX = 4.0            # 4:1
_PDF_RENDER_DPI = 200


def _crop_largest_image_on_pdf_page(page) -> bytes | None:
    """Return JPEG bytes of the largest non-icon image rect on `page`, or None."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None

    images = page.get_images(full=True)
    if not images:
        return None

    page_rect = page.rect
    page_area = page_rect.width * page_rect.height

    candidates: list[tuple[float, fitz.Rect]] = []
    for img_info in images:
        xref = img_info[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue
        for rect in rects:
            w, h = rect.width, rect.height
            if w <= 0 or h <= 0:
                continue
            if (w * h) < (page_area * _PDF_MIN_PAGE_AREA_FRACTION):
                continue
            ratio = w / h
            if ratio < _PDF_ASPECT_RATIO_MIN or ratio > _PDF_ASPECT_RATIO_MAX:
                continue
            candidates.append((w * h, rect))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[0], reverse=True)
    _, best_rect = candidates[0]

    # Render page at high DPI, crop best_rect from pixel-space.
    zoom = _PDF_RENDER_DPI / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, clip=best_rect, alpha=False)
    if pix.width * pix.height < _PDF_MIN_PIXEL_AREA:
        return None
    img_bytes = pix.tobytes("png")
    with Image.open(io.BytesIO(img_bytes)) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()


def recover_from_pdf_crop(row: dict, pdf_path: str | Path) -> ImageRecoveryResult:
    """
    Render the row's PDF page and crop the largest non-icon image region.

    Confidence rules:
      HIGH   — SKU OR product name appears as text on the same page as the crop
      MEDIUM — crop comes from same PDF but adjacent page, or only brand match
      LOW    — no SKU/name/brand evidence anywhere in the PDF
      NONE   — PDF unreadable, no images on this or adjacent pages
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ImageRecoveryResult(error="pymupdf_unavailable")

    pdf_path = str(pdf_path)
    if not Path(pdf_path).exists():
        return ImageRecoveryResult(image_source="pdf_crop", error="pdf_unreadable")

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        return ImageRecoveryResult(image_source="pdf_crop", error=f"pdf_unreadable: {exc}")

    page_number = row.get("_source_page_number")
    if not isinstance(page_number, int) or page_number < 1:
        page_number = 1

    sku = _str_val(row.get("Model/SKU"))
    brand = _str_val(row.get("Brand"))
    product_name = _str_val(row.get("Product Name"))

    try:
        # 1. Try the recorded page first.
        target_idx = page_number - 1
        if target_idx >= doc.page_count or target_idx < 0:
            target_idx = 0

        target_page = doc[target_idx]
        target_text = target_page.get_text("text") or ""

        jpeg = _crop_largest_image_on_pdf_page(target_page)
        is_adjacent = False

        if not jpeg:
            # 2. Fall back to ±1 adjacent pages.
            for offset in (-1, 1):
                idx = target_idx + offset
                if 0 <= idx < doc.page_count:
                    page = doc[idx]
                    j = _crop_largest_image_on_pdf_page(page)
                    if j:
                        jpeg = j
                        is_adjacent = True
                        break

        if not jpeg:
            return ImageRecoveryResult(image_source="pdf_crop", error="no_usable_images_in_pdf")

        # 3. Score confidence based on text on the recorded page.
        evidence: list[str] = []

        if is_adjacent:
            evidence.append("adjacent_page_crop")
            confidence = "MEDIUM"
        else:
            confidence = "LOW"
            sku_hit = bool(sku and sku_appears_in_text(sku, target_text))
            name_hit = bool(product_name and product_name_appears_in_text(product_name, target_text))
            brand_hit = bool(brand and brand.lower() in target_text.lower())

            if sku_hit:
                evidence.append("sku_on_pdf_page")
                confidence = "HIGH"
            elif name_hit:
                evidence.append("product_name_on_pdf_page")
                confidence = "HIGH"
            elif brand_hit:
                evidence.append("brand_on_pdf_page")
                confidence = "MEDIUM"

        return ImageRecoveryResult(
            image_source="pdf_crop",
            confidence=confidence,
            evidence=evidence,
            jpeg_bytes=jpeg,
        )
    finally:
        doc.close()
```

- [ ] **Step 4: Run PDF crop tests**

Run: `python3 -m pytest tests/test_image_recovery.py -v -k pdf_crop`
Expected: 6 PDF tests PASS.

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/image_recovery.py tests/test_image_recovery.py
git commit -m "feat(image-recovery): add recover_from_pdf_crop with PyMuPDF

Renders the recorded PDF page, picks the largest non-icon image rect
(filters tiny / extreme-aspect-ratio candidates), crops at 200 DPI, and
scores confidence based on SKU/name/brand presence in the page text.
Falls back to ±1 adjacent pages with confidence capped at MEDIUM."
```

---

## Task 5: `recover_from_screenshot`

**Goal:** Use Playwright to capture a product image from a product URL — element-screenshot first, full-page+bbox-crop fallback. All Playwright calls in tests are mocked.

**Files:**
- Modify: `src/image_recovery.py`
- Modify: `tests/test_image_recovery.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_image_recovery.py`:

```python
# ── recover_from_screenshot ───────────────────────────────────────────────────

from unittest.mock import MagicMock, patch
from src.image_recovery import recover_from_screenshot


@pytest.fixture
def mock_playwright():
    """Mock Playwright sync_api context manager. Tests configure .page behavior."""
    with patch("src.image_recovery.sync_playwright") as mock_sp:
        cm = MagicMock()
        pw = MagicMock()
        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        cm.__enter__.return_value = pw
        cm.__exit__.return_value = False
        mock_sp.return_value = cm
        pw.chromium.launch.return_value = browser
        browser.new_context.return_value = context
        context.new_page.return_value = page
        # Default: page renders empty content
        page.content.return_value = "<html><body></body></html>"
        yield page


def test_screenshot_high_when_element_selector_hits_with_sku(mock_playwright):
    page = mock_playwright
    page.content.return_value = "<html><body>Wolf MDD30TS warming drawer</body></html>"
    locator = MagicMock()
    locator.count.return_value = 1
    locator.first.bounding_box.return_value = {"x": 0, "y": 0, "width": 600, "height": 600}
    locator.first.is_visible.return_value = True
    locator.first.screenshot.return_value = _jpeg_bytes()
    page.locator.return_value = locator

    row = {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "Warming Drawer",
    }
    result = recover_from_screenshot(row, "https://www.subzero-wolf.com/wolf/warming-drawer")
    assert result.image_source == "page_screenshot"
    assert result.confidence == "HIGH"
    assert "sku_on_page" in result.evidence
    assert len(result.jpeg_bytes) > 0


def test_screenshot_medium_when_official_domain_no_sku(mock_playwright):
    page = mock_playwright
    page.content.return_value = "<html><body>Some unrelated text</body></html>"
    locator = MagicMock()
    locator.count.return_value = 1
    locator.first.bounding_box.return_value = {"x": 0, "y": 0, "width": 600, "height": 600}
    locator.first.is_visible.return_value = True
    locator.first.screenshot.return_value = _jpeg_bytes()
    page.locator.return_value = locator

    row = {"Brand": "Wolf", "Model/SKU": "ZZZ999", "Product Name": "Some Product"}
    result = recover_from_screenshot(row, "https://www.subzero-wolf.com/some-page")
    assert result.confidence == "MEDIUM"
    assert "official_domain" in result.evidence


def test_screenshot_low_when_unknown_domain_no_sku(mock_playwright):
    page = mock_playwright
    page.content.return_value = "<html><body>random page</body></html>"
    locator = MagicMock()
    locator.count.return_value = 1
    locator.first.bounding_box.return_value = {"x": 0, "y": 0, "width": 600, "height": 600}
    locator.first.is_visible.return_value = True
    locator.first.screenshot.return_value = _jpeg_bytes()
    page.locator.return_value = locator

    row = {"Brand": "Wolf", "Model/SKU": "ZZZ999", "Product Name": "Some Product"}
    result = recover_from_screenshot(row, "https://random-site.com/x")
    assert result.confidence == "LOW"


def test_screenshot_falls_back_to_bbox_crop_when_no_selector_matches(mock_playwright):
    page = mock_playwright
    page.content.return_value = "<html><body>Wolf MDD30TS</body></html>"
    no_match_locator = MagicMock()
    no_match_locator.count.return_value = 0
    page.locator.return_value = no_match_locator

    # Full-page screenshot returns valid JPEG bytes (a 1200×1200 plain image).
    full_page_jpeg = _jpeg_bytes(size=(1200, 1200))
    page.screenshot.return_value = full_page_jpeg

    # eval_on_selector_all returns one good <img> bbox candidate.
    page.eval_on_selector_all.return_value = [
        {
            "src": "https://example.com/hero.jpg",
            "x": 100, "y": 100, "width": 600, "height": 600,
        }
    ]
    page.viewport_size = {"width": 1280, "height": 800}

    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "Warming Drawer"}
    result = recover_from_screenshot(row, "https://www.subzero-wolf.com/x")
    assert result.image_source == "page_screenshot"
    assert result.confidence == "HIGH"
    assert "bbox_crop" in result.evidence
    assert len(result.jpeg_bytes) > 0


def test_screenshot_filters_logo_url(mock_playwright):
    page = mock_playwright
    page.content.return_value = "<html><body>Wolf MDD30TS</body></html>"
    no_match_locator = MagicMock()
    no_match_locator.count.return_value = 0
    page.locator.return_value = no_match_locator
    page.screenshot.return_value = _jpeg_bytes(size=(1200, 1200))
    page.eval_on_selector_all.return_value = [
        # logo URL → filtered
        {"src": "https://example.com/header-logo.png", "x": 0, "y": 0, "width": 800, "height": 200},
        # tiny image → filtered
        {"src": "https://example.com/icon.png", "x": 50, "y": 50, "width": 80, "height": 80},
    ]
    page.viewport_size = {"width": 1280, "height": 800}

    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS"}
    result = recover_from_screenshot(row, "https://www.subzero-wolf.com/x")
    # All candidates filtered; no usable image found.
    assert result.confidence == "NONE"
    assert result.error == "no_usable_image_element"


def test_screenshot_returns_none_on_browser_unavailable():
    with patch("src.image_recovery.sync_playwright", side_effect=Exception("browser fail")):
        result = recover_from_screenshot({"Brand": "Wolf"}, "https://example.com")
    assert result.confidence == "NONE"
    assert result.error == "browser_unavailable"


def test_screenshot_returns_none_on_page_load_timeout(mock_playwright):
    page = mock_playwright
    page.goto.side_effect = Exception("Timeout 15000ms exceeded")
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS"}
    result = recover_from_screenshot(row, "https://www.subzero-wolf.com/x")
    assert result.confidence == "NONE"
    assert result.error == "page_load_timeout"
```

- [ ] **Step 2: Run failing tests**

Run: `python3 -m pytest tests/test_image_recovery.py -v -k screenshot`
Expected: FAIL — `ImportError: cannot import name 'recover_from_screenshot'`.

- [ ] **Step 3: Append `recover_from_screenshot` to `src/image_recovery.py`**

Append to `src/image_recovery.py`:

```python
# ── recover_from_screenshot ───────────────────────────────────────────────────

try:
    from playwright.sync_api import sync_playwright  # type: ignore
except Exception:  # pragma: no cover - playwright optional at import
    sync_playwright = None  # type: ignore


_SCREENSHOT_SELECTORS = [
    "img[class*=product-image]",
    "[class*=product] img",
    "[class*=gallery] img",
    "[class*=hero] img",
    "[class*=media] img",
    'img[id*="product"]',
]
_SCREENSHOT_MIN_DIMENSION = 200            # px each side
_SCREENSHOT_PAGE_LOAD_TIMEOUT_MS = 15_000
_SCREENSHOT_LOGO_HINTS = ("logo", "icon", "sprite", "favicon")
_SCREENSHOT_ASPECT_RATIO_MIN = 0.25
_SCREENSHOT_ASPECT_RATIO_MAX = 4.0


def _is_logo_url(src: str) -> bool:
    s = (src or "").lower()
    return any(hint in s for hint in _SCREENSHOT_LOGO_HINTS)


def _bbox_passes_filters(bbox: dict) -> bool:
    w, h = bbox.get("width", 0), bbox.get("height", 0)
    if w < _SCREENSHOT_MIN_DIMENSION or h < _SCREENSHOT_MIN_DIMENSION:
        return False
    ratio = w / h if h else 0
    if ratio < _SCREENSHOT_ASPECT_RATIO_MIN or ratio > _SCREENSHOT_ASPECT_RATIO_MAX:
        return False
    return True


def _crop_jpeg_bytes_from_full_page(full_page_png: bytes, bbox: dict) -> bytes:
    """Crop bbox out of a full-page screenshot, return JPEG bytes."""
    with Image.open(io.BytesIO(full_page_png)) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        x, y = int(bbox["x"]), int(bbox["y"])
        w, h = int(bbox["width"]), int(bbox["height"])
        cropped = img.crop((x, y, x + w, y + h))
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()


def _score_screenshot_confidence(
    *,
    page_text: str,
    sku: str,
    product_name: str,
    brand: str,
    product_url: str,
) -> tuple[str, list[str]]:
    """Apply the screenshot confidence rules from the spec."""
    evidence: list[str] = []
    if sku and sku_appears_in_text(sku, page_text):
        evidence.append("sku_on_page")
        return "HIGH", evidence
    if product_name and product_name_appears_in_text(product_name, page_text):
        evidence.append("product_name_on_page")
        return "HIGH", evidence
    if is_official_domain(product_url, brand):
        evidence.append("official_domain")
        return "MEDIUM", evidence
    return "LOW", evidence


def recover_from_screenshot(row: dict, product_url: str) -> ImageRecoveryResult:
    """Open product_url in headless Chromium, capture and score a product image."""
    if sync_playwright is None:
        return ImageRecoveryResult(image_source="page_screenshot", error="browser_unavailable")
    if not product_url:
        return ImageRecoveryResult(image_source="page_screenshot", error="no_product_url")

    sku = _str_val(row.get("Model/SKU"))
    brand = _str_val(row.get("Brand"))
    product_name = _str_val(row.get("Product Name"))

    try:
        sp_ctx = sync_playwright()
    except Exception as exc:
        return ImageRecoveryResult(
            image_source="page_screenshot",
            error="browser_unavailable",
        )

    try:
        with sp_ctx as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=_USER_AGENT)
                page = context.new_page()

                try:
                    page.goto(
                        product_url,
                        wait_until="networkidle",
                        timeout=_SCREENSHOT_PAGE_LOAD_TIMEOUT_MS,
                    )
                except Exception:
                    return ImageRecoveryResult(
                        image_source="page_screenshot",
                        error="page_load_timeout",
                    )

                page_text = page.content() or ""
                # Strip tags lightly so token search isn't fooled by attributes.
                # We pass the raw HTML — sku/name matchers tokenize over alphanum only.

                # 1) Element-selector pass
                for selector in _SCREENSHOT_SELECTORS:
                    try:
                        loc = page.locator(selector)
                        if loc.count() == 0:
                            continue
                        first = loc.first
                        if not first.is_visible():
                            continue
                        bbox = first.bounding_box()
                        if not bbox or not _bbox_passes_filters(bbox):
                            continue
                        jpeg = first.screenshot(type="jpeg", quality=85)
                        if not jpeg:
                            continue
                        confidence, evidence = _score_screenshot_confidence(
                            page_text=page_text,
                            sku=sku,
                            product_name=product_name,
                            brand=brand,
                            product_url=product_url,
                        )
                        evidence.append(f"selector:{selector}")
                        return ImageRecoveryResult(
                            image_source="page_screenshot",
                            confidence=confidence,
                            evidence=evidence,
                            jpeg_bytes=jpeg,
                        )
                    except Exception:
                        continue

                # 2) Bounding-box fallback over all <img>
                try:
                    candidates = page.eval_on_selector_all(
                        "img",
                        """
                        (els) => els.map(el => {
                            const r = el.getBoundingClientRect();
                            return {
                                src: el.currentSrc || el.src || "",
                                x: r.left, y: r.top,
                                width: r.width, height: r.height,
                            };
                        })
                        """,
                    ) or []
                except Exception:
                    candidates = []

                viewport = page.viewport_size or {"width": 1280, "height": 800}
                fold = viewport.get("height", 800)

                filtered = [
                    c for c in candidates
                    if not _is_logo_url(c.get("src", ""))
                    and _bbox_passes_filters(c)
                    and c.get("y", 0) < fold
                ]
                if not filtered:
                    return ImageRecoveryResult(
                        image_source="page_screenshot",
                        error="no_usable_image_element",
                    )

                filtered.sort(key=lambda c: c["width"] * c["height"], reverse=True)
                best = filtered[0]

                try:
                    full_png = page.screenshot(full_page=True, type="png")
                except Exception:
                    return ImageRecoveryResult(
                        image_source="page_screenshot",
                        error="screenshot_failed",
                    )

                try:
                    jpeg = _crop_jpeg_bytes_from_full_page(full_png, best)
                except Exception as exc:
                    return ImageRecoveryResult(
                        image_source="page_screenshot",
                        error=f"crop_failed: {exc}",
                    )

                confidence, evidence = _score_screenshot_confidence(
                    page_text=page_text,
                    sku=sku,
                    product_name=product_name,
                    brand=brand,
                    product_url=product_url,
                )
                evidence.append("bbox_crop")
                return ImageRecoveryResult(
                    image_source="page_screenshot",
                    confidence=confidence,
                    evidence=evidence,
                    jpeg_bytes=jpeg,
                )
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as exc:
        return ImageRecoveryResult(
            image_source="page_screenshot",
            error="browser_unavailable",
        )
```

- [ ] **Step 4: Run screenshot tests**

Run: `python3 -m pytest tests/test_image_recovery.py -v -k screenshot`
Expected: 7 screenshot tests PASS.

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/image_recovery.py tests/test_image_recovery.py
git commit -m "feat(image-recovery): add Playwright screenshot fallback

recover_from_screenshot tries element-level screenshot first against a
prioritized selector list (product-image / gallery / hero / media), then
falls back to a full-page screenshot + PIL crop of the largest above-the-fold
img element. Filters logos/icons/sprites and extreme aspect ratios.
Confidence scored from page text (HIGH on SKU/name match, MEDIUM on official
domain, LOW otherwise)."
```

---

## Task 6: Orchestrator `recover_image_for_row`

**Goal:** Wire all three sources into the priority flow defined by the spec.

**Files:**
- Modify: `src/image_recovery.py`
- Modify: `tests/test_image_recovery.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_image_recovery.py`:

```python
# ── recover_image_for_row orchestrator ────────────────────────────────────────

from src.image_recovery import recover_image_for_row


def _result(confidence="HIGH", source="url", evidence=None, jpeg=b"x"):
    from src.image_recovery import ImageRecoveryResult
    return ImageRecoveryResult(
        image_source=source,
        confidence=confidence,
        evidence=evidence or [],
        jpeg_bytes=jpeg,
    )


def test_orchestrator_returns_url_high_immediately():
    row = {"Image URL": "https://x.com/y.jpg", "_source_pdf_id": "abc"}
    with patch("src.image_recovery.recover_from_url", return_value=_result(confidence="HIGH")) as m_url, \
         patch("src.image_recovery.recover_from_pdf_crop") as m_pdf, \
         patch("src.image_recovery.recover_from_screenshot") as m_shot:
        out = recover_image_for_row(row, pdf_lookup={"abc": "/tmp/x.pdf"}, session_id="s1")
    assert out.image_source == "url"
    assert out.confidence == "HIGH"
    m_pdf.assert_not_called()
    m_shot.assert_not_called()


def test_orchestrator_pdf_high_returns_immediately():
    row = {"Image URL": "", "_source_pdf_id": "abc", "Product URL": "https://x.com/p"}
    with patch("src.image_recovery.recover_from_url", return_value=_result(confidence="NONE", source="none", jpeg=b"")), \
         patch("src.image_recovery.recover_from_pdf_crop", return_value=_result(confidence="HIGH", source="pdf_crop")) as m_pdf, \
         patch("src.image_recovery.recover_from_screenshot") as m_shot:
        out = recover_image_for_row(row, pdf_lookup={"abc": "/tmp/x.pdf"}, session_id="s1")
    assert out.image_source == "pdf_crop"
    assert out.confidence == "HIGH"
    m_shot.assert_not_called()


def test_orchestrator_screenshot_high_beats_pdf_medium():
    row = {"Image URL": "", "_source_pdf_id": "abc", "Product URL": "https://x.com/p"}
    with patch("src.image_recovery.recover_from_url", return_value=_result(confidence="NONE", source="none", jpeg=b"")), \
         patch("src.image_recovery.recover_from_pdf_crop", return_value=_result(confidence="MEDIUM", source="pdf_crop")), \
         patch("src.image_recovery.recover_from_screenshot", return_value=_result(confidence="HIGH", source="page_screenshot")):
        out = recover_image_for_row(row, pdf_lookup={"abc": "/tmp/x.pdf"}, session_id="s1")
    assert out.image_source == "page_screenshot"
    assert out.confidence == "HIGH"


def test_orchestrator_pdf_medium_wins_tie_against_screenshot_medium():
    row = {"Image URL": "", "_source_pdf_id": "abc", "Product URL": "https://x.com/p"}
    with patch("src.image_recovery.recover_from_url", return_value=_result(confidence="NONE", source="none", jpeg=b"")), \
         patch("src.image_recovery.recover_from_pdf_crop", return_value=_result(confidence="MEDIUM", source="pdf_crop")), \
         patch("src.image_recovery.recover_from_screenshot", return_value=_result(confidence="MEDIUM", source="page_screenshot")):
        out = recover_image_for_row(row, pdf_lookup={"abc": "/tmp/x.pdf"}, session_id="s1")
    assert out.image_source == "pdf_crop"


def test_orchestrator_low_low_returns_first_low_no_file_attached():
    row = {"Image URL": "", "_source_pdf_id": "abc", "Product URL": "https://x.com/p"}
    with patch("src.image_recovery.recover_from_url", return_value=_result(confidence="NONE", source="none", jpeg=b"")), \
         patch("src.image_recovery.recover_from_pdf_crop", return_value=_result(confidence="LOW", source="pdf_crop")), \
         patch("src.image_recovery.recover_from_screenshot", return_value=_result(confidence="LOW", source="page_screenshot")):
        out = recover_image_for_row(row, pdf_lookup={"abc": "/tmp/x.pdf"}, session_id="s1")
    assert out.confidence == "LOW"
    assert out.image_source == "pdf_crop"  # first LOW returned


def test_orchestrator_all_none_returns_none_result():
    row = {"Image URL": "", "Product URL": ""}
    with patch("src.image_recovery.recover_from_url", return_value=_result(confidence="NONE", source="none", jpeg=b"")), \
         patch("src.image_recovery.recover_from_pdf_crop") as m_pdf, \
         patch("src.image_recovery.recover_from_screenshot") as m_shot:
        out = recover_image_for_row(row, pdf_lookup=None, session_id=None)
    assert out.confidence == "NONE"
    m_pdf.assert_not_called()
    m_shot.assert_not_called()


def test_orchestrator_skips_screenshot_when_disabled():
    row = {"Image URL": "", "_source_pdf_id": "abc", "Product URL": "https://x.com/p"}
    with patch("src.image_recovery.recover_from_url", return_value=_result(confidence="NONE", source="none", jpeg=b"")), \
         patch("src.image_recovery.recover_from_pdf_crop", return_value=_result(confidence="MEDIUM", source="pdf_crop")), \
         patch("src.image_recovery.recover_from_screenshot") as m_shot:
        out = recover_image_for_row(
            row,
            pdf_lookup={"abc": "/tmp/x.pdf"},
            session_id="s1",
            enable_screenshot=False,
        )
    assert out.image_source == "pdf_crop"
    m_shot.assert_not_called()
```

- [ ] **Step 2: Run failing tests**

Run: `python3 -m pytest tests/test_image_recovery.py -v -k orchestrator`
Expected: FAIL — `ImportError: cannot import name 'recover_image_for_row'`.

- [ ] **Step 3: Append `recover_image_for_row` to `src/image_recovery.py`**

Append to `src/image_recovery.py`:

```python
# ── recover_image_for_row orchestrator ────────────────────────────────────────

_CONFIDENCE_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


def _better(a: ImageRecoveryResult | None, b: ImageRecoveryResult) -> ImageRecoveryResult:
    """Return whichever result has higher confidence; on tie keep `a` (first wins)."""
    if a is None:
        return b
    if _CONFIDENCE_RANK[b.confidence] > _CONFIDENCE_RANK[a.confidence]:
        return b
    return a


def recover_image_for_row(
    row: dict,
    pdf_lookup: dict[str, str] | None = None,
    session_id: str | None = None,
    enable_screenshot: bool = True,
) -> ImageRecoveryResult:
    """
    Try sources in priority order:
      1) existing Image URL (HIGH short-circuits)
      2) PDF crop          (HIGH short-circuits; MEDIUM/LOW held)
      3) Screenshot        (HIGH short-circuits; MEDIUM/LOW compared)

    On tie between PDF MEDIUM and Screenshot MEDIUM, PDF wins (held first).
    """
    held: ImageRecoveryResult | None = None

    # 1) URL on row
    url_val = _str_val(row.get("Image URL"))
    if url_val:
        url_result = recover_from_url(row)
        if url_result.confidence == "HIGH":
            return url_result
        # URL recovery NONE/MEDIUM/LOW results are NOT held — when the row
        # already had a URL but it didn't validate to HIGH, we fall through
        # to PDF/screenshot. We don't record it because it was the row's
        # given URL — keeping the original would mask that we tried.
        if url_result.confidence in ("MEDIUM", "LOW"):
            held = url_result

    # 2) PDF crop
    pdf_id = _str_val(row.get("_source_pdf_id"))
    pdf_path = (pdf_lookup or {}).get(pdf_id) if pdf_id else None
    if pdf_id and pdf_path:
        pdf_result = recover_from_pdf_crop(row, pdf_path)
        if pdf_result.confidence == "HIGH":
            return pdf_result
        if pdf_result.confidence in ("MEDIUM", "LOW"):
            held = _better(held, pdf_result)

    # 3) Screenshot
    if enable_screenshot:
        product_url = _str_val(row.get("Product URL"))
        if product_url:
            shot_result = recover_from_screenshot(row, product_url)
            if shot_result.confidence == "HIGH":
                return shot_result
            if shot_result.confidence in ("MEDIUM", "LOW"):
                held = _better(held, shot_result)

    return held if held is not None else ImageRecoveryResult()
```

- [ ] **Step 4: Run orchestrator tests**

Run: `python3 -m pytest tests/test_image_recovery.py -v -k orchestrator`
Expected: 7 orchestrator tests PASS.

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/image_recovery.py tests/test_image_recovery.py
git commit -m "feat(image-recovery): add recover_image_for_row orchestrator

Implements the priority flow: URL → PDF → screenshot. HIGH at any step
short-circuits. PDF MEDIUM is held first, so a tied screenshot MEDIUM
loses (PDF is the user-provided source of truth)."
```

---

## Task 7: `recover_images_for_dataframe` + `cleanup_old_sessions`

**Goal:** Public batch API. Iterates rows, persists files to `.tmp/uploads/{session_id}/images/`, returns updated DataFrame plus diagnostics list (no `jpeg_bytes` ever leaks into diagnostics).

**Files:**
- Modify: `src/image_recovery.py`
- Modify: `tests/test_image_recovery.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_image_recovery.py`:

```python
# ── recover_images_for_dataframe + cleanup_old_sessions ───────────────────────

import os
import time

import pandas as pd

from src.image_recovery import recover_images_for_dataframe, cleanup_old_sessions


def test_dataframe_recovery_skips_high_confidence_rows(tmp_path):
    df = pd.DataFrame([
        {
            "Product Name": "Existing Good", "Brand": "Wolf", "Model/SKU": "AAA",
            "Image URL": "https://x.com/y.jpg", "Product URL": "",
            "confidence": "HIGH", "_source_pdf_id": "",
        },
        {
            "Product Name": "Needs Recovery", "Brand": "Wolf", "Model/SKU": "BBB",
            "Image URL": "", "Product URL": "https://x.com/p",
            "_source_pdf_id": "",
        },
    ])
    with patch("src.image_recovery.recover_image_for_row") as m:
        m.return_value = _result(confidence="HIGH", source="page_screenshot", jpeg=_jpeg_bytes())
        out_df, diags = recover_images_for_dataframe(
            df, pdf_lookup=None, session_id="testsess",
            enable_screenshot=True,
        )
    # Called only for the second row.
    assert m.call_count == 1


def test_dataframe_recovery_writes_files_to_session_images_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df = pd.DataFrame([
        {
            "Product Name": "Wolf Drawer", "Brand": "Wolf", "Model/SKU": "MDD30TS",
            "Image URL": "", "Product URL": "https://x.com/p",
            "_source_pdf_id": "",
        },
    ])
    with patch("src.image_recovery.recover_image_for_row") as m:
        m.return_value = _result(confidence="HIGH", source="page_screenshot", jpeg=_jpeg_bytes())
        out_df, diags = recover_images_for_dataframe(
            df, pdf_lookup=None, session_id="sess123",
            enable_screenshot=True,
        )
    expected_dir = tmp_path / ".tmp" / "uploads" / "sess123" / "images"
    files = list(expected_dir.glob("*.jpg"))
    assert len(files) == 1
    row = out_df.iloc[0]
    assert row["confidence"] == "HIGH"
    assert row["image_source"] == "page_screenshot"
    assert row["local_image_filename"].endswith(".jpg")
    assert row["local_image_path"] == str(files[0])


def test_dataframe_recovery_does_not_write_files_for_low_confidence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df = pd.DataFrame([
        {"Product Name": "X", "Brand": "Y", "Model/SKU": "Z",
         "Image URL": "", "Product URL": "https://x.com/p", "_source_pdf_id": ""},
    ])
    with patch("src.image_recovery.recover_image_for_row") as m:
        m.return_value = _result(confidence="LOW", source="page_screenshot", jpeg=_jpeg_bytes())
        out_df, diags = recover_images_for_dataframe(
            df, pdf_lookup=None, session_id="sess123",
            enable_screenshot=True,
        )
    images_dir = tmp_path / ".tmp" / "uploads" / "sess123" / "images"
    assert not images_dir.exists() or not list(images_dir.glob("*.jpg"))
    row = out_df.iloc[0]
    assert row["confidence"] == "LOW"
    assert row["image_source"] == "page_screenshot"
    assert row["local_image_filename"] == ""
    assert row["local_image_path"] == ""


def test_dataframe_recovery_diagnostics_have_no_jpeg_bytes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df = pd.DataFrame([
        {"Product Name": "X", "Brand": "Y", "Model/SKU": "Z",
         "Image URL": "", "Product URL": "https://x.com/p", "_source_pdf_id": ""},
    ])
    with patch("src.image_recovery.recover_image_for_row") as m:
        m.return_value = _result(confidence="HIGH", source="page_screenshot", jpeg=_jpeg_bytes())
        out_df, diags = recover_images_for_dataframe(
            df, pdf_lookup=None, session_id="sess123",
        )
    assert len(diags) == 1
    assert "jpeg_bytes" not in diags[0]
    # Required keys
    for k in ("row_index", "product_name", "brand", "model_sku", "image_source", "confidence", "evidence"):
        assert k in diags[0]


def test_dataframe_recovery_preserves_internal_source_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df = pd.DataFrame([
        {"Product Name": "X", "Brand": "Y", "Model/SKU": "Z",
         "Image URL": "", "Product URL": "https://x.com/p",
         "_source_pdf_id": "abc", "_source_page_number": 3, "_source_filename": "spec.pdf"},
    ])
    with patch("src.image_recovery.recover_image_for_row", return_value=_result(confidence="NONE", source="none", jpeg=b"")):
        out_df, _ = recover_images_for_dataframe(df, pdf_lookup=None, session_id="s")
    row = out_df.iloc[0]
    assert row["_source_pdf_id"] == "abc"
    assert row["_source_page_number"] == 3
    assert row["_source_filename"] == "spec.pdf"


# ── cleanup_old_sessions ──────────────────────────────────────────────────────

def test_cleanup_removes_old_session_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    base = tmp_path / ".tmp" / "uploads"
    base.mkdir(parents=True)

    fresh = base / "fresh-sess"
    fresh.mkdir()
    (fresh / "marker").write_text("x")

    stale = base / "stale-sess"
    stale.mkdir()
    (stale / "marker").write_text("x")

    # Backdate stale's mtime by 48 hours.
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    deleted = cleanup_old_sessions(max_age_hours=24)
    assert deleted == 1
    assert fresh.exists()
    assert not stale.exists()


def test_cleanup_handles_missing_root_quietly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    deleted = cleanup_old_sessions(max_age_hours=24)
    assert deleted == 0
```

- [ ] **Step 2: Run failing tests**

Run: `python3 -m pytest tests/test_image_recovery.py -v -k "dataframe_recovery or cleanup"`
Expected: FAIL — `ImportError: cannot import name 'recover_images_for_dataframe'`.

- [ ] **Step 3: Append `recover_images_for_dataframe` and `cleanup_old_sessions`**

Append to `src/image_recovery.py`:

```python
# ── recover_images_for_dataframe ──────────────────────────────────────────────

import os
import time

import pandas as pd

from src.image_assets import build_image_filename


_TMP_ROOT = ".tmp/uploads"


def _session_image_dir(session_id: str) -> Path:
    return Path(_TMP_ROOT) / session_id / "images"


_RECOVERY_COLUMNS = [
    "image_source", "confidence", "evidence", "needs_image_review",
    "local_image_filename", "local_image_path",
]


def _ensure_recovery_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in _RECOVERY_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    if "Image URL" not in df.columns:
        df["Image URL"] = ""
    return df


def _row_already_high(row: pd.Series) -> bool:
    return _str_val(row.get("confidence")).upper() == "HIGH"


def recover_images_for_dataframe(
    df: pd.DataFrame,
    pdf_lookup: dict[str, str] | None = None,
    session_id: str | None = None,
    enable_screenshot: bool = True,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Run the recovery pipeline on rows that don't already carry a HIGH-confidence
    image. Writes recovered files to .tmp/uploads/{session_id}/images/ and
    annotates rows with image_source / confidence / evidence / needs_image_review
    / local_image_filename / local_image_path.

    Returns (updated_df, diagnostics_list). Diagnostics never include jpeg_bytes.
    """
    df = df.copy()
    df = _ensure_recovery_columns(df)
    diagnostics: list[dict] = []

    if not session_id:
        session_id = "default"
    images_dir = _session_image_dir(session_id)

    for idx, row in df.iterrows():
        if _row_already_high(row):
            continue

        row_dict = row.to_dict()
        result = recover_image_for_row(
            row_dict,
            pdf_lookup=pdf_lookup,
            session_id=session_id,
            enable_screenshot=enable_screenshot,
        )

        # Persist evidence on the row regardless of confidence.
        df.at[idx, "image_source"] = result.image_source
        df.at[idx, "confidence"] = result.confidence
        df.at[idx, "evidence"] = ";".join(result.evidence)
        df.at[idx, "needs_image_review"] = result.needs_image_review

        if result.image_source == "url" and result.image_url:
            df.at[idx, "Image URL"] = result.image_url

        # Only write files for HIGH/MEDIUM. LOW results are diagnostic only.
        if result.confidence in ("HIGH", "MEDIUM") and result.jpeg_bytes:
            try:
                images_dir.mkdir(parents=True, exist_ok=True)
                filename = build_image_filename(
                    brand=_str_val(row_dict.get("Brand")),
                    model_sku=_str_val(row_dict.get("Model/SKU")),
                    product_name=_str_val(row_dict.get("Product Name")),
                )
                # Deduplicate against files already in this session dir.
                base_name = filename
                counter = 2
                target = images_dir / filename
                while target.exists():
                    stem, ext = base_name.rsplit(".", 1) if "." in base_name else (base_name, "jpg")
                    filename = f"{stem}_{counter}.{ext}"
                    target = images_dir / filename
                    counter += 1

                target.write_bytes(result.jpeg_bytes)
                df.at[idx, "local_image_filename"] = filename
                df.at[idx, "local_image_path"] = str(target)
            except Exception as exc:
                # File write failed — keep the result metadata but no path.
                _log.warning("[IMAGE RECOVERY] disk write failed idx=%s err=%s", idx, exc)

        diagnostics.append({
            "row_index": int(idx),
            "product_name": _str_val(row_dict.get("Product Name")),
            "brand": _str_val(row_dict.get("Brand")),
            "model_sku": _str_val(row_dict.get("Model/SKU")),
            "image_source": result.image_source,
            "confidence": result.confidence,
            "evidence": list(result.evidence),
            "error": result.error,
        })

    return df, diagnostics


# ── cleanup_old_sessions ──────────────────────────────────────────────────────

def cleanup_old_sessions(max_age_hours: int = 24) -> int:
    """Delete .tmp/uploads/<id> dirs older than max_age_hours. Returns count deleted."""
    import shutil

    root = Path(_TMP_ROOT)
    if not root.exists():
        return 0
    threshold = time.time() - max_age_hours * 3600
    deleted = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime < threshold:
                shutil.rmtree(child, ignore_errors=True)
                if not child.exists():
                    deleted += 1
        except Exception as exc:
            _log.warning("[IMAGE RECOVERY] cleanup failed dir=%s err=%s", child, exc)
    return deleted
```

- [ ] **Step 4: Run dataframe + cleanup tests**

Run: `python3 -m pytest tests/test_image_recovery.py -v -k "dataframe_recovery or cleanup"`
Expected: 7 tests PASS.

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/image_recovery.py tests/test_image_recovery.py
git commit -m "feat(image-recovery): add batch dataframe API + session cleanup

recover_images_for_dataframe annotates rows with image_source/confidence/
evidence/needs_image_review/local_image_filename/local_image_path,
writes recovered files to .tmp/uploads/{session_id}/images/, and never
leaks jpeg_bytes into diagnostics. cleanup_old_sessions removes session
directories older than max_age_hours."
```

---

## Task 8: `product_enrichment.py` becomes a thin delegator

**Goal:** Replace the existing `recover_images_for_dataframe` in `product_enrichment.py` with a delegator so existing callers (backend, Streamlit) continue to import the same path while gaining the new richer signature.

**Files:**
- Modify: `src/product_enrichment.py`
- Test: `tests/test_product_enrichment.py`

- [ ] **Step 1: Read current `recover_images_for_dataframe` to confirm shape**

Run: `grep -n "def recover_images_for_dataframe" src/product_enrichment.py`
Expected: a single function around line 813.

- [ ] **Step 2: Write the delegation test**

Append to `tests/test_product_enrichment.py`:

```python
def test_product_enrichment_delegates_to_image_recovery():
    """The legacy entry point now forwards to src.image_recovery.recover_images_for_dataframe."""
    from unittest.mock import patch
    import pandas as pd

    df = pd.DataFrame([{"Product Name": "X", "Image URL": "", "Product URL": ""}])

    with patch("src.image_recovery.recover_images_for_dataframe") as m:
        m.return_value = (df, [])
        from src.product_enrichment import recover_images_for_dataframe as _legacy
        _legacy(df, pdf_lookup={"a": "/tmp/x.pdf"}, session_id="s", enable_screenshot=True)
        assert m.called
        kwargs = m.call_args.kwargs
        assert kwargs["pdf_lookup"] == {"a": "/tmp/x.pdf"}
        assert kwargs["session_id"] == "s"
        assert kwargs["enable_screenshot"] is True
```

- [ ] **Step 3: Run the failing test**

Run: `python3 -m pytest tests/test_product_enrichment.py -v -k delegates`
Expected: FAIL — the existing function does not delegate; `m.called` is False.

- [ ] **Step 4: Replace the body of the existing `recover_images_for_dataframe`**

In `src/product_enrichment.py`, find the existing `def recover_images_for_dataframe(...)` (line ~813) and replace its **body** with the delegation. Keep the function signature compatible (accept the new kwargs); also keep its module-level docstring if present:

```python
def recover_images_for_dataframe(
    df,
    pdf_lookup: dict[str, str] | None = None,
    session_id: str | None = None,
    enable_screenshot: bool = True,
):
    """
    Backward-compatible alias for src.image_recovery.recover_images_for_dataframe.

    Existing callers that pass only `df` continue to work — PDF crop is
    skipped (no pdf_lookup) and screenshot defaults to True. Real production
    callers in app.py and backend/main.py pass pdf_lookup + session_id.
    """
    from src.image_recovery import recover_images_for_dataframe as _impl
    return _impl(
        df,
        pdf_lookup=pdf_lookup,
        session_id=session_id,
        enable_screenshot=enable_screenshot,
    )
```

Delete the previous body of the function (the one that called `_try_image_from_url` directly). Leave `_try_image_from_url`, `_check_image_content_type`, and `extract_image_url` in place — `enrich_row` still uses them.

- [ ] **Step 5: Run delegation test**

Run: `python3 -m pytest tests/test_product_enrichment.py -v -k delegates`
Expected: PASS.

- [ ] **Step 6: Run full suite**

Run: `python3 -m pytest -q`
Expected: all pass. **If existing `recover_images_for_dataframe` tests fail because the function no longer manually iterates rows, update them to mock `src.image_recovery.recover_images_for_dataframe` instead.**

- [ ] **Step 7: Commit**

```bash
git add src/product_enrichment.py tests/test_product_enrichment.py
git commit -m "refactor(product-enrichment): delegate recover_images_for_dataframe to image_recovery

Phase 1 image recovery lives in src/image_recovery.py. The legacy entry
point now forwards calls so backend/main.py and app.py imports continue
to work unchanged while gaining pdf_lookup/session_id/enable_screenshot
kwargs."
```

---

## Task 9: Annotate parsed PDF rows with `_source_*` metadata

**Goal:** `parse_pdf_rows` records the SHA1 of the PDF bytes, the page number per row, and the original filename so PDF crop can locate the right page later.

**Files:**
- Modify: `src/document_parser.py`
- Test: `tests/test_document_parser.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_document_parser.py`:

```python
def test_parsed_rows_carry_source_pdf_id_and_page(tmp_path):
    import io as _io
    import fitz
    from src.document_parser import parse_pdf_rows

    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 50), "Wolf MDD30TS Warming Drawer 1 ea $999")
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((50, 50), "Sub-Zero ID36R Refrigerator 1 ea $5000")
    pdf_path = tmp_path / "spec.pdf"
    doc.save(str(pdf_path))
    doc.close()

    class _Up:
        def __init__(self, raw, name):
            self._raw = raw
            self._pos = 0
            self.name = name
        def read(self):
            return self._raw
        def seek(self, p):
            self._pos = p

    raw = pdf_path.read_bytes()
    up = _Up(raw, "spec.pdf")
    rows = parse_pdf_rows(up)
    assert rows, "expected at least one row from synthetic PDF"

    # All rows share the same _source_pdf_id (it's the SHA1 of the bytes).
    pdf_ids = {r.get("_source_pdf_id") for r in rows}
    assert len(pdf_ids) == 1
    assert next(iter(pdf_ids))

    # Page numbers are 1-indexed.
    pages = {r.get("_source_page_number") for r in rows}
    assert pages.issubset({1, 2})

    # Filename preserved.
    assert all(r.get("_source_filename") == "spec.pdf" for r in rows)
```

- [ ] **Step 2: Run failing test**

Run: `python3 -m pytest tests/test_document_parser.py -v -k source_pdf_id`
Expected: FAIL — `_source_pdf_id` not in parsed rows.

- [ ] **Step 3: Modify `parse_pdf_rows` to annotate rows**

In `src/document_parser.py`, replace the existing `parse_pdf_rows` body to track page index, compute the SHA1 once, and annotate every appended row:

```python
def parse_pdf_rows(
    pdf_file,
    project: str = "",
    room: str = "",
    supplier: str = "",
    notes: str = "",
) -> list[dict]:
    """
    Extract structured product rows from a PDF using heuristic text/table parsing.

    Phase 1 image recovery (2026-05-08) annotates each row with internal
    `_source_pdf_id` (SHA1[:12] of the PDF bytes), `_source_page_number`
    (1-indexed page the row came from), and `_source_filename` (when the
    upload object exposes a `name` attribute).
    """
    try:
        import fitz
    except ImportError:
        raise ImportError("PyMuPDF is required. Run: pip install pymupdf")

    import hashlib

    raw = pdf_file.read()
    pdf_file.seek(0)

    pdf_id = hashlib.sha1(raw).hexdigest()[:12]
    filename = getattr(pdf_file, "name", "") or ""

    doc = fitz.open(stream=raw, filetype="pdf")
    all_rows: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()

    for page_index, page in enumerate(doc):
        page_number = page_index + 1

        # 1. Try table extraction first
        table_rows = _parse_table_rows(page, project, room, supplier, notes)
        if table_rows:
            for r in table_rows:
                key = (
                    str(r.get("Product Name", "")).lower().strip(),
                    str(r.get("Model/SKU", "")).lower().strip(),
                )
                if key not in seen_keys:
                    seen_keys.add(key)
                    r["_source_pdf_id"] = pdf_id
                    r["_source_page_number"] = page_number
                    r["_source_filename"] = filename
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
            key = (
                str(row.get("Product Name", "")).lower().strip(),
                str(row.get("Model/SKU", "")).lower().strip(),
            )
            if key not in seen_keys:
                seen_keys.add(key)
                row["_source_pdf_id"] = pdf_id
                row["_source_page_number"] = page_number
                row["_source_filename"] = filename
                all_rows.append(row)

    doc.close()
    return all_rows
```

- [ ] **Step 4: Run document parser tests**

Run: `python3 -m pytest tests/test_document_parser.py -v`
Expected: all PASS, including the new annotation test.

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/document_parser.py tests/test_document_parser.py
git commit -m "feat(document-parser): annotate PDF rows with _source_pdf_id/page/filename

Each row carries SHA1[:12] of the PDF bytes (so re-uploads dedupe), the
1-indexed page number, and the original filename. Image recovery uses
these to locate the source page for PDF crop fallback."
```

---

## Task 10: ZIP export — manifest columns, path validation, LOW skip

**Goal:** Manifest carries the four new columns. ZIP export reads from `local_image_path` first (validated), copies LOW images nowhere, surfaces internal `_source_*` only in debug export.

**Files:**
- Modify: `src/programa_export.py`
- Test: `tests/test_image_assets.py`
- Test: `tests/test_programa_export.py`

- [ ] **Step 1: Write failing tests in `tests/test_image_assets.py`**

Append to `tests/test_image_assets.py`:

```python
# ── manifest 4 new columns + path validation + LOW skip ───────────────────────

def test_manifest_includes_image_source_confidence_evidence_needs_review(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    row = _make_exportable_row()
    row["image_source"] = "page_screenshot"
    row["confidence"] = "MEDIUM"
    row["evidence"] = "official_domain;bbox_crop"
    row["needs_image_review"] = True
    row["Image URL"] = ""
    zip_bytes = export_programa_zip([row])
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        manifest_text = zf.read("manifest.csv").decode("utf-8")
    for col in ("Image Source", "Confidence", "Evidence", "Needs Image Review"):
        assert col in manifest_text


def test_zip_reads_local_image_path_first_no_remote_download(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    images_dir = tmp_path / ".tmp" / "uploads" / "sess1" / "images"
    images_dir.mkdir(parents=True)
    local = images_dir / "wolf_wwd30.jpg"
    local.write_bytes(_make_image_bytes())

    row = _make_exportable_row()
    row["local_image_path"] = str(local)
    row["local_image_filename"] = "wolf_wwd30.jpg"
    row["confidence"] = "HIGH"
    row["image_source"] = "pdf_crop"
    row["evidence"] = "sku_on_pdf_page"
    row["Image URL"] = "https://wolfappliance.com/img/wwd30.jpg"
    row["_session_id"] = "sess1"

    with patch("src.image_assets.httpx.get") as mock_get:
        zip_bytes = export_programa_zip([row], session_id="sess1")
    mock_get.assert_not_called()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    assert "images/wolf_wwd30.jpg" in names


def test_zip_skips_path_outside_session_images_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Write a file OUTSIDE .tmp/uploads/sess1/images
    bad = tmp_path / "outside.jpg"
    bad.write_bytes(_make_image_bytes())

    row = _make_exportable_row()
    row["local_image_path"] = str(bad)
    row["local_image_filename"] = "outside.jpg"
    row["confidence"] = "HIGH"
    row["image_source"] = "pdf_crop"
    row["evidence"] = ""
    row["Image URL"] = ""

    zip_bytes = export_programa_zip([row], session_id="sess1")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        manifest_text = zf.read("manifest.csv").decode("utf-8")
    assert not any(n.startswith("images/") for n in names)
    assert "invalid_local_path" in manifest_text


def test_zip_skips_missing_local_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    row = _make_exportable_row()
    row["local_image_path"] = str(tmp_path / ".tmp" / "uploads" / "sess1" / "images" / "missing.jpg")
    row["local_image_filename"] = "missing.jpg"
    row["confidence"] = "HIGH"
    row["image_source"] = "pdf_crop"
    row["evidence"] = ""
    row["Image URL"] = ""

    zip_bytes = export_programa_zip([row], session_id="sess1")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        manifest_text = zf.read("manifest.csv").decode("utf-8")
    assert not any(n.startswith("images/") for n in names)
    assert "invalid_local_path" in manifest_text


def test_zip_skips_low_confidence_row(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    images_dir = tmp_path / ".tmp" / "uploads" / "sess1" / "images"
    images_dir.mkdir(parents=True)
    local = images_dir / "wolf_wwd30.jpg"
    local.write_bytes(_make_image_bytes())

    row = _make_exportable_row()
    row["local_image_path"] = str(local)  # file exists in valid place
    row["local_image_filename"] = "wolf_wwd30.jpg"
    row["confidence"] = "LOW"
    row["image_source"] = "page_screenshot"
    row["evidence"] = ""
    row["Image URL"] = ""

    zip_bytes = export_programa_zip([row], session_id="sess1")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        manifest_text = zf.read("manifest.csv").decode("utf-8")
    assert not any(n.startswith("images/") for n in names)
    assert "low_confidence_skipped" in manifest_text
```

- [ ] **Step 2: Write failing tests in `tests/test_programa_export.py`**

Append to `tests/test_programa_export.py`:

```python
def test_internal_source_columns_excluded_from_standard_export():
    from src.programa_export import build_programa_import_dataframe, PROGRAMA_COLUMNS
    rows = [{
        "Include": True,
        "Product Name": "X",
        "Brand": "Y",
        "Model/SKU": "Z",
        "Quantity": 1,
        "_source_pdf_id": "abc",
        "_source_page_number": 2,
        "_source_filename": "spec.pdf",
    }]
    df = build_programa_import_dataframe(rows)
    assert list(df.columns) == PROGRAMA_COLUMNS
    assert "_source_pdf_id" not in df.columns


def test_internal_source_columns_present_in_debug_export():
    from src.programa_export import build_programa_debug_dataframe
    rows = [{
        "Include": True,
        "Product Name": "X",
        "Brand": "Y",
        "Model/SKU": "Z",
        "Quantity": 1,
        "_source_pdf_id": "abc123",
        "_source_page_number": 2,
        "_source_filename": "spec.pdf",
    }]
    df = build_programa_debug_dataframe(rows)
    assert "_source_pdf_id" in df.columns
    assert df.iloc[0]["_source_pdf_id"] == "abc123"
    assert df.iloc[0]["_source_page_number"] == 2
    assert df.iloc[0]["_source_filename"] == "spec.pdf"
```

- [ ] **Step 3: Run failing tests**

Run: `python3 -m pytest tests/test_image_assets.py tests/test_programa_export.py -v -k "manifest_includes or local_image_path or skips_path or low_confidence_row or skips_missing_local or internal_source"`
Expected: FAIL — manifest doesn't yet include the new columns; `export_programa_zip` doesn't yet honor `session_id` or `local_image_path`; debug df doesn't include `_source_*`.

- [ ] **Step 4: Update `_MANIFEST_COLUMNS` and `_DEBUG_EXTRA_COLUMNS`**

In `src/programa_export.py`, replace the existing constants:

```python
_DEBUG_EXTRA_COLUMNS: list[str] = [
    "Confidence Score",
    "Source Type",
    "AI Category Confidence",
    "Category Source",
    "Local Image Path",
    "Image Filename",
    "Dimension Source URL",
    "Dimension Confidence",
    "Dimension Source Type",
    "Dimension Lookup Status",
    # Phase 1 image recovery internal source metadata
    "_source_pdf_id",
    "_source_page_number",
    "_source_filename",
    # Phase 1 image recovery confidence/evidence
    "image_source",
    "confidence",
    "evidence",
    "needs_image_review",
]

_MANIFEST_COLUMNS: list[str] = [
    "Product Name",
    "Brand",
    "SKU/Model",
    "Product URL",
    "Original Image URL",
    "Local Image Filename",
    "Image Status",
    "Image Source",
    "Confidence",
    "Evidence",
    "Needs Image Review",
    "Error",
]
```

- [ ] **Step 5: Update `export_programa_zip` to honor `session_id`, `local_image_path` validation, and LOW skip**

Replace the body of `export_programa_zip` in `src/programa_export.py` with:

```python
def export_programa_zip(
    rows,
    include_images: bool = True,
    manual_images: dict | None = None,
    session_id: str | None = None,
) -> bytes:
    """
    Build a ZIP archive for the Programa export.

    Image-resolution priority per row:
      1. local_image_path (validated under .tmp/uploads/{session_id}/images/, .jpg)
      2. manual_images[i] bytes
      3. row's Image URL (download via download_and_convert_image)
      4. otherwise: status "missing_image_url"

    LOW-confidence rows are never copied to images/, even if local_image_path
    is set; manifest records "low_confidence_skipped".
    """
    from src.image_assets import download_and_convert_image as _download_convert, build_image_filename

    manual_images = manual_images or {}
    row_list = _to_row_list(rows)

    df = build_programa_import_dataframe(rows)
    csv_bytes = export_programa_csv(df)

    seen_filenames: dict[str, int] = {}

    def _unique_filename(filename: str) -> str:
        if filename not in seen_filenames:
            seen_filenames[filename] = 1
            return filename
        seen_filenames[filename] += 1
        if "." in filename:
            stem, ext = filename.rsplit(".", 1)
            return f"{stem}_{seen_filenames[filename]}.{ext}"
        return f"{filename}_{seen_filenames[filename]}"

    def _validate_local_path(path_str: str) -> tuple[bool, str]:
        """Return (ok, reason). Reason is empty when ok."""
        if not session_id:
            return False, "no_session_id"
        try:
            from pathlib import Path as _P
            p = _P(path_str).resolve()
        except Exception:
            return False, "invalid_path"
        if not p.exists():
            return False, "file_not_found"
        if p.suffix.lower() not in (".jpg", ".jpeg"):
            return False, "wrong_extension"
        if p.stat().st_size <= 0:
            return False, "empty_file"
        try:
            allowed_root = (_P(".tmp") / "uploads" / session_id / "images").resolve()
        except Exception:
            return False, "invalid_session_dir"
        try:
            p.relative_to(allowed_root)
        except ValueError:
            return False, "path_outside_session_dir"
        return True, ""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("programa_import.csv", csv_bytes.decode("utf-8"))

        manifest_rows: list[dict] = []
        for i, r in enumerate(row_list):
            if not _is_exportable(r):
                continue

            image_url = _str_val(r.get("Image URL"))
            brand = _str_val(r.get("Brand"))
            sku = _str_val(r.get("Model/SKU"))
            product_name = _str_val(r.get("Product Name"))
            confidence = _str_val(r.get("confidence")).upper()
            local_path = _str_val(r.get("local_image_path"))
            local_filename = _str_val(r.get("local_image_filename"))

            manifest_row: dict = {
                "Product Name": product_name,
                "Brand": brand,
                "SKU/Model": sku,
                "Product URL": _str_val(r.get("Product URL")),
                "Original Image URL": image_url,
                "Local Image Filename": "",
                "Image Status": "missing_image_url",
                "Image Source": _str_val(r.get("image_source")),
                "Confidence": confidence,
                "Evidence": _str_val(r.get("evidence")),
                # needs_image_review is stored as string "True"/"False" by image_recovery
                # (Task 7 contract — pandas string dtype constraint). Compare against the
                # string explicitly; do not call bool() on it (bool("False") is True).
                _nir_raw = str(r.get("needs_image_review", "")).strip().lower()
                "Needs Image Review": "false" if _nir_raw == "false" else "true",
                "Error": "",
            }

            if not include_images:
                manifest_rows.append(manifest_row)
                continue

            # LOW-confidence rows are never written to images/.
            if confidence == "LOW":
                manifest_row["Image Status"] = "low_confidence_skipped"
                manifest_rows.append(manifest_row)
                continue

            wrote_image = False

            # 1) local_image_path
            if local_path:
                ok, reason = _validate_local_path(local_path)
                if ok:
                    filename = _unique_filename(local_filename or build_image_filename(brand, sku, product_name))
                    from pathlib import Path as _P
                    manifest_row["Local Image Filename"] = filename
                    manifest_row["Image Status"] = "downloaded"
                    zf.writestr(f"images/{filename}", _P(local_path).read_bytes())
                    wrote_image = True
                else:
                    manifest_row["Image Status"] = "invalid_local_path"
                    manifest_row["Error"] = reason

            # 2) manual_images
            if not wrote_image and manual_images.get(i):
                filename = _unique_filename(build_image_filename(brand, sku, product_name))
                manifest_row["Local Image Filename"] = filename
                manifest_row["Image Status"] = "manually_uploaded"
                manifest_row["Error"] = ""
                zf.writestr(f"images/{filename}", manual_images[i])
                wrote_image = True

            # 3) remote URL download
            if not wrote_image and _is_public_https_image_url(image_url):
                result = _download_convert(
                    image_url,
                    brand=brand,
                    model_sku=sku,
                    product_name=product_name,
                )
                manifest_row["Image Status"] = result["image_status"]
                manifest_row["Error"] = result.get("error", "")
                if result["image_status"] == "downloaded" and result.get("jpeg_bytes"):
                    filename = _unique_filename(result["local_image_filename"])
                    manifest_row["Local Image Filename"] = filename
                    zf.writestr(f"images/{filename}", result["jpeg_bytes"])

            manifest_rows.append(manifest_row)

        if manifest_rows:
            manifest_df = pd.DataFrame(manifest_rows, columns=_MANIFEST_COLUMNS)
            mbuf = io.StringIO()
            manifest_df.to_csv(mbuf, index=False)
            zf.writestr("manifest.csv", mbuf.getvalue())

    return buf.getvalue()
```

- [ ] **Step 6: Run failing tests to verify pass**

Run: `python3 -m pytest tests/test_image_assets.py tests/test_programa_export.py -v -k "manifest_includes or local_image_path or skips_path or low_confidence_row or skips_missing_local or internal_source"`
Expected: all PASS.

- [ ] **Step 7: Run full suite**

Run: `python3 -m pytest -q`
Expected: all pass. **If existing manifest assertions read `Image Source`-less manifests**, fix them by ignoring the new columns or asserting their presence.

- [ ] **Step 8: Commit**

```bash
git add src/programa_export.py tests/test_image_assets.py tests/test_programa_export.py
git commit -m "feat(programa-export): add 4 manifest columns + local_image_path validation + LOW skip

Manifest gains Image Source, Confidence, Evidence, Needs Image Review.
ZIP export now resolves images in priority: validated local_image_path
under .tmp/uploads/{session_id}/images/, then manual_images bytes, then
remote URL download. LOW-confidence rows are never copied; status becomes
low_confidence_skipped. Internal _source_* columns surface only in
build_programa_debug_dataframe."
```

---

## Task 11: Streamlit integration (`app.py`)

**Goal:** On PDF upload, write to `.tmp/uploads/{session_id}/pdfs/{pdf_id}.pdf` and keep bytes in session state. The Recover button passes `pdf_lookup`, `session_id`, `enable_screenshot=True`. ZIP export passes `session_id`.

**Files:**
- Modify: `app.py`

> Manual smoke test only — Streamlit UI is not unit-tested in this repo. Existing automated tests must still pass.

- [ ] **Step 1: Locate the PDF upload handler**

Run: `grep -n "parse_pdf_rows\|file_uploader.*pdf\|application/pdf" app.py | head -10`
Expected: a small number of lines around the PDF upload step.

- [ ] **Step 2: Add a session_id helper near the top of `app.py`**

In `app.py`, after the existing imports and before the main UI body, add:

```python
import hashlib as _hashlib
import uuid as _uuid
from pathlib import Path as _Path

def _ensure_session_id() -> str:
    if "session_id" not in st.session_state:
        st.session_state.session_id = _uuid.uuid4().hex[:12]
    return st.session_state.session_id


def _save_uploaded_pdf(raw_bytes: bytes, filename: str) -> tuple[str, str]:
    """Save uploaded PDF to .tmp/uploads/{session_id}/pdfs/{pdf_id}.pdf.
    Returns (pdf_id, pdf_path)."""
    sid = _ensure_session_id()
    pdf_id = _hashlib.sha1(raw_bytes).hexdigest()[:12]
    pdfs_dir = _Path(".tmp") / "uploads" / sid / "pdfs"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdfs_dir / f"{pdf_id}.pdf"
    if not pdf_path.exists():
        pdf_path.write_bytes(raw_bytes)
    if "uploaded_pdfs" not in st.session_state:
        st.session_state.uploaded_pdfs = {}
    st.session_state.uploaded_pdfs[pdf_id] = raw_bytes
    return pdf_id, str(pdf_path)


def _build_pdf_lookup() -> dict[str, str]:
    sid = _ensure_session_id()
    pdfs_dir = _Path(".tmp") / "uploads" / sid / "pdfs"
    lookup: dict[str, str] = {}
    if pdfs_dir.exists():
        for f in pdfs_dir.glob("*.pdf"):
            lookup[f.stem] = str(f)
    # Fallback: write any session-state bytes back out if disk file missing.
    for pdf_id, raw in (st.session_state.get("uploaded_pdfs") or {}).items():
        if pdf_id not in lookup:
            pdfs_dir.mkdir(parents=True, exist_ok=True)
            target = pdfs_dir / f"{pdf_id}.pdf"
            target.write_bytes(raw)
            lookup[pdf_id] = str(target)
    return lookup
```

- [ ] **Step 3: Wire `_save_uploaded_pdf` into the existing PDF parse step**

Find the call to `parse_pdf_rows(uploaded_file)` in `app.py`. Immediately before it, capture the bytes and persist them:

```python
# Phase 1 image recovery: persist PDF bytes for later crop access.
_pdf_raw = uploaded_file.read()
uploaded_file.seek(0)
_save_uploaded_pdf(_pdf_raw, getattr(uploaded_file, "name", "upload.pdf"))
```

If `parse_pdf_rows` is called inside a loop over multiple `uploaded_file` objects, repeat the same two lines per file.

- [ ] **Step 4: Wire `_build_pdf_lookup()` into the Recover button**

Find the existing "Recover Missing Images" button (search `recover_images_for_dataframe` in `app.py`). Replace its call with:

```python
sid = _ensure_session_id()
pdf_lookup = _build_pdf_lookup()
recovered_df, diagnostics = recover_images_for_dataframe(
    df,
    pdf_lookup=pdf_lookup,
    session_id=sid,
    enable_screenshot=True,
)
```

- [ ] **Step 5: Pass `session_id` to `export_programa_zip`**

Find the existing ZIP download button (search `export_programa_zip(included` in `app.py`). Update the call:

```python
data=export_programa_zip(included, manual_images=_manual_uploads, session_id=_ensure_session_id()),
```

- [ ] **Step 6: Add a startup cleanup call**

Near the very top of `app.py` after imports, add:

```python
# Phase 1 image recovery: prune session dirs older than 24h on app boot.
try:
    from src.image_recovery import cleanup_old_sessions as _cleanup_old_sessions
    _cleanup_old_sessions(max_age_hours=24)
except Exception:
    pass
```

- [ ] **Step 7: Run full suite**

Run: `python3 -m pytest -q`
Expected: all pass (Streamlit code is not unit-tested but imports must succeed).

- [ ] **Step 8: Manual smoke test**

Run: `streamlit run app.py` and:
1. Upload a PDF with a Wolf or Sub-Zero spec sheet
2. Click "Recover Missing Images"
3. Confirm `.tmp/uploads/{session_id}/pdfs/{pdf_id}.pdf` exists on disk
4. Confirm rows that recovered an image now show `confidence` and `image_source` in the data editor
5. Click "Download ZIP" — open the ZIP and confirm:
   - `images/` contains JPGs for HIGH/MEDIUM rows only
   - `manifest.csv` has `Image Source`, `Confidence`, `Evidence`, `Needs Image Review` columns
6. Stop the server and verify `.tmp/` is gitignored (`git status` should show no `.tmp/` files)

- [ ] **Step 9: Commit**

```bash
git add app.py
git commit -m "feat(streamlit): wire PDF persistence + pdf_lookup into image recovery

Uploads now write to .tmp/uploads/{session_id}/pdfs/ and bytes are also
held in session state as a fallback. Recover button passes pdf_lookup,
session_id, enable_screenshot=True. ZIP export passes session_id so
local_image_path validation has the correct allowed root. App boot
prunes session dirs older than 24h."
```

---

## Task 12: Backend integration (`backend/main.py`)

**Goal:** New `/intake/upload-pdf` writes the same temp tree; existing `/intake/recover-images` builds `pdf_lookup` and passes it.

**Files:**
- Modify: `backend/main.py`
- Modify: `tests/test_backend_main.py` (extend) or create if it doesn't exist

> Inspect `backend/main.py` first — endpoint paths and the FastAPI app object name (`app`) may already exist. Don't duplicate routes.

- [ ] **Step 1: Inspect existing endpoints**

Run: `grep -n "POST\|@app\.post\|/intake/" backend/main.py | head -30`
Expected: list of existing endpoints; confirm whether `/intake/upload-pdf` already exists.

- [ ] **Step 2: Write the failing endpoint test**

In `tests/test_backend_main.py` (create if missing):

```python
def test_upload_pdf_writes_to_session_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)

    # Build a tiny valid PDF (one page, no images).
    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "Hello")
    pdf_bytes = doc.tobytes()
    doc.close()

    resp = client.post(
        "/intake/upload-pdf",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body
    assert "pdf_id" in body

    # File landed on disk.
    sid = body["session_id"]
    pdf_id = body["pdf_id"]
    expected = tmp_path / ".tmp" / "uploads" / sid / "pdfs" / f"{pdf_id}.pdf"
    assert expected.exists()


def test_recover_images_endpoint_uses_pdf_lookup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    from backend.main import app
    from unittest.mock import patch

    client = TestClient(app)

    # Pre-populate a session and pdf file.
    sid = "abcdef123456"
    pdfs_dir = tmp_path / ".tmp" / "uploads" / sid / "pdfs"
    pdfs_dir.mkdir(parents=True)
    (pdfs_dir / "deadbeef0001.pdf").write_bytes(b"%PDF-1.4 mock")

    rows = [{
        "Product Name": "X", "Brand": "Y", "Model/SKU": "Z",
        "Image URL": "", "Product URL": "https://example.com/p",
        "_source_pdf_id": "deadbeef0001",
    }]

    with patch("backend.main.recover_images_for_dataframe") as m:
        import pandas as pd
        m.return_value = (pd.DataFrame(rows), [])
        resp = client.post(
            "/intake/recover-images",
            json={"session_id": sid, "rows": rows},
        )
    assert resp.status_code == 200
    kwargs = m.call_args.kwargs
    assert kwargs["pdf_lookup"] == {"deadbeef0001": str(pdfs_dir / "deadbeef0001.pdf")}
    assert kwargs["session_id"] == sid
    assert kwargs["enable_screenshot"] is True
```

- [ ] **Step 3: Run failing tests**

Run: `python3 -m pytest tests/test_backend_main.py -v -k "upload_pdf or recover_images"`
Expected: FAIL — endpoints don't yet honor session_id / write to `.tmp/`.

- [ ] **Step 4: Implement the upload endpoint**

In `backend/main.py`, add (or replace the existing `/intake/upload-pdf` route):

```python
import hashlib
import uuid
from pathlib import Path as _Path
from fastapi import UploadFile, File, Header, HTTPException

from src.document_parser import parse_pdf_rows
from src.image_recovery import recover_images_for_dataframe, cleanup_old_sessions


_TMP_UPLOADS = _Path(".tmp/uploads")


def _ensure_session_dir(session_id: str) -> _Path:
    pdfs = _TMP_UPLOADS / session_id / "pdfs"
    pdfs.mkdir(parents=True, exist_ok=True)
    return pdfs


@app.post("/intake/upload-pdf")
async def upload_pdf(
    file: UploadFile = File(...),
    x_session_id: str | None = Header(default=None),
):
    raw = await file.read()
    session_id = x_session_id or uuid.uuid4().hex[:12]
    pdf_id = hashlib.sha1(raw).hexdigest()[:12]

    pdfs_dir = _ensure_session_dir(session_id)
    pdf_path = pdfs_dir / f"{pdf_id}.pdf"
    if not pdf_path.exists():
        pdf_path.write_bytes(raw)

    # Parse rows from the same bytes.
    import io as _io
    class _Wrap:
        def __init__(self, raw, name):
            self._raw = raw
            self.name = name
        def read(self):
            return self._raw
        def seek(self, p):
            pass
    rows = parse_pdf_rows(_Wrap(raw, file.filename or "upload.pdf"))
    return {"session_id": session_id, "pdf_id": pdf_id, "rows": rows}
```

If `/intake/upload-pdf` already exists, replace its body with the same logic.

- [ ] **Step 5: Implement / update the recover-images endpoint**

In `backend/main.py`, find the existing recover-images endpoint (search `recover_images_for_dataframe`). Replace its body so it builds `pdf_lookup` and passes the new kwargs:

```python
@app.post("/intake/recover-images")
async def recover_images(payload: dict):
    session_id = payload.get("session_id") or "default"
    rows = payload.get("rows") or []
    pdfs_dir = _TMP_UPLOADS / session_id / "pdfs"
    pdf_lookup = {f.stem: str(f) for f in pdfs_dir.glob("*.pdf")} if pdfs_dir.exists() else {}

    import pandas as pd
    df = pd.DataFrame(rows)
    out_df, diagnostics = recover_images_for_dataframe(
        df,
        pdf_lookup=pdf_lookup,
        session_id=session_id,
        enable_screenshot=True,
    )
    return {"rows": out_df.to_dict(orient="records"), "diagnostics": diagnostics}
```

- [ ] **Step 6: Add a startup cleanup hook**

In `backend/main.py`, add the FastAPI startup event (or extend if one exists):

```python
@app.on_event("startup")
def _startup_cleanup():
    try:
        cleanup_old_sessions(max_age_hours=24)
    except Exception:
        pass
```

- [ ] **Step 7: Run failing tests to verify pass**

Run: `python3 -m pytest tests/test_backend_main.py -v -k "upload_pdf or recover_images"`
Expected: 2 tests PASS.

- [ ] **Step 8: Run full suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add backend/main.py tests/test_backend_main.py
git commit -m "feat(backend): wire image recovery endpoints into temp session dirs

POST /intake/upload-pdf writes to .tmp/uploads/{session_id}/pdfs/ and
returns parsed rows. POST /intake/recover-images builds pdf_lookup from
the temp dir and passes session_id + enable_screenshot=True. Startup
hook prunes session dirs older than 24h."
```

---

## Final verification

- [ ] **Step 1: Run full suite**

Run: `python3 -m pytest -q`
Expected: all tests pass — no regressions.

- [ ] **Step 2: Run lint / formatter if configured**

Run: `python3 -m ruff check src/ tests/ 2>/dev/null || echo "ruff not configured"`
Expected: clean output or "ruff not configured".

- [ ] **Step 3: Smoke-test Streamlit one more time**

Run: `streamlit run app.py`
Verify:
- Upload a known-good Wolf PDF
- Click Recover Missing Images
- Inspect rows: at least one row should show `image_source = pdf_crop` and `confidence = HIGH` or `MEDIUM`
- Inspect ZIP: `images/` contains JPGs only for non-LOW rows; `manifest.csv` has all 4 new columns

- [ ] **Step 4: Confirm `.gitignore` is honored**

Run: `git status --short`
Expected: no `.tmp/...` paths in the output.

---

## Self-review notes (filled by author)

**Spec coverage:**
- Confidence model + dataclass → Task 3
- PDF crop fallback → Task 4
- Screenshot fallback → Task 5
- Orchestrator priority + LOW handling → Task 6
- Batch dataframe API + diagnostics-without-bytes → Task 7
- Backward-compat delegator → Task 8
- PDF row metadata annotation → Task 9
- Manifest 4 new columns + path validation + LOW skip → Task 10
- Streamlit integration → Task 11
- Backend integration → Task 12
- Internal `_source_*` excluded from standard export, present in debug → Task 1 schema + Task 10 debug columns

**Type/method consistency:**
- `recover_image_for_row(row, pdf_lookup, session_id, enable_screenshot)` — same signature in Tasks 6, 7, 11, 12
- `recover_images_for_dataframe(df, pdf_lookup, session_id, enable_screenshot)` — same signature in Tasks 7, 8, 11, 12
- `ImageRecoveryResult` field names consistent across all task code
- Manifest column order in `_MANIFEST_COLUMNS` matches the assertions in Task 10 tests
