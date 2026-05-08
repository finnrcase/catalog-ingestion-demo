def test_internal_source_fields_present_in_base_row():
    from src.intake_schema import make_base_row
    row = make_base_row()
    assert "_source_pdf_id" in row
    assert "_source_page_number" in row
    assert "_source_filename" in row
    # All three default to empty / None
    assert row["_source_pdf_id"] == ""
    assert row["_source_page_number"] is None
    assert row["_source_filename"] == ""


def test_internal_source_fields_not_in_all_columns_export_list():
    """Internal fields are plumbing — they must NOT appear in ALL_COLUMNS,
    which feeds the user-facing column ordering."""
    from src.intake_schema import ALL_COLUMNS
    assert "_source_pdf_id" not in ALL_COLUMNS
    assert "_source_page_number" not in ALL_COLUMNS
    assert "_source_filename" not in ALL_COLUMNS
