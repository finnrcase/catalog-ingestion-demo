import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.document_parser import (
    _extract_red_room_annotations,
    _is_skip_line,
    _extract_model_sku,
    _extract_quantity,
    _extract_price,
    _row_from_line,
    _parse_table_rows,
)


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


def test_row_from_line_extracts_brand_model_dimensions_and_finish():
    row = _row_from_line(
        'Wolf MDD30TS Drawer Microwave Dimensions: 30"W x 15"H x 17"D Finish: Stainless Steel',
        "1 Lily Pond",
        "Kitchen",
        "PC Richard",
        "",
    )

    assert row is not None
    assert row["Brand"] == "Wolf"
    assert row["Model/SKU"] == "MDD30TS"
    assert row["Dimensions"] == '30"W x 15"H x 17"D'
    assert row["Finish / Color"] == "Stainless Steel"
    assert row["Supplier"] == "PC Richard"
    assert row["Product Category"] == "Appliances"


def test_red_room_annotation_moves_to_room_not_description():
    row = _row_from_line(
        "Miele G7986SCVIK20 Fully Integrated Dishwasher Kitchen PNL",
        "1 Lily Pond",
        "",
        "PC Richard",
        "",
        room_annotations=["Kitchen"],
    )

    assert row is not None
    assert row["Brand"] == "Miele"
    assert row["Model/SKU"] == "G7986SCVIK20"
    assert row["Room"] == "Kitchen"
    assert "Kitchen" not in row["Product Name"]


def test_red_room_span_detection():
    class FakePage:
        def get_text(self, mode):
            assert mode == "dict"
            return {
                "blocks": [
                    {
                        "lines": [
                            {
                                "spans": [
                                    {"text": "Kitchen", "color": 0xD00000},
                                    {"text": "Not A Room", "color": 0x000000},
                                ]
                            }
                        ]
                    }
                ]
            }

    assert _extract_red_room_annotations(FakePage()) == ["Kitchen"]


def test_pc_richard_room_annotations_preserve_late_quote_models():
    class FakeTable:
        def extract(self):
            return [
                ["Item", "Manufacturer", "Model", "Description", "Color", "Qty"],
                ["26", "Miele", "G7986SCVIK20", "Fully Integrated Dishwasher Kitchen", "PNL", "1"],
                ["27", "Wolf", "WWD30", "Warming Drawer Kitchen", "SS", "1"],
                ["28", "Bosch", "SPV68C73UC", "Dishwasher Mudroom", "Panel Ready", "1"],
                ["29", "Wolf", "PL522212", "Liner Exterior", "Stainless", "1"],
                ["30", "Wolf", "829155", "Outdoor Grill Accessory Bar", "Stainless", "1"],
            ]

    class FakePage:
        def find_tables(self):
            return [FakeTable()]

    rows = _parse_table_rows(
        FakePage(),
        project="1 Lily Pond",
        room="",
        supplier="PC Richard",
        notes="",
        category="Appliances",
        room_annotations=["Kitchen", "Mudroom", "Exterior", "Bar"],
    )

    by_model = {row["Model/SKU"]: row for row in rows}
    assert set(by_model) == {"G7986SCVIK20", "WWD30", "SPV68C73UC", "PL522212", "829155"}
    assert by_model["G7986SCVIK20"]["Brand"] == "Miele"
    assert by_model["WWD30"]["Brand"] == "Wolf"
    assert by_model["SPV68C73UC"]["Brand"] == "Bosch"
    assert by_model["PL522212"]["Brand"] == "Wolf"
    assert by_model["829155"]["Brand"] == "Wolf"
    assert by_model["G7986SCVIK20"]["Room"] == "Kitchen"
    assert by_model["SPV68C73UC"]["Room"] == "Mudroom"
    assert by_model["PL522212"]["Room"] == "Exterior"
    assert by_model["829155"]["Room"] == "Bar"
    for row in rows:
        assert row["Room"] not in row["Product Name"]
