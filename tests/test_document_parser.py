import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.document_parser import (
    _is_skip_line,
    _extract_model_sku,
    _extract_quantity,
    _extract_price,
)
from src.pdf_item_normalizer import build_quote_item_rows, extract_quote_context


# ── Skip-line detection ────────────────────────────────────────────────────────

def test_skip_subtotal():
    assert _is_skip_line("Subtotal: $1,200.00") is True

def test_skip_tax():
    assert _is_skip_line("HST 13%: $156.00") is True

def test_skip_delivery():
    assert _is_skip_line("Delivery & Installation") is True

def test_skip_freight():
    assert _is_skip_line("Freight & Handling: $75.00") is True

def test_skip_deposit():
    assert _is_skip_line("Deposit paid") is True

def test_skip_total():
    assert _is_skip_line("TOTAL DUE: $2,500.00") is True

def test_not_skip_product():
    assert _is_skip_line('Wolf 30" Microwave MDD30TS') is False

def test_not_skip_empty():
    assert _is_skip_line("") is False


# ── Model/SKU extraction ───────────────────────────────────────────────────────

def test_extract_model_sku_model_label():
    val, label = _extract_model_sku("Model #: MDD30TS  Wolf Microwave")
    assert val == "MDD30TS"
    assert label == "Model #"

def test_extract_model_sku_sku_label():
    val, label = _extract_model_sku("SKU: 00884844")
    assert val == "00884844"
    assert label == "SKU"

def test_extract_model_sku_item_number():
    val, label = _extract_model_sku("Item #: 1234-AB  Sofa")
    assert val == "1234-AB"
    assert label == "Item #"

def test_extract_model_sku_part_number():
    val, label = _extract_model_sku("Part Number: XYZ-99 cushion")
    assert val == "XYZ-99"
    assert label == "Part Number"

def test_extract_model_sku_none():
    val, label = _extract_model_sku("Just a description with no model")
    assert val == ""
    assert label == ""


# ── Quantity extraction ────────────────────────────────────────────────────────

def test_extract_quantity_qty():
    assert _extract_quantity("Wolf Microwave  qty 2") == 2

def test_extract_quantity_x():
    assert _extract_quantity("Chair x3") == 3

def test_extract_quantity_parentheses():
    assert _extract_quantity("Sofa (2)") == 2

def test_extract_quantity_default():
    assert _extract_quantity("Just a product") == 1


# ── Price extraction ───────────────────────────────────────────────────────────

def test_extract_price_dollar():
    assert _extract_price("Wolf Microwave $1,250.00") == "$1,250.00"

def test_extract_price_decimal():
    assert _extract_price("Chair 899.00") == "899.00"

def test_extract_price_none():
    assert _extract_price("Chair no price here") == ""


# ── Source annotation ──────────────────────────────────────────────────────────

def test_parsed_rows_carry_source_pdf_id_and_page(tmp_path):
    import io as _io
    import fitz
    from src.document_parser import parse_pdf_rows

    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 50), "Wolf MDD30TS Warming Drawer 1 ea $999")
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((50, 50), "Sub-Zero ID36R Refrigerator 1 ea $5000")
    pdf_path = tmp_path / "spec.pdf"
    doc.save(str(pdf_path))
    doc.close()

    class _Up:
        def __init__(self, raw, name):
            self._raw = raw
            self._pos = 0
            self.name = name
        def read(self):
            return self._raw
        def seek(self, p):
            self._pos = p

    raw = pdf_path.read_bytes()
    up = _Up(raw, "spec.pdf")
    rows = parse_pdf_rows(up)
    assert rows, "expected at least one row from synthetic PDF"

    # All rows share the same _source_pdf_id (it's the SHA1 of the bytes).
    pdf_ids = {r.get("_source_pdf_id") for r in rows}
    assert len(pdf_ids) == 1
    assert next(iter(pdf_ids))

    # Page numbers are 1-indexed.
    pages = {r.get("_source_page_number") for r in rows}
    assert pages.issubset({1, 2})

    # Filename preserved.
    assert all(r.get("_source_filename") == "spec.pdf" for r in rows)
    assert all("_extracted_model_sku" in r for r in rows)
    assert all("_extraction_confidence" in r for r in rows)


def test_quote_item_grouping_combines_variables_into_one_item():
    lines = [
        "PC RICHARD APPLIANCE SELECTION",
        "Project: 1 Lily Pond Lane",
        "ITEM",
        "MANUFACTURER",
        "MODEL",
        "DESCRIPTION",
        "COLOR",
        "QTY",
        "1",
        "SCOTTSMAN",
        "SCN60PA1SU",
        "ICEMAKER BUILT IN PUMP",
        "PNL",
        "1",
        "$4,299.00",
        "5 Years Warranty",
    ]

    rows = build_quote_item_rows(lines)

    assert len(rows) == 1
    row = rows[0]
    assert row["Project"] == "1 Lily Pond Lane"
    assert row["Supplier"] == "PC Richard"
    assert row["Product Category"] == "Appliances"
    assert row["Brand"] == "Scotsman"
    assert row["Model/SKU"] == "SCN60PA1SU"
    assert row["_item_description"] == "Icemaker Built In Pump"
    assert row["Product Name"] == "Scotsman Icemaker Built In Pump"
    assert row["Finish / Color"] == "Panel Ready"
    assert row["Quantity"] == 1
    assert row["Price"] == "$4,299.00"
    assert "SCN60PA1SU" in row["_raw_grouped_text"]
    assert "brand, model, description" in row["_confidence_reason"]


def test_quote_item_grouping_multiple_appliance_items_no_header_rows():
    lines = [
        "QUOTATION SHEET",
        "Project:",
        "1 Lily Pond Lane",
        "Salesperson: Jane",
        "ITEM | MANUFACTURER | MODEL | DESCRIPTION | COLOR | QTY | PRICE",
        "1 | SUB ZERO | BI-36UFD/O | 36 IN BUILT-IN FRENCH DOOR REFRIGERATOR | SS | 1 | $9,999.00",
        "8 Years Warranty",
        "2 | MIELE | G7566SCVI | PANEL READY DISHWASHER | PNL | 1 | $2,599.00",
        "Date: 05/20/2026",
    ]

    rows = build_quote_item_rows(lines)

    assert [row["Brand"] for row in rows] == ["Sub-Zero", "Miele"]
    assert [row["Model/SKU"] for row in rows] == ["BI-36UFD/O", "G7566SCVI"]
    assert all(row["Product Category"] == "Appliances" for row in rows)
    assert not any(row["Product Name"].lower().startswith("quotation") for row in rows)
    assert rows[0]["_quote_date"] == "05/20/2026"


def test_quote_context_extracts_global_fields_without_items():
    context = extract_quote_context([
        "PC RICHARD APPLIANCE SELECTION",
        "Project:",
        "1 Lily Pond Lane",
        "Date 05/20/2026",
    ])

    assert context.project == "1 Lily Pond Lane"
    assert context.supplier == "PC Richard"
    assert context.category == "Appliances"
    assert context.quote_date == "05/20/2026"


def test_parse_pdf_rows_groups_separate_quote_tokens_into_single_product(tmp_path):
    import fitz
    from src.document_parser import parse_pdf_rows

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 50
    for line in [
        "PC RICHARD APPLIANCE SELECTION",
        "Project: 1 Lily Pond Lane",
        "ITEM",
        "MANUFACTURER",
        "MODEL",
        "DESCRIPTION",
        "COLOR",
        "QTY",
        "1",
        "SCOTSMAN",
        "SCN60PA1SU",
        "ICEMAKER BUILT IN PUMP",
        "PNL",
        "1",
        "$4,299.00",
    ]:
        page.insert_text((50, y), line)
        y += 18
    raw = doc.tobytes()
    doc.close()

    class _Up:
        name = "pc-richard.pdf"

        def read(self):
            return raw

        def seek(self, _pos):
            return None

    rows = parse_pdf_rows(_Up())

    assert len(rows) == 1
    assert rows[0]["Brand"] == "Scotsman"
    assert rows[0]["Model/SKU"] == "SCN60PA1SU"
    assert rows[0]["Finish / Color"] == "Panel Ready"
    assert rows[0]["_source_filename"] == "pc-richard.pdf"
