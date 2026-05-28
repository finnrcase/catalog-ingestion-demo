from __future__ import annotations

import pytest

from src.brave_search import SearchResult
from src.enrichment_cache import ProductEnrichmentCache
from src.product_evidence import ProductEvidence
from src.product_images import extract_product_page_image
from src.product_page_images import ProductPageImageResult
from src.product_lookup_cache import ProductLookupCache


def _row() -> dict:
    return {
        "Source Type": "PDF",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "30 Inch Warming Drawer",
        "Product URL": "",
        "Image URL": "",
        "Notes": "",
    }


def _high_page_evidence() -> ProductEvidence:
    return ProductEvidence(
        confidence="high",
        score=100,
        matched_sku=True,
        matched_brand=True,
        matched_product_name=True,
        official_domain=True,
        domain="wolfappliance.com",
        evidence_summary="exact_sku, brand, product_name, official_domain",
    )


def _resolver_response(url: str, html: str):
    class Resp:
        status_code = 200
        headers = {"content-type": "text/html"}
        content = html.encode("utf-8")

        def __init__(self):
            self.text = html
            self.url = url

    return Resp()


@pytest.fixture
def isolated_product_enrichment_caches(monkeypatch, tmp_path):
    import src.product_enrichment as pe

    product_cache = ProductEnrichmentCache()
    product_cache._data = {}
    product_cache._path = str(tmp_path / "product_enrichment_cache.json")
    lookup_cache = ProductLookupCache(tmp_path / "product_lookup_cache.json")
    lookup_cache._data = {}

    monkeypatch.setattr(pe, "_product_cache", product_cache)
    monkeypatch.setattr(pe, "_lookup_cache", lookup_cache)


def test_json_ld_image_extracted():
    html = """
    <html>
      <script type="application/ld+json">
        {"@type":"Product","name":"Wolf MDD30TS","sku":"MDD30TS",
         "image":"https://cdn.wolfappliance.com/images/mdd30ts-jsonld.jpg"}
      </script>
      <body>Wolf MDD30TS 30 Inch Warming Drawer</body>
    </html>
    """

    result = extract_product_page_image(
        html,
        "https://wolfappliance.com/products/mdd30ts",
        _row(),
        page_evidence=_high_page_evidence(),
        source_prefix="official_site",
    )

    assert result.image_found is True
    assert result.image_url == "https://cdn.wolfappliance.com/images/mdd30ts-jsonld.jpg"
    assert result.image_source == "official_site_jsonld_image"
    assert result.confidence in {"HIGH", "MEDIUM"}


def test_next_data_product_image_extracted():
    html = """
    <html>
      <script id="__NEXT_DATA__" type="application/json">
      {"props":{"pageProps":{"product":{
        "title":"Wolf MDD30TS 30 Inch Warming Drawer",
        "sku":"MDD30TS",
        "images":["https://cdn.wolfappliance.com/images/mdd30ts-next-data.jpg"]
      }}}}
      </script>
      <body>Wolf MDD30TS 30 Inch Warming Drawer</body>
    </html>
    """

    result = extract_product_page_image(
        html,
        "https://wolfappliance.com/products/mdd30ts",
        _row(),
        page_evidence=_high_page_evidence(),
        source_prefix="official_site",
    )

    assert result.image_found is True
    assert result.image_url == "https://cdn.wolfappliance.com/images/mdd30ts-next-data.jpg"
    assert result.image_source == "official_site_jsonld_image"


def test_product_css_background_image_extracted():
    html = """
    <html><body>
      <section class="product-gallery hero" style="background-image: url('/images/mdd30ts-background.jpg')">
        Wolf MDD30TS 30 Inch Warming Drawer
      </section>
    </body></html>
    """

    result = extract_product_page_image(
        html,
        "https://wolfappliance.com/products/mdd30ts",
        _row(),
        page_evidence=_high_page_evidence(),
    )

    assert result.image_found is True
    assert result.image_url == "https://wolfappliance.com/images/mdd30ts-background.jpg"
    assert result.image_source.endswith("_html_image")


def test_srcset_best_image_extracted():
    html = """
    <html><body>
      <div class="product-gallery">
        <img
          alt="Wolf MDD30TS 30 Inch Warming Drawer"
          srcset="/images/mdd30ts-small.jpg 320w, /images/mdd30ts-large.jpg 1200w"
          width="1200"
          height="900">
      </div>
    </body></html>
    """

    result = extract_product_page_image(
        html,
        "https://wolfappliance.com/products/mdd30ts",
        _row(),
        page_evidence=_high_page_evidence(),
    )

    assert result.image_found is True
    assert result.image_url == "https://wolfappliance.com/images/mdd30ts-large.jpg"
    assert result.debug["selected_image"].endswith("mdd30ts-large.jpg")


def test_lazy_loaded_data_src_extracted():
    html = """
    <html><body>
      <section class="product-media">
        <img
          alt="Wolf MDD30TS product photo"
          data-src="https://cdn.wolfappliance.com/gallery/mdd30ts-main.jpg"
          width="900"
          height="700">
      </section>
    </body></html>
    """

    result = extract_product_page_image(
        html,
        "https://wolfappliance.com/products/mdd30ts",
        _row(),
        page_evidence=_high_page_evidence(),
    )

    assert result.image_found is True
    assert result.image_url == "https://cdn.wolfappliance.com/gallery/mdd30ts-main.jpg"


def test_source_srcset_inside_picture_extracted():
    html = """
    <html><body>
      <picture class="product-carousel">
        <source srcset="/images/mdd30ts-600.jpg 600w, /images/mdd30ts-1400.jpg 1400w">
        <img alt="Wolf MDD30TS product view" src="/images/fallback.jpg">
      </picture>
    </body></html>
    """

    result = extract_product_page_image(
        html,
        "https://wolfappliance.com/products/mdd30ts",
        _row(),
        page_evidence=_high_page_evidence(),
    )

    assert result.image_found is True
    assert result.image_url == "https://wolfappliance.com/images/mdd30ts-1400.jpg"


def test_logo_icon_rejected():
    html = """
    <html><body>
      <img alt="Wolf logo" src="https://wolfappliance.com/assets/wolf-logo-icon.png" width="400" height="200">
    </body></html>
    """

    result = extract_product_page_image(
        html,
        "https://wolfappliance.com/products/mdd30ts",
        _row(),
        page_evidence=_high_page_evidence(),
    )

    assert result.image_found is False
    assert result.error == "no_usable_product_images"
    assert "bad_image_hint" in ";".join(result.debug["rejection_reasons"])


def test_default_meta_image_rejected_in_favor_of_gallery():
    html = """
    <html>
      <head><meta property="og:image" content="https://wolfappliance.com/assets/default-meta-image.jpg"></head>
      <body>
        <div class="product-gallery">
          <img alt="Wolf MDD30TS Warming Drawer" src="https://wolfappliance.com/images/mdd30ts-product.jpg" width="900" height="700">
        </div>
      </body>
    </html>
    """

    result = extract_product_page_image(
        html,
        "https://wolfappliance.com/products/mdd30ts",
        _row(),
        page_evidence=_high_page_evidence(),
    )

    assert result.image_found is True
    assert result.image_url == "https://wolfappliance.com/images/mdd30ts-product.jpg"
    assert any("default-meta-image" in reason for reason in result.debug["rejection_reasons"])


def test_verified_product_gallery_allows_generic_non_brand_alt_text():
    html = """
    <html><body>
      <div class="product-media hero">
        <img alt="Stainless steel appliance front view"
             src="https://cdn.wolfappliance.com/products/warming-drawer-front.jpg"
             width="1000"
             height="800">
      </div>
    </body></html>
    """

    result = extract_product_page_image(
        html,
        "https://wolfappliance.com/products/mdd30ts",
        _row(),
        page_evidence=_high_page_evidence(),
    )

    assert result.image_found is True
    assert result.image_url.endswith("warming-drawer-front.jpg")


def test_low_confidence_page_image_not_assigned(monkeypatch, isolated_product_enrichment_caches):
    import src.product_enrichment as pe

    row = {
        **_row(),
        "Dimensions": "",
        "Finish / Color": "",
        "Product Category": "Appliances",
        "Review Required": False,
        "Suggested Action": "",
    }
    html = "<html><body>Wolf MDD30TS 30 Inch Warming Drawer</body></html>"

    monkeypatch.setattr("src.product_resolver.search_product_candidates", lambda *a, **k: [SearchResult(
        title="Wolf MDD30TS 30 Inch Warming Drawer",
        url="https://wolfappliance.com/products/mdd30ts",
        description="Wolf MDD30TS specifications",
        domain_score=90,
    )])
    monkeypatch.setattr("src.product_resolver.httpx.get", lambda url, **kwargs: _resolver_response(url, html))
    monkeypatch.setattr(pe, "extract_product_page_image", lambda *a, **k: ProductPageImageResult(
        image_found=True,
        image_url="https://wolfappliance.com/images/low-confidence.jpg",
        image_source="official_site_html_image",
        confidence="LOW",
        evidence=["html_image", "page_evidence:low"],
        debug={"images_found": 1},
    ))

    updated, error, _ = pe.enrich_row(row)

    assert error is None
    assert updated["selected_image_url"] == "https://wolfappliance.com/images/low-confidence.jpg"
    assert updated["Image URL"] == ""
