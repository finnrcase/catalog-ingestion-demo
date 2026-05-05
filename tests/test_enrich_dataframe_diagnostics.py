import pytest
from unittest.mock import patch
import pandas as pd
from src.product_enrichment import enrich_dataframe
from src.dimension_enrichment import DimensionResult


@pytest.fixture(autouse=True)
def _reset_product_cache(monkeypatch):
    """Isolate each test from the module-level ProductEnrichmentCache singleton."""
    from src.enrichment_cache import ProductEnrichmentCache
    import src.product_enrichment as pe
    fresh_cache = ProductEnrichmentCache()
    fresh_cache._data = {}  # empty in-memory only, no disk I/O
    monkeypatch.setattr(pe, "_product_cache", fresh_cache)


def _make_df(dims="", brand="Kohler", model="K-3999") -> pd.DataFrame:
    return pd.DataFrame([{
        "Brand": brand,
        "Model/SKU": model,
        "Product Name": "Test Product",
        "Product Category": "Plumbing",
        "Dimensions": dims,
        "Source Type": "Manual",
        "Notes": "",
        "Include": True,
        "Status": "New",
        "Quantity": "1",
        "Price": "",
        "Supplier": "",
        "Product URL": "",
        "Finish / Color": "",
        "Color": "",
        "Material": "",
        "Room": "",
        "Project": "",
        "Import Type": "",
        "photo_only": False,
        "AI Category Confidence": "",
        "Category Source": "",
        "Image URL": "",
        "Local Image Path": "",
        "Image Filename": "",
        "Image Upload Status": "",
        "Dimension Source URL": "",
        "Dimension Confidence": "",
        "Dimension Source Type": "",
        "Dimension Lookup Status": "",
        "Width (in)": "",
        "Height (in)": "",
        "Depth (in)": "",
        "Length (in)": "",
    }])


def test_enrich_dataframe_returns_three_values():
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        result = enrich_dataframe(_make_df())
    assert len(result) == 3
    df, errors, diagnostics = result
    assert isinstance(diagnostics, list)


def test_enrich_dataframe_diagnostics_populated_when_lookup_ran():
    mock_result = DimensionResult(
        dimensions='28"W x 30"H x 17"D',
        width="28", height="30", depth="17",
        source_url="https://kohler.com/k-3999",
        confidence="high",
        source_type="manufacturer_page",
        status="found",
    )
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        with patch("src.product_enrichment._find_dimensions", return_value=mock_result):
            df, errors, diagnostics = enrich_dataframe(_make_df())

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d["row_index"] == 0
    assert d["product_name"] == "Test Product"
    assert d["model_searched"] == "K-3999"
    assert d["status"] == "found"
    assert d["confidence"] == "high"
    assert d["source_url"] == "https://kohler.com/k-3999"
    assert d["failure_reason"] == ""  # found results have no failure reason
    assert isinstance(d["queries_tried"], list)
    assert isinstance(d["urls_checked"], list)
    assert "evidence_text" in d
    assert d["domain_used"] == "kohler.com"


def test_enrich_dataframe_no_diagnostics_when_lookup_not_triggered():
    # Complete dims — lookup never runs
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        df, errors, diagnostics = enrich_dataframe(_make_df(dims='28"W x 30"H x 17"D'))

    assert diagnostics == []
    assert errors == []


def test_enrich_dataframe_diagnostics_failure_reason_on_not_found():
    mock_result = DimensionResult(
        status="not_found",
        confidence="none",
        failure_reason="no dimensions found after 5 queries and 3 URLs checked",
        queries_tried=["query1"],
        urls_checked=["https://example.com"],
    )
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        with patch("src.product_enrichment._find_dimensions", return_value=mock_result):
            df, errors, diagnostics = enrich_dataframe(_make_df())

    assert len(diagnostics) == 1
    assert diagnostics[0]["status"] == "not_found"
    assert diagnostics[0]["failure_reason"] == "no dimensions found after 5 queries and 3 URLs checked"
    assert diagnostics[0]["queries_tried"] == ["query1"]
    assert diagnostics[0]["urls_checked"] == ["https://example.com"]
    assert diagnostics[0]["domain_used"] == ""  # no source URL for not_found
