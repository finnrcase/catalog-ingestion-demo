import pytest
import pandas as pd
from src.product_enrichment import (
    _qualifies,
    _build_search_query,
    _apply_enrichment,
    enrich_row,
    enrich_dataframe,
)


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
        "Dimensions": '30"W',
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
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "Drawer Microwave"}
    assert _build_search_query(row) == "Wolf MDD30TS Drawer Microwave specifications official"


def test_build_search_query_no_product_name():
    row = {"Brand": "Sub-Zero", "Model/SKU": "ID36R", "Product Name": ""}
    assert _build_search_query(row) == "Sub-Zero ID36R specifications official"


def test_build_search_query_strips_whitespace():
    row = {"Brand": "  Miele  ", "Model/SKU": " CVA7440 ", "Product Name": ""}
    result = _build_search_query(row)
    assert result == "Miele CVA7440 specifications official"


def test_build_search_query_none_fields():
    row = {"Brand": "Wolf", "Model/SKU": None, "Product Name": None}
    assert _build_search_query(row) == "Wolf specifications official"


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
        "Dimensions": "29 7/8\" W × 23 1/2\" D",
        "Finish / Color": "",
        "Product Category": "Appliance",
        "materials": "",
    }
    updated = _apply_enrichment(row, extracted, "https://wolfappliance.com", 85)
    assert updated["Product Name"] == "Wolf 30\" Drawer Microwave"
    assert updated["Dimensions"] == "29 7/8\" W × 23 1/2\" D"
    assert updated["Product Category"] == "Appliance"
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
        "Product Category": "couch",  # alias → Sofa
        "materials": "",
    }
    updated = _apply_enrichment(row, extracted, "https://example.com", 70)
    assert updated["Product Category"] == "Sofa"


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
