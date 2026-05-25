from __future__ import annotations

import pytest

from src.brave_search import SearchResult
from src.dimension_enrichment import DimensionResult
from src.enrichment_cache import ProductEnrichmentCache, SessionCache
from src.product_lookup_cache import ProductLookupCache
from src.product_enrichment import (
    build_product_search_queries,
    enrich_row,
    find_best_product_page,
)


def _row() -> dict:
    return {
        "Source Type": "PDF",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "30 Inch Warming Drawer",
        "Dimensions": "",
        "Finish / Color": "",
        "Product Category": "Appliances",
        "Product URL": "",
        "Notes": "",
        "Review Required": False,
        "Suggested Action": "",
    }


@pytest.fixture(autouse=True)
def _isolate_enrichment_caches(monkeypatch, tmp_path):
    import src.product_enrichment as pe

    product_cache = ProductEnrichmentCache()
    product_cache._data = {}
    product_cache._path = str(tmp_path / "product_enrichment_cache.json")
    lookup_cache = ProductLookupCache(tmp_path / "product_lookup_cache.json")
    lookup_cache._data = {}

    monkeypatch.setattr(pe, "_product_cache", product_cache)
    monkeypatch.setattr(pe, "_lookup_cache", lookup_cache)


def test_build_product_search_queries_order_with_manufacturer_domain():
    queries = build_product_search_queries(_row(), manufacturer_domain="subzero-wolf.com")

    assert queries == [
        'site:subzero-wolf.com "MDD30TS" specifications',
        'site:subzero-wolf.com "MDD30TS" product',
        'site:subzero-wolf.com "MDD30TS" images',
        '"Wolf" "MDD30TS" official product page',
        '"Wolf" "30 Inch Warming Drawer" "MDD30TS"',
        '"MDD30TS" "30 Inch Warming Drawer"',
    ]


def test_exact_official_page_beats_retailer_page(monkeypatch):
    import src.product_enrichment as pe

    retailer = SearchResult(
        title="Wolf MDD30TS 30 Inch Warming Drawer",
        url="https://www.build.com/wolf-mdd30ts/p123",
        description="Wolf MDD30TS product listing",
        domain_score=80,
    )
    official = SearchResult(
        title="Wolf MDD30TS 30 Inch Warming Drawer",
        url="https://www.subzero-wolf.com/wolf/cooking/warming-drawers/mdd30ts",
        description="Official Wolf MDD30TS specifications",
        domain_score=90,
    )

    monkeypatch.setattr(pe, "search_product_candidates", lambda *a, **k: [retailer, official])
    monkeypatch.setattr(
        pe,
        "_fetch_page_html",
        lambda url: "<html><body>Wolf MDD30TS 30 Inch Warming Drawer specifications.</body></html>",
    )

    candidate = find_best_product_page(_row())

    assert candidate is not None
    assert candidate.url == official.url
    assert candidate.evidence.confidence == "high"
    assert any(item["url"] == retailer.url for item in candidate.rejected_candidates)


def test_retailer_page_used_only_if_no_official_high_page_exists(monkeypatch):
    import src.product_enrichment as pe

    retailer = SearchResult(
        title="Wolf MDD30TS 30 Inch Warming Drawer",
        url="https://www.build.com/wolf-mdd30ts/p123",
        description="Wolf MDD30TS product listing",
        domain_score=80,
    )

    monkeypatch.setattr(pe, "search_product_candidates", lambda *a, **k: [retailer])
    monkeypatch.setattr(
        pe,
        "_fetch_page_html",
        lambda url: "<html><body>Wolf MDD30TS 30 Inch Warming Drawer product listing.</body></html>",
    )

    candidate = find_best_product_page(_row())

    assert candidate is not None
    assert candidate.url == retailer.url
    assert candidate.evidence.confidence == "medium"
    assert candidate.evidence.official_domain is False


def test_unrelated_official_domain_page_rejected_if_sku_not_found(monkeypatch):
    import src.product_enrichment as pe

    unrelated = SearchResult(
        title="Wolf Warming Drawers",
        url="https://www.subzero-wolf.com/wolf/cooking/warming-drawers",
        description="Official Wolf warming drawer product family",
        domain_score=90,
    )
    session_cache = SessionCache()

    monkeypatch.setattr(pe, "search_product_candidates", lambda *a, **k: [unrelated])
    monkeypatch.setattr(
        pe,
        "_fetch_page_html",
        lambda url: "<html><body>Wolf warming drawer product family and design options.</body></html>",
    )

    candidate = find_best_product_page(_row(), session_cache=session_cache)

    assert candidate is None
    diagnostics = next(iter(session_cache.product_page_diagnostics.values()))
    assert diagnostics[0]["url"] == unrelated.url
    assert diagnostics[0]["confidence"] in {"low", "none"}
    assert diagnostics[0]["matched_sku"] is False


def test_no_good_page_leaves_row_unenriched_with_diagnostic_note(monkeypatch):
    import src.product_enrichment as pe

    unrelated = SearchResult(
        title="Wolf Warming Drawers",
        url="https://www.subzero-wolf.com/wolf/cooking/warming-drawers",
        description="Official Wolf warming drawer product family",
        domain_score=90,
    )

    monkeypatch.setattr(pe, "search_product_candidates", lambda *a, **k: [unrelated])
    monkeypatch.setattr(
        pe,
        "_fetch_page_html",
        lambda url: "<html><body>Wolf warming drawer product family and design options.</body></html>",
    )
    monkeypatch.setattr(pe, "_find_dimensions", lambda *a, **k: DimensionResult())

    updated, error, dim_result = enrich_row(_row())

    assert error is None
    assert dim_result is not None
    assert updated["Product URL"] == ""
    assert updated["Product Name"] == "30 Inch Warming Drawer"
    assert updated["Dimensions"] == ""
    assert updated["Source Type"] == "PDF"
    assert "[Enrichment: no confident source found]" in updated["Notes"]
