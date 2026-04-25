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


# ── Dimension verification note ───────────────────────────────────────────────

def test_pdf_ai_blank_dims_ready_row_gets_dim_note():
    """PDF_AI row with no missing fields and blank Dimensions → dim note only."""
    row = _pdf_ai_row()
    action = _suggested_action(row, missing=[])
    assert "Verify dimensions from spec sheet" in action


def test_pdf_ai_blank_dims_missing_fields_appends_dim_note():
    """PDF_AI row with missing fields AND blank Dimensions → field note + dim note."""
    row = _pdf_ai_row()
    action = _suggested_action(row, missing=["Room"])
    assert "Missing Room" in action
    assert "Verify dimensions from spec sheet" in action


def test_pdf_ai_filled_dims_no_dim_note():
    """PDF_AI row with filled Dimensions → no dim note."""
    row = _pdf_ai_row(**{"Dimensions": '30"W × 18"D'})
    action = _suggested_action(row, missing=[])
    assert "dimension" not in action.lower()


def test_non_pdf_ai_blank_dims_no_dim_note():
    """Non-PDF_AI rows do not get the dim note even if Dimensions is blank."""
    for source in ("PDF", "Manual", "URL"):
        row = {**_pdf_ai_row(), "Source Type": source}
        action = _suggested_action(row, missing=[])
        assert "dimension" not in action.lower(), f"Source {source} should not get dim note"


def test_dim_note_not_duplicated():
    """If action already mentions 'dimension', it is not appended again."""
    row = _pdf_ai_row()
    # The note should appear exactly once
    action = _suggested_action(row, missing=[])
    assert action.lower().count("dimension") == 1
