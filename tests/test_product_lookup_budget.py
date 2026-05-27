from __future__ import annotations

import pytest

from src.brave_search import SearchResult
from src.enrichment_cache import budget_for_mode
from src.product_lookup_budget import EnrichmentRunBudget, ProductLookupBudget, run_budget_for_mode
from src.product_lookup_cache import ProductLookupCache
from src.product_resolver import ProductCandidate, ProductResolutionResult, resolve_product_page


@pytest.fixture(autouse=True)
def _isolate_source_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("SOURCE_SUCCESS_REGISTRY_PATH", str(tmp_path / "source_success_registry.json"))


def _row() -> dict:
    return {
        "Source Type": "PDF",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "30 Inch Warming Drawer",
        "Product URL": "",
        "Dimensions": "",
        "Image URL": "",
        "Notes": "",
    }


def _resp(url: str, text: str):
    class Resp:
        headers = {"content-type": "text/html"}
        content = text.encode("utf-8")

        def __init__(self):
            self.text = text
            self.url = url

    return Resp()


def _high_html() -> str:
    return """
    <html>
      <script type="application/ld+json">
        {"@type":"Product","name":"Wolf MDD30TS Warming Drawer","brand":{"name":"Wolf"},"sku":"MDD30TS",
         "image":"https://subzero-wolf.com/images/mdd30ts.jpg",
         "additionalProperty":[{"name":"Dimensions","value":"29 7/8\\" W x 23 1/2\\" D x 11 7/8\\" H"}]}
      </script>
      <body>Wolf MDD30TS 30 Inch Warming Drawer dimensions</body>
    </html>
    """


def _high_candidate() -> ProductCandidate:
    return ProductCandidate(
        url="https://www.subzero-wolf.com/wolf/products/mdd30ts",
        domain="subzero-wolf.com",
        source_type="manufacturer_page",
        evidence_score=100,
        confidence="high",
        matched_sku=True,
        matched_brand=True,
        matched_product_name=True,
        is_official_domain=True,
        extracted_fields={},
    )


@pytest.fixture
def isolated_enrichment(monkeypatch, tmp_path):
    import src.product_enrichment as pe
    from src.enrichment_cache import ProductEnrichmentCache

    lookup_cache = ProductLookupCache(tmp_path / "product_lookup_cache.json")
    lookup_cache._data = {}
    product_cache = ProductEnrichmentCache()
    product_cache._path = str(tmp_path / "product_enrichment_cache.json")
    product_cache._data = {}
    monkeypatch.setattr(pe, "_lookup_cache", lookup_cache)
    monkeypatch.setattr(pe, "_product_cache", product_cache)
    return lookup_cache


def test_budget_for_modes_match_phase_9_limits():
    fast = budget_for_mode("fast")
    standard = budget_for_mode("standard")
    deep = budget_for_mode("deep")
    manual = budget_for_mode("manual_retry")

    assert (fast.brave_searches_limit, fast.page_fetches_limit, fast.ai_calls_limit) == (1, 1, 0)
    assert (standard.brave_searches_limit, standard.page_fetches_limit, standard.ai_calls_limit) == (3, 6, 1)
    assert (deep.brave_searches_limit, deep.page_fetches_limit, deep.ai_calls_limit) == (6, 12, 2)
    assert (manual.brave_searches_limit, manual.page_fetches_limit, manual.ai_calls_limit) == (10, 20, 3)


def test_fast_run_budget_defaults_target_and_hard_cap(monkeypatch):
    for name in (
        "ENRICHMENT_TARGET_BUDGET_USD",
        "ENRICHMENT_HARD_BUDGET_USD",
        "ENRICHMENT_MAX_AI_CALLS_PER_UPLOAD",
        "ENRICHMENT_MAX_EXTERNAL_LOOKUPS_PER_UPLOAD",
    ):
        monkeypatch.delenv(name, raising=False)

    budget = run_budget_for_mode("fast")

    assert budget.target_budget_usd == 0.10
    assert budget.hard_budget_usd == 0.25
    assert budget.max_ai_calls == 1
    assert budget.max_external_lookups == 12
    assert budget.max_image_searches == 3
    assert budget.max_retries == 3


def test_run_budget_blocks_paid_call_over_hard_cap():
    budget = EnrichmentRunBudget(
        hard_budget_usd=0.005,
        max_ai_calls=3,
        ai_call_cost_usd=0.03,
    )

    assert not budget.consume("ai", budget.ai_call_cost_usd, item_key="wolf_mdd30ts", field="Dimensions")
    assert budget.ai_calls_used == 0
    assert budget.estimated_cost_usd == 0
    assert budget.skipped_calls[-1]["reason"] == "hard budget exceeded"


def test_fast_budget_env_does_not_reduce_deep_mode(monkeypatch):
    monkeypatch.setenv("ENRICHMENT_TARGET_BUDGET_USD", "0.10")
    monkeypatch.setenv("ENRICHMENT_HARD_BUDGET_USD", "0.75")

    fast = run_budget_for_mode("fast")
    deep = run_budget_for_mode("deep")

    assert fast.hard_budget_usd == 0.25
    assert deep.hard_budget_usd == 2.00


def test_run_budget_tracks_provider_field_and_broad_search_costs():
    budget = run_budget_for_mode("fast")

    assert budget.consume(
        "search",
        budget.search_cost_usd,
        field="Dimensions",
        reason="brand model dimensions",
        stage="broad_search",
        query='"Wolf" "MDD30TS" dimensions',
    )

    diag = budget.diagnostics()
    assert diag["broad_searches"] == 1
    assert diag["cost_by_provider"]["brave"] == budget.search_cost_usd
    assert diag["cost_by_field"]["Dimensions"] == budget.search_cost_usd
    assert diag["brave_cost_usd"] == budget.search_cost_usd
    assert diag["brave_searches"] == 1
    assert diag["brave_calls"][0]["query"] == '"Wolf" "MDD30TS" dimensions'


def test_standard_mode_never_exceeds_three_brave_calls(monkeypatch):
    import src.product_resolver as pr

    calls = {"count": 0}

    def fake_search(*args, **kwargs):
        calls["count"] += 1
        return []

    budget = budget_for_mode("standard")
    monkeypatch.setattr(pr, "search_product_candidates", fake_search)

    resolve_product_page(_row(), budget=budget)

    assert calls["count"] == 3
    assert budget.brave_searches_used == 3
    assert budget.brave_searches_used <= budget.brave_searches_limit


def test_deep_mode_never_exceeds_six_brave_calls(monkeypatch):
    import src.product_resolver as pr

    calls = {"count": 0}

    def fake_search(*args, **kwargs):
        calls["count"] += 1
        return []

    budget = budget_for_mode("deep")
    monkeypatch.setattr(pr, "search_product_candidates", fake_search)

    resolve_product_page(_row(), budget=budget)

    assert calls["count"] == 6
    assert budget.brave_searches_used == 6
    assert budget.stopped_reason == "search budget exhausted"


def test_high_confidence_stops_early(monkeypatch):
    import src.product_resolver as pr

    calls = {"searches": 0, "fetches": 0}
    result = SearchResult(
        "Wolf MDD30TS Warming Drawer",
        "https://www.subzero-wolf.com/wolf/products/mdd30ts",
        "MDD30TS official product page",
        100,
    )

    def fake_search(*args, **kwargs):
        calls["searches"] += 1
        return [result]

    def fake_get(url, **kwargs):
        calls["fetches"] += 1
        return _resp(url, _high_html())

    budget = budget_for_mode("standard")
    monkeypatch.setattr(pr, "search_product_candidates", fake_search)
    monkeypatch.setattr(pr.httpx, "get", fake_get)

    resolved = resolve_product_page(_row(), budget=budget)

    assert resolved.selected is not None
    assert resolved.selected.confidence == "high"
    assert calls["searches"] == 1
    assert calls["fetches"] == 1
    assert budget.stopped_reason == "Stopped early: HIGH confidence official product page found"


def test_cache_hit_uses_zero_brave_calls(monkeypatch, isolated_enrichment):
    import src.product_enrichment as pe

    row = _row()
    isolated_enrichment.save_verified_lookup(
        row,
        selected_product_url="https://www.subzero-wolf.com/wolf/products/mdd30ts",
        confidence="high",
        evidence_score=100,
        dimensions='29.875"W x 23.5"D x 11.875"H',
        image_url="https://subzero-wolf.com/images/mdd30ts.jpg",
        image_confidence="HIGH",
        evidence_summary="official exact sku",
    )
    budget = budget_for_mode("standard")
    monkeypatch.setattr(pe, "resolve_product_page", lambda *a, **k: pytest.fail("live resolver should not run"))

    updated, dim_result, debug = pe._apply_official_product_lookup(row, budget=budget)

    assert updated["Product URL"] == "https://www.subzero-wolf.com/wolf/products/mdd30ts"
    assert dim_result is not None
    assert budget.brave_searches_used == 0
    assert budget.page_fetches_used == 0
    assert budget.ai_calls_used == 0
    assert debug["API Budget Stopped Reason"] == "Used cached result: no API cost"


def test_ai_not_called_on_low_confidence_candidates(monkeypatch, isolated_enrichment):
    import src.product_enrichment as pe

    budget = budget_for_mode("standard")
    low = ProductCandidate(
        url="https://www.subzero-wolf.com/wolf/products/other",
        domain="subzero-wolf.com",
        confidence="low",
        evidence_score=40,
        rejection_reason="sku_not_found",
    )
    result = ProductResolutionResult(
        selected=None,
        candidates=[low],
        diagnostics=[{"url": low.url, "confidence": "low", "rejection_reason": "sku_not_found"}],
        queries_tried=['site:subzero-wolf.com "MDD30TS"'],
        urls_checked=[low.url],
        confidence="none",
        rejection_reason="no_high_or_medium_candidate",
    )
    monkeypatch.setattr(pe, "resolve_product_page", lambda *a, **k: result)
    monkeypatch.setattr(pe, "_extract_with_claude", lambda *a, **k: pytest.fail("AI should not run"))

    updated, dim_result, debug = pe._apply_official_product_lookup(_row(), budget=budget, force_refresh=True)

    assert updated["Product URL"] == ""
    assert dim_result is None
    assert budget.ai_calls_used == 0
    assert debug["AI Extraction Status"] == "Skipped AI: no verified page"


def test_budget_exhausted_returns_clear_diagnostic(monkeypatch, isolated_enrichment):
    import src.product_enrichment as pe
    import src.product_resolver as pr

    monkeypatch.setattr(pr, "search_product_candidates", lambda *a, **k: pytest.fail("search should not run"))
    monkeypatch.setattr(pe, "resolve_product_page", pr.resolve_product_page)
    budget = ProductLookupBudget(max_searches=0, max_urls=0, max_ai_calls=0)

    updated, dim_result, debug = pe._apply_official_product_lookup(_row(), budget=budget, force_refresh=True)

    assert updated["Product URL"] == ""
    assert dim_result is None
    assert debug["API Budget Stopped Reason"] == "search budget exhausted"
    assert debug["Search Diagnostics"] == []
