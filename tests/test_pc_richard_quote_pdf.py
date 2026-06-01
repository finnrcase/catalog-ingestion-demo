from pathlib import Path

import pytest

from src.document_parser import parse_pdf_rows


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "1 Lily Pond- PC Richards QUOTE 01.19.2026 (4).pdf"
)


@pytest.mark.skipif(not FIXTURE.exists(), reason="PC Richard quote PDF fixture is not checked in")
def test_pc_richard_quote_pdf_parse_regressions():
    with FIXTURE.open("rb") as handle:
        rows = parse_pdf_rows(
            handle,
            project="1 Lily Pond",
            supplier="PC Richard",
            notes="",
        )

    included = [row for row in rows if row.get("Include") is not False]
    charges = [row for row in rows if row.get("Import Type") == "unresolved_charge"]
    by_model = {str(row.get("Model/SKU") or "").strip(): row for row in included}

    assert any(row.get("Price") == "$285.97" for row in charges)
    assert "631-287-2405" not in by_model
    for model in {"G7986SCVIK20", "WWD30", "SPV68C73UC", "PL522212", "829155"}:
        assert model in by_model
        assert by_model[model].get("Brand")

    identities = [
        (
            str(row.get("Brand") or "").strip().lower(),
            str(row.get("Model/SKU") or "").strip().lower(),
            str(row.get("Room") or "").strip().lower(),
            str(row.get("Product Name") or "").strip().lower(),
        )
        for row in included
        if row.get("Brand") and row.get("Model/SKU")
    ]
    assert len(identities) == len(set(identities))
    assert len(included) <= 26

    qty_two_rows = [
        row for row in included
        if str(row.get("Quantity") or "").strip() in {"2", "2.0"}
    ]
    assert qty_two_rows
