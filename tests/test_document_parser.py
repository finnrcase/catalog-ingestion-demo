import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.document_parser import (
    _is_skip_line,
    _extract_model_sku,
    _extract_quantity,
    _extract_price,
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
