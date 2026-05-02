import io

import pandas as pd
import pytest

from src.programa_export import (
    _clean_notes,
    _extract_material_from_notes,
)


# ── Shared test fixtures ──────────────────────────────────────────────────────

def _scotsman_row() -> dict:
    """Acceptance test fixture — Scotsman icemaker."""
    return {
        "Include": True,
        "Product Category": "Appliances",
        "Product Name": "Scotsman Icemaker Built-In Pump",
        "Brand": "Scotsman",
        "Model/SKU": "SCN60PA1SU",
        "Dimensions": "14.875 in W x 22 in D x 33.375 in H",
        "Finish / Color": "Stainless Steel",
        "Quantity": 1,
        "Price": "",
        "Supplier": "Scotsman",
        "Product URL": "https://scotsman-ice.com/product/scn60pa1su",
        "Image URL": "https://scotsman-ice.com/images/scn60pa1su.jpg",
        "Notes": "Verify delivery date. [Materials: Stainless Steel] [Enrichment: matched]",
        "Room": "Kitchen",
    }


def _make_rows(overrides_list: list[dict]) -> list[dict]:
    """Build a list of minimal valid rows, applying per-row overrides."""
    base = {
        "Include": True,
        "Product Name": "Test Product",
        "Product Category": "Lighting",
        "Dimensions": "12 in W x 10 in H x 8 in D",
        "Product URL": "https://example.com/product",
        "Image URL": "https://example.com/image.jpg",
    }
    return [{**base, **o} for o in overrides_list]


# ── _extract_material_from_notes ──────────────────────────────────────────────

def test_extract_material_from_tag():
    assert _extract_material_from_notes("[Materials: Stainless Steel]") == "Stainless Steel"

def test_extract_material_case_insensitive():
    assert _extract_material_from_notes("[materials: oak veneer]") == "oak veneer"

def test_extract_material_trims_whitespace():
    assert _extract_material_from_notes("[Materials:  solid oak  ]") == "solid oak"

def test_extract_material_with_surrounding_text():
    result = _extract_material_from_notes("Some note. [Materials: Brass] More text.")
    assert result == "Brass"

def test_extract_material_missing_tag_returns_empty():
    assert _extract_material_from_notes("Just a regular note.") == ""

def test_extract_material_empty_string():
    assert _extract_material_from_notes("") == ""


# ── _clean_notes ──────────────────────────────────────────────────────────────

def test_clean_notes_strips_materials_tag():
    result = _clean_notes("Good note. [Materials: Steel]")
    assert "[Materials:" not in result
    assert "Good note." in result

def test_clean_notes_strips_enrichment_tag():
    result = _clean_notes("[Enrichment: no confident source found] Some note.")
    assert "[Enrichment:" not in result
    assert "Some note." in result

def test_clean_notes_strips_partial_dimension_tag():
    raw = "Check with vendor. [Partial dimension found: 14 W; full W x H x D still needed]"
    result = _clean_notes(raw)
    assert "[Partial dimension" not in result
    assert "Check with vendor." in result

def test_clean_notes_removes_row_prefix():
    assert _clean_notes("3 - Verify finish") == "Verify finish"

def test_clean_notes_keeps_human_text():
    assert _clean_notes("Stainless steel finish, confirm with supplier.") == \
        "Stainless steel finish, confirm with supplier."

def test_clean_notes_empty_string():
    assert _clean_notes("") == ""

def test_clean_notes_only_system_tag_returns_empty():
    result = _clean_notes("[Enrichment: no confident source found]")
    assert result == ""
