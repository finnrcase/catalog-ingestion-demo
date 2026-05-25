from __future__ import annotations

from src.product_image_extraction import (
    extract_product_image_candidates,
    score_image_candidates,
    select_best_product_image,
)
from src.product_resolver import ProductCandidate


def _row() -> dict:
    return {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "30 Inch Warming Drawer",
    }


def _evidence(confidence: str = "high") -> ProductCandidate:
    return ProductCandidate(
        url="https://wolfappliance.com/products/mdd30ts",
        domain="wolfappliance.com",
        confidence=confidence,
        matched_sku=True,
        matched_brand=True,
        matched_product_name=True,
        is_official_domain=True,
        evidence_score=95,
    )


def test_extracts_jsonld_and_og_image_candidates():
    html = """
    <meta property="og:image" content="https://cdn.wolfappliance.com/images/mdd30ts-og.jpg">
    <script type="application/ld+json">
      {"@type":"Product","sku":"MDD30TS","image":"https://cdn.wolfappliance.com/images/mdd30ts-jsonld.jpg"}
    </script>
    """

    candidates = extract_product_image_candidates(html, "https://wolfappliance.com/products/mdd30ts", _row())
    urls = {candidate.url for candidate in candidates}

    assert "https://cdn.wolfappliance.com/images/mdd30ts-og.jpg" in urls
    assert "https://cdn.wolfappliance.com/images/mdd30ts-jsonld.jpg" in urls


def test_srcset_and_lazy_fields_are_collected_and_scored():
    html = """
    <picture class="product-gallery">
      <source srcset="/images/mdd30ts-600.jpg 600w, /images/mdd30ts-1600.jpg 1600w">
      <img alt="Wolf MDD30TS product" data-src="https://cdn.wolfappliance.com/mdd30ts-lazy.jpg" width="900" height="700">
    </picture>
    <img data-original="https://cdn.wolfappliance.com/mdd30ts-original.jpg" width="1000" height="800">
    <img data-large="https://cdn.wolfappliance.com/mdd30ts-large.jpg" width="1100" height="900">
    """

    candidates = extract_product_image_candidates(html, "https://wolfappliance.com/products/mdd30ts", _row())
    scored = score_image_candidates(candidates, _row(), _evidence())
    best = max(scored, key=lambda candidate: candidate.score)

    assert any(candidate.url.endswith("mdd30ts-1600.jpg") for candidate in candidates)
    assert any(candidate.url.endswith("mdd30ts-lazy.jpg") for candidate in candidates)
    assert any(candidate.url.endswith("mdd30ts-original.jpg") for candidate in candidates)
    assert any(candidate.url.endswith("mdd30ts-large.jpg") for candidate in candidates)
    assert best.confidence in {"HIGH", "MEDIUM"}


def test_rejects_logo_icon_placeholder_and_unrelated_alt_text():
    html = """
    <img src="https://wolfappliance.com/assets/logo-icon.png" width="800" height="400">
    <img src="https://wolfappliance.com/images/mdd30ts.jpg" alt="Miele coffee maker" width="900" height="700">
    """

    candidates = extract_product_image_candidates(html, "https://wolfappliance.com/products/mdd30ts", _row())
    scored = score_image_candidates(candidates, _row(), _evidence())

    assert all(candidate.rejection_reason for candidate in scored)
    assert {candidate.rejection_reason for candidate in scored} == {
        "bad_image_hint:logo",
        "unrelated_alt_text",
    }


def test_select_best_requires_page_evidence_for_high_medium():
    html = """
    <div class="product-media">
      <img src="https://cdn.wolfappliance.com/images/mdd30ts-main.jpg" alt="Wolf MDD30TS" width="1200" height="900">
    </div>
    """
    candidates = extract_product_image_candidates(html, "https://wolfappliance.com/products/mdd30ts", _row())

    scored_low_page = score_image_candidates(candidates, _row(), _evidence("low"))
    assert select_best_product_image(scored_low_page, _evidence("low")).confidence == "LOW"

    scored_high_page = score_image_candidates(candidates, _row(), _evidence("high"))
    assert select_best_product_image(scored_high_page, _evidence("high")).confidence in {"HIGH", "MEDIUM"}
