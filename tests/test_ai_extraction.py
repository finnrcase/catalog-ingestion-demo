import pytest
from src.ai_extraction import (
    _item_to_row,
    merge_ai_rows_with_deterministic,
    should_run_ai_parse,
    stamp_deterministic_parse_debug,
)


def _base_item(**overrides):
    """Minimal AI JSON object that produces a valid row."""
    base = {
        "project": "Test Project",
        "room": "Kitchen",
        "product_name": "Wolf Microwave",
        "brand": "Wolf",
        "dimensions": "",
        "finish_color": "",
        "model_sku": "MDD30TS",
        "quantity": 1,
        "price": "$1,200",
        "supplier": "AEG",
        "product_url": "",
        "notes": "",
        "product_category": "Appliance",
        "confidence_score": 85,
        "review_required": False,
        "missing_fields": "",
        "suggested_action": "",
    }
    base.update(overrides)
    return base


# ── Location normalisation ────────────────────────────────────────────────────

def test_item_to_row_messy_location_cleaned():
    """Uncertain location qualifier is stripped; room is title-cased."""
    item = _base_item(room="bar - if we can fit it")
    row = _item_to_row(item, "Test Project", "Living Room", "AEG")
    assert row["Room"] == "Bar"


def test_item_to_row_messy_location_preserves_original_in_notes():
    """Original messy location note is preserved in Notes."""
    item = _base_item(room="bar - if we can fit it", notes="")
    row = _item_to_row(item, "Test Project", "Living Room", "AEG")
    assert "[Location note: bar - if we can fit it]" in row["Notes"]


def test_item_to_row_messy_location_lowers_confidence():
    """Messy location subtracts 15 from Claude's confidence score."""
    item = _base_item(room="bar - if we can fit it", confidence_score=85)
    row = _item_to_row(item, "Test Project", "Living Room", "AEG")
    assert row["Confidence Score"] == 70  # 85 - 15


def test_item_to_row_clean_location_unchanged():
    """Clean location is title-cased but does not affect confidence or Notes."""
    item = _base_item(room="laundry room floor 2", notes="")
    row = _item_to_row(item, "Test Project", "Kitchen", "AEG")
    assert row["Room"] == "Laundry Room Floor 2"
    assert row["Notes"] == ""
    assert row["Confidence Score"] == 85  # unchanged


def test_item_to_row_empty_location_uses_default():
    """Empty AI-extracted location falls back to default_room."""
    item = _base_item(room="")
    row = _item_to_row(item, "Test Project", "Master Bedroom", "AEG")
    assert row["Room"] == "Master Bedroom"


def test_should_run_ai_parse_when_core_source_fields_missing():
    rows = [{
        "Product Name": "Panel Ready Icemaker",
        "Brand": "Scotsman",
        "Model/SKU": "SCN60PA1SU",
        "Quantity": 1,
        "Supplier": "",
        "Dimensions": "",
        "Product Category": "",
    }]

    should_run, reason = should_run_ai_parse(rows)

    assert should_run is True
    assert "missing critical fields" in reason


def test_merge_ai_rows_fills_missing_without_blank_overwrite_and_adds_debug():
    deterministic = [{
        "Project": "1 Lily Pond",
        "Room": "Kitchen",
        "Product Name": "SCN60PA1SU",
        "Brand": "",
        "Model/SKU": "SCN60PA1SU",
        "Dimensions": "",
        "Supplier": "",
        "Quantity": 1,
        "Source Type": "PDF",
    }]
    ai = [{
        "Project": "1 Lily Pond",
        "Room": "Kitchen",
        "Product Name": "Scotsman Panel Ready Icemaker",
        "Brand": "Scotsman",
        "Model/SKU": "SCN60PA1SU",
        "Dimensions": '14 7/8"W x 33 3/8"H x 22"D',
        "Supplier": "PC Richard",
        "Quantity": 1,
        "Product Category": "Appliances",
        "Confidence Score": 92,
        "Source Type": "PDF_AI",
    }]

    merged = merge_ai_rows_with_deterministic(deterministic, ai)

    assert len(merged) == 1
    row = merged[0]
    assert row["Product Name"] == "Scotsman Panel Ready Icemaker"
    assert row["Brand"] == "Scotsman"
    assert row["Supplier"] == "PC Richard"
    assert row["Dimensions"] == '14 7/8"W x 33 3/8"H x 22"D'
    assert row["ai_used"] is True
    assert row["deterministic_product_name"] == "SCN60PA1SU"
    assert row["ai_product_name"] == "Scotsman Panel Ready Icemaker"
    assert row["final_product_name"] == "Scotsman Panel Ready Icemaker"
    assert "Supplier" in row["missing_critical_fields_before_ai"]
    assert row["missing_critical_fields_after_ai"] == ""


def test_stamp_deterministic_parse_debug_when_ai_not_requested():
    rows = [{"Product Name": "Wolf Microwave", "Model/SKU": "MDD30TS", "Quantity": 1}]

    stamped = stamp_deterministic_parse_debug(rows, "AI not requested")

    assert stamped[0]["ai_used"] is False
    assert stamped[0]["ai_skipped_reason"] == "AI not requested"
    assert stamped[0]["final_product_name"] == "Wolf Microwave"


def test_item_to_row_location_note_not_duplicated():
    """Re-running _item_to_row on same item does not duplicate the Notes tag."""
    item = _base_item(room="bar - if we can fit it", notes="")
    row1 = _item_to_row(item, "Test Project", "Living Room", "AEG")
    # Simulate re-processing: set room to already-cleaned value, notes already set
    item2 = _base_item(room="Bar", notes=row1["Notes"])
    row2 = _item_to_row(item2, "Test Project", "Living Room", "AEG")
    assert row2["Notes"].count("[Location note:") <= 1
