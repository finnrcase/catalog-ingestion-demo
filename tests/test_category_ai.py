import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── _normalise_category ────────────────────────────────────────────────────────

def test_normalise_exact_match():
    from src.category_ai import _normalise_category
    assert _normalise_category("Appliances") == "Appliances"

def test_normalise_case_insensitive():
    from src.category_ai import _normalise_category
    assert _normalise_category("appliances") == "Appliances"

def test_normalise_couch_maps_to_seating():
    from src.category_ai import _normalise_category
    assert _normalise_category("Couch") == "Seating"
    assert _normalise_category("couch") == "Seating"

def test_normalise_sofa_maps_to_seating():
    from src.category_ai import _normalise_category
    assert _normalise_category("Sofa") == "Seating"
    assert _normalise_category("Chair") == "Seating"

def test_normalise_old_singular_aliases():
    from src.category_ai import _normalise_category
    assert _normalise_category("Appliance") == "Appliances"
    assert _normalise_category("Rug") == "Rugs"
    assert _normalise_category("Mirror") == "Mirrors"
    assert _normalise_category("Table") == "Tables"
    assert _normalise_category("Paint") == "Paint/Wallpaper"
    assert _normalise_category("Fabric") == "Fabrics/Pillows"

def test_normalise_unknown_maps_to_empty():
    from src.category_ai import _normalise_category
    assert _normalise_category("Spaceship") == ""

def test_normalise_empty_maps_to_empty():
    from src.category_ai import _normalise_category
    assert _normalise_category("") == ""

def test_normalise_other_maps_to_empty():
    from src.category_ai import _normalise_category
    assert _normalise_category("Other") == ""


# ── _parse_batch_response ──────────────────────────────────────────────────────

def test_parse_batch_response_valid():
    from src.category_ai import _parse_batch_response
    raw = '[{"id": 0, "category": "Appliances", "confidence": 92, "reason": "Wolf line"}]'
    result = _parse_batch_response(raw)
    assert result[0]["category"] == "Appliances"
    assert result[0]["confidence"] == 92
    assert result[0]["id"] == 0

def test_parse_batch_response_strips_markdown():
    from src.category_ai import _parse_batch_response
    raw = '```json\n[{"id": 1, "category": "Seating", "confidence": 80, "reason": "chair"}]\n```'
    result = _parse_batch_response(raw)
    assert result[0]["category"] == "Seating"

def test_parse_batch_response_invalid_raises():
    from src.category_ai import _parse_batch_response
    with pytest.raises(ValueError):
        _parse_batch_response("not json at all")
