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
    assert normalize_mode("manual_retry") == "manual_retry"


def test_normalize_mode_invalid_falls_back_to_fast():
    from src.enrichment_cache import normalize_mode
    assert normalize_mode("turbo") == "fast"
    assert normalize_mode("") == "fast"
    assert normalize_mode("FAST") == "fast"  # case-sensitive


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
    assert b.max_searches == 3
    assert b.max_urls == 6
    assert b.max_ai_calls == 1
    assert not b.allows_retailer
    assert b.allows_general_fallback


def test_budget_for_mode_fast():
    from src.enrichment_cache import budget_for_mode
    b = budget_for_mode("fast")
    assert b.max_searches == 1
    assert b.max_urls == 1
    assert b.max_ai_calls == 0
    assert not b.allows_retailer
    assert not b.allows_general_fallback


def test_budget_for_mode_fast_clamps_generic_env(monkeypatch):
    from src.enrichment_cache import budget_for_mode

    monkeypatch.setenv("BRAVE_MAX_SEARCHES_PER_PRODUCT", "5")
    monkeypatch.setenv("ENRICHMENT_MAX_URLS_PER_PRODUCT", "5")
    monkeypatch.setenv("ENRICHMENT_MAX_AI_CALLS_PER_PRODUCT", "2")

    b = budget_for_mode("fast")

    assert (b.max_searches, b.max_urls, b.max_ai_calls) == (1, 1, 0)


def test_budget_for_mode_deep():
    from src.enrichment_cache import budget_for_mode
    b = budget_for_mode("deep")
    assert b.max_searches == 6
    assert b.max_urls == 12
    assert b.max_ai_calls == 2
    assert b.allows_retailer
    assert b.allows_general_fallback


def test_budget_for_mode_manual_retry():
    from src.enrichment_cache import budget_for_mode
    b = budget_for_mode("manual_retry")
    assert b.max_searches == 10
    assert b.max_urls == 20
    assert b.max_ai_calls == 3
    assert b.allows_retailer


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


def test_product_cache_null_reason_stored_in_null_fields(product_cache):
    product_cache.update("wolf_mdd30ts", {"image_url": None, "image_url__reason": "HTTP 404"})
    entry = product_cache.get("wolf_mdd30ts")
    assert entry["image_url"] is None
    assert entry["null_fields"]["image_url"]["failure_reason"] == "HTTP 404"
    assert "image_url__reason" not in entry  # sidecar key is not stored as a top-level field


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
