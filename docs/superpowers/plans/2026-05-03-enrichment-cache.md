# Enrichment Cache & Search Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent manufacturer-domain and product-enrichment caches, per-product Brave search budget, and session-level query/URL deduplication so the same product is never searched twice.

**Architecture:** New `src/enrichment_cache.py` owns all caching, budget, and deduplication logic. Existing modules (`dimension_enrichment.py`, `product_enrichment.py`, `brave_search.py`, `backend/main.py`) receive minimal targeted changes — no restructuring of the dimension pipeline.

**Tech Stack:** Python stdlib (`json`, `os`, `re`, `datetime`), pytest, existing FastAPI/Pydantic models.

**Spec:** `docs/superpowers/specs/2026-05-03-enrichment-cache-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/enrichment_cache.py` | **Create** | All cache + budget + dedup logic |
| `tests/test_enrichment_cache.py` | **Create** | Unit tests for new module |
| `src/brave_search.py` | **Modify** | Accept `session_cache` param for query dedup |
| `src/dimension_enrichment.py` | **Modify** | Replace `_discovered_domains`, accept `session_cache`/`budget` |
| `src/product_enrichment.py` | **Modify** | Cache check at entry, partial hit, write-back, pass params |
| `backend/main.py` | **Modify** | Add `enrichment_mode`/`force_refresh` to `RowsPayload` |
| `data/manufacturer_domain_cache.json` | **Created at runtime** | Persistent brand → domain store |
| `data/product_enrichment_cache.json` | **Created at runtime** | Persistent product enrichment store |

---

## Task 1: Write Failing Tests for `src/enrichment_cache.py`

**Files:**
- Create: `tests/test_enrichment_cache.py`

- [ ] **Step 1: Create test file**

```python
# tests/test_enrichment_cache.py
import json
import os
import tempfile
import pytest


# ── normalize_key ──────────────────────────────────────────────────────────────

def test_normalize_key_basic():
    from src.enrichment_cache import normalize_key
    assert normalize_key("Wolf", "MDD30TS") == "wolf_mdd30ts"


def test_normalize_key_strips_special_chars():
    from src.enrichment_cache import normalize_key
    assert normalize_key("Sub-Zero", "BI-36U/S") == "subzero_bi36us"


def test_normalize_key_collapses_spaces():
    from src.enrichment_cache import normalize_key
    assert normalize_key("GE Appliances", "JB735SPSS") == "geappliances_jb735spss"


# ── normalize_mode ─────────────────────────────────────────────────────────────

def test_normalize_mode_valid():
    from src.enrichment_cache import normalize_mode
    assert normalize_mode("fast") == "fast"
    assert normalize_mode("standard") == "standard"
    assert normalize_mode("deep") == "deep"


def test_normalize_mode_invalid_falls_back_to_standard():
    from src.enrichment_cache import normalize_mode
    assert normalize_mode("turbo") == "standard"
    assert normalize_mode("") == "standard"
    assert normalize_mode("FAST") == "standard"  # case-sensitive


# ── SearchBudget ───────────────────────────────────────────────────────────────

def test_search_budget_fresh_can_search():
    from src.enrichment_cache import SearchBudget
    b = SearchBudget(max_searches=4, max_urls=5)
    assert b.can_search()
    assert b.can_fetch()


def test_search_budget_exhausted():
    from src.enrichment_cache import SearchBudget
    b = SearchBudget(max_searches=1, max_urls=1)
    b.consume_search()
    assert not b.can_search()
    b.consume_fetch()
    assert not b.can_fetch()


def test_budget_for_mode_standard_defaults():
    from src.enrichment_cache import budget_for_mode
    b = budget_for_mode("standard")
    assert b.max_searches == 4
    assert b.max_urls == 5
    assert not b.allows_retailer
    assert b.allows_general_fallback


def test_budget_for_mode_fast():
    from src.enrichment_cache import budget_for_mode
    b = budget_for_mode("fast")
    assert b.max_searches == 1
    assert not b.allows_retailer
    assert not b.allows_general_fallback


def test_budget_for_mode_deep():
    from src.enrichment_cache import budget_for_mode
    b = budget_for_mode("deep")
    assert b.max_searches == 8
    assert b.allows_retailer
    assert b.allows_general_fallback


# ── SessionCache ───────────────────────────────────────────────────────────────

def test_session_cache_defaults():
    from src.enrichment_cache import SessionCache
    sc = SessionCache()
    assert sc.queries == {}
    assert sc.urls == {}
    assert sc.force_refresh is False


def test_session_cache_stores_query():
    from src.enrichment_cache import SessionCache
    sc = SessionCache()
    sc.queries["site:wolf.com MDD30TS"] = [{"url": "https://example.com"}]
    assert "site:wolf.com MDD30TS" in sc.queries


# ── ManufacturerDomainCache ────────────────────────────────────────────────────

@pytest.fixture
def mfr_cache(tmp_path):
    from src.enrichment_cache import ManufacturerDomainCache
    cache = ManufacturerDomainCache()
    cache._path = str(tmp_path / "mfr_cache.json")
    return cache


def test_mfr_cache_get_missing_key_returns_none(mfr_cache):
    assert mfr_cache.get("unknownbrand") is None


def test_mfr_cache_set_and_get(mfr_cache):
    mfr_cache.set("acme", "acme.com", source="discovered")
    result = mfr_cache.get("acme")
    assert result["domain"] == "acme.com"
    assert result["source"] == "discovered"


def test_mfr_cache_persists_to_disk(mfr_cache):
    mfr_cache.set("acme", "acme.com", source="discovered")
    # Load a fresh instance pointing at same file
    from src.enrichment_cache import ManufacturerDomainCache
    cache2 = ManufacturerDomainCache()
    cache2._path = mfr_cache._path
    assert cache2.get("acme")["domain"] == "acme.com"


def test_mfr_cache_does_not_overwrite_hardcoded(mfr_cache):
    mfr_cache.set("wolf", "subzero-wolf.com", source="hardcoded")
    mfr_cache.set("wolf", "wrong.com", source="discovered")
    assert mfr_cache.get("wolf")["domain"] == "subzero-wolf.com"


def test_mfr_cache_creates_file_if_missing(mfr_cache):
    assert mfr_cache.get("x") is None   # triggers load on missing file
    # No exception; file still doesn't need to exist yet


# ── ProductEnrichmentCache ─────────────────────────────────────────────────────

@pytest.fixture
def product_cache(tmp_path):
    from src.enrichment_cache import ProductEnrichmentCache
    cache = ProductEnrichmentCache()
    cache._path = str(tmp_path / "product_cache.json")
    return cache


def test_product_cache_get_missing_returns_none(product_cache):
    assert product_cache.get("wolf_mdd30ts") is None


def test_product_cache_update_and_get(product_cache):
    product_cache.update("wolf_mdd30ts", {"dimensions": '30"W x 15"H x 17"D', "dimension_confidence": "high"})
    entry = product_cache.get("wolf_mdd30ts")
    assert entry["dimensions"] == '30"W x 15"H x 17"D'


def test_product_cache_partial_merge(product_cache):
    product_cache.update("wolf_mdd30ts", {"dimensions": '30"W x 15"H x 17"D'})
    product_cache.update("wolf_mdd30ts", {"product_url": "https://wolf.com/mdd30ts"})
    entry = product_cache.get("wolf_mdd30ts")
    assert entry["dimensions"] == '30"W x 15"H x 17"D'
    assert entry["product_url"] == "https://wolf.com/mdd30ts"


def test_product_cache_null_stored_in_null_fields(product_cache):
    product_cache.update("wolf_mdd30ts", {"image_url": None})
    entry = product_cache.get("wolf_mdd30ts")
    assert entry["image_url"] is None
    assert "image_url" in entry.get("null_fields", {})


def test_product_cache_skips_empty_strings(product_cache):
    product_cache.update("wolf_mdd30ts", {"product_url": ""})
    entry = product_cache.get("wolf_mdd30ts")
    # Empty string not stored (only non-empty values or explicit None)
    assert entry is None or "product_url" not in (entry or {})


def test_product_cache_persists_across_instances(product_cache):
    product_cache.update("wolf_mdd30ts", {"dimensions": '30"W x 15"H x 17"D'})
    from src.enrichment_cache import ProductEnrichmentCache
    cache2 = ProductEnrichmentCache()
    cache2._path = product_cache._path
    assert cache2.get("wolf_mdd30ts")["dimensions"] == '30"W x 15"H x 17"D'


# ── confidence_ok ──────────────────────────────────────────────────────────────

def test_confidence_ok_dimension_field_uses_dimension_confidence():
    from src.enrichment_cache import confidence_ok
    entry = {"dimension_confidence": "high", "general_confidence": "low"}
    assert confidence_ok(entry, "dimensions") is True
    assert confidence_ok(entry, "width_in") is True


def test_confidence_ok_general_field_uses_general_confidence():
    from src.enrichment_cache import confidence_ok
    entry = {"dimension_confidence": "none", "general_confidence": "medium"}
    assert confidence_ok(entry, "product_url") is True
    assert confidence_ok(entry, "finish") is True


def test_confidence_ok_low_confidence_returns_false():
    from src.enrichment_cache import confidence_ok
    entry = {"dimension_confidence": "low", "general_confidence": "low"}
    assert confidence_ok(entry, "dimensions") is False
    assert confidence_ok(entry, "product_url") is False
```

- [ ] **Step 2: Run tests to confirm they all fail (module doesn't exist yet)**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && pytest tests/test_enrichment_cache.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'src.enrichment_cache'`

---

## Task 2: Implement `src/enrichment_cache.py`

**Files:**
- Create: `src/enrichment_cache.py`

- [ ] **Step 1: Create the module**

```python
# src/enrichment_cache.py
"""
Enrichment cache, session deduplication, and search budget for SCH DesignOps Intake.

Public API
----------
normalize_key(brand, model) -> str
normalize_mode(mode) -> str
budget_for_mode(mode) -> SearchBudget
confidence_ok(entry, field_name) -> bool
SearchBudget       — per-product Brave call counter
SessionCache       — in-memory per-run query/URL dedup store
ManufacturerDomainCache  — persistent data/manufacturer_domain_cache.json
ProductEnrichmentCache   — persistent data/product_enrichment_cache.json
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_MFR_CACHE_PATH = os.path.normpath(os.path.join(_DATA_DIR, "manufacturer_domain_cache.json"))
_PRODUCT_CACHE_PATH = os.path.normpath(os.path.join(_DATA_DIR, "product_enrichment_cache.json"))

_CACHE_ENABLED: bool = os.getenv("ENRICHMENT_CACHE_ENABLED", "true").lower() != "false"

_MODE_LIMITS: dict[str, dict] = {
    "fast":     {"max_searches": 1, "max_urls": 3,  "retailer": False, "general_fallback": False},
    "standard": {"max_searches": 4, "max_urls": 5,  "retailer": False, "general_fallback": True},
    "deep":     {"max_searches": 8, "max_urls": 10, "retailer": True,  "general_fallback": True},
}

VALID_MODES: frozenset[str] = frozenset(_MODE_LIMITS)

# Fields whose confidence is governed by dimension_confidence vs general_confidence
_DIMENSION_FIELDS: frozenset[str] = frozenset(
    {"dimensions", "width_in", "height_in", "depth_in", "length_in"}
)


def normalize_key(brand: str, model: str) -> str:
    """Stable cache key: lowercase, alphanumeric only, joined by underscore."""
    def _clean(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", s.lower().strip())
    return f"{_clean(brand)}_{_clean(model)}"


def normalize_mode(mode: str) -> str:
    """Return mode unchanged if valid, else 'standard'."""
    return mode if mode in VALID_MODES else "standard"


def budget_for_mode(mode: str) -> "SearchBudget":
    """Create a SearchBudget for the given enrichment mode, respecting env overrides."""
    limits = _MODE_LIMITS[normalize_mode(mode)]
    env_searches = os.getenv("BRAVE_MAX_SEARCHES_PER_PRODUCT")
    env_urls = os.getenv("ENRICHMENT_MAX_URLS_PER_PRODUCT")
    return SearchBudget(
        max_searches=int(env_searches) if env_searches else limits["max_searches"],
        max_urls=int(env_urls) if env_urls else limits["max_urls"],
        allows_retailer=limits["retailer"],
        allows_general_fallback=limits["general_fallback"],
    )


def confidence_ok(entry: dict, field_name: str) -> bool:
    """True if the relevant confidence for this field is 'high' or 'medium'."""
    conf_key = "dimension_confidence" if field_name in _DIMENSION_FIELDS else "general_confidence"
    return entry.get(conf_key, "") in ("high", "medium")


# ── SearchBudget ───────────────────────────────────────────────────────────────

@dataclass
class SearchBudget:
    max_searches: int
    max_urls: int
    allows_retailer: bool = False
    allows_general_fallback: bool = True
    searches_used: int = 0
    urls_used: int = 0

    def can_search(self) -> bool:
        return self.searches_used < self.max_searches

    def can_fetch(self) -> bool:
        return self.urls_used < self.max_urls

    def consume_search(self) -> None:
        self.searches_used += 1

    def consume_fetch(self) -> None:
        self.urls_used += 1


# ── SessionCache ───────────────────────────────────────────────────────────────

@dataclass
class SessionCache:
    queries: dict = field(default_factory=dict)  # query str → list[SearchResult]
    urls: dict = field(default_factory=dict)      # url str → page text/html
    force_refresh: bool = False


# ── ManufacturerDomainCache ────────────────────────────────────────────────────

class ManufacturerDomainCache:
    """Persistent cache for brand → official domain mappings."""

    def __init__(self) -> None:
        self._data: dict | None = None
        self._path: str = _MFR_CACHE_PATH

    def _load(self) -> None:
        if self._data is not None:
            return
        if not _CACHE_ENABLED or not os.path.exists(self._path):
            self._data = {}
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception:
            self._data = {}

    def _save(self) -> None:
        if not _CACHE_ENABLED or self._data is None:
            return
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            pass

    def get(self, brand_key: str) -> dict | None:
        self._load()
        return self._data.get(brand_key)

    def set(self, brand_key: str, domain: str, source: str = "discovered") -> None:
        self._load()
        existing = self._data.get(brand_key)
        if existing and existing.get("source") == "hardcoded":
            return  # never overwrite hardcoded entries
        self._data[brand_key] = {
            "domain": domain,
            "source": source,
            "last_verified": datetime.now().strftime("%Y-%m-%d"),
        }
        self._save()


# ── ProductEnrichmentCache ─────────────────────────────────────────────────────

class ProductEnrichmentCache:
    """Persistent unified cache for product enrichment fields (general + dimension)."""

    def __init__(self) -> None:
        self._data: dict | None = None
        self._path: str = _PRODUCT_CACHE_PATH

    def _load(self) -> None:
        if self._data is not None:
            return
        if not _CACHE_ENABLED or not os.path.exists(self._path):
            self._data = {}
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception:
            self._data = {}

    def _save(self) -> None:
        if not _CACHE_ENABLED or self._data is None:
            return
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            pass

    def get(self, key: str) -> dict | None:
        self._load()
        return self._data.get(key)

    def update(self, key: str, fields: dict) -> None:
        """Merge fields into existing entry. Only stores non-empty values or explicit None."""
        self._load()
        entry = dict(self._data.get(key) or {})
        now = datetime.now().isoformat(timespec="seconds")
        for field_name, value in fields.items():
            if field_name.endswith("__reason"):
                continue  # skip internal reason hints
            if value is None:
                null_fields = entry.setdefault("null_fields", {})
                null_fields[field_name] = {
                    "last_attempted": now,
                    "failure_reason": fields.get(f"{field_name}__reason", "not found"),
                }
                entry[field_name] = None
            elif value != "":
                entry[field_name] = value
        if "timestamp" not in entry:
            entry["timestamp"] = now
        self._data[key] = entry
        self._save()
```

- [ ] **Step 2: Run the failing tests — they should now pass**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && pytest tests/test_enrichment_cache.py -v
```

Expected: all green. Fix any failures before continuing.

- [ ] **Step 3: Run full test suite to confirm no regressions**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && pytest --tb=short -q
```

Expected: same pass count as before this task.

- [ ] **Step 4: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/enrichment_cache.py tests/test_enrichment_cache.py && git commit -m "feat: add enrichment_cache module with persistent caches, session dedup, and search budget"
```

---

## Task 3: Update `src/brave_search.py` — Session Query Deduplication

**Files:**
- Modify: `src/brave_search.py`
- Modify: `tests/test_brave_search.py`

- [ ] **Step 1: Add a failing test for session cache dedup**

Append to `tests/test_brave_search.py`:

```python
def test_search_uses_session_cache_hit_without_calling_api(monkeypatch):
    """If query is in session_cache.queries, return cached result without hitting Brave."""
    from src.enrichment_cache import SessionCache
    monkeypatch.setattr(bs, "BRAVE_API_KEY", "real_key")

    call_count = {"n": 0}
    def fake_urlopen(*args, **kwargs):
        call_count["n"] += 1
        raise AssertionError("Should not have called Brave API")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    sc = SessionCache()
    fake_result = bs.SearchResult(title="Cached", url="https://cached.com", description="", domain_score=80)
    sc.queries["Wolf MDD30TS specifications"] = [fake_result]

    results = bs.search_product_candidates("Wolf MDD30TS specifications", "Wolf", session_cache=sc)
    assert results == [fake_result]
    assert call_count["n"] == 0


def test_search_stores_result_in_session_cache(monkeypatch):
    """After a real Brave call, the result is stored in session_cache.queries."""
    from src.enrichment_cache import SessionCache
    import urllib.request, io, json as _json
    monkeypatch.setattr(bs, "BRAVE_API_KEY", "fake_key")

    fake_response_data = {"web": {"results": [{"url": "https://wolf.com/p", "title": "Wolf", "description": ""}]}}
    class FakeResp:
        def read(self): return _json.dumps(fake_response_data).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: FakeResp())

    sc = SessionCache()
    bs.search_product_candidates("Wolf MDD30TS specs", "Wolf", session_cache=sc)
    assert "Wolf MDD30TS specs" in sc.queries
```

- [ ] **Step 2: Run new tests — confirm they fail**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && pytest tests/test_brave_search.py::test_search_uses_session_cache_hit_without_calling_api tests/test_brave_search.py::test_search_stores_result_in_session_cache -v
```

Expected: `TypeError` (unexpected keyword argument `session_cache`).

- [ ] **Step 3: Update `search_product_candidates()` in `src/brave_search.py`**

Change the function signature and body. The only lines that change are the signature and the block before the `try`:

```python
def search_product_candidates(query: str, brand: str = "", session_cache=None) -> list:
    """
    Search Brave Web Search and return results ranked by domain trustworthiness.
    Returns an empty list if BRAVE_API_KEY is not set or the request fails.
    If session_cache is provided, checks for a cached result first and stores
    new results after a live call (no budget is tracked here).
    """
    # Session cache dedup — return immediately without hitting Brave
    if session_cache is not None and query in session_cache.queries:
        return session_cache.queries[query]

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
        results = results[:5]

        # Store in session cache after live call
        if session_cache is not None:
            session_cache.queries[query] = results

        return results
    except Exception:
        return []
```

- [ ] **Step 4: Run brave_search tests — all should pass**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && pytest tests/test_brave_search.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/brave_search.py tests/test_brave_search.py && git commit -m "feat: add session_cache param to search_product_candidates for query deduplication"
```

---

## Task 4: Update `src/dimension_enrichment.py`

**Files:**
- Modify: `src/dimension_enrichment.py`
- Modify: `tests/test_dimension_enrichment.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_dimension_enrichment.py`:

```python
def test_find_dimensions_accepts_session_cache_and_budget():
    """find_dimensions must accept session_cache and budget kwargs without error."""
    from src.dimension_enrichment import find_dimensions
    from src.enrichment_cache import SessionCache, SearchBudget
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Dimensions": "", "Product Name": "", "Product Category": ""}
    sc = SessionCache()
    budget = SearchBudget(max_searches=0, max_urls=0)  # budget exhausted immediately
    result = find_dimensions(row, session_cache=sc, budget=budget)
    # With zero budget, must return not_found without crashing
    assert result.status in ("not_found", "low_confidence_skipped")


def test_brave_search_urls_respects_session_cache():
    """_brave_search_urls returns session cache hit without consuming budget."""
    from src.dimension_enrichment import _brave_search_urls
    from src.enrichment_cache import SessionCache, SearchBudget
    from src.brave_search import SearchResult
    sc = SessionCache()
    sc.queries["site:wolf.com MDD30TS dimensions"] = [
        SearchResult(title="Wolf", url="https://wolf.com/mdd30ts", description="", domain_score=90)
    ]
    budget = SearchBudget(max_searches=0, max_urls=5)  # zero searches allowed
    urls = _brave_search_urls("site:wolf.com MDD30TS dimensions", session_cache=sc, budget=budget)
    assert "https://wolf.com/mdd30ts" in urls
    assert budget.searches_used == 0  # session hit, no budget consumed


def test_brave_search_urls_respects_budget_exhaustion(monkeypatch):
    """_brave_search_urls returns [] without calling Brave when budget is exhausted."""
    from src.dimension_enrichment import _brave_search_urls
    from src.enrichment_cache import SessionCache, SearchBudget
    import src.brave_search as bs
    monkeypatch.setattr(bs, "BRAVE_API_KEY", "fake")
    sc = SessionCache()
    budget = SearchBudget(max_searches=0, max_urls=5)
    urls = _brave_search_urls("wolf MDD30TS dimensions", session_cache=sc, budget=budget)
    assert urls == []


def test_get_manufacturer_domain_uses_persistent_cache(tmp_path, monkeypatch):
    """_get_manufacturer_domain writes discovered domains to ManufacturerDomainCache."""
    from src.enrichment_cache import ManufacturerDomainCache
    import src.dimension_enrichment as de
    # Give the module-level cache a tmp path
    monkeypatch.setattr(de._mfr_cache, "_path", str(tmp_path / "mfr.json"))
    monkeypatch.setattr(de._mfr_cache, "_data", None)  # force re-load

    def fake_search(query):
        return ["https://acme-brand.com/products"]

    domain = de._get_manufacturer_domain("Acme Brand", _search_fn=fake_search)
    assert domain == "acme-brand.com"
    # Should now be in persistent cache
    de._mfr_cache._load()
    assert de._mfr_cache.get("acme brand") is not None
```

- [ ] **Step 2: Run new tests — confirm they fail**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && pytest tests/test_dimension_enrichment.py::test_find_dimensions_accepts_session_cache_and_budget tests/test_dimension_enrichment.py::test_brave_search_urls_respects_session_cache tests/test_dimension_enrichment.py::test_brave_search_urls_respects_budget_exhaustion tests/test_dimension_enrichment.py::test_get_manufacturer_domain_uses_persistent_cache -v
```

Expected: failures (missing params/attributes).

- [ ] **Step 3: Apply changes to `src/dimension_enrichment.py`**

**3a — Add imports at top** (after existing try/except imports):

```python
from src.enrichment_cache import ManufacturerDomainCache, SessionCache, SearchBudget
```

**3b — Replace module-level `_discovered_domains` dict** with the persistent cache singleton. Find and remove:

```python
# Simple session-scoped cache — not a cross-process or persistent cache.
_discovered_domains: dict[str, str] = {}
```

Replace with:

```python
# Persistent manufacturer domain cache (lazy-loads from data/manufacturer_domain_cache.json)
_mfr_cache = ManufacturerDomainCache()
```

**3c — Update `_get_manufacturer_domain()`**. Replace the current body (lines referencing `_discovered_domains`):

```python
def _get_manufacturer_domain(
    brand: str,
    *,
    _search_fn=None,
) -> str | None:
    brand_stripped = brand.strip()
    key = brand_stripped.lower()
    if not key:
        return None
    # 1. Hardcoded table (fastest, authoritative)
    if key in BRAND_DOMAIN_TABLE:
        return BRAND_DOMAIN_TABLE[key]
    # 2. Persistent discovered cache
    cached = _mfr_cache.get(key)
    if cached:
        return cached["domain"]
    # 3. Live discovery search
    if _search_fn is None:
        return None
    try:
        urls = _search_fn(f'"{brand_stripped}" official website product specifications')
        if not urls:
            return None
        netloc = _urlparse.urlparse(urls[0]).netloc.lower()
        domain = netloc[4:] if netloc.startswith("www.") else netloc
        if domain:
            _mfr_cache.set(key, domain, source="discovered")
            return domain
    except Exception:
        pass
    return None
```

**3d — Update `_brave_search_urls()`** signature and body:

```python
def _brave_search_urls(
    query: str,
    limit: int = 5,
    brand: str = "",
    session_cache: "SessionCache | None" = None,
    budget: "SearchBudget | None" = None,
) -> list[str]:
    """Call Brave Search and return up to `limit` result URLs.
    Checks session cache first (no budget consumed). Skips call if budget exhausted."""
    if _brave_candidates is None:
        return []
    # Session cache hit — free, no budget consumed
    if session_cache is not None and query in session_cache.queries:
        return [r.url for r in session_cache.queries[query][:limit]]
    # Budget check before real API call
    if budget is not None and not budget.can_search():
        return []
    try:
        results = _brave_candidates(query, brand, session_cache=session_cache)
        if budget is not None:
            budget.consume_search()
        return [r.url for r in results[:limit]]
    except Exception:
        return []
```

**3e — Update `find_dimensions()` signature** to accept and thread through `session_cache` and `budget`:

```python
def find_dimensions(
    row: dict,
    session_cache: "SessionCache | None" = None,
    budget: "SearchBudget | None" = None,
) -> DimensionResult:
```

Inside `find_dimensions()`, update the two calls to `_brave_search_urls` that are inside `_try_queries`. The nested `_try_queries` closure currently calls:

```python
search_urls = _brave_search_urls(query, limit=5, brand=brand)
```

Change to:

```python
search_urls = _brave_search_urls(query, limit=5, brand=brand, session_cache=session_cache, budget=budget)
```

Also update the domain discovery call inside `find_dimensions()`. It calls `_get_manufacturer_domain(brand, _search_fn=_brave_search_urls)`. Update to a lambda that threads the session_cache and budget:

```python
domain = _get_manufacturer_domain(
    brand,
    _search_fn=lambda q: _brave_search_urls(q, limit=5, brand=brand, session_cache=session_cache, budget=budget),
)
```

Also update URL fetch in `_try_queries` to track URL budget:

After `urls_checked.append(url)`, add:
```python
if budget is not None:
    if not budget.can_fetch():
        break
    budget.consume_fetch()
```

- [ ] **Step 4: Run dimension enrichment tests**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && pytest tests/test_dimension_enrichment.py -v
```

Expected: all green. Fix any failures before continuing.

- [ ] **Step 5: Run full test suite**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && pytest --tb=short -q
```

Expected: same pass count as before this task (no regressions).

- [ ] **Step 6: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/dimension_enrichment.py tests/test_dimension_enrichment.py && git commit -m "feat: replace session-only _discovered_domains with persistent ManufacturerDomainCache; add session_cache/budget threading to dimension lookup"
```

---

## Task 5: Update `src/product_enrichment.py`

**Files:**
- Modify: `src/product_enrichment.py`
- Modify: `tests/test_product_enrichment.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_product_enrichment.py`:

```python
def test_enrich_row_accepts_enrichment_mode_and_session_cache():
    """enrich_row must accept enrichment_mode and session_cache kwargs without error."""
    from src.product_enrichment import enrich_row
    from src.enrichment_cache import SessionCache
    row = {
        "Source Type": "PDF", "Brand": "Wolf", "Model/SKU": "MDD30TS",
        "Product Name": "", "Dimensions": "", "Finish / Color": "",
        "Product Category": "", "Product URL": "", "Notes": "",
        "Review Required": False, "Suggested Action": "",
    }
    sc = SessionCache()
    # With no API keys configured, should return row without crashing
    updated, err, dim_result = enrich_row(row, enrichment_mode="standard", session_cache=sc)
    assert isinstance(updated, dict)


def test_enrich_row_returns_early_on_full_cache_hit(monkeypatch, tmp_path):
    """When cache has all essentials, enrich_row must not call Brave."""
    from src.product_enrichment import enrich_row
    from src.enrichment_cache import SessionCache, ProductEnrichmentCache, normalize_key
    import src.product_enrichment as pe
    import src.brave_search as bs

    call_count = {"n": 0}
    def fake_search(*args, **kwargs):
        call_count["n"] += 1
        return []
    monkeypatch.setattr(bs, "search_product_candidates", fake_search)

    cache = ProductEnrichmentCache()
    cache._path = str(tmp_path / "product_cache.json")
    cache.update(normalize_key("Wolf", "MDD30TS"), {
        "dimensions": '30"W x 15"H x 17"D',
        "product_url": "https://wolf.com/mdd30ts",
        "dimension_confidence": "high",
        "general_confidence": "high",
    })
    monkeypatch.setattr(pe, "_product_cache", cache)

    row = {
        "Source Type": "PDF", "Brand": "Wolf", "Model/SKU": "MDD30TS",
        "Product Name": "", "Dimensions": "", "Finish / Color": "",
        "Product Category": "", "Product URL": "", "Notes": "",
        "Review Required": False, "Suggested Action": "",
    }
    sc = SessionCache()
    updated, err, _ = enrich_row(row, enrichment_mode="standard", session_cache=sc)
    assert call_count["n"] == 0  # no Brave calls made
    assert updated["Dimensions"] == '30"W x 15"H x 17"D'
    assert updated["Product URL"] == "https://wolf.com/mdd30ts"


def test_enrich_dataframe_creates_session_cache_once(monkeypatch):
    """enrich_dataframe creates one SessionCache and passes it to all rows."""
    import pandas as pd
    from src.product_enrichment import enrich_dataframe
    from src.enrichment_cache import SessionCache
    import src.product_enrichment as pe

    created = []
    original_enrich_row = pe.enrich_row
    def tracking_enrich_row(row, enrichment_mode="standard", session_cache=None):
        created.append(id(session_cache))
        return original_enrich_row(row, enrichment_mode=enrichment_mode, session_cache=session_cache)
    monkeypatch.setattr(pe, "enrich_row", tracking_enrich_row)

    rows = [
        {"Source Type": "PDF", "Brand": "Wolf", "Model/SKU": "MDD30TS",
         "Product Name": "", "Dimensions": "", "Finish / Color": "",
         "Product Category": "", "Product URL": "", "Notes": "",
         "Review Required": False, "Suggested Action": ""},
        {"Source Type": "PDF", "Brand": "Kohler", "Model/SKU": "K-596",
         "Product Name": "", "Dimensions": "", "Finish / Color": "",
         "Product Category": "", "Product URL": "", "Notes": "",
         "Review Required": False, "Suggested Action": ""},
    ]
    df = pd.DataFrame(rows)
    enrich_dataframe(df, enrichment_mode="standard")
    # All rows received the same SessionCache instance
    assert len(set(created)) == 1
```

- [ ] **Step 2: Run new tests — confirm they fail**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && pytest tests/test_product_enrichment.py::test_enrich_row_accepts_enrichment_mode_and_session_cache tests/test_product_enrichment.py::test_enrich_row_returns_early_on_full_cache_hit tests/test_product_enrichment.py::test_enrich_dataframe_creates_session_cache_once -v
```

Expected: `TypeError` on new kwargs.

- [ ] **Step 3: Add imports and module-level cache singleton to `src/product_enrichment.py`**

After the existing imports block (after `load_dotenv()`), add:

```python
from src.enrichment_cache import (
    SessionCache as _SessionCache,
    SearchBudget as _SearchBudget,
    ProductEnrichmentCache as _ProductEnrichmentCache,
    normalize_key as _normalize_key,
    normalize_mode as _normalize_mode,
    budget_for_mode as _budget_for_mode,
    confidence_ok as _confidence_ok,
)

# Module-level singleton — lazy-loads on first access
_product_cache = _ProductEnrichmentCache()

# Fields the product cache can fill into a row, and their mapping to row column names
_CACHE_GENERAL_FIELDS: dict[str, str] = {
    "product_url": "Product URL",
    "finish": "Finish / Color",
}
_CACHE_DIM_FIELDS: dict[str, str] = {
    "dimensions": "Dimensions",
    "width_in": "Width (in)",
    "height_in": "Height (in)",
    "depth_in": "Depth (in)",
    "length_in": "Length (in)",
}
_ESSENTIAL_CACHE_FIELDS: list[str] = ["dimensions", "product_url"]
```

- [ ] **Step 4: Add cache helper function to `src/product_enrichment.py`**

Insert before `enrich_row()`:

```python
def _apply_cache_to_row(
    row: dict,
    cache_entry: dict,
    force_refresh: bool,
) -> tuple[dict, list[str], list[str]]:
    """
    Fill row from cache entry where confidence is high/medium.
    Returns (updated_row, cache_fields_filled, still_missing_essentials).
    A null cache value is skipped (unless force_refresh=True).
    """
    updated = row.copy()
    filled: list[str] = []

    all_cache_fields = {**_CACHE_GENERAL_FIELDS, **_CACHE_DIM_FIELDS}
    for cache_field, row_col in all_cache_fields.items():
        cached_val = cache_entry.get(cache_field)
        if cached_val is None:
            if not force_refresh:
                import logging
                logging.getLogger(__name__).debug(
                    "[CACHED NULL SKIPPED] field=%s", cache_field
                )
            continue  # null = searched before, no result
        if not _confidence_ok(cache_entry, cache_field):
            continue
        if not _str_val(updated.get(row_col)):
            updated[row_col] = cached_val
            filled.append(cache_field)

    missing = [
        f for f in _ESSENTIAL_CACHE_FIELDS
        if not _str_val(updated.get(all_cache_fields.get(f, f)))
        or (f == "dimensions" and not has_complete_3d_dimensions(_str_val(updated.get("Dimensions", ""))))
    ]
    return updated, filled, missing
```

- [ ] **Step 5: Update `enrich_row()` signature and body**

Replace the `enrich_row` function signature:

```python
def enrich_row(
    row: dict,
    enrichment_mode: str = "standard",
    session_cache: "_SessionCache | None" = None,
) -> tuple[dict, str | None, _DimensionResult | None]:
```

At the **very top** of the `try:` block inside `enrich_row`, before the existing `query = _build_search_query(row)` line, insert the cache check:

```python
        import logging as _logging
        _log = _logging.getLogger(__name__)

        mode = _normalize_mode(enrichment_mode)
        budget = _budget_for_mode(mode)
        brand = _str_val(row.get("Brand"))
        model_sku = _str_val(row.get("Model/SKU"))
        cache_key = _normalize_key(brand, model_sku) if brand and model_sku else ""
        force_refresh = session_cache.force_refresh if session_cache else False

        # ── Cache check ────────────────────────────────────────────────────────
        cache_fields_filled: list[str] = []
        fields_searched: list[str] = []
        product_cache_hit = "miss"

        if cache_key:
            cache_entry = _product_cache.get(cache_key)
            if cache_entry is not None:
                row, cache_fields_filled, still_missing = _apply_cache_to_row(
                    row, cache_entry, force_refresh
                )
                if not still_missing:
                    _log.info("[CACHE HIT: full] key=%s", cache_key)
                    product_cache_hit = "full"
                    # Still run dimension lookup if dims not complete (already filled above)
                    # Return early — no Brave calls needed
                    updated = row.copy()
                    if not _str_val(updated.get("Source Type", "")).endswith("_Enriched"):
                        original = _str_val(updated.get("Source Type", ""))
                        updated["Source Type"] = f"{original}_Enriched" if original else "Enriched"
                    return updated, None, None
                else:
                    product_cache_hit = "partial"
                    fields_searched.extend(still_missing)
                    _log.info("[CACHE HIT: partial] key=%s still_missing=%s", cache_key, still_missing)
            else:
                fields_searched.extend(_ESSENTIAL_CACHE_FIELDS)
                _log.info("[CACHE MISS] key=%s", cache_key)

        # Fast mode: skip general Brave search if manufacturer domain is known
        # (dimension lookup will handle targeted search)
        skip_general_search = (mode == "fast")
```

Then wrap the existing Brave search block with a budget and mode check. Find the existing:

```python
        query = _build_search_query(row)
        brand = _str_val(row.get("Brand"))
        results = search_product_candidates(query, brand)
```

Replace with:

```python
        query = _build_search_query(row)
        results = []
        if not skip_general_search and budget.can_search():
            _log.info("[LIVE SEARCH] query=%s", query[:80])
            results = search_product_candidates(query, brand, session_cache=session_cache)
            budget.consume_search()
        elif not budget.can_search():
            _log.info("[BUDGET EXHAUSTED] skipping general search for key=%s", cache_key)
```

After the existing `updated = _apply_enrichment(row, extracted, best.url, best.domain_score)` line and before the dimension enrichment block, add the cache write-back:

```python
        # ── Cache write-back (general fields) ──────────────────────────────────
        if cache_key:
            _cache_fields: dict = {}
            if _str_val(updated.get("Product URL")):
                _cache_fields["product_url"] = _str_val(updated.get("Product URL"))
            if _str_val(updated.get("Finish / Color")):
                _cache_fields["finish"] = _str_val(updated.get("Finish / Color"))
            if _cache_fields:
                _cache_fields["general_confidence"] = "medium"
                _product_cache.update(cache_key, _cache_fields)
```

Update the `find_dimensions()` call to pass session_cache and budget:

```python
            dim_result = _find_dimensions(updated, session_cache=session_cache, budget=budget)
```

After the dimension fields are written to `updated`, add dimension cache write-back:

```python
                if cache_key and dim_result.status == "found":
                    _product_cache.update(cache_key, {
                        "dimensions": dim_result.dimensions,
                        "width_in": dim_result.width or None,
                        "height_in": dim_result.height or None,
                        "depth_in": dim_result.depth or None,
                        "length_in": dim_result.length or None,
                        "dimension_source_url": dim_result.source_url,
                        "dimension_confidence": dim_result.confidence,
                    })
```

- [ ] **Step 6: Update `enrich_dataframe()` signature**

Replace:
```python
def enrich_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[dict]]:
```

With:
```python
def enrich_dataframe(
    df: pd.DataFrame,
    enrichment_mode: str = "standard",
    force_refresh: bool = False,
) -> tuple[pd.DataFrame, list[str], list[dict]]:
```

At the start of `enrich_dataframe()`, before the `for idx, row in df.iterrows():` loop, add:

```python
    from src.enrichment_cache import SessionCache as _SC
    _session = _SC(force_refresh=force_refresh)
```

Update the `enrich_row()` call inside the loop:

```python
            updated, error, dim_result = enrich_row(r, enrichment_mode=enrichment_mode, session_cache=_session)
```

- [ ] **Step 7: Run product enrichment tests**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && pytest tests/test_product_enrichment.py tests/test_enrich_dataframe_diagnostics.py -v
```

Expected: all green. Fix any failures.

- [ ] **Step 8: Run full test suite**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && pytest --tb=short -q
```

Expected: same pass count as before this task.

- [ ] **Step 9: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add src/product_enrichment.py tests/test_product_enrichment.py && git commit -m "feat: add product cache check/write-back, partial hit logic, search budget, and session cache threading to enrich_row/enrich_dataframe"
```

---

## Task 6: Update `backend/main.py`

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Add `enrichment_mode` and `force_refresh` to `RowsPayload`**

Find the `RowsPayload` class in `backend/main.py`. It currently looks like:

```python
class RowsPayload(BaseModel):
    rows: list[dict]
```

Add the two new optional fields:

```python
class RowsPayload(BaseModel):
    rows: list[dict]
    enrichment_mode: str = "standard"
    force_refresh: bool = False
```

- [ ] **Step 2: Update `enrich_intake()` to pass the new fields**

Find:
```python
@app.post("/intake/enrich", response_model=IntakeResponse)
def enrich_intake(payload: RowsPayload) -> IntakeResponse:
    df, errors, dimension_diagnostics = enrich_dataframe(pd.DataFrame(payload.rows))
```

Replace with:
```python
@app.post("/intake/enrich", response_model=IntakeResponse)
def enrich_intake(payload: RowsPayload) -> IntakeResponse:
    df, errors, dimension_diagnostics = enrich_dataframe(
        pd.DataFrame(payload.rows),
        enrichment_mode=payload.enrichment_mode,
        force_refresh=payload.force_refresh,
    )
```

- [ ] **Step 3: Run full test suite**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && pytest --tb=short -q
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add backend/main.py && git commit -m "feat: expose enrichment_mode and force_refresh in /intake/enrich API endpoint"
```

---

## Task 7: Verify Cache Files Are Created and Working

**Files:** None (verification only)

- [ ] **Step 1: Start the backend**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && uvicorn backend.main:app --port 8000 &
```

- [ ] **Step 2: POST a single row to `/intake/enrich` with a known brand/model**

```bash
curl -s -X POST http://localhost:8000/intake/enrich \
  -H "Content-Type: application/json" \
  -d '{"rows": [{"Source Type": "PDF", "Brand": "Scotsman", "Model/SKU": "HV48SS", "Product Name": "", "Dimensions": "", "Finish / Color": "", "Product Category": "", "Product URL": "", "Notes": "", "Review Required": false, "Suggested Action": ""}], "enrichment_mode": "standard"}' \
  | python3 -m json.tool | head -40
```

- [ ] **Step 3: Confirm cache files were created**

```bash
ls -lh "/Users/finncase/Desktop/Dev/SCH data input proj/data/"
cat "/Users/finncase/Desktop/Dev/SCH data input proj/data/manufacturer_domain_cache.json" 2>/dev/null | python3 -m json.tool
cat "/Users/finncase/Desktop/Dev/SCH data input proj/data/product_enrichment_cache.json" 2>/dev/null | python3 -m json.tool
```

Expected: both files exist and contain entries.

- [ ] **Step 4: POST the same row again — confirm no Brave calls in logs**

Re-run the same `curl` from Step 2. Watch the uvicorn log output. Expected: `[CACHE HIT: full]` log line; no `[LIVE SEARCH]` line for this row.

- [ ] **Step 5: Stop the dev server**

```bash
kill %1 2>/dev/null; true
```

- [ ] **Step 6: Final full test suite run**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && pytest --tb=short -q
```

Expected: all green.

- [ ] **Step 7: Final commit**

```bash
cd "/Users/finncase/Desktop/Dev/SCH data input proj" && git add -u && git commit -m "chore: verify enrichment cache end-to-end — manufacturer and product caches populate on first run, hit on second"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Persistent manufacturer domain cache | Task 2 (`ManufacturerDomainCache`) + Task 4 (replaces `_discovered_domains`) |
| Persistent product enrichment cache | Task 2 (`ProductEnrichmentCache`) + Task 5 (check + write-back) |
| Session query dedup | Task 3 (`brave_search.py`) + Task 4 (`_brave_search_urls`) |
| Session URL dedup | Task 4 (URL budget tracking in `_try_queries`) |
| Per-product search budget | Task 2 (`SearchBudget`) + Tasks 4 & 5 (threading) |
| Enrichment modes (fast/standard/deep) | Task 2 (`budget_for_mode`) + Task 5 (skip general search in fast mode) |
| Partial cache hit logic | Task 5 (`_apply_cache_to_row`, `_ESSENTIAL_CACHE_FIELDS`) |
| force_refresh bypasses cached nulls | Task 5 (`_apply_cache_to_row` + `SessionCache.force_refresh`) |
| confidence_ok per field type | Task 2 (`confidence_ok`, `_DIMENSION_FIELDS`) |
| `enrichment_mode` in API | Task 6 (`RowsPayload`) |
| `force_refresh` in API | Task 6 (`RowsPayload`) |
| Cache files created safely if missing | Task 2 (`_load` checks `os.path.exists`, starts with `{}`) |
| Atomic saves | Task 2 (`.tmp` + `os.replace`) |
| Log event labels | Task 5 (all `[CACHE HIT]`, `[CACHE MISS]`, `[LIVE SEARCH]`, `[BUDGET EXHAUSTED]` labels) |
| `ENRICHMENT_CACHE_ENABLED` env var | Task 2 (`_CACHE_ENABLED` flag) |
| `BRAVE_MAX_SEARCHES_PER_PRODUCT` env var | Task 2 (`budget_for_mode` reads env override) |
| Hardcoded entries never overwritten | Task 2 (`ManufacturerDomainCache.set` guard) |

**Placeholder scan:** No TBDs. All steps contain actual code.

**Type consistency check:**
- `SessionCache` is imported as `_SessionCache` in `product_enrichment.py` (alias avoids name collision with local vars) — Task 5 uses `_SC` in `enrich_dataframe` for the same reason; consistent.
- `_find_dimensions` is the import alias for `find_dimensions` from `dimension_enrichment` in `product_enrichment.py` — the updated call in Task 5 uses `_find_dimensions(updated, session_cache=session_cache, budget=budget)`, which matches Task 4's updated signature.
- `_product_cache` is the module-level singleton referenced in the `monkeypatch` test in Task 5 — matches the name defined in Step 3.
- `cache_key` is computed before the cache check block and reused in the write-back blocks — consistent across Steps 4 and 5.

**One note for the implementer:** `product_enrichment.py` currently aliases `find_dimensions` on import as `_find_dimensions`. Confirm the import line at the top reads `from src.dimension_enrichment import find_dimensions as _find_dimensions` before applying Task 5 changes, and use `_find_dimensions` (not `find_dimensions`) in the updated call.
