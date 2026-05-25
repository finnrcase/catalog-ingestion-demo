from __future__ import annotations

from src.official_product_lookup import ProductPageLookupResult
from src.pdf_product_workflow import (
    PDF_PRODUCT_URL_LOOKUP_NOTE,
    enrich_pdf_rows_with_official_product_urls,
    normalize_pdf_product_row,
    normalize_pdf_product_rows,
)


def test_normalize_pdf_product_row_preserves_extraction_audit_fields():
    row = normalize_pdf_product_row({
        "Manufacturer": " Wolf ",
        "Description": "  Warming   Drawer ",
        "SKU": "Model #: MDD30TS",
        "Finish": " Stainless Steel ",
        "Source Type": "PDF",
        "_source_pdf_id": "abc123",
        "_source_page_number": 4,
        "_source_filename": "quote.pdf",
    })

    assert row["Brand"] == "Wolf"
    assert row["Product Name"] == "Warming Drawer"
    assert row["Model/SKU"] == "MDD30TS"
    assert row["Finish / Color"] == "Stainless Steel"
    assert row["Product Category"] == "Appliances"
    assert row["_source_pdf_id"] == "abc123"
    assert row["_source_page_number"] == 4
    assert row["_source_filename"] == "quote.pdf"
    assert row["_extracted_model_sku"] == "MDD30TS"
    assert int(row["_extraction_confidence"]) >= 80


def test_normalize_pdf_product_rows_attaches_source_page_by_sku():
    source_rows = [{
        "Product Name": "Wolf Warming Drawer",
        "Model/SKU": "MDD30TS",
        "_source_pdf_id": "pdf1",
        "_source_page_number": 2,
        "_source_filename": "spec.pdf",
    }]

    rows = normalize_pdf_product_rows(
        [{"Product Name": "Wolf Drawer", "Brand": "Wolf", "Model/SKU": "MDD30TS", "Source Type": "PDF_AI"}],
        source_rows=source_rows,
    )

    assert rows[0]["_source_pdf_id"] == "pdf1"
    assert rows[0]["_source_page_number"] == 2
    assert rows[0]["_source_filename"] == "spec.pdf"


def test_pdf_lookup_note_documents_confirmed_url_policy():
    assert "extracted Model/SKU" in PDF_PRODUCT_URL_LOOKUP_NOTE
    assert "SKU, brand, and product-name evidence" in PDF_PRODUCT_URL_LOOKUP_NOTE


def test_enrich_pdf_rows_saves_only_confirmed_official_product_url():
    calls = []

    def fake_lookup(row, *, session_cache=None, validate_pages=False):
        calls.append(validate_pages)
        return ProductPageLookupResult(
            selected_url="https://www.visualcomfort.com/products/tob-1234",
            confidence="HIGH",
            reason="brand_registry_domain;sku_match;page_validation_ok",
            queries_used=["Visual Comfort TOB 1234 official product page"],
            candidate_pages=[{"url": "https://www.visualcomfort.com/products/tob-1234"}],
        )

    rows, errors = enrich_pdf_rows_with_official_product_urls([{
        "Product Name": "Table Lamp",
        "Brand": "Visual Comfort",
        "Model/SKU": "TOB 1234",
        "Source Type": "PDF",
        "Product URL": "",
    }], lookup_fn=fake_lookup)

    assert errors == []
    assert calls == [True]
    assert rows[0]["Product URL"] == "https://www.visualcomfort.com/products/tob-1234"
    assert rows[0]["_product_url_lookup_status"] == "confirmed"


def test_enrich_pdf_rows_leaves_unvalidated_lookup_for_manual_review():
    def fake_lookup(row, *, session_cache=None, validate_pages=False):
        return ProductPageLookupResult(
            selected_url="https://www.visualcomfort.com/search?q=TOB+1234",
            confidence="NONE",
            reason="page_validation_failed:generic_page",
        )

    rows, errors = enrich_pdf_rows_with_official_product_urls([{
        "Product Name": "Table Lamp",
        "Brand": "Visual Comfort",
        "Model/SKU": "TOB 1234",
        "Source Type": "PDF",
        "Product URL": "",
    }], lookup_fn=fake_lookup)

    assert errors == []
    assert rows[0]["Product URL"] == ""
    assert rows[0]["_product_url_lookup_status"] == "needs_manual_lookup"
