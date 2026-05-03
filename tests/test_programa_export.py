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


# ── _row_to_programa_dict ─────────────────────────────────────────────────────

def test_scotsman_section_maps_to_product_category():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Section"] == "Appliances"

def test_scotsman_sku_mapped():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["SKU"] == "SCN60PA1SU"

def test_scotsman_model_blank():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Model"] == ""

def test_scotsman_dimensions_direct():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Dimensions"] == "14.875 in W x 22 in D x 33.375 in H"

def test_scotsman_width_parsed():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Width (in)"] == "14.875"

def test_scotsman_height_parsed():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Height (in)"] == "33.375"

def test_scotsman_depth_parsed():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Depth (in)"] == "22"

def test_scotsman_length_blank_when_absent():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Length (in)"] == ""

def test_scotsman_finish_from_finish_color():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Finish"] == "Stainless Steel"

def test_scotsman_color_blank():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Color"] == ""

def test_scotsman_material_extracted_from_notes():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Material"] == "Stainless Steel"

def test_scotsman_notes_cleaned():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert "[Materials:" not in result["Notes"]
    assert "[Enrichment:" not in result["Notes"]
    assert "Verify delivery date." in result["Notes"]

def test_scotsman_location_from_room():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Location"] == "Kitchen"

def test_section_fallback_to_general_when_category_blank():
    from src.programa_export import _row_to_programa_dict
    row = _scotsman_row()
    row["Product Category"] = ""
    result = _row_to_programa_dict(row)
    assert result["Section"] == "General"

def test_material_explicit_field_takes_priority():
    from src.programa_export import _row_to_programa_dict
    row = _scotsman_row()
    row["Material"] = "Cast Iron"
    result = _row_to_programa_dict(row)
    assert result["Material"] == "Cast Iron"

def test_lead_time_blank_when_no_field():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Lead Time"] == ""

def test_lead_time_explicit_field_used():
    from src.programa_export import _row_to_programa_dict
    row = _scotsman_row()
    row["Lead Time"] = "8-10 weeks"
    result = _row_to_programa_dict(row)
    assert result["Lead Time"] == "8-10 weeks"

def test_quantity_coerced_to_int():
    from src.programa_export import _row_to_programa_dict
    result = _row_to_programa_dict(_scotsman_row())
    assert result["Quantity"] == 1

def test_quantity_string_input_coerced():
    from src.programa_export import _row_to_programa_dict
    row = _scotsman_row()
    row["Quantity"] = "3"
    result = _row_to_programa_dict(row)
    assert result["Quantity"] == 3

def test_output_has_all_programa_columns():
    from src.programa_export import _row_to_programa_dict, PROGRAMA_COLUMNS
    result = _row_to_programa_dict(_scotsman_row())
    for col in PROGRAMA_COLUMNS:
        assert col in result, f"Missing column: {col}"


def test_photo_only_export_allows_blank_brand_sku_model_url_dimensions():
    from src.programa_export import build_programa_import_dataframe

    row = {
        "Include": True,
        "Source Type": "Photo",
        "Import Type": "Photo Inventory Upload",
        "photo_only": True,
        "Product Name": "Handmade Ceramic Bowl",
        "Product Category": "Accessories",
        "Image URL": "https://res.cloudinary.com/demo/image/upload/bowl.jpg",
        "Color": "Ivory",
        "Material": "Ceramic",
        "Notes": "Photo-only item; details generated from uploaded image.",
    }

    df = build_programa_import_dataframe([row])
    assert len(df) == 1
    assert df.iloc[0]["Brand"] == ""
    assert df.iloc[0]["SKU"] == ""
    assert df.iloc[0]["Model"] == ""
    assert df.iloc[0]["Product URL"] == ""
    assert df.iloc[0]["Dimensions"] == ""
    assert df.iloc[0]["Section"] == "Accessories"
    assert df.iloc[0]["Image URL"].startswith("https://")


def test_photo_only_export_skips_missing_image_url():
    from src.programa_export import build_programa_import_dataframe

    row = {
        "Include": True,
        "Source Type": "Photo",
        "photo_only": True,
        "Product Name": "Handmade Bowl",
        "Product Category": "Accessories",
        "Image URL": "",
    }

    assert build_programa_import_dataframe([row]).empty
