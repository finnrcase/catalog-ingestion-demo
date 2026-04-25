import pytest
from src.ai_extraction import _item_to_row


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


def test_item_to_row_location_note_not_duplicated():
    """Re-running _item_to_row on same item does not duplicate the Notes tag."""
    item = _base_item(room="bar - if we can fit it", notes="")
    row1 = _item_to_row(item, "Test Project", "Living Room", "AEG")
    # Simulate re-processing: set room to already-cleaned value, notes already set
    item2 = _base_item(room="Bar", notes=row1["Notes"])
    row2 = _item_to_row(item2, "Test Project", "Living Room", "AEG")
    assert row2["Notes"].count("[Location note:") <= 1
