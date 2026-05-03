import pytest
from src.confidence import _suggested_action, _missing_important, classify_row_status, identify_missing_fields, should_require_review


def _row(**overrides):
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


# ── _suggested_action — dimensions message ─────────────────────────────────────

def test_blank_dims_gets_programa_note():
    """Any row with blank Dimensions gets the 'Enter full W×H×D' note."""
    action = _suggested_action(_row(), missing=["Dimensions"])
    assert "W" in action and "H" in action and "D" in action
    assert "Programa" in action


def test_partial_dims_gets_programa_note():
    """Any row with partial dims ('36 inch') gets the 'Enter full W×H×D' note."""
    action = _suggested_action(_row(Dimensions="36 inch"), missing=["Dimensions"])
    assert "Programa" in action


def test_complete_3d_dims_no_dim_note():
    """Row with full 3D dims → Dimensions not in missing → no dim note."""
    action = _suggested_action(_row(**{"Dimensions": '36"W x 84"H x 24"D'}), missing=[])
    assert "Programa" not in action or action.startswith("Ready for Programa")
    assert "Enter full" not in action


def test_non_pdf_ai_blank_dims_gets_programa_note():
    """Non-PDF_AI rows also get the dim note when Dimensions is in missing.
    Manual rows with Model/SKU are routed to enrichment first (early return),
    so test Manual without a serial number to reach the missing-fields block."""
    # PDF — straight through to missing-fields logic
    pdf_row = {**_row(), "Source Type": "PDF"}
    action = _suggested_action(pdf_row, missing=["Dimensions"])
    assert "Programa" in action, "PDF source should get dim note"

    # Manual without Model/SKU — falls through to missing-fields block
    manual_row = {**_row(), "Source Type": "Manual", "Model/SKU": ""}
    action = _suggested_action(manual_row, missing=["Dimensions"])
    assert "Programa" in action, "Manual (no SKU) source should get dim note"


def test_dim_note_appears_once():
    """The dim note appears exactly once."""
    action = _suggested_action(_row(), missing=["Dimensions"])
    assert action.count("Programa upload") == 1


def test_missing_fields_and_blank_dims_both_surfaced():
    """Missing fields AND blank dims → both appear in the action string."""
    action = _suggested_action(_row(), missing=["Room", "Dimensions"])
    assert "Room" in action
    assert "Programa" in action


def test_no_spec_sheet_reference():
    """Old 'spec sheet' message is gone entirely."""
    action = _suggested_action(_row(), missing=["Dimensions"])
    assert "spec sheet" not in action.lower()


# ── _missing_important — Dimensions field ──────────────────────────────────────

def _base_non_url_row(**overrides):
    base = {
        "Source Type": "PDF_AI",
        "Product Name": "Wolf Microwave",
        "Brand": "Wolf",
        "Dimensions": "",
        "Quantity": 1,
        "Supplier": "AEG",
        "Room": "Kitchen",
        "Product Category": "Appliance",
    }
    base.update(overrides)
    return base


def test_missing_important_blank_dims_flagged():
    """Blank Dimensions → included in missing list."""
    missing = _missing_important(_base_non_url_row(Dimensions=""))
    assert "Dimensions" in missing


def test_missing_important_partial_dims_flagged():
    """Partial dims ('36 inch') → included in missing list."""
    missing = _missing_important(_base_non_url_row(Dimensions="36 inch"))
    assert "Dimensions" in missing


def test_missing_important_complete_3d_dims_not_flagged():
    """Complete 3D dims → NOT in missing list."""
    missing = _missing_important(_base_non_url_row(Dimensions='36"W x 84"H x 24"D'))
    assert "Dimensions" not in missing


def test_missing_important_unlabeled_triple_flagged():
    """Unlabeled triple (36 x 34.5 x 24) still needs explicit W/H/D labels."""
    missing = _missing_important(_base_non_url_row(Dimensions="36 x 34.5 x 24"))
    assert "Dimensions" in missing


def test_photo_only_rows_are_not_auto_ignored():
    row = {
        "Source Type": "Photo",
        "Product Name": "",
        "Brand": "",
        "Model/SKU": "",
        "Product URL": "",
        "Supplier": "",
        "Quantity": None,
        "Dimensions": "",
        "Room": "",
        "Product Category": "",
        "Import Type": "Photo Upload",
        "photo_only": True,
    }

    assert classify_row_status(row) == "Needs Review"
    assert should_require_review(row) is True
    assert identify_missing_fields(row) == ["Product Name", "Product Category", "Image URL"]
    assert "Photo-only item missing Product Name, Product Category, Image URL" == _suggested_action(row, [])
