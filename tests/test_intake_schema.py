"""Tests for src/intake_schema.py — base row factory, column lists, internal source fields."""


def test_internal_source_fields_present_in_base_row():
    from src.intake_schema import make_base_row
    row = make_base_row()
    assert "_source_pdf_id" in row
    assert "_source_page_number" in row
    assert "_source_filename" in row
    assert "_extracted_model_sku" in row
    assert "_extraction_confidence" in row
    # All three default to empty / None
    assert row["_source_pdf_id"] == ""
    assert row["_source_page_number"] is None
    assert row["_source_filename"] == ""
    assert row["_extracted_model_sku"] == ""
    assert row["_extraction_confidence"] == ""


def test_internal_source_fields_not_in_all_columns_export_list():
    """Internal fields are plumbing — they must NOT appear in ALL_COLUMNS,
    which feeds the user-facing column ordering."""
    from src.intake_schema import ALL_COLUMNS
    assert "_source_pdf_id" not in ALL_COLUMNS
    assert "_source_page_number" not in ALL_COLUMNS
    assert "_source_filename" not in ALL_COLUMNS
    assert "_extracted_model_sku" not in ALL_COLUMNS
    assert "_extraction_confidence" not in ALL_COLUMNS


def test_base_row_covers_all_columns():
    """Every key in ALL_COLUMNS must appear in make_base_row output.

    Guards against drift between the canonical column list and the row
    factory: if a future task adds a column to ALL_COLUMNS but forgets
    to add it to make_base_row, this test fails immediately.
    """
    from src.intake_schema import make_base_row, ALL_COLUMNS
    row = make_base_row()
    missing = [col for col in ALL_COLUMNS if col not in row]
    assert missing == [], f"make_base_row is missing columns: {missing}"


def test_build_intake_dataframe_preserves_pdf_audit_fields():
    from src.intake import build_intake_dataframe

    df = build_intake_dataframe([], [{
        "Product Name": "Wolf Warming Drawer",
        "Model/SKU": "MDD30TS",
        "_source_pdf_id": "abc123",
        "_source_page_number": 2,
        "_source_filename": "spec.pdf",
        "_extracted_model_sku": "MDD30TS",
        "_extraction_confidence": 85,
    }])

    assert df.iloc[0]["_source_pdf_id"] == "abc123"
    assert df.iloc[0]["_source_page_number"] == 2
    assert df.iloc[0]["_source_filename"] == "spec.pdf"
    assert df.iloc[0]["_extracted_model_sku"] == "MDD30TS"
    assert df.iloc[0]["_extraction_confidence"] == 85
