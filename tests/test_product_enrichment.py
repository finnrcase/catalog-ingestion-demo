import pytest
import pandas as pd
from src.product_enrichment import (
    _qualifies,
    _build_search_query,
    _apply_enrichment,
    enrich_row,
    enrich_dataframe,
)


@pytest.fixture(autouse=True)
def _isolate_manufacturer_domain_cache(monkeypatch):
    monkeypatch.setattr("src.product_enrichment.get_domain_for_brand", lambda brand: None)
    monkeypatch.setattr("src.product_enrichment.record_discovered_domain", lambda brand, domain: None)


@pytest.fixture(autouse=True)
def _reset_product_cache(monkeypatch):
    """Isolate each test from the module-level ProductEnrichmentCache singleton."""
    from src.enrichment_cache import ProductEnrichmentCache
    import src.product_enrichment as pe
    fresh_cache = ProductEnrichmentCache()
    fresh_cache._data = {}  # empty in-memory only, no disk I/O
    monkeypatch.setattr(pe, "_product_cache", fresh_cache)


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
    with patch("src.product_enrichment.search_product_candidates", return_value=[good_result]), \
         patch("src.product_enrichment._fetch_page_text", return_value=""):
        updated, error, _ = enrich_row(_qualifying_row())
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
        updated, error, _ = enrich_row(_qualifying_row())

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
