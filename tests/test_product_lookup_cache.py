from __future__ import annotations

import pytest

from src.enrichment_cache import ProductEnrichmentCache
from src.product_lookup_cache import (
    ProductLookupCache,
    can_reuse_lookup,
    is_no_result,
    make_lookup_cache_key,
    product_name_hash,
)
from src.product_resolver import ProductCandidate, ProductResolutionResult


def _row() -> dict:
    return {
        "Source Type": "PDF",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "30 Inch Warming Drawer",
        "Product URL": "",
        "Dimensions": "",
        "Finish / Color": "",
        "Material": "",
        "Image URL": "",
        "Notes": "",
    }


def _html() -> str:
    return """
    <html>
      <head>
        <meta property="og:image" content="https://cdn.wolfappliance.com/images/mdd30ts.jpg">
      </head>
      <body>
        <h1>Wolf MDD30TS 30 Inch Warming Drawer</h1>
        <table class="specs">
          <tr><th>Dimensions</th><td>29 7/8&quot; W x 23 1/2&quot; D x 11 7/8&quot; H</td></tr>
          <tr><th>Finish</th><td>Stainless Steel</td></tr>
          <tr><th>Material</th><td>Stainless Steel</td></tr>
        </table>
      </body>
    </html>
    """


def _high_candidate() -> ProductCandidate:
    return ProductCandidate(
        url="https://wolfappliance.com/products/mdd30ts",
        domain="wolfappliance.com",
        title="Wolf MDD30TS",
        html=_html(),
        text="Wolf MDD30TS 30 Inch Warming Drawer",
        source_type="manufacturer_page",
        evidence_score=100,
        confidence="high",
        matched_sku=True,
        matched_brand=True,
        matched_product_name=True,
        is_official_domain=True,
        extracted_dimensions='29.875"W x 23.5"D x 11.875"H',
        extracted_image_url="https://cdn.wolfappliance.com/images/mdd30ts.jpg",
        extracted_fields={
            "Dimensions": '29.875"W x 23.5"D x 11.875"H',
            "width": "29.875",
            "height": "11.875",
            "depth": "23.5",
            "Image URL": "https://cdn.wolfappliance.com/images/mdd30ts.jpg",
            "image_confidence": "HIGH",
            "image_source": "jsonld_image",
            "image_evidence": "sku_in_url",
            "dimension_confidence": "high",
        },
    )


@pytest.fixture
def isolated_lookup_cache(monkeypatch, tmp_path):
    import src.product_enrichment as pe

    lookup_cache = ProductLookupCache(tmp_path / "product_lookup_cache.json")
    lookup_cache._data = {}
    product_cache = ProductEnrichmentCache()
    product_cache._path = str(tmp_path / "product_enrichment_cache.json")
    product_cache._data = {}
    monkeypatch.setattr(pe, "_lookup_cache", lookup_cache)
    monkeypatch.setattr(pe, "_product_cache", product_cache)
    return lookup_cache


def test_cache_key_is_normalized_brand_and_sku_only():
    first = make_lookup_cache_key({"Brand": " Wolf ", "Model/SKU": "MDD-30 TS", "Product Name": "A"})
    second = make_lookup_cache_key({"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "B"})

    assert first == second == "wolf_mdd30ts"


def test_product_lookup_cache_saves_high_result(tmp_path):
    row = _row()
    cache = ProductLookupCache(tmp_path / "product_lookup_cache.json")

    entry = cache.save_verified_lookup(
        row,
        selected_product_url="https://wolfappliance.com/products/mdd30ts",
        source_type="manufacturer_page",
        confidence="high",
        evidence_score=100,
        dimensions='29.875"W x 23.5"D x 11.875"H',
        width_in="29.875",
        height_in="11.875",
        depth_in="23.5",
        finish="Stainless Steel",
        material="Stainless Steel",
        image_url="https://cdn.wolfappliance.com/images/mdd30ts.jpg",
        cloudinary_url="https://res.cloudinary.com/demo/image/upload/mdd30ts.jpg",
        image_confidence="HIGH",
        evidence_summary="official exact sku",
    )

    reloaded = ProductLookupCache(tmp_path / "product_lookup_cache.json")
    saved = reloaded.get(make_lookup_cache_key(row))
    assert can_reuse_lookup(entry) is True
    assert saved["brand"] == "Wolf"
    assert saved["sku"] == "MDD30TS"
    assert saved["selected_product_url"] == "https://wolfappliance.com/products/mdd30ts"
    assert saved["selected_product_page_url"] == "https://wolfappliance.com/products/mdd30ts"
    assert saved["verified_product_url"] == "https://wolfappliance.com/products/mdd30ts"
    assert saved["confidence"] == "high"
    assert saved["image_confidence"] == "HIGH"
    assert saved["verified_image_url"] == "https://cdn.wolfappliance.com/images/mdd30ts.jpg"
    assert saved["cloudinary_url"] == "https://res.cloudinary.com/demo/image/upload/mdd30ts.jpg"
    assert saved["product_name_hash"] == product_name_hash("30 Inch Warming Drawer")
    assert saved["last_verified"]


def test_second_lookup_uses_cache_and_does_not_call_live_search(monkeypatch, isolated_lookup_cache):
    import src.product_enrichment as pe

    row = _row()
    isolated_lookup_cache.save_verified_lookup(
        row,
        selected_product_url="https://wolfappliance.com/products/mdd30ts",
        source_type="manufacturer_page",
        confidence="high",
        evidence_score=100,
        dimensions='29.875"W x 23.5"D x 11.875"H',
        width_in="29.875",
        height_in="11.875",
        depth_in="23.5",
        image_url="https://cdn.wolfappliance.com/images/mdd30ts.jpg",
        cloudinary_url="https://res.cloudinary.com/demo/image/upload/mdd30ts.jpg",
        image_confidence="HIGH",
        evidence_summary="official exact sku",
    )
    monkeypatch.setattr(pe, "resolve_product_page", lambda *a, **k: pytest.fail("live search should not run"))

    updated, dim_result, debug = pe._apply_official_product_lookup(row)

    assert updated["Product URL"] == "https://wolfappliance.com/products/mdd30ts"
    assert updated["Dimensions"] == '29.875"W x 23.5"D x 11.875"H'
    assert updated["Image URL"] == "https://res.cloudinary.com/demo/image/upload/mdd30ts.jpg"
    assert updated["cloudinary_url"] == "https://res.cloudinary.com/demo/image/upload/mdd30ts.jpg"
    assert updated["Original Image URL"] == "https://cdn.wolfappliance.com/images/mdd30ts.jpg"
    assert dim_result is not None
    assert debug["product_lookup_cache_status"] == "hit"


def test_medium_cached_result_fills_blanks_but_marks_review(monkeypatch, isolated_lookup_cache):
    import src.product_enrichment as pe

    row = _row()
    isolated_lookup_cache.save_verified_lookup(
        row,
        selected_product_url="https://www.build.com/wolf-mdd30ts/p123",
        source_type="retailer_page",
        confidence="medium",
        evidence_score=70,
        dimensions='29.875"W x 23.5"D x 11.875"H',
        image_url="https://cdn.build.com/images/wolf-mdd30ts.jpg",
        image_confidence="MEDIUM",
        evidence_summary="retailer exact sku",
    )
    monkeypatch.setattr(pe, "resolve_product_page", lambda *a, **k: pytest.fail("live search should not run"))

    updated, _dim_result, debug = pe._apply_official_product_lookup(row)

    assert updated["Product URL"] == "https://www.build.com/wolf-mdd30ts/p123"
    assert updated["Review Required"] is True
    assert updated["needs_image_review"] == "True"
    assert debug["Product Resolution Confidence"] == "medium"


def test_low_none_cached_result_does_not_fill_fields(monkeypatch, isolated_lookup_cache):
    import src.product_enrichment as pe

    row = _row()
    isolated_lookup_cache.record_no_result(row, confidence="none", evidence_summary="no verified product page")
    monkeypatch.setattr(pe, "resolve_product_page", lambda *a, **k: pytest.fail("live search should not run"))

    updated, dim_result, debug = pe._apply_official_product_lookup(row)

    assert updated["Product URL"] == ""
    assert updated["Dimensions"] == ""
    assert updated["Image URL"] == ""
    assert dim_result is None
    assert is_no_result(isolated_lookup_cache.get(make_lookup_cache_key(row))) is True
    assert debug["product_lookup_cache_status"] == "searched_no_result"


def test_force_refresh_bypasses_cache(monkeypatch, isolated_lookup_cache):
    import src.product_enrichment as pe

    row = _row()
    isolated_lookup_cache.save_verified_lookup(
        row,
        selected_product_url="https://wolfappliance.com/products/cached",
        confidence="high",
        evidence_score=100,
    )
    calls = {"count": 0}

    def fake_resolve(*args, **kwargs):
        calls["count"] += 1
        return ProductResolutionResult(queries_tried=["live"], diagnostics=[], rejection_reason="no_match")

    monkeypatch.setattr(pe, "resolve_product_page", fake_resolve)

    updated, _dim_result, debug = pe._apply_official_product_lookup(row, force_refresh=True)

    assert calls["count"] == 1
    assert updated["Product URL"] == ""
    assert debug["product_lookup_cache_status"] == "force_refresh"


def test_successful_resolution_writes_product_lookup_cache(monkeypatch, isolated_lookup_cache):
    import src.product_enrichment as pe

    row = _row()
    candidate = _high_candidate()
    result = ProductResolutionResult(
        selected=candidate,
        candidates=[candidate],
        diagnostics=[{"url": candidate.url, "confidence": "high"}],
        queries_tried=['site:wolfappliance.com "MDD30TS"'],
        urls_checked=[candidate.url],
        confidence="high",
        evidence_score=100,
        selected_url=candidate.url,
    )
    monkeypatch.setattr(pe, "resolve_product_page", lambda *a, **k: result)

    updated, _dim_result, debug = pe._apply_official_product_lookup(row, force_refresh=True)
    saved = isolated_lookup_cache.get(make_lookup_cache_key(row))

    assert updated["Product URL"] == candidate.url
    assert saved["selected_product_url"] == candidate.url
    assert saved["source_type"] == "manufacturer_page"
    assert saved["confidence"] == "high"
    assert saved["evidence_score"] == 100
    assert saved["dimensions"] == '29.875"W x 23.5"D x 11.875"H'
    assert saved["image_url"] == "https://cdn.wolfappliance.com/images/mdd30ts.jpg"
    assert saved["image_confidence"] == "HIGH"
    assert debug["Product Resolution Confidence"] == "high"
