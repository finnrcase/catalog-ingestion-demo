from __future__ import annotations

import pytest

from src.brave_search import SearchResult
from src.product_resolver import build_resolver_queries, resolve_product_page
from src.source_success_registry import record_source_success


@pytest.fixture(autouse=True)
def _isolate_source_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("SOURCE_SUCCESS_REGISTRY_PATH", str(tmp_path / "source_success_registry.json"))


def _wolf_row() -> dict:
    return {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "30 Inch Warming Drawer",
    }


def _resp(url: str, text: str, content_type: str = "text/html"):
    class Resp:
        status_code = 200
        headers = {"content-type": content_type}
        content = text.encode("utf-8")

        def __init__(self):
            self.text = text
            self.url = url

    return Resp()


def _wolf_html() -> str:
    return """
    <html>
      <head>
        <script type="application/ld+json">
          {
            "@type":"Product",
            "name":"Wolf 30 Inch Warming Drawer",
            "brand":{"name":"Wolf"},
            "sku":"MDD30TS",
            "image":"https://cdn.wolfappliance.com/images/mdd30ts-product.jpg",
            "additionalProperty":[
              {"name":"Dimensions","value":"29 7/8\\" W x 23 1/2\\" D x 11 7/8\\" H"}
            ]
          }
        </script>
      </head>
      <body>
        <h1>Wolf MDD30TS 30 Inch Warming Drawer</h1>
        <div class="product-gallery">
          <img src="https://cdn.wolfappliance.com/images/mdd30ts-gallery.jpg" alt="Wolf MDD30TS" width="1200" height="900">
        </div>
      </body>
    </html>
    """


def _scotsman_html() -> str:
    return """
    <html><body>
      <h1>Scotsman SCN60PA1SU Ice Machine</h1>
      <script type="application/ld+json">
        {"@type":"Product","name":"Scotsman SCN60PA1SU Ice Machine","brand":{"name":"Scotsman"},"sku":"SCN60PA1SU",
         "image":"https://scotsman-ice.com/images/scn60pa1su.jpg"}
      </script>
      <table class="specs">
        <tr><th>Width</th><td>14.875 in</td></tr>
        <tr><th>Height</th><td>33.375 in</td></tr>
        <tr><th>Depth</th><td>22 in</td></tr>
      </table>
    </body></html>
    """


def _kohler_html() -> str:
    return """
    <html><body>
      <h1>Kohler K-596 Kitchen Faucet</h1>
      <script type="application/ld+json">
        {"@type":"Product","name":"Kohler K-596 Faucet","brand":{"name":"Kohler"},"sku":"K-596",
         "image":"https://kohler.com/images/k-596-product.jpg"}
      </script>
      <dl class="product-specs">
        <dt>Overall Dimensions</dt><dd>Width: 10 in Height: 16 in Depth: 8 in</dd>
      </dl>
      <picture class="product-media">
        <source srcset="/images/k-596-800.jpg 800w, /images/k-596-1400.jpg 1400w">
        <img alt="Kohler K-596 Faucet" src="/images/k-596-fallback.jpg">
      </picture>
    </body></html>
    """


def test_build_resolver_queries_prioritizes_official_domain():
    queries, domains = build_resolver_queries(_wolf_row())

    assert "wolfappliance.com" in domains or "subzero-wolf.com" in domains
    assert any(query.startswith("site:") and '"MDD30TS"' in query for query in queries)
    assert '"Wolf" "MDD30TS" official product page' in queries


def test_build_resolver_queries_uses_successful_source_registry_first():
    row = {"Brand": "Acme", "Model/SKU": "AX100", "Product Category": "Appliances"}
    record_source_success(row, domain="specs.acme.com", url="https://specs.acme.com/ax100", confidence="high")

    queries, domains = build_resolver_queries(row)

    assert domains[0] == "specs.acme.com"
    assert queries[0] == 'site:specs.acme.com "AX100" dimensions'


def test_existing_product_url_extracted_before_search(monkeypatch):
    import src.product_resolver as pr

    row = {
        **_wolf_row(),
        "Product URL": "https://wolfappliance.com/products/mdd30ts",
    }
    monkeypatch.setattr(pr, "search_product_candidates", lambda *a, **k: pytest.fail("search should not run"))
    monkeypatch.setattr(pr.httpx, "get", lambda url, **kwargs: _resp(url, _wolf_html()))

    result = resolve_product_page(row)

    assert result.selected is not None
    assert result.selected.url == row["Product URL"]
    assert result.selected.diagnostics["candidate_origin"] == "existing_product_url"
    assert result.selected.extracted_dimensions == '29.875"W x 23.5"D x 11.875"H'
    assert result.selected.extracted_image_url == "https://cdn.wolfappliance.com/images/mdd30ts-product.jpg"


def test_official_exact_sku_page_produces_high_dimensions_and_image(monkeypatch):
    import src.product_resolver as pr

    official = SearchResult(
        "Wolf MDD30TS 30 Inch Warming Drawer",
        "https://wolfappliance.com/products/mdd30ts",
        "Wolf MDD30TS specifications",
        90,
    )
    retailer = SearchResult(
        "Wolf MDD30TS at Build",
        "https://www.build.com/wolf-mdd30ts/p123",
        "Retail listing",
        70,
    )

    monkeypatch.setattr(pr, "search_product_candidates", lambda *a, **k: [retailer, official])
    monkeypatch.setattr(pr.httpx, "get", lambda url, **kwargs: _resp(url, _wolf_html()))

    result = resolve_product_page(_wolf_row())

    assert result.selected is not None
    assert result.selected.url == official.url
    assert result.confidence == "high"
    assert result.selected.extracted_dimensions == '29.875"W x 23.5"D x 11.875"H'
    assert result.selected.extracted_image_url == "https://cdn.wolfappliance.com/images/mdd30ts-product.jpg"


def test_scotsman_real_like_fixture_extracts_dimensions_and_image(monkeypatch):
    import src.product_resolver as pr

    row = {"Brand": "Scotsman", "Model/SKU": "SCN60PA1SU", "Product Name": "Ice Machine"}
    result_item = SearchResult(
        "Scotsman SCN60PA1SU Ice Machine",
        "https://scotsman-ice.com/products/scn60pa1su",
        "SCN60PA1SU dimensions",
        90,
    )
    monkeypatch.setattr(pr, "search_product_candidates", lambda *a, **k: [result_item])
    monkeypatch.setattr(pr.httpx, "get", lambda url, **kwargs: _resp(url, _scotsman_html()))

    result = resolve_product_page(row)

    assert result.selected is not None
    assert result.selected.confidence == "high"
    assert result.selected.extracted_dimensions == '14.875"W x 22"D x 33.375"H'
    assert result.selected.extracted_image_url == "https://scotsman-ice.com/images/scn60pa1su.jpg"


def test_kohler_fixture_extracts_spec_table_and_picture_image(monkeypatch):
    import src.product_resolver as pr

    row = {"Brand": "Kohler", "Model/SKU": "K-596", "Product Name": "Kitchen Faucet"}
    result_item = SearchResult("Kohler K-596 Faucet", "https://kohler.com/products/k-596", "K-596 specs", 90)
    monkeypatch.setattr(pr, "search_product_candidates", lambda *a, **k: [result_item])
    monkeypatch.setattr(pr.httpx, "get", lambda url, **kwargs: _resp(url, _kohler_html()))

    result = resolve_product_page(row)

    assert result.selected is not None
    assert result.selected.extracted_dimensions == '10"W x 8"D x 16"H'
    assert result.selected.extracted_image_url in {
        "https://kohler.com/images/k-596-product.jpg",
        "https://kohler.com/images/k-596-1400.jpg",
    }


def test_retailer_fallback_is_medium_max(monkeypatch):
    import src.product_resolver as pr

    retailer = SearchResult(
        "Wolf MDD30TS 30 Inch Warming Drawer",
        "https://www.build.com/wolf-mdd30ts/p123",
        "Wolf MDD30TS dimensions",
        70,
    )
    monkeypatch.setattr(pr, "search_product_candidates", lambda *a, **k: [retailer])
    monkeypatch.setattr(pr.httpx, "get", lambda url, **kwargs: _resp(url, _wolf_html()))

    result = resolve_product_page(_wolf_row())

    assert result.selected is not None
    assert result.selected.source_type == "retailer_page"
    assert result.selected.confidence == "medium"


def test_wrong_product_same_brand_rejected(monkeypatch):
    import src.product_resolver as pr

    wrong = SearchResult(
        "Wolf Warming Drawer",
        "https://wolfappliance.com/products/other-warming-drawer",
        "Wolf product family",
        90,
    )
    wrong_html = _wolf_html().replace("MDD30TS", "OTHER999").replace("mdd30ts", "other999")
    monkeypatch.setattr(pr, "search_product_candidates", lambda *a, **k: [wrong])
    monkeypatch.setattr(pr.httpx, "get", lambda url, **kwargs: _resp(url, wrong_html))

    result = resolve_product_page(_wolf_row())

    assert result.selected is None
    assert result.diagnostics[0]["rejection_reason"] == "sku_not_found"
    assert result.diagnostics[0]["confidence"] in {"low", "none"}
