import pytest
import pandas as pd
from src.product_enrichment import (
    _qualifies,
    _build_search_query,
    _apply_enrichment,
    enrich_row,
    enrich_dataframe,
)


# ── _qualifies ─────────────────────────────────────────────────────────────────

def _base_qualifying_row():
    return {
        "Source Type": "PDF",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "",
        "Dimensions": "",
        "Finish / Color": "",
        "Product Category": "",
        "Product URL": "",
        "Notes": "",
        "Review Required": False,
        "Suggested Action": "",
    }


def test_qualifies_url_row_skipped():
    row = {**_base_qualifying_row(), "Source Type": "URL"}
    assert not _qualifies(row)


def test_qualifies_enriched_row_skipped():
    row = {**_base_qualifying_row(), "Source Type": "PDF_Enriched"}
    assert not _qualifies(row)


def test_qualifies_no_brand_skipped():
    row = {**_base_qualifying_row(), "Brand": ""}
    assert not _qualifies(row)


def test_qualifies_no_sku_skipped():
    row = {**_base_qualifying_row(), "Model/SKU": ""}
    assert not _qualifies(row)


def test_qualifies_all_enrichable_fields_present_skipped():
    row = {
        **_base_qualifying_row(),
        "Product Name": "Wolf Microwave",
        "Dimensions": '30"W',
        "Finish / Color": "Stainless",
        "Product Category": "Appliance",
    }
    assert not _qualifies(row)


def test_qualifies_missing_dimensions():
    row = {
        **_base_qualifying_row(),
        "Product Name": "Wolf Microwave",
        "Dimensions": "",
        "Finish / Color": "Stainless",
        "Product Category": "Appliance",
    }
    assert _qualifies(row)


def test_qualifies_all_blank_enrichable_fields():
    assert _qualifies(_base_qualifying_row())


# ── _build_search_query ────────────────────────────────────────────────────────

def test_build_search_query_full():
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "Drawer Microwave"}
    assert _build_search_query(row) == "Wolf MDD30TS Drawer Microwave specifications official"


def test_build_search_query_no_product_name():
    row = {"Brand": "Sub-Zero", "Model/SKU": "ID36R", "Product Name": ""}
    assert _build_search_query(row) == "Sub-Zero ID36R specifications official"


def test_build_search_query_strips_whitespace():
    row = {"Brand": "  Miele  ", "Model/SKU": " CVA7440 ", "Product Name": ""}
    result = _build_search_query(row)
    assert result == "Miele CVA7440 specifications official"
