import pytest
import pandas as pd
from src.product_enrichment import (
    _qualifies,
    _build_search_query,
    _apply_enrichment,
    build_search_queries,
    enrich_row,
    enrich_dataframe,
    has_enough_search_identity,
)


def _resolver_response(url: str, html: str):
    class Resp:
        status_code = 200
        headers = {"content-type": "text/html"}
        content = html.encode("utf-8")

        def __init__(self):
            self.text = html
            self.url = url

    return Resp()


@pytest.fixture(autouse=True)
def _isolate_caches(monkeypatch, tmp_path):
    """Isolate each test from all cache singletons and manufacturer_domains disk writes."""
    from src.enrichment_cache import ProductEnrichmentCache, ManufacturerDomainCache
    import src.product_enrichment as pe
    import src.dimension_enrichment as de
    # Prevent manufacturer_domains.py from writing to real data/manufacturer_domain_cache.json
    monkeypatch.setattr("src.product_enrichment.get_domain_for_brand", lambda brand: None)
    monkeypatch.setattr("src.product_enrichment.record_discovered_domain", lambda brand, domain: None)
    # Isolate ProductEnrichmentCache singleton — redirect _path so _save() never hits real file
    fresh_cache = ProductEnrichmentCache()
    fresh_cache._data = {}
    fresh_cache._path = str(tmp_path / "product_enrichment_cache.json")
    monkeypatch.setattr(pe, "_product_cache", fresh_cache)
    from src.product_lookup_cache import ProductLookupCache
    fresh_lookup_cache = ProductLookupCache(tmp_path / "product_lookup_cache.json")
    fresh_lookup_cache._data = {}
    monkeypatch.setattr(pe, "_lookup_cache", fresh_lookup_cache)
    # Isolate ManufacturerDomainCache singleton in dimension_enrichment
    fresh_mfr = ManufacturerDomainCache()
    fresh_mfr._data = {}
    fresh_mfr._path = str(tmp_path / "manufacturer_domain_cache.json")
    monkeypatch.setattr(de, "_mfr_cache", fresh_mfr)


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
        "Dimensions": '30"W x 15"H x 17"D',
        "Finish / Color": "Stainless",
        "Product Category": "Appliance",
        "Product URL": "https://example.com",
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


def test_qualifies_none_brand_skipped():
    row = {**_base_qualifying_row(), "Brand": None}
    assert not _qualifies(row)


def test_qualifies_whitespace_only_sku_skipped():
    row = {**_base_qualifying_row(), "Model/SKU": "   "}
    assert not _qualifies(row)


def test_qualifies_missing_url_only():
    # URL missing but other fields present → should qualify
    row = {
        **_base_qualifying_row(),
        "Product Name": "Wolf Microwave",
        "Dimensions": '30"W',
        "Finish / Color": "Stainless",
        "Product Category": "Appliance",
        "Product URL": "",  # only URL is blank
    }
    assert _qualifies(row)


# ── _build_search_query ────────────────────────────────────────────────────────

def test_build_search_query_full():
    """Rows without complete 3D dims get the dimension-focused suffix."""
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "Drawer Microwave"}
    assert _build_search_query(row) == "Wolf MDD30TS Drawer Microwave dimensions width height depth spec sheet official"


def test_build_search_query_no_product_name():
    row = {"Brand": "Sub-Zero", "Model/SKU": "ID36R", "Product Name": ""}
    assert _build_search_query(row) == "Sub-Zero ID36R dimensions width height depth spec sheet official"


def test_build_search_query_strips_whitespace():
    row = {"Brand": "  Miele  ", "Model/SKU": " CVA7440 ", "Product Name": ""}
    result = _build_search_query(row)
    assert result == "Miele CVA7440 dimensions width height depth spec sheet official"


def test_build_search_query_none_fields():
    row = {"Brand": "Wolf", "Model/SKU": None, "Product Name": None}
    assert _build_search_query(row) == "Wolf dimensions width height depth spec sheet official"


def test_build_search_query_uses_manufacturer_override_domain(monkeypatch):
    monkeypatch.setattr(
        "src.product_enrichment.get_domain_for_brand",
        lambda brand: ("scotsman-ice.com", "user"),
    )
    row = {"Brand": "Scotsman", "Model/SKU": "SCN60PA1SU", "Product Name": "Ice Machine"}

    assert _build_search_query(row).startswith("site:scotsman-ice.com Scotsman SCN60PA1SU")


def test_precise_query_builder_prioritizes_brand_sku():
    row = {"Brand": "Wolf", "Model/SKU": "MDD-30/TS", "Product Name": "Warming Drawer", "Supplier": "Ferguson"}
    queries = build_search_queries(row)
    assert queries[0] == '"Wolf" "MDD-30/TS"'
    assert "MDD-30/TS" in queries[0]


def test_precise_query_builder_does_not_run_with_insufficient_info():
    assert has_enough_search_identity({"Product Name": "Chair"}) is False


def test_precise_query_builder_preserves_sku_hyphens_slashes():
    queries = build_search_queries({"Brand": "Sub-Zero", "Model/SKU": "DEC3650RID/R"})
    assert queries[0] == '"Sub-Zero" "DEC3650RID/R"'


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
        "Dimensions": '29 7/8" W × 23 1/2" D × 11 7/8" H',
        "Finish / Color": "",
        "Product Category": "Appliance",
        "materials": "",
    }
    updated = _apply_enrichment(row, extracted, "https://wolfappliance.com", 85)
    assert updated["Product Name"] == "Wolf 30\" Drawer Microwave"
    assert updated["Dimensions"] == '29 7/8" W × 23 1/2" D × 11 7/8" H'
    assert updated["Product Category"] == "Appliances"
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
    assert updated["Finish / Color"] == "Black"  # blank field IS filled


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
        "materials": "stainless steel",
    }
    updated = _apply_enrichment(row, extracted, "https://example.com", 70)
    assert "Materials" not in updated.get("Notes", "")


def test_apply_enrichment_normalises_category():
    row = _base_row_for_apply()
    extracted = {
        "Product Name": "",
        "Dimensions": "",
        "Finish / Color": "",
        "Product Category": "couch",  # alias → Seating
        "materials": "",
    }
    updated = _apply_enrichment(row, extracted, "https://example.com", 70)
    assert updated["Product Category"] == "Seating"


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


# ── extract_image_url / _is_valid_image_url ────────────────────────────────────

from src.product_enrichment import extract_image_url, _is_valid_image_url


def test_is_valid_image_url_accepts_https_jpg():
    assert _is_valid_image_url("https://example.com/photo.jpg") is True


def test_is_valid_image_url_accepts_https_jpeg():
    assert _is_valid_image_url("https://cdn.example.com/img.jpeg") is True


def test_is_valid_image_url_accepts_https_png():
    assert _is_valid_image_url("https://example.com/img.png") is True


def test_is_valid_image_url_accepts_query_string():
    assert _is_valid_image_url("https://example.com/img.jpg?size=large") is True


def test_is_valid_image_url_rejects_http():
    assert _is_valid_image_url("http://example.com/img.jpg") is False


def test_is_valid_image_url_rejects_no_extension():
    assert _is_valid_image_url("https://example.com/product") is False


def test_is_valid_image_url_accepts_webp():
    assert _is_valid_image_url("https://example.com/photo.webp") is True


def test_is_valid_image_url_accepts_gif():
    assert _is_valid_image_url("https://example.com/anim.gif") is True


def test_is_valid_image_url_rejects_relative():
    assert _is_valid_image_url("/images/photo.jpg") is False


def test_is_valid_image_url_rejects_empty():
    assert _is_valid_image_url("") is False


def test_extract_image_url_og_image():
    html = '<meta property="og:image" content="https://example.com/hero.jpg">'
    assert extract_image_url(html) == "https://example.com/hero.jpg"


def test_extract_image_url_og_image_content_first():
    html = '<meta content="https://example.com/hero.jpg" property="og:image">'
    assert extract_image_url(html) == "https://example.com/hero.jpg"


def test_extract_image_url_og_image_webp_returned():
    """og:image with .webp is now returned by extract_image_url — content-type validation happens in enrich_row."""
    html = '<meta property="og:image" content="https://example.com/hero.webp">'
    assert extract_image_url(html) == "https://example.com/hero.webp"


def test_extract_image_url_og_image_no_extension_returned():
    """og:image CDN URLs without file extension are returned (Scene7-style)."""
    html = '<meta property="og:image" content="https://s7d4.scene7.com/is/image/Brand/Model">'
    assert extract_image_url(html) == "https://s7d4.scene7.com/is/image/Brand/Model"


def test_extract_image_url_jsonld_image():
    import json
    data = {"@type": "Product", "image": "https://example.com/product.png"}
    html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
    assert extract_image_url(html) == "https://example.com/product.png"


def test_extract_image_url_jsonld_image_list():
    import json
    data = {"@type": "Product", "image": ["https://example.com/p.png", "https://example.com/q.png"]}
    html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
    assert extract_image_url(html) == "https://example.com/p.png"


def test_extract_image_url_largest_img():
    html = (
        '<img src="https://example.com/small.jpg" width="50" height="50">'
        '<img src="https://example.com/large.jpg" width="800" height="600">'
        '<img src="https://example.com/medium.jpg" width="400" height="300">'
    )
    assert extract_image_url(html) == "https://example.com/large.jpg"


def test_extract_image_url_no_valid_images_returns_none():
    html = '<img src="/relative/path.jpg"><img src="https://example.com/page">'
    assert extract_image_url(html) is None


def test_extract_image_url_empty_html_returns_none():
    assert extract_image_url("") is None


# ── _check_image_content_type ─────────────────────────────────────────────────

from src.product_enrichment import _check_image_content_type


def test_check_image_content_type_accepts_image_jpeg():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "image/jpeg"}
    with patch("src.product_enrichment.httpx.head", return_value=mock_resp):
        assert _check_image_content_type("https://example.com/photo.jpg") is True


def test_check_image_content_type_accepts_image_png():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "image/png"}
    with patch("src.product_enrichment.httpx.head", return_value=mock_resp):
        assert _check_image_content_type("https://cdn.example.com/asset") is True


def test_check_image_content_type_rejects_html():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
    with patch("src.product_enrichment.httpx.head", return_value=mock_resp):
        assert _check_image_content_type("https://example.com/product") is False


def test_check_image_content_type_rejects_non_200():
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.headers = {"content-type": "text/html"}
    with patch("src.product_enrichment.httpx.head", return_value=mock_resp):
        assert _check_image_content_type("https://example.com/missing.jpg") is False


def test_check_image_content_type_rejects_on_exception():
    with patch("src.product_enrichment.httpx.head", side_effect=Exception("timeout")):
        assert _check_image_content_type("https://example.com/photo.jpg") is False


def test_check_image_content_type_rejects_empty_content_type():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}
    with patch("src.product_enrichment.httpx.head", return_value=mock_resp):
        assert _check_image_content_type("https://example.com/asset") is False


def test_check_image_content_type_get_fallback_when_head_fails():
    """If HEAD returns 405/403, retry with GET range request."""
    head_resp = MagicMock()
    head_resp.status_code = 405
    head_resp.headers = {}

    get_resp = MagicMock()
    get_resp.status_code = 206
    get_resp.headers = {"content-type": "image/jpeg"}

    with patch("src.product_enrichment.httpx.head", return_value=head_resp), \
         patch("src.product_enrichment.httpx.get", return_value=get_resp):
        assert _check_image_content_type("https://cdn.example.com/image") is True


def test_check_image_content_type_rejects_when_both_head_and_get_fail():
    """HEAD 405, GET 404 → False."""
    head_resp = MagicMock()
    head_resp.status_code = 405
    head_resp.headers = {}

    get_resp = MagicMock()
    get_resp.status_code = 404
    get_resp.headers = {"content-type": "text/html"}

    with patch("src.product_enrichment.httpx.head", return_value=head_resp), \
         patch("src.product_enrichment.httpx.get", return_value=get_resp):
        assert _check_image_content_type("https://cdn.example.com/missing") is False


# ── extract_image_url — extended sources ──────────────────────────────────────

def test_extract_image_url_twitter_image():
    html = '<meta name="twitter:image" content="https://example.com/card.jpg">'
    assert extract_image_url(html) == "https://example.com/card.jpg"


def test_extract_image_url_twitter_image_content_first():
    html = '<meta content="https://example.com/card.jpg" name="twitter:image">'
    assert extract_image_url(html) == "https://example.com/card.jpg"


def test_extract_image_url_data_src():
    html = '<img data-src="https://example.com/lazy.jpg" width="800" height="600">'
    assert extract_image_url(html) == "https://example.com/lazy.jpg"


def test_extract_image_url_data_original():
    html = '<img data-original="https://example.com/orig.jpg" width="600" height="400">'
    assert extract_image_url(html) == "https://example.com/orig.jpg"


def test_extract_image_url_srcset_returns_last_largest():
    html = (
        '<img srcset="https://example.com/sm.jpg 320w, '
        'https://example.com/lg.jpg 1200w" '
        'src="https://example.com/fallback.jpg">'
    )
    result = extract_image_url(html)
    assert result == "https://example.com/lg.jpg"


def test_extract_image_url_rejects_tiny_image():
    """Images with both width and height < 100 are skipped (icons, tracking pixels)."""
    html = (
        '<img src="https://example.com/icon.png" width="32" height="32">'
        '<img src="https://example.com/product.jpg" width="800" height="600">'
    )
    assert extract_image_url(html) == "https://example.com/product.jpg"


def test_extract_image_url_og_takes_priority_over_twitter():
    html = (
        '<meta property="og:image" content="https://example.com/og.jpg">'
        '<meta name="twitter:image" content="https://example.com/tw.jpg">'
    )
    assert extract_image_url(html) == "https://example.com/og.jpg"


def test_extract_image_url_twitter_before_jsonld():
    import json
    data = {"image": "https://example.com/ld.jpg"}
    html = (
        f'<meta name="twitter:image" content="https://example.com/tw.jpg">'
        f'<script type="application/ld+json">{json.dumps(data)}</script>'
    )
    assert extract_image_url(html) == "https://example.com/tw.jpg"


# ── enrich_row image recovery on full cache hit ───────────────────────────────

def test_enrich_row_image_from_product_url_on_full_cache_hit(monkeypatch, tmp_path):
    """On full cache hit where image_url is null, enrich_row fetches the product URL to get image."""
    from src.enrichment_cache import ProductEnrichmentCache, normalize_key
    import src.product_enrichment as pe

    cache = ProductEnrichmentCache()
    cache._path = str(tmp_path / "c.json")
    cache._data = {}
    key = normalize_key("Wolf", "MDD30TS")
    cache.update(key, {
        "product_url": "https://wolfappliance.com/mdd30ts",
        "dimensions": '30"W x 15"H x 17"D',
        "image_url": None,
        "general_confidence": "high",
        "dimension_confidence": "high",
    })
    monkeypatch.setattr(pe, "_product_cache", cache)

    html_with_image = '<meta property="og:image" content="https://wolfappliance.com/img/mdd30ts.jpg">'

    with patch("src.product_enrichment._fetch_page_html", return_value=html_with_image), \
         patch("src.product_enrichment._check_image_content_type", return_value=True):
        updated, error, _ = enrich_row(_qualifying_row())

    assert error is None
    assert updated.get("Image URL") == "https://wolfappliance.com/img/mdd30ts.jpg"


def test_enrich_row_full_cache_hit_no_extra_search_when_image_already_cached(monkeypatch, tmp_path):
    """Full cache hit with valid cached image_url should NOT trigger a page fetch."""
    from src.enrichment_cache import ProductEnrichmentCache, normalize_key
    import src.product_enrichment as pe

    cache = ProductEnrichmentCache()
    cache._path = str(tmp_path / "c.json")
    cache._data = {}
    key = normalize_key("Wolf", "MDD30TS")
    cache.update(key, {
        "product_url": "https://wolfappliance.com/mdd30ts",
        "dimensions": '30"W x 15"H x 17"D',
        "image_url": "https://wolfappliance.com/img/cached.jpg",
        "general_confidence": "high",
        "dimension_confidence": "high",
    })
    monkeypatch.setattr(pe, "_product_cache", cache)

    with patch("src.product_enrichment._fetch_page_html") as mock_fetch:
        updated, error, _ = enrich_row(_qualifying_row())

    mock_fetch.assert_not_called()
    assert updated.get("Image URL") == "https://wolfappliance.com/img/cached.jpg"


# ── recover_images_for_dataframe ──────────────────────────────────────────────
# These tests verify the legacy entry point in product_enrichment delegates to
# src.image_recovery.recover_images_for_dataframe. The detailed row-iteration
# mechanics (skip-existing, find-from-url, not-found, multi-row counts) are
# fully covered by tests/test_image_recovery.py (Task 7).

from src.product_enrichment import recover_images_for_dataframe


def test_recover_images_legacy_entry_point_delegates():
    """Legacy entry point forwards to image_recovery and returns its result unchanged."""
    rows = [{**_qualifying_row(), "Image URL": "", "Product URL": "https://wolfappliance.com/p"}]
    df = pd.DataFrame(rows)
    expected_df = df.copy()
    expected_df.at[0, "Image URL"] = "https://wolfappliance.com/img.jpg"
    expected_diagnostics = [{"row_index": 0, "status": "found"}]

    with patch("src.image_recovery.recover_images_for_dataframe", return_value=(expected_df, expected_diagnostics)) as m:
        updated_df, diagnostics = recover_images_for_dataframe(df)

    assert m.called
    assert updated_df.iloc[0]["Image URL"] == "https://wolfappliance.com/img.jpg"
    assert diagnostics == expected_diagnostics


def test_recover_images_passes_kwargs_to_impl():
    """Legacy entry point forwards pdf_lookup, session_id, enable_screenshot kwargs."""
    df = pd.DataFrame([{"Product Name": "X", "Image URL": "", "Product URL": ""}])

    with patch("src.image_recovery.recover_images_for_dataframe", return_value=(df, [])) as m:
        recover_images_for_dataframe(
            df,
            pdf_lookup={"a": "/tmp/x.pdf"},
            session_id="sess1",
            enable_screenshot=False,
        )

    kwargs = m.call_args.kwargs
    assert kwargs["pdf_lookup"] == {"a": "/tmp/x.pdf"}
    assert kwargs["session_id"] == "sess1"
    assert kwargs["enable_screenshot"] is False


def test_recover_images_defaults_enable_screenshot_true():
    """enable_screenshot defaults to True when not explicitly provided."""
    df = pd.DataFrame([{"Product Name": "X", "Image URL": "", "Product URL": ""}])

    with patch("src.image_recovery.recover_images_for_dataframe", return_value=(df, [])) as m:
        recover_images_for_dataframe(df)

    kwargs = m.call_args.kwargs
    assert kwargs["enable_screenshot"] is True
    assert kwargs["pdf_lookup"] is None
    assert kwargs["session_id"] is None


def test_enrich_row_extracts_and_fills_image_url():
    """When page HTML contains a valid image, enrich_row fills Image URL on the row."""
    good_result = SearchResult(
        "Wolf MDD30TS Spec",
        "https://wolfappliance.com/mdd30ts",
        "Wolf MDD30TS specifications",
        90,
    )
    html_with_image = """
    <html>
      <head><meta property="og:image" content="https://wolfappliance.com/img/mdd30ts.jpg"></head>
      <body>Wolf MDD30TS warming drawer specifications.</body>
    </html>
    """
    extracted = {
        "Product Name": "Wolf Drawer Microwave",
        "Dimensions": "", "Finish / Color": "", "Product Category": "Appliance", "materials": "",
    }
    with patch("src.product_resolver.search_product_candidates", return_value=[good_result]), \
         patch("src.product_resolver.httpx.get", return_value=_resolver_response(good_result.url, html_with_image)), \
         patch("src.product_enrichment._extract_with_claude", return_value=extracted), \
         patch("src.product_enrichment._check_image_content_type", return_value=True):
        updated, error, _ = enrich_row(_qualifying_row())

    assert updated.get("Image URL") == "https://wolfappliance.com/img/mdd30ts.jpg"


def test_enrich_row_invalid_image_not_stored(monkeypatch, tmp_path):
    """Image URL that fails content-type check is rejected; Image URL stays empty."""
    from src.enrichment_cache import ProductEnrichmentCache
    import src.product_enrichment as pe
    cache = ProductEnrichmentCache()
    cache._path = str(tmp_path / "c.json")
    cache._data = {}
    monkeypatch.setattr(pe, "_product_cache", cache)

    good_result = SearchResult("Wolf MDD30TS Spec", "https://wolfappliance.com/mdd30ts", "Wolf MDD30TS", 90)
    html_with_image = """
    <html>
      <head><meta property="og:image" content="http://wolfappliance.com/product-page"></head>
      <body>Wolf MDD30TS warming drawer specifications.</body>
    </html>
    """
    extracted = {"Product Name": "", "Dimensions": "", "Finish / Color": "", "Product Category": "", "materials": ""}
    with patch("src.product_resolver.search_product_candidates", return_value=[good_result]), \
         patch("src.product_resolver.httpx.get", return_value=_resolver_response(good_result.url, html_with_image)), \
         patch("src.product_enrichment._extract_with_claude", return_value=extracted), \
         patch("src.product_enrichment._check_image_content_type", return_value=False):
        updated, _, _ = enrich_row(_qualifying_row())

    assert not updated.get("Image URL")


def test_enrich_row_image_written_to_cache(monkeypatch, tmp_path):
    """Valid image_url from page is stored in ProductEnrichmentCache."""
    from src.enrichment_cache import ProductEnrichmentCache, normalize_key
    import src.product_enrichment as pe
    cache = ProductEnrichmentCache()
    cache._path = str(tmp_path / "c.json")
    cache._data = {}
    monkeypatch.setattr(pe, "_product_cache", cache)

    good_result = SearchResult("Wolf MDD30TS Spec", "https://wolfappliance.com/mdd30ts", "Wolf MDD30TS", 90)
    html = """
    <html>
      <head><meta property="og:image" content="https://wolfappliance.com/img/spec.jpg"></head>
      <body>Wolf MDD30TS warming drawer specifications.</body>
    </html>
    """
    extracted = {"Product Name": "", "Dimensions": "", "Finish / Color": "", "Product Category": "", "materials": ""}
    with patch("src.product_resolver.search_product_candidates", return_value=[good_result]), \
         patch("src.product_resolver.httpx.get", return_value=_resolver_response(good_result.url, html)), \
         patch("src.product_enrichment._extract_with_claude", return_value=extracted), \
         patch("src.product_enrichment._check_image_content_type", return_value=True):
        enrich_row(_qualifying_row())

    key = normalize_key("Wolf", "MDD30TS")
    entry = cache.get(key)
    assert entry is not None
    assert entry.get("image_url") == "https://wolfappliance.com/img/spec.jpg"


def test_enrich_row_does_not_overwrite_cached_image(monkeypatch, tmp_path):
    """Existing valid image_url in cache is not overwritten by a new extraction."""
    from src.enrichment_cache import ProductEnrichmentCache, normalize_key
    import src.product_enrichment as pe
    cache = ProductEnrichmentCache()
    cache._path = str(tmp_path / "c.json")
    cache._data = {}
    key = normalize_key("Wolf", "MDD30TS")
    cache.update(key, {"image_url": "https://wolfappliance.com/img/original.jpg", "general_confidence": "medium"})
    monkeypatch.setattr(pe, "_product_cache", cache)

    good_result = SearchResult("Wolf MDD30TS Spec", "https://wolfappliance.com/mdd30ts", "Wolf MDD30TS", 90)
    html = """
    <html>
      <head><meta property="og:image" content="https://wolfappliance.com/img/new.jpg"></head>
      <body>Wolf MDD30TS warming drawer specifications.</body>
    </html>
    """
    extracted = {"Product Name": "", "Dimensions": "", "Finish / Color": "", "Product Category": "", "materials": ""}
    with patch("src.product_resolver.search_product_candidates", return_value=[good_result]), \
         patch("src.product_resolver.httpx.get", return_value=_resolver_response(good_result.url, html)), \
         patch("src.product_enrichment._extract_with_claude", return_value=extracted), \
         patch("src.product_enrichment._check_image_content_type", return_value=True):
        enrich_row(_qualifying_row())

    assert cache.get(key)["image_url"] == "https://wolfappliance.com/img/original.jpg"


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
        updated, error, _ = enrich_row(_qualifying_row())
    assert error is None
    assert "[Enrichment: no confident source found]" in updated["Notes"]
    assert updated["Product Name"] == ""


def test_enrich_row_low_score_result_leaves_note():
    low_result = SearchResult("title", "https://amazon.com/dp/B001", "desc", 10)
    with patch("src.product_enrichment.search_product_candidates", return_value=[low_result]):
        updated, error, _ = enrich_row(_qualifying_row())
    assert "[Enrichment: no confident source found]" in updated["Notes"]


def test_enrich_row_fetch_failure_leaves_note():
    good_result = SearchResult("Wolf Spec", "https://wolfappliance.com", "desc", 90)
    with patch("src.product_resolver.search_product_candidates", return_value=[good_result]), \
         patch("src.product_resolver.httpx.get", side_effect=RuntimeError("fetch failed")):
        updated, error, _ = enrich_row(_qualifying_row())
    assert "[Enrichment: no confident source found]" in updated["Notes"]


def test_enrich_row_fills_fields_on_success():
    good_result = SearchResult("Wolf MDD30TS Spec", "https://wolfappliance.com/mdd30ts", "Wolf MDD30TS", 90)
    extracted = {
        "Product Name": "Wolf 30\" Drawer Microwave",
        "Dimensions": '29 7/8"W x 23 1/2"D x 11 7/8"H',
        "Finish / Color": "",
        "Product Category": "Appliance",
        "materials": "",
    }
    with patch("src.product_resolver.search_product_candidates", return_value=[good_result]), \
         patch("src.product_resolver.httpx.get", return_value=_resolver_response(good_result.url, "Wolf MDD30TS official page content")), \
         patch("src.product_enrichment._extract_with_claude", return_value=extracted):
        updated, error, _ = enrich_row(_qualifying_row())

    assert error is None
    assert updated["Product Name"] == "Wolf 30\" Drawer Microwave"
    assert updated["Product URL"] == "https://wolfappliance.com/mdd30ts"
    assert updated["Dimensions"] == '29 7/8"W x 23 1/2"D x 11 7/8"H'


def test_enrich_row_web_enrichment_disabled_skips_search_domain_and_dimension_lookup():
    with patch("src.product_enrichment.search_product_candidates") as mock_search, \
         patch("src.product_enrichment.get_domain_for_brand") as mock_domain, \
         patch("src.product_enrichment._find_dimensions") as mock_dimensions:
        updated, error, dim_result = enrich_row(_qualifying_row(), use_web_enrichment=False)

    assert error is None
    assert dim_result is None
    assert updated == _qualifying_row()
    mock_search.assert_not_called()
    mock_domain.assert_not_called()
    mock_dimensions.assert_not_called()


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
        updated_df, errors, diagnostics = enrich_dataframe(df)
    mock_search.assert_not_called()
    assert errors == []


def test_enrich_dataframe_isolates_exceptions():
    rows = [
        {**_qualifying_row(), "Brand": "Wolf"},
        {**_qualifying_row(), "Brand": "Miele", "Model/SKU": "CVA7440"},
    ]
    df = pd.DataFrame(rows)

    def bad_enrich_row(row, enrichment_mode="standard", session_cache=None):
        if row["Brand"] == "Wolf":
            raise RuntimeError("network error")
        return row, None, None

    with patch("src.product_enrichment.enrich_row", side_effect=bad_enrich_row), \
         patch("src.product_enrichment.time.sleep"):
        updated_df, errors, diagnostics = enrich_dataframe(df)

    assert len(errors) == 1
    assert "Wolf" in errors[0]


def test_enrich_dataframe_web_enrichment_disabled_skips_cache_and_search(monkeypatch):
    import src.product_enrichment as pe

    df = pd.DataFrame([_qualifying_row()])
    monkeypatch.setattr(pe._product_cache, "get", lambda key: (_ for _ in ()).throw(AssertionError("cache read")))
    monkeypatch.setattr(pe._product_cache, "update", lambda key, fields: (_ for _ in ()).throw(AssertionError("cache write")))

    with patch("src.product_enrichment.search_product_candidates") as mock_search, \
         patch("src.product_enrichment.get_domain_for_brand") as mock_domain, \
         patch("src.product_enrichment._find_dimensions") as mock_dimensions:
        updated_df, errors, diagnostics = enrich_dataframe(df, use_web_enrichment=False)

    assert errors == []
    assert diagnostics == []
    assert updated_df.to_dict("records") == df.to_dict("records")
    mock_search.assert_not_called()
    mock_domain.assert_not_called()
    mock_dimensions.assert_not_called()


# ── has_complete_3d_dimensions ─────────────────────────────────────────────────

from src.product_enrichment import has_complete_3d_dimensions


def test_3d_complete_standard_format():
    assert has_complete_3d_dimensions('36"W x 34.5"H x 24"D') is True


def test_3d_complete_unicode_times():
    assert has_complete_3d_dimensions('36"W × 34.5"H × 24"D') is True


def test_3d_complete_space_before_letter():
    assert has_complete_3d_dimensions('29 7/8" W × 23 1/2" D × 11 7/8" H') is True


def test_3d_complete_full_words():
    assert has_complete_3d_dimensions('Width: 30", Height: 84", Depth: 24"') is True


def test_3d_incomplete_one_dim():
    assert has_complete_3d_dimensions('36 inch') is False


def test_3d_incomplete_one_label():
    assert has_complete_3d_dimensions('30"W') is False


def test_3d_incomplete_two_dims():
    assert has_complete_3d_dimensions('36"W x 34.5"H') is False


def test_3d_incomplete_missing_height():
    assert has_complete_3d_dimensions('36"W x 24"D') is False


def test_3d_empty_string():
    assert has_complete_3d_dimensions('') is False


def test_3d_none_safe():
    # The function must handle any falsy input without raising
    assert has_complete_3d_dimensions(None) is False  # type: ignore[arg-type]


# ── _qualifies with incomplete dimensions ──────────────────────────────────────

def test_qualifies_partial_dimension_qualifies():
    """Row with a partial dimension string (not full 3D) should qualify."""
    row = {
        **_base_qualifying_row(),
        "Product Name": "Fridge Drawers",
        "Dimensions": "36 inch",           # partial — not 3D
        "Finish / Color": "Stainless",
        "Product Category": "Appliance",
        "Product URL": "https://example.com",
    }
    assert _qualifies(row)


def test_qualifies_complete_3d_dimension_does_not_qualify():
    """Row with full W×H×D dimensions should NOT qualify (nothing to enrich)."""
    row = {
        **_base_qualifying_row(),
        "Product Name": "Fridge",
        "Dimensions": '36"W x 84"H x 24"D',
        "Finish / Color": "Stainless",
        "Product Category": "Appliance",
        "Product URL": "https://example.com",
    }
    assert not _qualifies(row)


# ── _build_search_query with dimension intent ──────────────────────────────────

def test_build_query_blank_dimensions_adds_dim_terms():
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "", "Dimensions": ""}
    query = _build_search_query(row)
    assert "dimensions" in query.lower()
    assert "width" in query.lower()
    assert "depth" in query.lower()


def test_build_query_partial_dimensions_adds_dim_terms():
    row = {"Brand": "Sub-Zero", "Model/SKU": "ID36R", "Product Name": "", "Dimensions": "36 inch"}
    query = _build_search_query(row)
    assert "dimensions" in query.lower()


def test_build_query_complete_3d_dimensions_uses_general_terms():
    """When dimensions are already complete 3D, use the general suffix, not dim-specific."""
    row = {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "Microwave",
        "Dimensions": '30"W x 15"H x 17"D',
    }
    query = _build_search_query(row)
    assert "specifications" in query.lower() or "official" in query.lower()
    assert "width height depth" not in query.lower()


# ── _apply_enrichment with 3D dimensions ──────────────────────────────────────

def test_apply_enrichment_fills_complete_3d_dims():
    """Complete 3D extracted → fill Dimensions."""
    row = _base_row_for_apply()
    extracted = {
        "Product Name": "",
        "Dimensions": '36"W x 84"H x 24"D',
        "Finish / Color": "",
        "Product Category": "",
        "materials": "",
    }
    updated = _apply_enrichment(row, extracted, "https://example.com", 85)
    assert updated["Dimensions"] == '36"W x 84"H x 24"D'


def test_apply_enrichment_overwrites_partial_with_complete_3d():
    """Complete 3D extracted → overwrite even if row already had partial dims."""
    row = {**_base_row_for_apply(), "Dimensions": "36 inch"}
    extracted = {
        "Product Name": "",
        "Dimensions": '36"W x 84"H x 24"D',
        "Finish / Color": "",
        "Product Category": "",
        "materials": "",
    }
    updated = _apply_enrichment(row, extracted, "https://example.com", 85)
    assert updated["Dimensions"] == '36"W x 84"H x 24"D'


def test_apply_enrichment_partial_extracted_adds_note():
    """Partial extracted → no fill, note appended to Notes."""
    row = _base_row_for_apply()
    extracted = {
        "Product Name": "",
        "Dimensions": "36 inch",   # partial
        "Finish / Color": "",
        "Product Category": "",
        "materials": "",
    }
    updated = _apply_enrichment(row, extracted, "https://example.com", 85)
    assert updated["Dimensions"] == ""   # not filled
    assert "[Partial dimension found:" in updated["Notes"]
    assert "36 inch" in updated["Notes"]
    assert "full W x H x D still needed" in updated["Notes"]


def test_apply_enrichment_partial_note_not_duplicated():
    """Partial dim note is not appended twice if already present."""
    row = _base_row_for_apply()
    row["Notes"] = "[Partial dimension found: 36 inch; full W x H x D still needed]"
    extracted = {"Product Name": "", "Dimensions": "36 inch", "Finish / Color": "", "Product Category": "", "materials": ""}
    updated = _apply_enrichment(row, extracted, "https://example.com", 85)
    assert updated["Notes"].count("[Partial dimension found:") == 1


def test_apply_enrichment_no_dim_extracted_blank_row_unchanged():
    """No dimensions extracted and row has blank dims → Dimensions stays blank."""
    row = _base_row_for_apply()
    extracted = {"Product Name": "", "Dimensions": "", "Finish / Color": "", "Product Category": "", "materials": ""}
    updated = _apply_enrichment(row, extracted, "https://example.com", 85)
    assert updated["Dimensions"] == ""


# ── _build_extraction_prompt with dimensions ───────────────────────────────────

from src.product_enrichment import _build_extraction_prompt


def test_extraction_prompt_requests_3d_when_dims_blank():
    """When Dimensions is blank, prompt must ask for W, H, D explicitly."""
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "Microwave",
           "Dimensions": "", "Finish / Color": "", "Product Category": ""}
    prompt = _build_extraction_prompt("page text", row)
    assert "width" in prompt.lower()
    assert "height" in prompt.lower()
    assert "depth" in prompt.lower()


def test_extraction_prompt_requests_3d_when_dims_partial():
    """When Dimensions is partial, prompt must still ask for full 3D."""
    row = {"Brand": "Sub-Zero", "Model/SKU": "ID36R", "Product Name": "Fridge",
           "Dimensions": "36 inch", "Finish / Color": "", "Product Category": ""}
    prompt = _build_extraction_prompt("page text", row)
    assert "width" in prompt.lower()
    assert "depth" in prompt.lower()


def test_extraction_prompt_no_dim_request_when_3d_complete():
    """When 3D dimensions are already complete, prompt should NOT ask for dimensions."""
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "",
           "Dimensions": '36"W x 84"H x 24"D', "Finish / Color": "", "Product Category": ""}
    prompt = _build_extraction_prompt("page text", row)
    # "Dimensions" must not be in the blank-fields list in the prompt
    assert '"Dimensions"' not in prompt or "already complete" in prompt.lower()


# ── has_complete_3d_dimensions — extended cases ────────────────────────────────

def test_3d_unlabeled_triple_fails():
    """Three bare numbers are not enough unless W, H, and D are explicit."""
    assert has_complete_3d_dimensions("36 x 34.5 x 24") is False


def test_3d_colon_labeled_passes():
    """W:36 H:34.5 D:24 format must pass."""
    assert has_complete_3d_dimensions("W:36 H:34.5 D:24") is True


def test_3d_mixed_fraction_triple_passes():
    """Mixed fractions with labels must pass."""
    assert has_complete_3d_dimensions('29 7/8" W × 23 1/2" D × 11 7/8" H') is True


def test_3d_single_number_word_fails():
    """'36 inch fridge' has only one numeric value — must fail."""
    assert has_complete_3d_dimensions("36 inch fridge") is False


def test_3d_single_number_phrase_fails():
    """'42 built in' has only one numeric value — must fail."""
    assert has_complete_3d_dimensions("42 built in") is False


def test_3d_warming_drawer_phrase_fails():
    """'30 warming drawer' has only one numeric value — must fail."""
    assert has_complete_3d_dimensions("30 warming drawer") is False


def test_3d_two_numbers_with_not_fails():
    """'24 not 36' has two numeric values — must fail."""
    assert has_complete_3d_dimensions("24 not 36") is False


def test_3d_two_number_unlabeled_fails():
    """'36 x 24' is only two dimensions — must fail."""
    assert has_complete_3d_dimensions("36 x 24") is False


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
    assert updated["Source Type"] == "PDF_Enriched"


def test_enrich_dataframe_creates_session_cache_once(monkeypatch):
    """enrich_dataframe creates one SessionCache and passes it to all rows."""
    import pandas as pd
    from src.product_enrichment import enrich_dataframe
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


# ── delegation: product_enrichment → image_recovery ───────────────────────────

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


# ── official brand/SKU lookup enhancement ─────────────────────────────────────

def _official_html() -> str:
    return """
    <html>
      <head>
        <meta property="og:image" content="https://www.visualcomfort.com/images/tob-1234.jpg">
        <script type="application/ld+json">
        {
          "@type": "Product",
          "name": "Visual Comfort Table Lamp",
          "brand": {"name": "Visual Comfort"},
          "sku": "TOB 1234",
          "category": "Lighting",
          "color": "Bronze",
          "description": "A table lamp for living spaces.",
          "additionalProperty": [
            {"name": "Dimensions", "value": "Width: 36 in, Depth: 18 in, Height: 72 in"},
            {"name": "Finish", "value": "Bronze"},
            {"name": "Material", "value": "Steel"}
          ]
        }
        </script>
      </head>
      <body>
        <h1>Visual Comfort TOB 1234 Table Lamp</h1>
        <table><tr><th>Dimensions</th><td>Width: 36 in, Depth: 18 in, Height: 72 in</td></tr></table>
      </body>
    </html>
    """


def test_enrich_row_uses_official_registry_page_for_specs_and_image(monkeypatch):
    from src.brave_search import SearchResult
    import src.product_enrichment as pe

    def fake_search(query, brand="", session_cache=None):
        return [SearchResult(
            title="Visual Comfort TOB 1234 Table Lamp",
            url="https://www.visualcomfort.com/products/tob-1234",
            description="TOB 1234 dimensions",
            domain_score=80,
        )]

    monkeypatch.setattr("src.product_resolver.search_product_candidates", fake_search)
    monkeypatch.setattr("src.product_resolver.httpx.get", lambda url, **kwargs: _resolver_response(url, _official_html()))

    row = {
        "Source Type": "PDF",
        "Brand": "Visual Comfort",
        "Model/SKU": "TOB 1234",
        "Product Name": "",
        "Dimensions": "",
        "Finish / Color": "",
        "Product Category": "Lighting",
        "Product URL": "",
        "Notes": "",
    }
    updated, err, dim_result = enrich_row(row)
    assert err is None
    assert updated["Product URL"] == "https://www.visualcomfort.com/products/tob-1234"
    assert updated["Brand"] == "Visual Comfort"
    assert updated["Product Name"] == "Visual Comfort Table Lamp"
    assert updated["Model/SKU"] == "TOB 1234"
    assert updated["Dimensions"] == '36"W x 18"D x 72"H'
    assert updated["Width (in)"] == "36"
    assert updated["Depth (in)"] == "18"
    assert updated["Height (in)"] == "72"
    assert updated["Image URL"] == "https://www.visualcomfort.com/images/tob-1234.jpg"
    assert updated["image_source"] == "official_site_og_image"
    assert updated["Color"] == "Bronze"
    assert updated["Material"] == "Steel"
    assert updated["Description"] == "A table lamp for living spaces."
    assert "[Manufacturer Description: A table lamp for living spaces.]" in updated["Notes"]
    assert updated["manufacturer_page_exact_sku"] is True
    assert updated["brand_registry_match"] is True
    assert dim_result is not None
    assert dim_result.confidence == "high"


def test_manual_uploaded_image_is_not_overwritten_by_official_lookup(monkeypatch):
    from src.brave_search import SearchResult
    import src.product_enrichment as pe

    monkeypatch.setattr("src.product_resolver.search_product_candidates", lambda *a, **k: [SearchResult(
        title="Visual Comfort TOB 1234 Table Lamp",
        url="https://www.visualcomfort.com/products/tob-1234",
        description="TOB 1234 dimensions",
        domain_score=80,
    )])
    monkeypatch.setattr("src.product_resolver.httpx.get", lambda url, **kwargs: _resolver_response(url, _official_html()))

    row = {
        "Source Type": "PDF",
        "Brand": "Visual Comfort",
        "Model/SKU": "TOB 1234",
        "Product Name": "Table Lamp",
        "Dimensions": "",
        "Product Category": "Lighting",
        "Image URL": "https://res.cloudinary.com/demo/image/upload/manual.jpg",
        "image_source": "manual_upload",
        "confidence": "HIGH",
    }
    updated, err, _ = enrich_row(row)
    assert err is None
    assert updated["Image URL"] == "https://res.cloudinary.com/demo/image/upload/manual.jpg"
    assert updated["image_source"] == "manual_upload"


def test_verified_page_preserves_existing_populated_fields(monkeypatch):
    from src.brave_search import SearchResult
    import src.product_enrichment as pe

    monkeypatch.setattr("src.product_resolver.search_product_candidates", lambda *a, **k: [SearchResult(
        title="Visual Comfort TOB 1234 Table Lamp",
        url="https://www.visualcomfort.com/products/tob-1234",
        description="TOB 1234 dimensions",
        domain_score=80,
    )])
    monkeypatch.setattr("src.product_resolver.httpx.get", lambda url, **kwargs: _resolver_response(url, _official_html()))

    row = {
        "Source Type": "PDF",
        "Brand": "Visual Comfort",
        "Model/SKU": "TOB 1234",
        "Product Name": "PDF Product Name",
        "Dimensions": '10"W x 20"H x 30"D',
        "Finish / Color": "PDF Bronze",
        "Material": "PDF Metal",
        "Product Category": "Lighting",
        "Product URL": "",
        "Notes": "",
    }

    updated, err, _ = enrich_row(row)

    assert err is None
    assert updated["Product URL"] == "https://www.visualcomfort.com/products/tob-1234"
    assert updated["Product Name"] == "PDF Product Name"
    assert updated["Dimensions"] == '10"W x 20"H x 30"D'
    assert updated["Finish / Color"] == "PDF Bronze"
    assert updated["Material"] == "PDF Metal"


def test_low_confidence_page_image_is_diagnostic_only(monkeypatch):
    from src.brave_search import SearchResult
    from src.product_page_images import ProductPageImageResult
    import src.product_enrichment as pe

    monkeypatch.setattr("src.product_resolver.search_product_candidates", lambda *a, **k: [SearchResult(
        title="Visual Comfort TOB 1234 Table Lamp",
        url="https://www.visualcomfort.com/products/tob-1234",
        description="TOB 1234 dimensions",
        domain_score=80,
    )])
    monkeypatch.setattr("src.product_resolver.httpx.get", lambda url, **kwargs: _resolver_response(url, _official_html()))
    monkeypatch.setattr(pe, "extract_product_page_image", lambda *a, **k: ProductPageImageResult(
        image_found=True,
        image_url="https://www.visualcomfort.com/images/low-confidence.jpg",
        image_source="official_site_html_image",
        confidence="LOW",
        evidence=["html_image"],
        debug={"images_found": 1},
    ))

    row = {
        "Source Type": "PDF",
        "Brand": "Visual Comfort",
        "Model/SKU": "TOB 1234",
        "Product Name": "",
        "Dimensions": "",
        "Product Category": "Lighting",
        "Image URL": "",
        "Product URL": "",
        "Notes": "",
    }

    updated, err, _ = enrich_row(row)

    assert err is None
    assert updated["Product URL"] == "https://www.visualcomfort.com/products/tob-1234"
    assert updated["selected_image_url"] == "https://www.visualcomfort.com/images/low-confidence.jpg"
    assert updated["Image URL"] == ""


def test_existing_medium_pdf_image_not_overwritten_by_low_web_image(monkeypatch):
    import src.product_enrichment as pe

    monkeypatch.setattr("src.product_resolver.search_product_candidates", lambda *a, **k: [])
    monkeypatch.setattr("src.product_resolver.httpx.get", lambda url, **kwargs: _resolver_response(url, _official_html()))

    row = {
        "Source Type": "PDF",
        "Brand": "Unknown Brand",
        "Model/SKU": "ABC-1",
        "Product Name": "Lamp",
        "Dimensions": "",
        "Product Category": "Lighting",
        "Image Filename": "unknown_abc_1.jpg",
        "local_image_path": "/tmp/session/images/unknown_abc_1.jpg",
        "image_source": "pdf_page_render_content_crop",
        "confidence": "MEDIUM",
    }
    updated, err, _ = enrich_row(row)
    assert err is None
    assert updated["Image Filename"] == "unknown_abc_1.jpg"
    assert updated["image_source"] == "pdf_page_render_content_crop"


def test_existing_high_dimensions_not_overwritten_by_medium_page(monkeypatch):
    from src.brave_search import SearchResult
    import src.product_enrichment as pe

    html = _official_html().replace("TOB 1234", "Table Lamp")
    monkeypatch.setattr("src.product_resolver.search_product_candidates", lambda *a, **k: [SearchResult(
        title="Visual Comfort Table Lamp",
        url="https://www.visualcomfort.com/products/table-lamp",
        description="Table Lamp dimensions",
        domain_score=80,
    )])
    monkeypatch.setattr("src.product_resolver.httpx.get", lambda url, **kwargs: _resolver_response(url, html))

    row = {
        "Source Type": "PDF",
        "Brand": "Visual Comfort",
        "Model/SKU": "TOB 1234",
        "Product Name": "Table Lamp",
        "Dimensions": '10"W x 20"H x 30"D',
        "Dimension Confidence": "high",
        "Finish / Color": "",
        "Product Category": "Lighting",
        "Product URL": "",
    }
    updated, err, _ = enrich_row(row)
    assert err is None
    assert updated["Dimensions"] == '10"W x 20"H x 30"D'


def test_manufacturer_page_does_not_overwrite_pdf_values_without_exact_sku(monkeypatch):
    from src.brave_search import SearchResult
    import src.product_enrichment as pe

    html = (
        _official_html()
        .replace("TOB 1234", "OTHER 999")
        .replace("tob-1234", "other-999")
        .replace("Visual Comfort Table Lamp", "Manufacturer Name")
    )
    monkeypatch.setattr("src.product_resolver.search_product_candidates", lambda *a, **k: [SearchResult(
        title="Visual Comfort Table Lamp",
        url="https://www.visualcomfort.com/products/table-lamp",
        description="dimensions",
        domain_score=80,
    )])
    monkeypatch.setattr("src.product_resolver.httpx.get", lambda url, **kwargs: _resolver_response(url, html))

    row = {
        "Source Type": "PDF",
        "Brand": "Visual Comfort",
        "Model/SKU": "TOB 1234",
        "Product Name": "PDF Extracted Name",
        "Dimensions": "",
        "Finish / Color": "PDF Bronze",
        "Product Category": "Lighting",
        "Product URL": "",
        "Notes": "",
    }
    updated, err, _ = enrich_row(row)

    assert err is None
    assert updated["Product Name"] == "PDF Extracted Name"
    assert updated["Model/SKU"] == "TOB 1234"
    assert updated["Finish / Color"] == "PDF Bronze"
    assert updated["manufacturer_page_exact_sku"] is False
