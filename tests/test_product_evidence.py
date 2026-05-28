from __future__ import annotations

from src.product_evidence import (
    normalize_brand,
    normalize_sku,
    product_name_similarity,
    score_product_page,
)


def _wolf_row():
    return {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "30 Inch Warming Drawer",
    }


def test_normalize_sku_removes_punctuation_and_case():
    assert normalize_sku(" MDD-30/TS ") == "mdd30ts"


def test_normalize_brand_removes_company_suffix_noise():
    assert normalize_brand("The Wolf Appliance Co.") == "wolf appliance"


def test_product_name_similarity_prefers_shared_product_terms():
    assert product_name_similarity("30 Inch Warming Drawer", "Wolf 30 inch warming drawer specs") >= 0.68


def test_official_manufacturer_exact_sku_is_high():
    evidence = score_product_page(
        _wolf_row(),
        "https://www.subzero-wolf.com/wolf/cooking/warming-drawers/mdd30ts",
        """
        <html>
          <title>Wolf MDD30TS 30 Inch Warming Drawer</title>
          <body>Wolf MDD-30TS 30 Inch Warming Drawer specifications.</body>
        </html>
        """,
        title="Wolf MDD30TS 30 Inch Warming Drawer",
        description="Official Wolf product page.",
    )

    assert evidence.confidence == "high"
    assert evidence.score == 100
    assert evidence.matched_sku is True
    assert evidence.matched_brand is True
    assert evidence.matched_product_name is True
    assert evidence.official_domain is True
    assert evidence.rejection_reason == ""


def test_retailer_exact_sku_is_medium_max():
    evidence = score_product_page(
        _wolf_row(),
        "https://www.build.com/wolf-mdd30ts/p123",
        "<html><body>Wolf MDD30TS 30 Inch Warming Drawer for sale.</body></html>",
        title="Wolf MDD30TS 30 Inch Warming Drawer",
        description="Retail product listing.",
    )

    assert evidence.confidence == "medium"
    assert evidence.matched_sku is True
    assert evidence.official_domain is False


def test_no_sku_match_when_sku_exists_is_low_or_none():
    evidence = score_product_page(
        _wolf_row(),
        "https://www.subzero-wolf.com/wolf/cooking/warming-drawers",
        "<html><body>Wolf warming drawer product family and design options.</body></html>",
        title="Wolf Warming Drawers",
    )

    assert evidence.confidence in {"low", "none"}
    assert evidence.confidence != "medium"
    assert evidence.confidence != "high"
    assert evidence.matched_sku is False
    assert evidence.rejection_reason in {"sku_not_found_on_page", "insufficient_evidence"}


def test_marketplace_domain_is_none_even_with_exact_sku():
    evidence = score_product_page(
        _wolf_row(),
        "https://www.amazon.com/Wolf-MDD30TS-Warming-Drawer/dp/example",
        "<html><body>Wolf MDD30TS 30 Inch Warming Drawer.</body></html>",
        title="Wolf MDD30TS Warming Drawer",
    )

    assert evidence.confidence == "none"
    assert evidence.rejection_reason == "blocked_marketplace_domain"
    assert evidence.score == 0


def test_wrong_brand_is_none():
    evidence = score_product_page(
        _wolf_row(),
        "https://www.mieleusa.com/products/mdd30ts",
        "<html><body>Miele MDD30TS coffee system specification page.</body></html>",
        title="Miele MDD30TS Coffee System",
    )

    assert evidence.confidence == "none"
    assert evidence.rejection_reason == "wrong_brand"
    assert evidence.matched_sku is True
    assert evidence.matched_brand is False
