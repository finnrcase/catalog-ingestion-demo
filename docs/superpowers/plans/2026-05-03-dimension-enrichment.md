# Dimension Enrichment Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/dimension_enrichment.py` — a manufacturer-first dimension lookup pipeline — and integrate it as an automatic second pass inside the existing `enrich_row()` flow so every product with Brand + Model/SKU gets a targeted dimension search before export.

**Architecture:** A new standalone module owns brand→domain lookup, query generation, HTML/PDF content parsing, and confidence assignment. `product_enrichment.py` calls `find_dimensions(row)` after its regular enrichment pass whenever a row still lacks complete 3D dimensions. Four new internal columns (`Dimension Source URL`, `Dimension Confidence`, `Dimension Source Type`, `Dimension Lookup Status`) are persisted on the row for review and debug export; full diagnostic data (queries tried, URLs checked, evidence text) is returned in the API response only.

**Tech Stack:** Python, httpx (existing), PyMuPDF/fitz (existing), BeautifulSoup4 (new), Brave Search API (existing via `src/brave_search.py`), pytest.

---

## File Map

| Action | File | Purpose |
|--------|------|---------|
| Modify | `src/intake_schema.py` | Add 4 new internal columns to `ALL_COLUMNS` |
| Modify | `src/programa_export.py` | Add 4 columns to `_DEBUG_EXTRA_COLUMNS` |
| Modify | `requirements.txt` | Add `beautifulsoup4>=4.12.0` |
| Create | `src/dimension_enrichment.py` | New module — full dimension lookup pipeline |
| Create | `tests/test_dimension_enrichment.py` | All tests for the new module |
| Modify | `src/product_enrichment.py` | Call `find_dimensions()` after regular enrichment pass |
| Modify | `backend/main.py` | Add `dimension_diagnostics` to `IntakeResponse` and enrich endpoint |

---

### Task 1: Schema columns + debug export columns

**Files:**
- Modify: `src/intake_schema.py:14-41`
- Modify: `src/programa_export.py:64-70`
- Modify: `requirements.txt`

- [ ] **Step 1: Add 4 new columns to `ALL_COLUMNS` in `src/intake_schema.py`**

Insert after `"Image Upload Status"` (line 40):

```python
ALL_COLUMNS: list[str] = [
    "Include",
    "Project",
    "Room",
    "Product Name",
    "Brand",
    "Dimensions",
    "Finish / Color",
    "Color",
    "Material",
    "Model/SKU",
    "Product Category",
    "Quantity",
    "Price",
    "Supplier",
    "Product URL",
    "Notes",
    "Source Type",
    "Status",
    "Import Type",
    "photo_only",
    "AI Category Confidence",
    "Category Source",
    "Image URL",
    "Local Image Path",
    "Image Filename",
    "Image Upload Status",
    "Dimension Source URL",
    "Dimension Confidence",
    "Dimension Source Type",
    "Dimension Lookup Status",
]
```

- [ ] **Step 2: Add 4 columns to `_DEBUG_EXTRA_COLUMNS` in `src/programa_export.py`**

```python
_DEBUG_EXTRA_COLUMNS: list[str] = [
    "Confidence Score",
    "Source Type",
    "AI Category Confidence",
    "Category Source",
    "Local Image Path",
    "Dimension Source URL",
    "Dimension Confidence",
    "Dimension Source Type",
    "Dimension Lookup Status",
]
```

- [ ] **Step 3: Add `beautifulsoup4` to `requirements.txt`**

Add after the existing html2text line (or at the end of the HTML/parsing section):
```
beautifulsoup4>=4.12.0
```

- [ ] **Step 4: Run existing tests to confirm nothing broke**

```
pytest tests/test_programa_export.py -v
```
Expected: all passing.

- [ ] **Step 5: Commit**

```
git add src/intake_schema.py src/programa_export.py requirements.txt
git commit -m "feat: add dimension diagnostic columns to schema and debug export"
```

---

### Task 2: Module scaffold — DimensionResult, constants, empty-result factory

**Files:**
- Create: `tests/test_dimension_enrichment.py`
- Create: `src/dimension_enrichment.py`

- [ ] **Step 1: Write failing test — import and DimensionResult shape**

```python
# tests/test_dimension_enrichment.py
from src.dimension_enrichment import (
    DimensionResult,
    BRAND_DOMAIN_TABLE,
    RETAILER_DOMAINS,
    _make_not_found_result,
)


def test_dimension_result_defaults():
    r = DimensionResult()
    assert r.dimensions == ""
    assert r.width == ""
    assert r.height == ""
    assert r.depth == ""
    assert r.length == ""
    assert r.source_url == ""
    assert r.confidence == "none"
    assert r.source_type == "none"
    assert r.status == "not_found"
    assert r.queries_tried == []
    assert r.urls_checked == []
    assert r.evidence_text == ""
    assert r.failure_reason == ""


def test_brand_domain_table_has_known_brands():
    assert BRAND_DOMAIN_TABLE["scotsman"] == "scotsman-ice.com"
    assert BRAND_DOMAIN_TABLE["kohler"] == "kohler.com"
    assert BRAND_DOMAIN_TABLE["wolf"] == "subzero-wolf.com"
    assert BRAND_DOMAIN_TABLE["ge"] == "geappliances.com"


def test_retailer_domains_has_expected_sites():
    assert "build.com" in RETAILER_DOMAINS
    assert "ajmadison.com" in RETAILER_DOMAINS
    assert "homedepot.com" in RETAILER_DOMAINS


def test_make_not_found_result_carries_diagnostics():
    r = _make_not_found_result(
        queries_tried=["q1", "q2"],
        urls_checked=["https://example.com"],
        failure_reason="no results found",
    )
    assert r.status == "not_found"
    assert r.confidence == "none"
    assert r.queries_tried == ["q1", "q2"]
    assert r.failure_reason == "no results found"
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_dimension_enrichment.py -v
```
Expected: ImportError — `src.dimension_enrichment` does not exist.

- [ ] **Step 3: Create `src/dimension_enrichment.py` with scaffold**

```python
"""
Dimension lookup pipeline for SCH DesignOps Intake.

Searches manufacturer and retailer sources for product W/H/D dimensions.
Called by product_enrichment.enrich_row() after the regular enrichment pass.

Public API
----------
find_dimensions(row: dict) -> DimensionResult
    Perform full dimension lookup for a single intake row.

BRAND_DOMAIN_TABLE : dict[str, str]
    Known brand → official domain mappings. Add new entries here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class DimensionResult:
    # Persisted to intake row
    dimensions: str = ""
    width: str = ""
    height: str = ""
    depth: str = ""
    length: str = ""
    source_url: str = ""
    confidence: str = "none"      # "high" | "medium" | "low" | "none"
    source_type: str = "none"     # "manufacturer_page" | "manufacturer_pdf"
                                  # | "retailer_page" | "retailer_pdf" | "none"
    status: str = "not_found"     # "found" | "not_found" | "low_confidence_skipped"
    # Diagnostics — API response only, not persisted
    queries_tried: list[str] = field(default_factory=list)
    urls_checked: list[str] = field(default_factory=list)
    evidence_text: str = ""
    failure_reason: str = ""


# ── Constants ──────────────────────────────────────────────────────────────────

BRAND_DOMAIN_TABLE: dict[str, str] = {
    "scotsman": "scotsman-ice.com",
    "kohler": "kohler.com",
    "kallista": "kallista.com",
    "miele": "mieleusa.com",
    "wolf": "subzero-wolf.com",
    "sub-zero": "subzero-wolf.com",
    "thermador": "thermador.com",
    "dacor": "dacor.com",
    "samsung": "samsung.com",
    "ge": "geappliances.com",
    "ge appliances": "geappliances.com",
    "bosch": "bosch-home.com",
    "fisher & paykel": "fisherpaykel.com",
    "frigidaire": "frigidaire.com",
    "lg": "lg.com",
    "whirlpool": "whirlpool.com",
    "kitchenaid": "kitchenaid.com",
    "viking": "vikingrange.com",
}

RETAILER_DOMAINS: list[str] = [
    "build.com",
    "ajmadison.com",
    "bestbuy.com",
    "homedepot.com",
    "lowes.com",
    "wayfair.com",
    "ferguson.com",
    "appliancesconnection.com",
]

_APPLIANCE_CATEGORIES: frozenset[str] = frozenset({
    "Appliances", "Appliance", "Kitchen Appliances",
    "Kitchen Appliance", "Built-in Appliances",
})

# Session-scoped domain discovery cache
_discovered_domains: dict[str, str] = {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_not_found_result(
    queries_tried: list[str] | None = None,
    urls_checked: list[str] | None = None,
    failure_reason: str = "",
) -> DimensionResult:
    return DimensionResult(
        status="not_found",
        confidence="none",
        source_type="none",
        queries_tried=queries_tried or [],
        urls_checked=urls_checked or [],
        failure_reason=failure_reason,
    )


def find_dimensions(row: dict) -> DimensionResult:
    """Perform full dimension lookup for a single intake row. Stub — implemented in Task 11."""
    return _make_not_found_result(failure_reason="not implemented")
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_dimension_enrichment.py -v
```
Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```
git add src/dimension_enrichment.py tests/test_dimension_enrichment.py
git commit -m "feat: scaffold DimensionResult, brand domain table, constants"
```

---

### Task 3: Model normalization

**Files:**
- Modify: `tests/test_dimension_enrichment.py` — append new tests
- Modify: `src/dimension_enrichment.py` — add `_normalize_model_variants`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_dimension_enrichment.py`:

```python
from src.dimension_enrichment import _normalize_model_variants


def test_normalize_model_no_spaces_unchanged():
    result = _normalize_model_variants("SCN60PA1SU")
    assert result[0] == "SCN60PA1SU"
    # No spaces → no-spaces variant is the same, no dashes variant either
    assert len(result) == 1


def test_normalize_model_with_spaces_generates_variants():
    result = _normalize_model_variants("HV 48 SS")
    assert "HV 48 SS" in result
    assert "HV48SS" in result
    assert "HV-48-SS" in result


def test_normalize_model_suffix_stripped_when_short():
    # Last token "SS" is 2 chars → stripped variant included
    result = _normalize_model_variants("HV48SS")
    assert "HV48SS" in result
    assert "HV48" in result


def test_normalize_model_no_suffix_strip_for_long_token():
    # Last token "PA1SU" is > 3 chars → no suffix stripping
    result = _normalize_model_variants("SCN60PA1SU")
    assert "SCN60" not in result


def test_normalize_model_single_char_suffix_stripped():
    result = _normalize_model_variants("MODEL-W")
    assert "MODEL" in result


def test_normalize_model_deduplicates():
    result = _normalize_model_variants("MODEL")
    assert result == list(dict.fromkeys(result))  # no duplicates, order preserved
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_dimension_enrichment.py::test_normalize_model_no_spaces_unchanged -v
```
Expected: ImportError or AttributeError on `_normalize_model_variants`.

- [ ] **Step 3: Implement `_normalize_model_variants` in `src/dimension_enrichment.py`**

Add after `_make_not_found_result`:

```python
def _normalize_model_variants(model: str) -> list[str]:
    """Return up to 4 model variants to try in order: exact, no-spaces, dashes, suffix-stripped."""
    model = model.strip()
    seen: list[str] = [model]

    no_spaces = re.sub(r"\s+", "", model)
    if no_spaces not in seen:
        seen.append(no_spaces)

    with_dashes = re.sub(r"\s+", "-", model)
    if with_dashes not in seen:
        seen.append(with_dashes)

    # Suffix strip: last dash/space token of 1–3 chars
    tokens = re.split(r"[-\s]+", model)
    if len(tokens) > 1 and 1 <= len(tokens[-1]) <= 3:
        suffix_start = model.rfind(tokens[-1])
        without_suffix = model[:suffix_start].rstrip(" -")
        if without_suffix and without_suffix not in seen:
            seen.append(without_suffix)

    return seen
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_dimension_enrichment.py -v
```
Expected: all passing.

- [ ] **Step 5: Commit**

```
git add src/dimension_enrichment.py tests/test_dimension_enrichment.py
git commit -m "feat: add model normalization variants for dimension lookup"
```

---

### Task 4: Brand → domain lookup with session cache

**Files:**
- Modify: `tests/test_dimension_enrichment.py`
- Modify: `src/dimension_enrichment.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_dimension_enrichment.py`:

```python
from src.dimension_enrichment import _get_manufacturer_domain, _discovered_domains


def test_get_domain_known_brand():
    assert _get_manufacturer_domain("Scotsman") == "scotsman-ice.com"


def test_get_domain_known_brand_case_insensitive():
    assert _get_manufacturer_domain("KOHLER") == "kohler.com"
    assert _get_manufacturer_domain("kohler") == "kohler.com"


def test_get_domain_wolf_and_subzero():
    assert _get_manufacturer_domain("Wolf") == "subzero-wolf.com"
    assert _get_manufacturer_domain("Sub-Zero") == "subzero-wolf.com"


def test_get_domain_unknown_brand_returns_none_without_search():
    # Without injecting a search fn, unknown brand returns None
    result = _get_manufacturer_domain("UnknownBrandXYZ")
    assert result is None


def test_get_domain_unknown_brand_discovered_via_injected_search():
    import src.dimension_enrichment as _mod
    # Inject a mock search function
    def _mock_search(query):
        return ["https://unknownbrandxyz.com/products/spec"]
    result = _get_manufacturer_domain("UnknownBrandXYZ2", _search_fn=_mock_search)
    assert result == "unknownbrandxyz.com"
    # Cached
    assert _mod._discovered_domains.get("unknownbrandxyz2") == "unknownbrandxyz.com"


def test_get_domain_discovery_failure_returns_none():
    def _empty_search(query):
        return []
    result = _get_manufacturer_domain("NoResultsBrand", _search_fn=_empty_search)
    assert result is None
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_dimension_enrichment.py::test_get_domain_known_brand -v
```
Expected: ImportError on `_get_manufacturer_domain`.

- [ ] **Step 3: Implement `_get_manufacturer_domain` in `src/dimension_enrichment.py`**

Add after `_normalize_model_variants`:

```python
import urllib.parse as _urlparse


def _get_manufacturer_domain(
    brand: str,
    *,
    _search_fn=None,
) -> str | None:
    """
    Return official manufacturer domain for a brand, or None.
    Checks BRAND_DOMAIN_TABLE first, then _discovered_domains cache,
    then optionally runs a discovery search via _search_fn(query) -> list[str].
    """
    key = brand.strip().lower()
    if key in BRAND_DOMAIN_TABLE:
        return BRAND_DOMAIN_TABLE[key]
    if key in _discovered_domains:
        return _discovered_domains[key]
    if _search_fn is None:
        return None
    try:
        urls = _search_fn(f'"{brand}" official website product specifications')
        if not urls:
            return None
        domain = _urlparse.urlparse(urls[0]).netloc.lstrip("www.")
        if domain:
            _discovered_domains[key] = domain
            return domain
    except Exception:
        pass
    return None
```

- [ ] **Step 4: Add `import urllib.parse as _urlparse` at the top of `src/dimension_enrichment.py`**

The `import urllib.parse as _urlparse` is already inline above — move it to the top-level imports block:

```python
from __future__ import annotations

import re
import urllib.parse as _urlparse
from dataclasses import dataclass, field
```

Remove the inline `import urllib.parse as _urlparse` that was added in Step 3.

- [ ] **Step 5: Run tests**

```
pytest tests/test_dimension_enrichment.py -v
```
Expected: all passing.

- [ ] **Step 6: Commit**

```
git add src/dimension_enrichment.py tests/test_dimension_enrichment.py
git commit -m "feat: add brand-to-domain lookup with session discovery cache"
```

---

### Task 5: Query generation

**Files:**
- Modify: `tests/test_dimension_enrichment.py`
- Modify: `src/dimension_enrichment.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_dimension_enrichment.py`:

```python
from src.dimension_enrichment import _generate_queries, _generate_retailer_queries


def test_generate_queries_with_domain_produces_site_queries():
    queries = _generate_queries(
        brand="Scotsman",
        model="SCN60PA1SU",
        domain="scotsman-ice.com",
    )
    assert 'site:scotsman-ice.com "SCN60PA1SU" dimensions' in queries
    assert 'site:scotsman-ice.com "SCN60PA1SU" specifications' in queries
    assert 'site:scotsman-ice.com "SCN60PA1SU" spec sheet' in queries
    assert 'site:scotsman-ice.com "SCN60PA1SU" installation guide' in queries


def test_generate_queries_always_includes_general_queries():
    queries = _generate_queries(brand="Scotsman", model="SCN60PA1SU", domain=None)
    assert '"Scotsman" "SCN60PA1SU" "dimensions"' in queries
    assert '"Scotsman" "SCN60PA1SU" "specifications"' in queries


def test_generate_queries_without_domain_skips_site_queries():
    queries = _generate_queries(brand="Unknown", model="XYZ", domain=None)
    assert not any(q.startswith("site:") for q in queries)


def test_generate_queries_fallbacks_with_product_name():
    queries = _generate_queries(
        brand="Scotsman",
        model="SCN60PA1SU",
        domain=None,
        product_name="Icemaker Built-In",
        sku="SCN60PA1SU",
    )
    assert '"Scotsman" "Icemaker Built-In" dimensions' in queries
    assert '"SCN60PA1SU" dimensions specifications' in queries


def test_generate_queries_order_site_before_general():
    queries = _generate_queries(
        brand="Kohler",
        model="K-3999",
        domain="kohler.com",
    )
    first_site = next(i for i, q in enumerate(queries) if q.startswith("site:"))
    first_general = next(i for i, q in enumerate(queries) if '"Kohler"' in q and "site:" not in q)
    assert first_site < first_general


def test_generate_retailer_queries():
    queries = _generate_retailer_queries(brand="Kohler", model="K-3999")
    assert any("build.com" in q for q in queries)
    assert any("ajmadison.com" in q for q in queries)
    assert all(q.startswith("site:") for q in queries)
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_dimension_enrichment.py::test_generate_queries_with_domain_produces_site_queries -v
```
Expected: ImportError on `_generate_queries`.

- [ ] **Step 3: Implement `_generate_queries` and `_generate_retailer_queries`**

Add to `src/dimension_enrichment.py`:

```python
def _generate_queries(
    brand: str,
    model: str,
    domain: str | None,
    product_name: str = "",
    sku: str = "",
) -> list[str]:
    """
    Return search queries in priority order: site: targeted (phases 1+2),
    then general brand queries, then final fallbacks (phase 4).
    Retailer queries (phase 3) are generated separately by _generate_retailer_queries.
    """
    queries: list[str] = []

    # Phase 1 — manufacturer targeted
    if domain:
        queries.extend([
            f'site:{domain} "{model}" dimensions',
            f'site:{domain} "{model}" specifications',
            f'site:{domain} "{model}" spec sheet',
            f'site:{domain} "{model}" installation guide',
        ])

    # Phase 2 — general brand queries
    queries.extend([
        f'"{brand}" "{model}" "dimensions"',
        f'"{brand}" "{model}" "specifications"',
    ])

    # Phase 4 — final fallbacks
    if product_name:
        queries.append(f'"{brand}" "{product_name}" dimensions')
    if sku:
        queries.append(f'"{sku}" dimensions specifications')
    queries.append(f'"{brand}" "{model}" dimensions')

    return queries


def _generate_retailer_queries(brand: str, model: str) -> list[str]:
    """Return one site: query per trusted retailer domain (phase 3)."""
    return [
        f'site:{domain} "{brand}" "{model}" dimensions'
        for domain in RETAILER_DOMAINS
    ]
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_dimension_enrichment.py -v
```
Expected: all passing.

- [ ] **Step 5: Commit**

```
git add src/dimension_enrichment.py tests/test_dimension_enrichment.py
git commit -m "feat: add query generation for manufacturer and retailer phases"
```

---

### Task 6: Fraction normalization + dimension text extraction

**Files:**
- Modify: `tests/test_dimension_enrichment.py`
- Modify: `src/dimension_enrichment.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_dimension_enrichment.py`:

```python
from src.dimension_enrichment import _fraction_to_decimal, _find_dimension_candidates


def test_fraction_to_decimal_mixed_number():
    assert _fraction_to_decimal("14 7/8") == "14.875"


def test_fraction_to_decimal_simple_fraction():
    assert _fraction_to_decimal("3/4") == "0.75"


def test_fraction_to_decimal_whole_number():
    assert _fraction_to_decimal("22") == "22"


def test_fraction_to_decimal_decimal_string():
    assert _fraction_to_decimal("14.875") == "14.875"


def test_fraction_to_decimal_empty():
    assert _fraction_to_decimal("") == ""


def test_find_dimension_candidates_product_dimensions_label():
    text = "Product Dimensions: 14 7/8\"W x 22\"D x 33 3/8\"H\nOther info"
    candidates = _find_dimension_candidates(text)
    assert len(candidates) >= 1
    assert any("14 7/8" in c for c in candidates)


def test_find_dimension_candidates_overall_dimensions_label():
    text = "Overall Dimensions: 36\"W x 34.5\"H x 24\"D"
    candidates = _find_dimension_candidates(text)
    assert any("36" in c for c in candidates)


def test_find_dimension_candidates_inline_pattern():
    text = "The unit measures 14.875\"W × 22\"D × 33.375\"H in the installed position."
    candidates = _find_dimension_candidates(text)
    assert any("14.875" in c for c in candidates)


def test_find_dimension_candidates_returns_empty_for_no_match():
    candidates = _find_dimension_candidates("No dimensions here at all.")
    assert candidates == []


def test_find_dimension_candidates_product_dims_before_cutout():
    text = (
        "Product Dimensions: 14\"W x 33\"H x 22\"D\n"
        "Cutout Dimensions: 13.5\"W x 32.5\"H x 21.5\"D"
    )
    candidates = _find_dimension_candidates(text)
    # Product Dimensions candidate comes first
    assert candidates[0].startswith("14")


def test_find_dimension_candidates_cutout_labeled():
    text = "Cutout Dimensions: 13.5\"W x 32.5\"H x 21.5\"D"
    candidates = _find_dimension_candidates(text, include_cutout=True)
    assert any("13.5" in c for c in candidates)


def test_find_dimension_candidates_excludes_shipping():
    text = "Shipping Dimensions: 18\"W x 40\"H x 28\"D\nNo other dimensions listed."
    # Default: shipping excluded
    candidates = _find_dimension_candidates(text)
    assert candidates == []
    # With include_shipping flag: included but marked
    candidates_ship = _find_dimension_candidates(text, include_shipping=True)
    assert any("18" in c for c in candidates_ship)
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_dimension_enrichment.py::test_fraction_to_decimal_mixed_number -v
```
Expected: ImportError on `_fraction_to_decimal`.

- [ ] **Step 3: Implement `_fraction_to_decimal` and `_find_dimension_candidates`**

Add to `src/dimension_enrichment.py`:

```python
def _fraction_to_decimal(s: str) -> str:
    """Convert fraction strings like "14 7/8" → "14.875". Returns input unchanged if not a fraction."""
    s = s.strip()
    if not s:
        return s
    # Mixed number: "14 7/8"
    m = re.match(r"^(\d+)\s+(\d+)/(\d+)$", s)
    if m:
        val = int(m.group(1)) + int(m.group(2)) / int(m.group(3))
        return str(int(val)) if val == int(val) else f"{val:.6f}".rstrip("0").rstrip(".")
    # Simple fraction: "3/4"
    m = re.match(r"^(\d+)/(\d+)$", s)
    if m:
        val = int(m.group(1)) / int(m.group(2))
        return f"{val:.6f}".rstrip("0").rstrip(".")
    # Float or int string
    try:
        val = float(s)
        return str(int(val)) if val == int(val) else s
    except ValueError:
        return s


# Dimension label prefixes in priority order (highest priority first)
_PRODUCT_DIM_LABELS = re.compile(
    r"(?:product|overall)\s+dimensions?\s*[:\-]\s*([^\n]{5,100})",
    re.IGNORECASE,
)
_DIM_LABEL = re.compile(
    r"(?<!\w)dimensions?\s*[:\-]\s*([^\n]{5,100})",
    re.IGNORECASE,
)
_CUTOUT_DIM_LABEL = re.compile(
    r"cutout\s+dimensions?\s*[:\-]\s*([^\n]{5,100})",
    re.IGNORECASE,
)
_SHIPPING_DIM_LABEL = re.compile(
    r"shipping\s+(?:dimensions?|size)\s*[:\-]\s*([^\n]{5,100})",
    re.IGNORECASE,
)
# Inline: "14.875"W × 22"D × 33.375"H" or "14 7/8 W x 22 D x 33 3/8 H"
_INLINE_DIM = re.compile(
    r"[\d][.\d /]*\"?\s*[WwHhDd]\b[\s×xX]+[\d][.\d /]*\"?\s*[WwHhDd]\b[\s×xX]+[\d][.\d /]*\"?\s*[WwHhDd]\b",
)


def _find_dimension_candidates(
    text: str,
    *,
    include_cutout: bool = False,
    include_shipping: bool = False,
) -> list[str]:
    """
    Return candidate dimension strings from plain text, in priority order.
    Shipping dimensions excluded by default; cutout excluded unless include_cutout=True.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(s: str) -> None:
        s = s.strip().rstrip(".,;")
        if s and s not in seen:
            seen.add(s)
            candidates.append(s)

    # Priority 1: "Product Dimensions" / "Overall Dimensions"
    for m in _PRODUCT_DIM_LABELS.finditer(text):
        _add(m.group(1))

    # Priority 2: bare "Dimensions:"
    for m in _DIM_LABEL.finditer(text):
        _add(m.group(1))

    # Priority 3: inline pattern
    for m in _INLINE_DIM.finditer(text):
        _add(m.group(0))

    # Optional: cutout
    if include_cutout:
        for m in _CUTOUT_DIM_LABEL.finditer(text):
            _add(m.group(1))

    # Optional: shipping (low confidence)
    if include_shipping and not candidates:
        for m in _SHIPPING_DIM_LABEL.finditer(text):
            _add(m.group(1))

    return candidates
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_dimension_enrichment.py -v
```
Expected: all passing.

- [ ] **Step 5: Commit**

```
git add src/dimension_enrichment.py tests/test_dimension_enrichment.py
git commit -m "feat: add fraction normalization and dimension text extraction"
```

---

### Task 7: HTML parser

**Files:**
- Modify: `tests/test_dimension_enrichment.py`
- Modify: `src/dimension_enrichment.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_dimension_enrichment.py`:

```python
from src.dimension_enrichment import _parse_html_for_dimensions


def test_parse_html_json_ld_product_dimensions():
    html = """
    <html><body>
    <script type="application/ld+json">
    {"@type": "Product", "name": "Scotsman Icemaker",
     "description": "Product Dimensions: 14 7/8\\"W x 22\\"D x 33 3/8\\"H"}
    </script>
    </body></html>
    """
    product_dims, cutout_dims = _parse_html_for_dimensions(html)
    assert product_dims is not None
    assert "14" in product_dims
    assert cutout_dims is None


def test_parse_html_spec_table_dl():
    html = """
    <html><body>
    <dl>
      <dt>Width</dt><dd>14.875 in</dd>
      <dt>Height</dt><dd>33.375 in</dd>
      <dt>Depth</dt><dd>22 in</dd>
    </dl>
    </body></html>
    """
    product_dims, _ = _parse_html_for_dimensions(html)
    assert product_dims is not None
    assert "14.875" in product_dims or "Width" in product_dims


def test_parse_html_spec_table_tr():
    html = """
    <html><body>
    <table>
      <tr><th>Product Dimensions</th><td>36"W x 34.5"H x 24"D</td></tr>
    </table>
    </body></html>
    """
    product_dims, _ = _parse_html_for_dimensions(html)
    assert product_dims is not None
    assert "36" in product_dims


def test_parse_html_visible_text_inline():
    html = """
    <html><body>
    <p>The refrigerator measures 35.75"W x 69.875"H x 28.75"D.</p>
    </body></html>
    """
    product_dims, _ = _parse_html_for_dimensions(html)
    assert product_dims is not None
    assert "35.75" in product_dims


def test_parse_html_appliance_cutout_stored_separately():
    html = """
    <html><body>
    <p>Product Dimensions: 23.875"W x 33.375"H x 22"D</p>
    <p>Cutout Dimensions: 23"W x 33"H x 21"D</p>
    </body></html>
    """
    product_dims, cutout_dims = _parse_html_for_dimensions(html, is_appliance=True)
    assert product_dims is not None
    assert "23.875" in product_dims
    assert cutout_dims is not None
    assert "23\"W" in cutout_dims or "23" in cutout_dims


def test_parse_html_no_dimensions_returns_none():
    html = "<html><body><p>No specifications here.</p></body></html>"
    product_dims, cutout_dims = _parse_html_for_dimensions(html)
    assert product_dims is None
    assert cutout_dims is None
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_dimension_enrichment.py::test_parse_html_json_ld_product_dimensions -v
```
Expected: ImportError on `_parse_html_for_dimensions`.

- [ ] **Step 3: Install beautifulsoup4 if not already installed**

```
pip install beautifulsoup4
```

- [ ] **Step 4: Implement `_parse_html_for_dimensions`**

Add to `src/dimension_enrichment.py` (add `import json` and bs4 import near the top):

```python
import json as _json

try:
    from bs4 import BeautifulSoup as _BeautifulSoup
except ImportError:
    _BeautifulSoup = None
```

Then add the function:

```python
_SPEC_LABEL_KEYWORDS = frozenset({
    "width", "height", "depth", "dimensions", "overall dimensions",
    "product dimensions", "w×h×d", "w x h x d",
})


def _parse_html_for_dimensions(
    html: str,
    *,
    is_appliance: bool = False,
) -> tuple[str | None, str | None]:
    """
    Extract (product_dimensions, cutout_dimensions) from an HTML page.
    Three passes: JSON-LD → spec tables/dl → visible text regex.
    Returns (None, None) if no complete 3D dimensions found.
    """
    from src.dimensions import has_complete_3d_dimensions

    product_dims: str | None = None
    cutout_dims: str | None = None

    # ── Pass 1: JSON-LD ────────────────────────────────────────────────────────
    if _BeautifulSoup:
        soup = _BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = _json.loads(script.string or "")
                blob = _json.dumps(data)
            except Exception:
                continue
            candidates = _find_dimension_candidates(blob, include_cutout=is_appliance)
            for c in candidates:
                if has_complete_3d_dimensions(c):
                    product_dims = c
                    break
            if product_dims:
                break

    # ── Pass 2: spec tables / dl ───────────────────────────────────────────────
    if not product_dims and _BeautifulSoup:
        soup = _BeautifulSoup(html, "html.parser")

        # dl elements: dt (label) → dd (value)
        assembled: dict[str, str] = {}
        for dl in soup.find_all("dl"):
            items = dl.find_all(["dt", "dd"])
            label = ""
            for item in items:
                text = item.get_text(strip=True).lower()
                if item.name == "dt":
                    label = text
                elif item.name == "dd" and label in _SPEC_LABEL_KEYWORDS:
                    assembled[label] = item.get_text(strip=True)
                    label = ""
        # Try assembling W/H/D from separate labels
        w = assembled.get("width", "")
        h = assembled.get("height", "")
        d = assembled.get("depth", "")
        if w and h and d:
            candidate = f"{w} W x {h} H x {d} D"
            if has_complete_3d_dimensions(candidate):
                product_dims = candidate

        if not product_dims:
            # table th→td pairs
            for table in soup.find_all("table"):
                for row in table.find_all("tr"):
                    cells = row.find_all(["th", "td"])
                    if len(cells) >= 2:
                        label = cells[0].get_text(strip=True).lower()
                        value = cells[1].get_text(strip=True)
                        if label in _SPEC_LABEL_KEYWORDS and has_complete_3d_dimensions(value):
                            product_dims = value
                            break
                if product_dims:
                    break

    # ── Pass 3: visible text regex ─────────────────────────────────────────────
    if not product_dims:
        if _BeautifulSoup:
            text = _BeautifulSoup(html, "html.parser").get_text(" ")
        else:
            text = re.sub(r"<[^>]+>", " ", html)
        candidates = _find_dimension_candidates(text, include_cutout=is_appliance)
        for c in candidates:
            if has_complete_3d_dimensions(c):
                product_dims = c
                break

    # ── Cutout pass (appliances) ───────────────────────────────────────────────
    if is_appliance and product_dims:
        if _BeautifulSoup:
            text = _BeautifulSoup(html, "html.parser").get_text(" ")
        else:
            text = re.sub(r"<[^>]+>", " ", html)
        cutout_candidates = _find_dimension_candidates(text, include_cutout=True)
        for c in cutout_candidates:
            if c != product_dims and "cutout" in text[max(0, text.find(c) - 20):text.find(c)].lower():
                cutout_dims = c
                break

    return product_dims, cutout_dims
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_dimension_enrichment.py -v
```
Expected: all passing.

- [ ] **Step 6: Commit**

```
git add src/dimension_enrichment.py tests/test_dimension_enrichment.py
git commit -m "feat: add HTML dimension parser (JSON-LD, spec tables, visible text)"
```

---

### Task 8: PDF parser

**Files:**
- Modify: `tests/test_dimension_enrichment.py`
- Modify: `src/dimension_enrichment.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_dimension_enrichment.py`:

```python
from src.dimension_enrichment import _parse_text_pages_for_dimensions


def test_parse_text_pages_product_dimensions_label():
    pages = ["Product Dimensions: 14 7/8\"W x 22\"D x 33 3/8\"H\nSome other text."]
    product_dims, cutout_dims = _parse_text_pages_for_dimensions(pages)
    assert product_dims is not None
    assert "14 7/8" in product_dims
    assert cutout_dims is None


def test_parse_text_pages_appliance_extracts_cutout():
    pages = [
        "Product Dimensions: 23.875\"W x 33.375\"H x 22\"D\n"
        "Cutout Dimensions: 23\"W x 33\"H x 21\"D"
    ]
    product_dims, cutout_dims = _parse_text_pages_for_dimensions(pages, is_appliance=True)
    assert product_dims is not None
    assert cutout_dims is not None
    assert "23.875" in product_dims
    assert "23\"W" in cutout_dims or "23" in cutout_dims


def test_parse_text_pages_ignores_shipping_when_product_found():
    pages = [
        "Product Dimensions: 14\"W x 33\"H x 22\"D\n"
        "Shipping Dimensions: 18\"W x 40\"H x 28\"D"
    ]
    product_dims, _ = _parse_text_pages_for_dimensions(pages)
    assert "14" in product_dims
    assert "18" not in product_dims


def test_parse_text_pages_falls_back_to_shipping_when_nothing_else():
    pages = ["Shipping Dimensions: 18\"W x 40\"H x 28\"D"]
    product_dims, _ = _parse_text_pages_for_dimensions(pages, include_shipping_fallback=True)
    assert product_dims is not None
    assert "18" in product_dims


def test_parse_text_pages_no_dimensions_returns_none():
    pages = ["Installation instructions. Plug into outlet. Done."]
    product_dims, cutout_dims = _parse_text_pages_for_dimensions(pages)
    assert product_dims is None
    assert cutout_dims is None


def test_parse_text_pages_stops_at_first_match_across_pages():
    pages = [
        "No dimensions on page 1.",
        "Product Dimensions: 36\"W x 34.5\"H x 24\"D",
        "Another page with 50\"W x 50\"H x 50\"D",
    ]
    product_dims, _ = _parse_text_pages_for_dimensions(pages)
    assert "36" in product_dims
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_dimension_enrichment.py::test_parse_text_pages_product_dimensions_label -v
```
Expected: ImportError on `_parse_text_pages_for_dimensions`.

- [ ] **Step 3: Implement `_parse_text_pages_for_dimensions` and `_parse_pdf_for_dimensions`**

Add to `src/dimension_enrichment.py`:

```python
def _parse_text_pages_for_dimensions(
    pages: list[str],
    *,
    is_appliance: bool = False,
    include_shipping_fallback: bool = False,
) -> tuple[str | None, str | None]:
    """
    Find dimensions in a list of page text strings (from PDF extraction).
    Stops at the first page that yields a complete 3D result.
    Returns (product_dims, cutout_dims).
    """
    from src.dimensions import has_complete_3d_dimensions

    product_dims: str | None = None
    cutout_dims: str | None = None
    shipping_fallback: str | None = None

    for page_text in pages:
        candidates = _find_dimension_candidates(
            page_text, include_cutout=is_appliance
        )
        for c in candidates:
            if has_complete_3d_dimensions(c):
                product_dims = c
                break
        if product_dims:
            # Look for cutout on same page if appliance
            if is_appliance:
                cutout_candidates = _find_dimension_candidates(
                    page_text, include_cutout=True
                )
                for c in cutout_candidates:
                    if c != product_dims and has_complete_3d_dimensions(c):
                        lower_context = page_text[
                            max(0, page_text.find(c) - 30): page_text.find(c)
                        ].lower()
                        if "cutout" in lower_context:
                            cutout_dims = c
                            break
            break
        # Shipping fallback — only used when nothing better found
        if include_shipping_fallback and not shipping_fallback:
            shipping_candidates = _find_dimension_candidates(
                page_text, include_shipping=True
            )
            for c in shipping_candidates:
                if has_complete_3d_dimensions(c):
                    shipping_fallback = c
                    break

    if not product_dims and include_shipping_fallback and shipping_fallback:
        product_dims = shipping_fallback

    return product_dims, cutout_dims


def _parse_pdf_for_dimensions(
    pdf_bytes: bytes,
    *,
    is_appliance: bool = False,
) -> tuple[str | None, str | None]:
    """
    Extract dimension text from a PDF using PyMuPDF. Scans first 10 pages.
    Returns (product_dims, cutout_dims). Returns (None, None) on error.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None, None

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = [doc[i].get_text() for i in range(min(10, doc.page_count))]
        return _parse_text_pages_for_dimensions(
            pages,
            is_appliance=is_appliance,
            include_shipping_fallback=True,
        )
    except Exception:
        return None, None
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_dimension_enrichment.py -v
```
Expected: all passing.

- [ ] **Step 5: Commit**

```
git add src/dimension_enrichment.py tests/test_dimension_enrichment.py
git commit -m "feat: add PDF dimension parser via PyMuPDF text extraction"
```

---

### Task 9: URL fetch + route to parser

**Files:**
- Modify: `tests/test_dimension_enrichment.py`
- Modify: `src/dimension_enrichment.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_dimension_enrichment.py`:

```python
from unittest.mock import MagicMock, patch
from src.dimension_enrichment import _fetch_and_parse_url


def test_fetch_and_parse_url_html_page(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.headers = {"content-type": "text/html"}
    mock_resp.text = (
        "<html><body>"
        "<p>Product Dimensions: 36\"W x 34.5\"H x 24\"D</p>"
        "</body></html>"
    )
    mock_resp.content = mock_resp.text.encode()
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_resp):
        product_dims, cutout_dims, source_type_suffix = _fetch_and_parse_url(
            "https://example.com/product"
        )
    assert product_dims is not None
    assert "36" in product_dims
    assert source_type_suffix == "page"


def test_fetch_and_parse_url_pdf_content_type(monkeypatch):
    # A real fitz call would need real PDF bytes; here we stub _parse_pdf_for_dimensions
    mock_resp = MagicMock()
    mock_resp.headers = {"content-type": "application/pdf"}
    mock_resp.content = b"%PDF fake"
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_resp):
        with patch(
            "src.dimension_enrichment._parse_pdf_for_dimensions",
            return_value=('36"W x 34.5"H x 24"D', None),
        ):
            product_dims, cutout_dims, source_type_suffix = _fetch_and_parse_url(
                "https://example.com/spec.pdf"
            )
    assert product_dims is not None
    assert source_type_suffix == "pdf"


def test_fetch_and_parse_url_returns_none_on_http_error(monkeypatch):
    with patch("httpx.get", side_effect=Exception("connection error")):
        product_dims, cutout_dims, source_type_suffix = _fetch_and_parse_url(
            "https://example.com/product"
        )
    assert product_dims is None
    assert source_type_suffix == "page"
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_dimension_enrichment.py::test_fetch_and_parse_url_html_page -v
```
Expected: ImportError on `_fetch_and_parse_url`.

- [ ] **Step 3: Implement `_fetch_and_parse_url`**

Add `import httpx` near the top of `src/dimension_enrichment.py`:

```python
try:
    import httpx as _httpx
except ImportError:
    _httpx = None
```

Then add the function:

```python
_PDF_EXTENSIONS = frozenset({".pdf"})
_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SCH-Intake/1.0)"}


def _fetch_and_parse_url(
    url: str,
    *,
    is_appliance: bool = False,
) -> tuple[str | None, str | None, str]:
    """
    Fetch a URL and route to the correct parser.
    Returns (product_dims, cutout_dims, source_type_suffix).
    source_type_suffix is "page" or "pdf".
    Both dimension values are None on fetch failure.
    """
    suffix = "page"
    if _urlparse.urlparse(url).path.lower().endswith(tuple(_PDF_EXTENSIONS)):
        suffix = "pdf"

    try:
        resp = _httpx.get(url, headers=_REQUEST_HEADERS, timeout=12, follow_redirects=True)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "").lower()
        if "pdf" in content_type or suffix == "pdf":
            suffix = "pdf"
            return (*_parse_pdf_for_dimensions(resp.content, is_appliance=is_appliance), suffix)
        return (*_parse_html_for_dimensions(resp.text, is_appliance=is_appliance), suffix)
    except Exception:
        return None, None, suffix
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_dimension_enrichment.py -v
```
Expected: all passing.

- [ ] **Step 5: Commit**

```
git add src/dimension_enrichment.py tests/test_dimension_enrichment.py
git commit -m "feat: add URL fetch and HTML/PDF routing for dimension extraction"
```

---

### Task 10: Confidence assignment

**Files:**
- Modify: `tests/test_dimension_enrichment.py`
- Modify: `src/dimension_enrichment.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_dimension_enrichment.py`:

```python
from src.dimension_enrichment import _assign_confidence


def test_confidence_exact_model_manufacturer_page_is_high():
    assert _assign_confidence(
        model_variant="SCN60PA1SU",
        primary_model="SCN60PA1SU",
        is_manufacturer=True,
        source_type_suffix="page",
    ) == "high"


def test_confidence_exact_model_manufacturer_pdf_is_high():
    assert _assign_confidence(
        model_variant="SCN60PA1SU",
        primary_model="SCN60PA1SU",
        is_manufacturer=True,
        source_type_suffix="pdf",
    ) == "high"


def test_confidence_exact_model_retailer_is_medium():
    assert _assign_confidence(
        model_variant="SCN60PA1SU",
        primary_model="SCN60PA1SU",
        is_manufacturer=False,
        source_type_suffix="page",
    ) == "medium"


def test_confidence_variant_match_manufacturer_is_medium():
    # No-spaces variant matches — not exact primary model
    assert _assign_confidence(
        model_variant="HV48SS",    # variant
        primary_model="HV 48 SS",  # primary
        is_manufacturer=True,
        source_type_suffix="page",
    ) == "medium"


def test_confidence_suffix_stripped_variant_is_low():
    assert _assign_confidence(
        model_variant="HV48",      # suffix stripped — partial
        primary_model="HV48SS",
        is_manufacturer=True,
        source_type_suffix="page",
    ) == "low"


def test_confidence_suffix_stripped_variant_retailer_is_low():
    assert _assign_confidence(
        model_variant="HV48",
        primary_model="HV48SS",
        is_manufacturer=False,
        source_type_suffix="page",
    ) == "low"
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_dimension_enrichment.py::test_confidence_exact_model_manufacturer_page_is_high -v
```
Expected: ImportError on `_assign_confidence`.

- [ ] **Step 3: Implement `_assign_confidence`**

Add to `src/dimension_enrichment.py`:

```python
def _assign_confidence(
    model_variant: str,
    primary_model: str,
    *,
    is_manufacturer: bool,
    source_type_suffix: str,
) -> str:
    """
    Assign confidence tier based on model match quality and source authority.

    Exact match: model_variant is primary model or a spaces/dashes-normalized version.
    Partial match: model_variant is a suffix-stripped version (shorter, loses info).
    """
    # Normalize primary for comparison
    primary_norm = re.sub(r"[\s-]+", "", primary_model.strip().lower())
    variant_norm = re.sub(r"[\s-]+", "", model_variant.strip().lower())

    is_exact = variant_norm == primary_norm or variant_norm == primary_model.strip().lower()

    if not is_exact:
        # Partial / suffix-stripped match → always low
        return "low"

    if is_manufacturer:
        return "high"

    return "medium"
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_dimension_enrichment.py -v
```
Expected: all passing.

- [ ] **Step 5: Commit**

```
git add src/dimension_enrichment.py tests/test_dimension_enrichment.py
git commit -m "feat: add confidence assignment for dimension lookup results"
```

---

### Task 11: `find_dimensions` orchestrator

**Files:**
- Modify: `tests/test_dimension_enrichment.py`
- Modify: `src/dimension_enrichment.py`

This task replaces the stub `find_dimensions` from Task 2 with the full implementation.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_dimension_enrichment.py`:

```python
from unittest.mock import patch
from src.dimension_enrichment import find_dimensions
from src.dimensions import has_complete_3d_dimensions


def _scotsman_row() -> dict:
    return {
        "Brand": "Scotsman",
        "Model/SKU": "SCN60PA1SU",
        "Product Name": "Scotsman Icemaker Built-In Pump",
        "Product Category": "Appliances",
        "Dimensions": "",
    }


def test_find_dimensions_skips_row_with_complete_dims():
    row = _scotsman_row()
    row["Dimensions"] = '14 7/8"W x 22"D x 33 3/8"H'
    result = find_dimensions(row)
    assert result.status == "not_found"
    assert result.failure_reason == "dimensions already complete"


def test_find_dimensions_skips_row_missing_brand():
    row = _scotsman_row()
    row["Brand"] = ""
    result = find_dimensions(row)
    assert result.status == "not_found"
    assert "brand" in result.failure_reason.lower()


def test_find_dimensions_skips_row_missing_sku():
    row = _scotsman_row()
    row["Model/SKU"] = ""
    result = find_dimensions(row)
    assert result.status == "not_found"
    assert "model" in result.failure_reason.lower()


def test_find_dimensions_returns_found_on_high_confidence_result(monkeypatch):
    def _mock_search(query):
        return ["https://scotsman-ice.com/products/scn60pa1su"]

    def _mock_fetch(url, *, is_appliance=False):
        return ('14 7/8"W x 22"D x 33 3/8"H', None, "page")

    with patch("src.dimension_enrichment._brave_search_urls", side_effect=_mock_search):
        with patch("src.dimension_enrichment._fetch_and_parse_url", side_effect=_mock_fetch):
            result = find_dimensions(_scotsman_row())

    assert result.status == "found"
    assert result.confidence == "high"
    assert result.source_type == "manufacturer_page"
    assert has_complete_3d_dimensions(result.dimensions)
    assert result.source_url == "https://scotsman-ice.com/products/scn60pa1su"
    assert len(result.queries_tried) >= 1
    assert len(result.urls_checked) >= 1


def test_find_dimensions_returns_not_found_when_no_results(monkeypatch):
    with patch("src.dimension_enrichment._brave_search_urls", return_value=[]):
        result = find_dimensions(_scotsman_row())
    assert result.status == "not_found"
    assert result.failure_reason != ""


def test_find_dimensions_records_low_confidence_skipped(monkeypatch):
    # Suffix-stripped variant match → low confidence → skipped
    def _mock_search(query):
        return ["https://scotsman-ice.com/other"]

    def _mock_fetch(url, *, is_appliance=False):
        # Returns valid dims but will be assigned low confidence
        # by using the suffix-stripped variant "SCN60PA1" not exact "SCN60PA1SU"
        return ('14 7/8"W x 22"D x 33 3/8"H', None, "page")

    row = _scotsman_row()
    # Force only suffix-stripped variant to match by setting a model with short suffix
    row["Model/SKU"] = "SCN60SS"  # SS is 2 chars → will be stripped to SCN60

    with patch("src.dimension_enrichment._brave_search_urls", side_effect=_mock_search):
        with patch("src.dimension_enrichment._fetch_and_parse_url", side_effect=_mock_fetch):
            with patch(
                "src.dimension_enrichment._assign_confidence",
                return_value="low",
            ):
                result = find_dimensions(row)
    assert result.status == "low_confidence_skipped"
    assert result.dimensions == ""


def test_find_dimensions_appliance_cutout_in_evidence(monkeypatch):
    def _mock_search(query):
        return ["https://scotsman-ice.com/products/scn60pa1su"]

    def _mock_fetch(url, *, is_appliance=False):
        return ('14 7/8"W x 22"D x 33 3/8"H', '13.5"W x 21.5"D x 32"H', "page")

    with patch("src.dimension_enrichment._brave_search_urls", side_effect=_mock_search):
        with patch("src.dimension_enrichment._fetch_and_parse_url", side_effect=_mock_fetch):
            result = find_dimensions(_scotsman_row())

    assert result.status == "found"
    assert result.evidence_text != ""
    # cutout is stored in evidence, not in result.dimensions
    assert "13.5" not in result.dimensions
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_dimension_enrichment.py::test_find_dimensions_skips_row_with_complete_dims -v
```
Expected: FAIL — stub returns "not implemented" failure_reason, not "dimensions already complete".

- [ ] **Step 3: Add `_brave_search_urls` helper to `src/dimension_enrichment.py`**

Add near the top (after imports):

```python
try:
    from src.brave_search import search_product_candidates as _brave_candidates
except ImportError:
    _brave_candidates = None


def _brave_search_urls(query: str, limit: int = 5) -> list[str]:
    """Call Brave Search and return up to `limit` result URLs."""
    if _brave_candidates is None:
        return []
    try:
        results = _brave_candidates(query, "")
        return [r.url for r in results[:limit]]
    except Exception:
        return []
```

- [ ] **Step 4: Replace the stub `find_dimensions` with the full implementation**

Replace the stub at the bottom of `src/dimension_enrichment.py`:

```python
def find_dimensions(row: dict) -> DimensionResult:
    """
    Perform full dimension lookup for one intake row.
    Returns DimensionResult with status "found", "not_found", or "low_confidence_skipped".
    Only rows with Brand + Model/SKU that are missing complete 3D dimensions are processed.
    """
    from src.dimensions import has_complete_3d_dimensions, extract_labeled_dimensions

    brand = (row.get("Brand") or "").strip()
    model = (row.get("Model/SKU") or "").strip()
    product_name = (row.get("Product Name") or "").strip()
    category = (row.get("Product Category") or "").strip()
    current_dims = (row.get("Dimensions") or "").strip()
    is_appliance = category in _APPLIANCE_CATEGORIES

    # Pre-flight checks
    if has_complete_3d_dimensions(current_dims):
        return _make_not_found_result(failure_reason="dimensions already complete")
    if not brand:
        return _make_not_found_result(failure_reason="brand required for dimension lookup")
    if not model:
        return _make_not_found_result(failure_reason="model/sku required for dimension lookup")

    domain = _get_manufacturer_domain(brand, _search_fn=_brave_search_urls)
    model_variants = _normalize_model_variants(model)

    queries_tried: list[str] = []
    urls_checked: list[str] = []
    low_confidence_result: DimensionResult | None = None

    def _try_queries(query_list: list[str], is_manufacturer: bool) -> DimensionResult | None:
        for query in query_list:
            queries_tried.append(query)
            search_urls = _brave_search_urls(query, limit=5)
            for url in search_urls:
                urls_checked.append(url)
                product_dims, cutout_dims, src_suffix = _fetch_and_parse_url(
                    url, is_appliance=is_appliance
                )
                if not product_dims or not has_complete_3d_dimensions(product_dims):
                    continue
                # Determine which model variant matched (use the query's model)
                matched_variant = model_variants[0]  # default to exact
                for v in model_variants:
                    if v.lower() in product_dims.lower() or v.lower() in url.lower():
                        matched_variant = v
                        break
                src_type_key = "manufacturer" if is_manufacturer else "retailer"
                source_type = f"{src_type_key}_{src_suffix}"
                conf = _assign_confidence(
                    matched_variant,
                    model,
                    is_manufacturer=is_manufacturer,
                    source_type_suffix=src_suffix,
                )
                # Build structured width/height/depth
                parts = extract_labeled_dimensions(product_dims)
                evidence = product_dims
                if cutout_dims:
                    evidence += f" | Cutout: {cutout_dims}"
                result = DimensionResult(
                    dimensions=product_dims,
                    width=_fraction_to_decimal(parts.get("width", "")),
                    height=_fraction_to_decimal(parts.get("height", "")),
                    depth=_fraction_to_decimal(parts.get("depth", "")),
                    length=_fraction_to_decimal(parts.get("length", "")),
                    source_url=url,
                    confidence=conf,
                    source_type=source_type,
                    status="found" if conf in ("high", "medium") else "low_confidence_skipped",
                    queries_tried=list(queries_tried),
                    urls_checked=list(urls_checked),
                    evidence_text=evidence,
                    failure_reason="",
                )
                if conf == "low":
                    nonlocal low_confidence_result
                    if low_confidence_result is None:
                        low_confidence_result = result
                    continue
                return result
        return None

    # Try each model variant across phases
    for variant in model_variants:
        variant_queries = _generate_queries(brand, variant, domain, product_name, model)
        result = _try_queries(variant_queries, is_manufacturer=bool(domain))
        if result:
            return result

    # Phase 3: retailer fallback
    retailer_queries = _generate_retailer_queries(brand, model)
    result = _try_queries(retailer_queries, is_manufacturer=False)
    if result:
        return result

    # Low confidence recorded
    if low_confidence_result:
        low_confidence_result.queries_tried = list(queries_tried)
        low_confidence_result.urls_checked = list(urls_checked)
        low_confidence_result.dimensions = ""  # do not fill
        low_confidence_result.width = ""
        low_confidence_result.height = ""
        low_confidence_result.depth = ""
        return low_confidence_result

    return _make_not_found_result(
        queries_tried=queries_tried,
        urls_checked=urls_checked,
        failure_reason=f"no dimensions found after {len(queries_tried)} queries and {len(urls_checked)} URLs checked",
    )
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_dimension_enrichment.py -v
```
Expected: all passing.

- [ ] **Step 6: Run full test suite**

```
pytest tests/ -v --ignore=tests/test_programa_automation.py
```
Expected: all passing (the automation tests are skipped due to Windows permission issues unrelated to this work).

- [ ] **Step 7: Commit**

```
git add src/dimension_enrichment.py tests/test_dimension_enrichment.py
git commit -m "feat: implement find_dimensions orchestrator with full search pipeline"
```

---

### Task 12: Integrate into `product_enrichment.py`

**Files:**
- Modify: `src/product_enrichment.py:280-317` (the `enrich_row` function)
- Create: `tests/test_dimension_enrichment_integration.py`

- [ ] **Step 1: Write failing integration test**

```python
# tests/test_dimension_enrichment_integration.py
from unittest.mock import patch, MagicMock
from src.product_enrichment import enrich_row
from src.dimension_enrichment import DimensionResult


def _row_missing_dims() -> dict:
    return {
        "Brand": "Kohler",
        "Model/SKU": "K-3999",
        "Product Name": "Highline Toilet",
        "Product Category": "Plumbing",
        "Dimensions": "",
        "Source Type": "Manual",
        "Notes": "",
        "Include": True,
    }


def test_enrich_row_calls_find_dimensions_when_dims_missing(monkeypatch):
    mock_result = DimensionResult(
        dimensions='28"W x 30"H x 17"D',
        width="28",
        height="30",
        depth="17",
        source_url="https://kohler.com/k-3999",
        confidence="high",
        source_type="manufacturer_page",
        status="found",
    )
    # Stub regular enrichment to return a row still missing dims
    def _mock_search(*args, **kwargs):
        return MagicMock(domain_score=80, url="https://kohler.com/k-3999")

    with patch("src.product_enrichment.search_product_candidates", return_value=[_mock_search()]):
        with patch("src.product_enrichment._fetch_page_text", return_value="Highline toilet specs"):
            with patch("src.product_enrichment._extract_with_claude", return_value={}):
                with patch("src.dimension_enrichment.find_dimensions", return_value=mock_result):
                    updated, error = enrich_row(_row_missing_dims())

    assert error is None
    assert updated["Dimensions"] == '28"W x 30"H x 17"D'
    assert updated["Dimension Source URL"] == "https://kohler.com/k-3999"
    assert updated["Dimension Confidence"] == "high"
    assert updated["Dimension Source Type"] == "manufacturer_page"
    assert updated["Dimension Lookup Status"] == "found"


def test_enrich_row_skips_dimension_pass_when_dims_already_complete(monkeypatch):
    row = _row_missing_dims()
    row["Dimensions"] = '28"W x 30"H x 17"D'

    find_dims_called = []
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        with patch(
            "src.dimension_enrichment.find_dimensions",
            side_effect=lambda r: find_dims_called.append(r) or DimensionResult(),
        ):
            updated, error = enrich_row(row)

    assert find_dims_called == []


def test_enrich_row_not_found_sets_status_not_found(monkeypatch):
    mock_result = DimensionResult(
        status="not_found",
        confidence="none",
        failure_reason="no results",
    )
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        with patch("src.dimension_enrichment.find_dimensions", return_value=mock_result):
            updated, error = enrich_row(_row_missing_dims())

    assert updated.get("Dimension Lookup Status") == "not_found"
    assert updated.get("Dimensions", "") == ""


def test_enrich_row_appliance_appends_cutout_to_notes(monkeypatch):
    mock_result = DimensionResult(
        dimensions='23.875"W x 33.375"H x 22"D',
        width="23.875", height="33.375", depth="22",
        confidence="high",
        source_type="manufacturer_page",
        status="found",
        evidence_text='23.875"W x 33.375"H x 22"D | Cutout: 23"W x 33"H x 21"D',
    )
    row = _row_missing_dims()
    row["Product Category"] = "Appliances"

    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        with patch("src.dimension_enrichment.find_dimensions", return_value=mock_result):
            updated, error = enrich_row(row)

    assert updated["Dimensions"] == '23.875"W x 33.375"H x 22"D'
    assert "[Cutout Dimensions:" in updated.get("Notes", "")


def test_enrich_row_does_not_overwrite_existing_complete_dims(monkeypatch):
    row = _row_missing_dims()
    row["Dimensions"] = '28"W x 30"H x 17"D'
    original_dims = row["Dimensions"]

    mock_result = DimensionResult(
        dimensions='99"W x 99"H x 99"D',
        confidence="high",
        status="found",
    )
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        with patch("src.dimension_enrichment.find_dimensions", return_value=mock_result):
            updated, error = enrich_row(row)

    assert updated["Dimensions"] == original_dims
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_dimension_enrichment_integration.py -v
```
Expected: FAIL — `enrich_row` does not call `find_dimensions`.

- [ ] **Step 3: Modify `enrich_row` in `src/product_enrichment.py`**

Add import at the top of `src/product_enrichment.py`:

```python
from src.dimension_enrichment import find_dimensions as _find_dimensions
from src.intake_schema import ALL_COLUMNS as _ALL_COLUMNS
```

Modify `enrich_row` — add the dimension pass after `_apply_enrichment`:

```python
def enrich_row(row: dict) -> tuple[dict, str | None]:
    try:
        query = _build_search_query(row)
        brand = _str_val(row.get("Brand"))

        results = search_product_candidates(query, brand)

        if not results or results[0].domain_score < MIN_USE_SCORE:
            updated = row.copy()
            existing = _str_val(updated.get("Notes"))
            note = "[Enrichment: no confident source found]"
            if note not in existing:
                updated["Notes"] = f"{existing} {note}".strip() if existing else note
        else:
            best = results[0]
            page_text = _fetch_page_text(best.url)

            if not page_text:
                updated = row.copy()
                existing = _str_val(updated.get("Notes"))
                domain = urllib.parse.urlparse(best.url).netloc or best.url[:50]
                note = f"[Enrichment: could not fetch {domain}]"
                if note not in existing:
                    updated["Notes"] = f"{existing} {note}".strip() if existing else note
            else:
                extracted = _extract_with_claude(page_text, row)
                updated = _apply_enrichment(row, extracted, best.url, best.domain_score)

        # ── Dimension enrichment pass ──────────────────────────────────────────
        brand_val = _str_val(updated.get("Brand"))
        model_val = _str_val(updated.get("Model/SKU"))
        dims_val = _str_val(updated.get("Dimensions"))
        if brand_val and model_val and not has_complete_3d_dimensions(dims_val):
            dim_result = _find_dimensions(updated)
            # Apply high/medium confidence results only
            if dim_result.status == "found" and dim_result.confidence in ("high", "medium"):
                updated["Dimensions"] = dim_result.dimensions
                if dim_result.width:
                    updated["Width (in)"] = dim_result.width
                if dim_result.height:
                    updated["Height (in)"] = dim_result.height
                if dim_result.depth:
                    updated["Depth (in)"] = dim_result.depth
                if dim_result.length:
                    updated["Length (in)"] = dim_result.length
                # Appliance cutout → Notes
                if "Cutout:" in dim_result.evidence_text:
                    cutout_part = dim_result.evidence_text.split("Cutout:")[-1].strip()
                    existing_notes = _str_val(updated.get("Notes"))
                    tag = f"[Cutout Dimensions: {cutout_part}]"
                    if tag not in existing_notes:
                        updated["Notes"] = f"{existing_notes} {tag}".strip() if existing_notes else tag
            # Always persist status/source regardless of confidence
            updated["Dimension Source URL"] = dim_result.source_url
            updated["Dimension Confidence"] = dim_result.confidence if dim_result.confidence != "none" else ""
            updated["Dimension Source Type"] = dim_result.source_type if dim_result.source_type != "none" else ""
            updated["Dimension Lookup Status"] = dim_result.status

        return updated, None
    except Exception as exc:
        return row, str(exc)
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_dimension_enrichment_integration.py -v
```
Expected: all passing.

- [ ] **Step 5: Run full test suite**

```
pytest tests/ -v --ignore=tests/test_programa_automation.py
```
Expected: all passing.

- [ ] **Step 6: Commit**

```
git add src/product_enrichment.py tests/test_dimension_enrichment_integration.py
git commit -m "feat: integrate find_dimensions into enrich_row as automatic second pass"
```

---

### Task 13: API response update — `dimension_diagnostics`

**Files:**
- Modify: `backend/main.py:102-105` (`IntakeResponse` model)
- Modify: `backend/main.py:153-163` (`_df_response` function)
- Modify: `src/product_enrichment.py` — `enrich_dataframe` return type

- [ ] **Step 1: Extend `enrich_dataframe` to return diagnostics**

In `src/product_enrichment.py`, modify `enrich_dataframe` to collect dimension diagnostics:

```python
def enrich_dataframe(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str], list[dict]]:
    """
    Enrich all qualifying rows in df.
    Returns (updated_df, error_list, dimension_diagnostics).
    dimension_diagnostics is a list of per-row dicts for the API response.
    """
    df = df.copy()
    errors: list[str] = []
    dimension_diagnostics: list[dict] = []

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
                # Collect dimension diagnostics if lookup ran
                lookup_status = updated.get("Dimension Lookup Status", "")
                if lookup_status:
                    dimension_diagnostics.append({
                        "row_index": int(idx),
                        "product_name": _str_val(updated.get("Product Name")),
                        "model_searched": _str_val(updated.get("Model/SKU")),
                        "domain_used": urllib.parse.urlparse(_str_val(updated.get("Dimension Source URL", ""))).netloc or "",
                        "confidence": _str_val(updated.get("Dimension Confidence")),
                        "status": lookup_status,
                        "source_url": _str_val(updated.get("Dimension Source URL")),
                        "failure_reason": "",
                    })
        except Exception as exc:
            label = (
                _str_val(r.get("Product Name"))
                or _str_val(r.get("Brand"))
                or _str_val(r.get("Model/SKU"))
                or str(idx)
            )
            errors.append(f"Row '{label}': {exc}")

        time.sleep(0.5)

    return df, errors, dimension_diagnostics
```

- [ ] **Step 2: Extend `IntakeResponse` in `backend/main.py`**

```python
class IntakeResponse(BaseModel):
    rows: list[dict]
    errors: list[str] = Field(default_factory=list)
    eligible_count: int = 0
    dimension_diagnostics: list[dict] = Field(default_factory=list)
```

- [ ] **Step 3: Update `_df_response` to accept diagnostics**

```python
def _df_response(
    df: pd.DataFrame,
    errors: list[str] | None = None,
    dimension_diagnostics: list[dict] | None = None,
) -> IntakeResponse:
    df = df.copy()
    if "Notes" in df.columns:
        df["Notes"] = df["Notes"].apply(remove_notes_row_prefix)
    rows = df.fillna("").to_dict("records")
    eligible, blocked = split_eligible_rows(rows)
    return IntakeResponse(
        rows=rows,
        errors=errors or [],
        eligible_count=len(eligible),
        dimension_diagnostics=dimension_diagnostics or [],
    )
```

- [ ] **Step 4: Update `enrich_intake` endpoint to pass diagnostics**

```python
@app.post("/intake/enrich", response_model=IntakeResponse)
def enrich_intake(payload: RowsPayload) -> IntakeResponse:
    df, errors, dimension_diagnostics = enrich_dataframe(pd.DataFrame(payload.rows))
    df = apply_confidence_checks(df)
    return _df_response(df, errors, dimension_diagnostics)
```

- [ ] **Step 5: Verify `_df_response` call sites still pass** — check the other callers in `backend/main.py` do not break (they call `_df_response(df)` or `_df_response(df, errors)` which still works since `dimension_diagnostics` defaults to None).

Run:
```
pytest tests/ -v --ignore=tests/test_programa_automation.py
```
Expected: all passing.

- [ ] **Step 6: Commit**

```
git add src/product_enrichment.py backend/main.py
git commit -m "feat: add dimension_diagnostics to enrich_dataframe and API response"
```

---

## Acceptance Test

With the full implementation in place, run the 24-product test set through the enrichment endpoint and verify:

```
pytest tests/ -v --ignore=tests/test_programa_automation.py
```

All unit tests pass. To manually verify dimension coverage improvement:

1. Start backend: `uvicorn backend.main:app --reload --port 8000`
2. Upload the 24-product CSV via the intake UI or POST to `/intake/generate`
3. POST rows to `/intake/enrich`
4. Check response: `dimension_diagnostics` shows per-row results
5. Export debug CSV — `Dimension Source URL` and `Dimension Confidence` populated for found rows
6. Main Programa CSV — `Dimension Source URL` absent (not in PROGRAMA_COLUMNS)

**Expected:** substantially more than 5/24 rows with complete dimensions, each with a non-blank source URL and confidence of "high" or "medium".
