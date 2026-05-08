"""Tests for src/intake_schema.py — base row factory, column lists, internal source fields."""


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
