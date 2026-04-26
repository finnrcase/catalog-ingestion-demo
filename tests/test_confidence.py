import pytest
from src.confidence import _suggested_action


def _pdf_ai_row(**overrides):
    base = {
        "Source Type": "PDF_AI",
        "Product Name": "Wolf Microwave",
        "Brand": "Wolf",
        "Dimensions": "",
        "Room": "Kitchen",
        "Quantity": 1,
        "Supplier": "AEG",
        "Product Category": "Appliance",
        "Model/SKU": "MDD30TS",
        "Product URL": "",
        "Price": "$1,200",
    }
    base.update(overrides)
    return base


def test_pdf_ai_blank_dims_gets_3d_verify_note():
    """PDF_AI row with blank Dimensions → 'Verify full W×H×D' note."""
    action = _suggested_action(_pdf_ai_row(), missing=[])
    assert "W" in action and "H" in action and "D" in action
    assert "spec sheet" in action.lower()


def test_pdf_ai_partial_dims_gets_3d_verify_note():
    """PDF_AI row with partial dims ('36 inch') → 'Verify full W×H×D' note."""
    action = _suggested_action(_pdf_ai_row(Dimensions="36 inch"), missing=[])
    assert "W" in action and "H" in action and "D" in action
    assert "spec sheet" in action.lower()


def test_pdf_ai_complete_3d_dims_no_verify_note():
    """PDF_AI row with full 3D dims → no dimension verification note."""
    action = _suggested_action(_pdf_ai_row(**{"Dimensions": '36"W x 84"H x 24"D'}), missing=[])
    assert "spec sheet" not in action.lower()


def test_non_pdf_ai_blank_dims_no_verify_note():
    """Non-PDF_AI rows never get the dimension verification note."""
    for source in ("PDF", "Manual", "URL"):
        row = {**_pdf_ai_row(), "Source Type": source}
        action = _suggested_action(row, missing=[])
        assert "spec sheet" not in action.lower(), f"Source '{source}' should not get dim note"


def test_dim_note_not_duplicated():
    """The note appears exactly once even if _suggested_action is called twice."""
    action = _suggested_action(_pdf_ai_row(), missing=[])
    assert action.lower().count("spec sheet") == 1


def test_missing_fields_and_blank_dims_both_surfaced():
    """Missing fields AND blank dims → both appear in the action string."""
    action = _suggested_action(_pdf_ai_row(), missing=["Room"])
    assert "Room" in action
    assert "spec sheet" in action.lower()
