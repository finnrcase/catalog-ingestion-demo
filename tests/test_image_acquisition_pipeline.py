from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pandas as pd
from PIL import Image

from src.image_acquisition import (
    IMAGE_RECOVERY_DEBUG_COLUMNS,
    image_recovery_debug_row,
    recover_product_image,
)
from src.product_page_images import extract_product_page_image
from src.web_product_lookup import build_image_lookup_queries, lookup_official_product_image


def _jpeg_bytes() -> bytes:
    img = Image.new("RGB", (300, 300), "blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _html_response(html: str, status_code: int = 200):
    response = MagicMock()
    response.text = html
    response.status_code = status_code
    return response


def _row(**overrides) -> dict:
    base = {
        "Product Name": "Wolf Warming Drawer",
        "Brand": "Wolf",
        "Model/SKU": "WWD30",
        "Supplier": "Wolf",
        "Product URL": "https://wolfappliance.com/product/wwd30",
    }
    return {**base, **overrides}


def test_product_url_with_og_image():
    html = """
    <html><head><meta property="og:image" content="https://wolfappliance.com/images/wwd30.jpg"></head>
    <body>Wolf WWD30 Warming Drawer</body></html>
    """
    with patch("src.product_page_images.httpx.get", return_value=_html_response(html)), \
         patch("src.image_acquisition._download_jpeg_bytes", return_value=_jpeg_bytes()):
        result = recover_product_image(_row())
    assert result.image_found is True
    assert result.image_source == "product_url_og_image"
    assert result.confidence == "HIGH"
    assert result.image_url == "https://wolfappliance.com/images/wwd30.jpg"


def test_product_url_with_jsonld_image():
    data = {"@type": "Product", "name": "Wolf Warming Drawer", "sku": "WWD30", "image": "https://wolfappliance.com/i/wwd30.jpg"}
    html = f"<script type='application/ld+json'>{json.dumps(data)}</script><body>Wolf WWD30</body>"
    result = extract_product_page_image(html, "https://wolfappliance.com/product/wwd30", _row())
    assert result.image_found is True
    assert result.image_source == "product_url_jsonld_image"
    assert result.confidence == "HIGH"


def test_product_url_with_gallery_img_tags():
    html = """
    <body>Wolf WWD30
      <img src="https://wolfappliance.com/logo.png" width="400" height="120">
      <img class="product-gallery" src="https://wolfappliance.com/images/wwd30-gallery.jpg" width="900" height="900">
    </body>
    """
    result = extract_product_page_image(html, "https://wolfappliance.com/product/wwd30", _row())
    assert result.image_found is True
    assert result.image_source == "product_url_html_image"
    assert result.image_url.endswith("wwd30-gallery.jpg")


def test_brand_sku_official_lookup_success():
    from src.brave_search import SearchResult

    html = '<meta property="og:image" content="https://wolfappliance.com/images/wwd30.jpg"><body>Wolf WWD30</body>'
    search_result = SearchResult(
        title="Wolf WWD30 Warming Drawer",
        url="https://wolfappliance.com/product/wwd30",
        description="Official Wolf product page",
        domain_score=90,
    )
    with patch("src.web_product_lookup.search_product_candidates", return_value=[search_result]), \
         patch("src.product_page_images.httpx.get", return_value=_html_response(html)):
        result = lookup_official_product_image(_row())
    assert result.image_result.image_found is True
    assert result.image_result.image_source == "official_site_og_image"
    assert result.debug["selected_product_page_url"] == "https://wolfappliance.com/product/wwd30"


def test_lookup_queries_follow_required_order():
    queries = build_image_lookup_queries(_row(Supplier="Ferguson"))
    assert queries[0] == '"Wolf" "WWD30" product'
    assert queries[1] == '"Wolf" "Wolf Warming Drawer" "WWD30"'
    assert queries[2] == '"Ferguson" "Wolf Warming Drawer" "WWD30"'
    assert queries[3] == '"Wolf" "Wolf Warming Drawer" dimensions image'


def test_pdf_with_embedded_image(tmp_path):
    import fitz

    pdf_path = tmp_path / "product.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 50), "Wolf WWD30 Warming Drawer")
    img = Image.new("RGB", (400, 400), "red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    page.insert_image(fitz.Rect(100, 100, 320, 320), stream=buf.getvalue())
    doc.save(str(pdf_path))
    doc.close()

    with patch("src.web_product_lookup.search_product_candidates", return_value=[]):
        result = recover_product_image(_row(**{"Product URL": ""}), source_pdf_path=pdf_path, page_number=1)
    assert result.image_found is True
    assert result.image_source == "pdf_embedded_image"
    assert result.confidence == "HIGH"


def test_pdf_with_no_embedded_image_uses_render_fallback(tmp_path):
    import fitz

    pdf_path = tmp_path / "render.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(150, 200, 450, 600), color=(0, 0, 0), fill=(0, 0, 0))
    doc.save(str(pdf_path))
    doc.close()

    with patch("src.web_product_lookup.search_product_candidates", return_value=[]):
        result = recover_product_image(_row(**{"Product URL": ""}), source_pdf_path=pdf_path, page_number=1)
    assert result.image_found is True
    assert result.image_source in {"pdf_page_render_content_crop", "pdf_page_render_full"}
    assert result.needs_image_review is True


def test_missing_image_remains_missing_with_debug_reason():
    with patch("src.product_page_images.httpx.get", return_value=_html_response("<html>No images</html>")), \
         patch("src.web_product_lookup.search_product_candidates", return_value=[]):
        result = recover_product_image(_row())
    assert result.image_found is False
    assert result.confidence == "NONE"
    assert result.debug["product_url_fetch_ran"] is True
    assert result.debug["final_error"]


def test_debug_csv_includes_required_columns():
    result = recover_product_image(_row(**{"Product URL": ""}), config={"use_web_lookup": False})
    row = image_recovery_debug_row(_row(), result)
    df = pd.DataFrame([row], columns=IMAGE_RECOVERY_DEBUG_COLUMNS)
    assert list(df.columns) == IMAGE_RECOVERY_DEBUG_COLUMNS
