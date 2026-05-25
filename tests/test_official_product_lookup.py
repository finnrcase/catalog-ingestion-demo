from __future__ import annotations

import pandas as pd

from src.brave_search import SearchResult
from src.brand_lookup_registry import build_brand_search_queries, get_brand_lookup_entry
from src.enrichment_diagnostics import ENRICHMENT_DEBUG_COLUMNS, build_enrichment_debug_dataframe
from src.official_product_lookup import (
    build_official_lookup_queries,
    lookup_official_product_page,
    validate_official_product_page,
)
from src.product_lookup_cache import ProductLookupCache, make_lookup_cache_key


def test_brand_registry_returns_visual_comfort_site_templates():
    entry = get_brand_lookup_entry("Visual Comfort")
    assert entry is not None
    assert "visualcomfort.com" in entry.domains
    queries, matched = build_brand_search_queries({
        "Brand": "Visual Comfort",
        "Model/SKU": "TOB 1234",
        "Product Name": "Table Lamp",
    })
    assert matched is entry
    assert queries[0] == "site:visualcomfort.com TOB 1234"
    assert "site:visualcomfort.com Visual Comfort TOB 1234" in queries


def test_official_sku_page_beats_generic_marketplace():
    row = {
        "Brand": "Visual Comfort",
        "Model/SKU": "TOB 1234",
        "Product Name": "Table Lamp",
    }

    def fake_search(query, brand="", session_cache=None):
        return [
            SearchResult(
                title="Marketplace table lamp",
                url="https://example-marketplace.com/table-lamp",
                description="Generic lamp listing",
                domain_score=20,
            ),
            SearchResult(
                title="Visual Comfort TOB 1234 Table Lamp",
                url="https://www.visualcomfort.com/products/tob-1234",
                description="TOB 1234 table lamp dimensions and finish",
                domain_score=80,
            ),
        ]

    result = lookup_official_product_page(row, search_fn=fake_search)
    assert result.selected_url == "https://www.visualcomfort.com/products/tob-1234"
    assert result.confidence == "HIGH"
    assert "brand_registry_domain" in result.reason
    assert "sku_match" in result.reason


def test_official_lookup_queries_prioritize_pdf_sku_workflow():
    row = {
        "Brand": "Visual Comfort",
        "Model/SKU": "TOB 1234",
        "Product Name": "Table Lamp",
    }

    queries, registry_match, domains = build_official_lookup_queries(row)

    assert registry_match is True
    assert "visualcomfort.com" in domains
    assert queries[:3] == [
        "Visual Comfort TOB 1234 official product page",
        "Visual Comfort TOB 1234 dimensions",
        "site:visualcomfort.com TOB 1234",
    ]


def test_validate_official_product_page_requires_sku_brand_and_name(monkeypatch):
    class Resp:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = """
        <html><title>Visual Comfort TOB 1234 Table Lamp</title>
        <body>Visual Comfort TOB 1234 Table Lamp dimensions and finishes.</body></html>
        """

    monkeypatch.setattr("src.official_product_lookup.httpx.get", lambda *args, **kwargs: Resp())

    result = validate_official_product_page(
        {"Brand": "Visual Comfort", "Model/SKU": "TOB 1234", "Product Name": "Table Lamp"},
        "https://www.visualcomfort.com/products/tob-1234",
        registry_domains=["visualcomfort.com"],
    )

    assert result.valid is True
    assert result.sku_match is True
    assert result.brand_match is True
    assert result.product_name_match is True


def test_lookup_with_page_validation_rejects_generic_category_page(monkeypatch):
    row = {
        "Brand": "Visual Comfort",
        "Model/SKU": "TOB 1234",
        "Product Name": "Table Lamp",
    }

    def fake_search(query, brand="", session_cache=None):
        return [
            SearchResult(
                title="Visual Comfort search results for TOB 1234",
                url="https://www.visualcomfort.com/search?q=TOB+1234",
                description="TOB 1234 table lamp",
                domain_score=90,
            )
        ]

    class Resp:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = "<html><body>Search results for Visual Comfort TOB 1234 Table Lamp</body></html>"

    monkeypatch.setattr("src.official_product_lookup.httpx.get", lambda *args, **kwargs: Resp())

    result = lookup_official_product_page(row, search_fn=fake_search, validate_pages=True)

    assert result.selected_url == ""
    assert result.confidence == "NONE"
    assert "page_validation_failed:generic_page" in result.candidate_pages[0]["reasons"]


def test_product_lookup_cache_saves_and_reloads(tmp_path):
    cache_path = tmp_path / "product_lookup_cache.json"
    row = {"Brand": "Palecek", "Model/SKU": "ABC-1", "Product Name": "Chair", "Supplier": ""}
    key = make_lookup_cache_key(row)
    cache = ProductLookupCache(cache_path)
    cache.set(key, {
        "selected_product_page_url": "https://palecek.com/products/abc-1",
        "selected_image_url": "https://palecek.com/images/abc-1.jpg",
        "dimensions": '20"W x 30"H x 24"D',
        "confidence": "high",
        "evidence": "brand_registry_domain;sku_match",
        "source_domain": "palecek.com",
    })
    reloaded = ProductLookupCache(cache_path)
    entry = reloaded.get(key)
    assert entry["selected_product_page_url"] == "https://palecek.com/products/abc-1"
    assert entry["selected_image_url"].endswith("abc-1.jpg")
    assert entry["dimensions"] == '20"W x 30"H x 24"D'
    assert entry["timestamp"]


def test_debug_columns_include_brand_lookup_scoring_fields():
    df = pd.DataFrame([{
        "Product Name": "Lamp",
        "Brand": "Visual Comfort",
        "Model/SKU": "TOB 1234",
        "brand_registry_match": True,
        "selected_product_page_score": 180,
    }])
    debug_df = build_enrichment_debug_dataframe(df)
    for col in (
        "brand_registry_match",
        "brand_registry_domains_checked",
        "brand_search_queries_used",
        "candidate_page_scores",
        "selected_product_page_score",
        "selected_image_reason",
        "dimension_evidence",
        "web_lookup_error",
    ):
        assert col in ENRICHMENT_DEBUG_COLUMNS
        assert col in debug_df.columns
