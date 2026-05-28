from __future__ import annotations

import pytest

from src.spec_extraction import (
    extract_dimensions_from_html,
    extract_dimensions_from_pdf_bytes,
    extract_specs_from_verified_candidate,
)
from src.product_page_specs import extract_product_page_specs


def test_extract_dimensions_from_jsonld_product_fields():
    html = """
    <script type="application/ld+json">
    {
      "@type": "Product",
      "name": "Wolf MDD30TS",
      "sku": "MDD30TS",
      "additionalProperty": [
        {"name": "Dimensions", "value": "29 7/8\\" W x 23 1/2\\" D x 11 7/8\\" H"}
      ]
    }
    </script>
    """

    result = extract_dimensions_from_html(html, {"Brand": "Wolf", "Model/SKU": "MDD30TS"})

    assert result.dimensions == '29.875"W x 23.5"D x 11.875"H'
    assert result.confidence == "high"


def test_extract_dimensions_from_jsonld_quantitative_values():
    html = """
    <script type="application/ld+json">
    {
      "@type": "Product",
      "name": "Sub-Zero ID36R",
      "sku": "ID36R",
      "width": {"@type": "QuantitativeValue", "value": "36", "unitCode": "INH"},
      "height": {"@type": "QuantitativeValue", "value": "84", "unitText": "in"},
      "depth": {"@type": "QuantitativeValue", "value": "24", "unitText": "in"}
    }
    </script>
    """

    result = extract_dimensions_from_html(html, {"Brand": "Sub-Zero", "Model/SKU": "ID36R"})
    specs = extract_product_page_specs(
        html,
        "https://subzero-wolf.com/products/id36r",
        {"Brand": "Sub-Zero", "Model/SKU": "ID36R"},
        official_domain=True,
        sku_match=True,
    )

    assert result.dimensions == '36"W x 24"D x 84"H'
    assert specs.dimensions == '36"W x 24"D x 84"H'


def test_extract_separate_width_height_depth_rows_from_table():
    html = """
    <table class="specifications">
      <tr><th>Width</th><td>30 in</td></tr>
      <tr><th>Height</th><td>12 in</td></tr>
      <tr><th>Depth</th><td>24 in</td></tr>
    </table>
    """

    result = extract_dimensions_from_html(html, {"Brand": "Scotsman", "Model/SKU": "SCN60PA1SU"})

    assert result.dimensions == '30"W x 24"D x 12"H'
    assert result.width == "30"
    assert result.depth == "24"
    assert result.height == "12"


def test_extract_w_h_d_axis_first_format():
    html = "<div class='product-specs'>Appliance Dimensions: W 29.875 x H 11.875 x D 23.5</div>"

    result = extract_dimensions_from_html(html, {"Brand": "Wolf", "Model/SKU": "MDD30TS"})

    assert result.dimensions == '29.875"W x 23.5"D x 11.875"H'


def test_extract_h_w_d_ordered_measurements():
    html = """
    <section class="measurements">
      <h2>Measurements</h2>
      <p>H x W x D: 12 x 30 x 18 in</p>
    </section>
    """

    result = extract_dimensions_from_html(html, {"Brand": "Visual Comfort", "Model/SKU": "KW123"})

    assert result.dimensions == '30"W x 18"D x 12"H'
    assert result.width == "30"
    assert result.depth == "18"
    assert result.height == "12"
    assert result.unit == "in"
    assert result.confidence_score == 90


def test_extract_dimensions_from_meta_product_schema():
    html = """
    <meta itemprop="width" content="762 mm">
    <meta itemprop="depth" content="457.2 mm">
    <meta itemprop="height" content="304.8 mm">
    """

    result = extract_dimensions_from_html(html, {"Brand": "Wolf", "Model/SKU": "MDD30TS"})

    assert result.dimensions == '30"W x 18"D x 12"H'


def test_extract_dimensions_and_metadata_from_next_data_product_json():
    html = """
    <html>
      <script id="__NEXT_DATA__" type="application/json">
      {
        "props": {
          "pageProps": {
            "product": {
              "title": "Wolf MDD30TS Drawer Microwave",
              "vendor": "Wolf",
              "sku": "MDD30TS",
              "dimensions": "Width: 30 in Height: 12 in Depth: 24 in",
              "material": "Stainless Steel",
              "finish": "Stainless"
            }
          }
        }
      }
      </script>
      <body>Wolf MDD30TS drawer microwave</body>
    </html>
    """

    result = extract_dimensions_from_html(html, {"Brand": "Wolf", "Model/SKU": "MDD30TS"})
    specs = extract_product_page_specs(
        html,
        "https://wolfappliance.com/products/mdd30ts",
        {"Brand": "Wolf", "Model/SKU": "MDD30TS"},
        official_domain=True,
        sku_match=True,
    )

    assert result.dimensions == '30"W x 24"D x 12"H'
    assert specs.dimensions == '30"W x 24"D x 12"H'
    assert specs.material == "Stainless Steel"
    assert specs.finish == "Stainless"
    assert specs.debug["embedded_product_json_found"] is True


def test_impossible_dimensions_are_rejected():
    html = "<table><tr><th>Dimensions</th><td>Width: 9999 in Height: 12 in Depth: 24 in</td></tr></table>"

    result = extract_dimensions_from_html(html, {"Brand": "Acme", "Model/SKU": "BAD999"})

    assert result.dimensions == ""
    assert result.confidence == "none"
    assert result.diagnostics["failure_reason"] == "complete_w_h_d_not_found"


def test_shipping_dimensions_are_medium_fallback_only():
    html = """
    <dl>
      <dt>Shipping Dimensions</dt>
      <dd>Width: 40 in Height: 20 in Depth: 30 in</dd>
    </dl>
    """

    result = extract_dimensions_from_html(html, {"Brand": "Kohler", "Model/SKU": "K-123"})

    assert result.dimensions == ""
    assert result.confidence == "none"
    assert result.shipping_dimensions == '40"W x 30"D x 20"H'
    assert result.shipping_width == "40"
    assert result.shipping_depth == "30"
    assert result.shipping_height == "20"
    assert result.diagnostics["shipping_rejected"] is True


def test_cutout_dimensions_are_reported_separately():
    html = """
    <table>
      <tr><th>Overall Dimensions</th><td>30"W x 24"D x 12"H</td></tr>
      <tr><th>Cutout Dimensions</th><td>28"W x 22"D x 10"H</td></tr>
    </table>
    """

    result = extract_dimensions_from_html(html, {"Brand": "Wolf", "Model/SKU": "MDD30TS"})

    assert result.dimensions == '30"W x 24"D x 12"H'
    assert result.cutout_dimensions == '28"W x 22"D x 10"H'
    assert result.cutout_width == "28"
    assert result.cutout_depth == "22"
    assert result.cutout_height == "10"


def test_extract_two_product_dimensions_as_medium_partial():
    html = """
    <table class="specifications">
      <tr><th>Width</th><td>30 in</td></tr>
      <tr><th>Depth</th><td>24 in</td></tr>
    </table>
    """

    result = extract_dimensions_from_html(html, {"Brand": "Wolf", "Model/SKU": "MDD30TS"})

    assert result.dimensions == '30"W x 24"D'
    assert result.width == "30"
    assert result.depth == "24"
    assert result.height == ""
    assert result.confidence == "medium"


def test_extract_one_product_dimension_as_low_partial():
    html = '<section class="product-specs"><p>Overall Width: 30 in</p></section>'

    result = extract_dimensions_from_html(html, {"Brand": "Wolf", "Model/SKU": "MDD30TS"})

    assert result.dimensions == '30"W'
    assert result.width == "30"
    assert result.confidence == "low"


def test_extract_dimensions_from_pdf_bytes():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), 'Wolf MDD30TS Product Dimensions: Width: 30 in Height: 12 in Depth: 24 in')
    pdf_bytes = doc.tobytes()
    doc.close()

    result = extract_dimensions_from_pdf_bytes(pdf_bytes, {"Brand": "Wolf", "Model/SKU": "MDD30TS"})

    assert result.dimensions == '30"W x 24"D x 12"H'
    assert result.source_type == "pdf"


def test_extract_specs_from_verified_candidate_html():
    class Candidate:
        html = """
        <h1>Kallista One Faucet</h1>
        <script type="application/ld+json">
          {"@type":"Product","name":"Kallista One Faucet","brand":{"name":"Kallista"},"sku":"P123","material":"Brass"}
        </script>
        <table><tr><th>Dimensions</th><td>Width: 8 in Height: 10 in Depth: 6 in</td></tr></table>
        """
        text = ""
        pdf_bytes = b""
        source_type = "manufacturer_page"

    fields = extract_specs_from_verified_candidate(Candidate(), {"Brand": "Kallista", "Model/SKU": "P123"})

    assert fields["Product Name"] == "Kallista One Faucet"
    assert fields["Brand"] == "Kallista"
    assert fields["Model/SKU"] == "P123"
    assert fields["Material"] == "Brass"
    assert fields["Dimensions"] == '8"W x 6"D x 10"H'
