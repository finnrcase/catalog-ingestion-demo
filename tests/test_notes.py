import pandas as pd

from src.export import get_csv_bytes
from src.notes import remove_notes_row_prefix


def test_remove_notes_row_prefix_only_removes_leading_index():
    assert remove_notes_row_prefix("12 - Verify dimensions") == "Verify dimensions"
    assert remove_notes_row_prefix("3: Stainless steel finish") == "Stainless steel finish"
    assert remove_notes_row_prefix("Row 7. Confirm finish") == "Confirm finish"


def test_remove_notes_row_prefix_preserves_legitimate_numbers():
    assert remove_notes_row_prefix("12 - 14 week lead time") == "12 - 14 week lead time"
    assert remove_notes_row_prefix("3:00 PM delivery") == "3:00 PM delivery"
    assert remove_notes_row_prefix("36 inch clearance required") == "36 inch clearance required"


def test_export_cleans_notes_without_changing_other_fields():
    df = pd.DataFrame(
        [
            {
                "Product Name": "Chair 12",
                "Notes": "12 - Verify dimensions",
            }
        ]
    )

    csv_text = get_csv_bytes(df).decode("utf-8")

    assert "Verify dimensions" in csv_text
    assert "12 - Verify dimensions" not in csv_text
    assert "Chair 12" in csv_text
