from unittest.mock import patch, MagicMock
from src.product_enrichment import enrich_row
from src.dimension_enrichment import DimensionResult


def _row_missing_dims() -> dict:
    return {
        "Brand": "Kohler",
        "Model/SKU": "K-3999",
        "Product Name": "Highline Toilet",
        "Product Category": "Plumbing",
        "Dimensions": "",
        "Source Type": "Manual",
        "Notes": "",
        "Include": True,
    }


def test_enrich_row_calls_find_dimensions_when_dims_missing():
    mock_result = DimensionResult(
        dimensions='28"W x 30"H x 17"D',
        width="28",
        height="30",
        depth="17",
        source_url="https://kohler.com/k-3999",
        confidence="high",
        source_type="manufacturer_page",
        status="found",
    )
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        with patch("src.product_enrichment._find_dimensions", return_value=mock_result):
            updated, error = enrich_row(_row_missing_dims())

    assert error is None
    assert updated["Dimensions"] == '28"W x 30"H x 17"D'
    assert updated["Dimension Source URL"] == "https://kohler.com/k-3999"
    assert updated["Dimension Confidence"] == "high"
    assert updated["Dimension Source Type"] == "manufacturer_page"
    assert updated["Dimension Lookup Status"] == "found"


def test_enrich_row_skips_dimension_pass_when_dims_already_complete():
    row = _row_missing_dims()
    row["Dimensions"] = '28"W x 30"H x 17"D'

    find_dims_called = []
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        with patch(
            "src.product_enrichment._find_dimensions",
            side_effect=lambda r: find_dims_called.append(r) or DimensionResult(),
        ):
            updated, error = enrich_row(row)

    assert find_dims_called == []


def test_enrich_row_not_found_sets_status_not_found():
    mock_result = DimensionResult(
        status="not_found",
        confidence="none",
        failure_reason="no results",
    )
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        with patch("src.product_enrichment._find_dimensions", return_value=mock_result):
            updated, error = enrich_row(_row_missing_dims())

    assert updated.get("Dimension Lookup Status") == "not_found"
    assert updated.get("Dimensions", "") == ""


def test_enrich_row_appliance_appends_cutout_to_notes():
    mock_result = DimensionResult(
        dimensions='23.875"W x 33.375"H x 22"D',
        width="23.875", height="33.375", depth="22",
        confidence="high",
        source_type="manufacturer_page",
        status="found",
        evidence_text='23.875"W x 33.375"H x 22"D | Cutout: 23"W x 33"H x 21"D',
    )
    row = _row_missing_dims()
    row["Product Category"] = "Appliances"

    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        with patch("src.product_enrichment._find_dimensions", return_value=mock_result):
            updated, error = enrich_row(row)

    assert updated["Dimensions"] == '23.875"W x 33.375"H x 22"D'
    assert "[Cutout Dimensions:" in updated.get("Notes", "")


def test_enrich_row_does_not_overwrite_existing_complete_dims():
    row = _row_missing_dims()
    row["Dimensions"] = '28"W x 30"H x 17"D'
    original_dims = row["Dimensions"]

    mock_result = DimensionResult(
        dimensions='99"W x 99"H x 99"D',
        confidence="high",
        status="found",
    )
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        with patch("src.product_enrichment._find_dimensions", return_value=mock_result):
            updated, error = enrich_row(row)

    assert updated["Dimensions"] == original_dims


def test_enrich_row_low_confidence_does_not_write_dimensions():
    mock_result = DimensionResult(
        dimensions='28"W x 30"H x 17"D',  # found but low confidence
        width="28",
        height="30",
        depth="17",
        source_url="https://example.com/k-3999",
        confidence="low",
        source_type="retailer_page",
        status="low_confidence_skipped",
    )
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        with patch("src.product_enrichment._find_dimensions", return_value=mock_result):
            updated, error = enrich_row(_row_missing_dims())

    assert error is None
    assert updated.get("Dimensions", "") == ""
    assert updated["Dimension Lookup Status"] == "low_confidence_skipped"
    assert updated["Dimension Confidence"] == "low"
    assert updated["Dimension Source URL"] == "https://example.com/k-3999"


def test_enrich_row_skips_dimension_pass_when_brand_missing():
    row = _row_missing_dims()
    row["Brand"] = ""

    find_dims_called = []
    with patch("src.product_enrichment.search_product_candidates", return_value=[]):
        with patch(
            "src.product_enrichment._find_dimensions",
            side_effect=lambda r: find_dims_called.append(r) or DimensionResult(),
        ):
            updated, error = enrich_row(row)

    assert find_dims_called == []
    assert updated.get("Dimension Lookup Status", "") == ""
