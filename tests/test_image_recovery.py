"""Tests for src/image_recovery.py — Phase 1 confidence-gated image recovery."""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from src.image_recovery import (
    ImageRecoveryResult,
    recover_from_url,
    recover_from_pdf_crop,
)


def _jpeg_bytes(size: tuple[int, int] = (200, 200), color: str = "red") -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _mock_get(content: bytes, content_type: str = "image/jpeg"):
    resp = MagicMock()
    resp.content = content
    resp.headers = {"content-type": content_type}
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    return resp


# ── Dataclass shape ───────────────────────────────────────────────────────────

def test_result_defaults_are_safe():
    r = ImageRecoveryResult()
    assert r.image_source == "none"
    assert r.confidence == "NONE"
    assert r.evidence == []
    assert r.needs_image_review is True  # only HIGH bypasses review
    assert r.image_url == ""
    assert r.local_image_filename == ""
    assert r.local_image_path == ""
    assert r.jpeg_bytes == b""
    assert r.error == ""


def test_result_high_confidence_does_not_need_review():
    r = ImageRecoveryResult(confidence="HIGH")
    assert r.needs_image_review is False


def test_result_medium_needs_review():
    r = ImageRecoveryResult(confidence="MEDIUM")
    assert r.needs_image_review is True


def test_result_low_needs_review():
    r = ImageRecoveryResult(confidence="LOW")
    assert r.needs_image_review is True


# ── recover_from_url: input gating ────────────────────────────────────────────

def test_url_recover_no_image_url_returns_none():
    row = {"Image URL": "", "Brand": "Wolf", "Model/SKU": "MDD30TS"}
    result = recover_from_url(row)
    assert result.confidence == "NONE"
    assert result.image_source == "none"


def test_url_recover_invalid_content_type_returns_none():
    row = {
        "Image URL": "https://example.com/x.jpg",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
    }
    with patch("src.image_recovery._check_image_content_type", return_value=False):
        result = recover_from_url(row)
    assert result.confidence == "NONE"


# ── recover_from_url: confidence ──────────────────────────────────────────────

def test_url_recover_high_when_sku_in_url_path():
    row = {
        "Image URL": "https://www.subzero-wolf.com/img/MDD30TS_hero.jpg",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "30 Inch Warming Drawer",
    }
    with patch("src.image_recovery._check_image_content_type", return_value=True), \
         patch("src.image_recovery.httpx.get", return_value=_mock_get(_jpeg_bytes())):
        result = recover_from_url(row)
    assert result.confidence == "HIGH"
    assert result.image_source == "url"
    assert "sku_in_image_url" in result.evidence
    assert result.image_url == "https://www.subzero-wolf.com/img/MDD30TS_hero.jpg"
    assert len(result.jpeg_bytes) > 0


def test_url_recover_medium_on_official_domain_no_sku():
    row = {
        "Image URL": "https://www.subzero-wolf.com/img/generic-hero.jpg",
        "Brand": "Wolf",
        "Model/SKU": "ZZZ999",  # not in URL or in body
        "Product Name": "Some Product",
    }
    with patch("src.image_recovery._check_image_content_type", return_value=True), \
         patch("src.image_recovery.httpx.get", return_value=_mock_get(_jpeg_bytes())):
        result = recover_from_url(row)
    assert result.confidence == "MEDIUM"
    assert "official_domain" in result.evidence


def test_url_recover_low_on_unknown_domain_no_sku():
    row = {
        "Image URL": "https://random-cdn.com/img/x.jpg",
        "Brand": "Wolf",
        "Model/SKU": "ZZZ999",
        "Product Name": "Some Product",
    }
    with patch("src.image_recovery._check_image_content_type", return_value=True), \
         patch("src.image_recovery.httpx.get", return_value=_mock_get(_jpeg_bytes())):
        result = recover_from_url(row)
    assert result.confidence == "LOW"


# ── recover_from_pdf_crop ─────────────────────────────────────────────────────

import fitz  # PyMuPDF


def _make_pdf_with_image(
    tmp_path,
    text: str,
    image_size: tuple[int, int] = (400, 400),
) -> str:
    """Build a synthetic PDF: page 1 has `text` and an embedded image of `image_size`."""
    pdf_path = tmp_path / "test.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_text((50, 50), text)

    img = Image.new("RGB", image_size, "blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    rect = fitz.Rect(100, 100, 100 + image_size[0] / 2, 100 + image_size[1] / 2)
    page.insert_image(rect, stream=buf.getvalue())

    doc.save(str(pdf_path))
    doc.close()
    return str(pdf_path)


def test_pdf_crop_high_when_sku_on_same_page(tmp_path):
    pdf_path = _make_pdf_with_image(tmp_path, "Wolf MDD30TS Warming Drawer Specifications")
    row = {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "Warming Drawer",
        "_source_page_number": 1,
    }
    result = recover_from_pdf_crop(row, pdf_path)
    assert result.image_source == "pdf_crop"
    assert result.confidence == "HIGH"
    assert "sku_on_pdf_page" in result.evidence
    assert len(result.jpeg_bytes) > 0


def test_pdf_crop_medium_when_no_sku_evidence(tmp_path):
    pdf_path = _make_pdf_with_image(tmp_path, "Generic catalog page with no SKU")
    row = {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "Warming Drawer",
        "_source_page_number": 1,
    }
    result = recover_from_pdf_crop(row, pdf_path)
    assert result.confidence == "MEDIUM"


def test_pdf_crop_falls_back_to_adjacent_page_capped_at_medium(tmp_path):
    # Page 1 has no image; page 2 has the image. Row says it came from page 1.
    pdf_path = tmp_path / "test.pdf"
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 50), "Wolf MDD30TS")  # SKU is here, not where the image is
    p2 = doc.new_page(width=595, height=842)
    img = Image.new("RGB", (400, 400), "blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    p2.insert_image(fitz.Rect(100, 100, 300, 300), stream=buf.getvalue())
    doc.save(str(pdf_path))
    doc.close()

    row = {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "Warming Drawer",
        "_source_page_number": 1,
    }
    result = recover_from_pdf_crop(row, str(pdf_path))
    # Even if SKU appears on the original page, the crop is from an adjacent
    # page so confidence is capped at MEDIUM.
    assert result.confidence == "MEDIUM"
    assert "adjacent_page_crop" in result.evidence


def test_pdf_crop_none_when_no_images_anywhere(tmp_path):
    pdf_path = tmp_path / "test.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "Wolf MDD30TS no images here")
    doc.save(str(pdf_path))
    doc.close()
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "_source_page_number": 1}
    result = recover_from_pdf_crop(row, str(pdf_path))
    assert result.confidence == "NONE"


def test_pdf_crop_none_when_pdf_unreadable(tmp_path):
    pdf_path = tmp_path / "missing.pdf"
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "_source_page_number": 1}
    result = recover_from_pdf_crop(row, str(pdf_path))
    assert result.confidence == "NONE"
    assert result.error.startswith("pdf_unreadable")


def test_pdf_crop_filters_tiny_icons(tmp_path):
    pdf_path = tmp_path / "test.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 50), "Wolf MDD30TS")
    # Insert a tiny 50x50 image only — should be filtered as an icon.
    img = Image.new("RGB", (50, 50), "blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    page.insert_image(fitz.Rect(100, 100, 110, 110), stream=buf.getvalue())  # ~10x10pt rect
    doc.save(str(pdf_path))
    doc.close()
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "_source_page_number": 1}
    result = recover_from_pdf_crop(row, str(pdf_path))
    assert result.confidence == "NONE"


def test_pdf_crop_filters_extreme_aspect_ratio(tmp_path):
    """Aspect ratios outside [1:4, 4:1] are rejected as banners/dividers."""
    pdf_path = tmp_path / "test.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 50), "Wolf MDD30TS")
    # 800x50 banner = 16:1 aspect, well outside the [1:4, 4:1] bounds.
    img = Image.new("RGB", (800, 50), "red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    page.insert_image(fitz.Rect(50, 100, 550, 130), stream=buf.getvalue())  # ~17:1
    doc.save(str(pdf_path))
    doc.close()

    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "_source_page_number": 1}
    result = recover_from_pdf_crop(row, str(pdf_path))
    assert result.confidence == "NONE"


def test_pdf_crop_none_when_pdf_body_is_corrupt(tmp_path):
    """A file that exists but isn't a valid PDF should hit the fitz.open exception path."""
    pdf_path = tmp_path / "corrupt.pdf"
    pdf_path.write_bytes(b"not a real pdf")
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "_source_page_number": 1}
    result = recover_from_pdf_crop(row, str(pdf_path))
    assert result.confidence == "NONE"
    assert result.error.startswith("pdf_unreadable")


# ── recover_from_screenshot ───────────────────────────────────────────────────

from src.image_recovery import recover_from_screenshot


@pytest.fixture
def mock_playwright():
    """Mock Playwright sync_api context manager. Tests configure .page behavior."""
    with patch("src.image_recovery.sync_playwright") as mock_sp:
        cm = MagicMock()
        pw = MagicMock()
        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        cm.__enter__.return_value = pw
        cm.__exit__.return_value = False
        mock_sp.return_value = cm
        pw.chromium.launch.return_value = browser
        browser.new_context.return_value = context
        context.new_page.return_value = page
        # Default: page renders empty content
        page.content.return_value = "<html><body></body></html>"
        yield page


def test_screenshot_high_when_element_selector_hits_with_sku(mock_playwright):
    page = mock_playwright
    page.content.return_value = "<html><body>Wolf MDD30TS warming drawer</body></html>"
    locator = MagicMock()
    locator.count.return_value = 1
    locator.first.bounding_box.return_value = {"x": 0, "y": 0, "width": 600, "height": 600}
    locator.first.is_visible.return_value = True
    locator.first.screenshot.return_value = _jpeg_bytes()
    page.locator.return_value = locator

    row = {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "Warming Drawer",
    }
    result = recover_from_screenshot(row, "https://www.subzero-wolf.com/wolf/warming-drawer")
    assert result.image_source == "page_screenshot"
    assert result.confidence == "HIGH"
    assert "sku_on_page" in result.evidence
    assert len(result.jpeg_bytes) > 0


def test_screenshot_medium_when_official_domain_no_sku(mock_playwright):
    page = mock_playwright
    page.content.return_value = "<html><body>Some unrelated text</body></html>"
    locator = MagicMock()
    locator.count.return_value = 1
    locator.first.bounding_box.return_value = {"x": 0, "y": 0, "width": 600, "height": 600}
    locator.first.is_visible.return_value = True
    locator.first.screenshot.return_value = _jpeg_bytes()
    page.locator.return_value = locator

    row = {"Brand": "Wolf", "Model/SKU": "ZZZ999", "Product Name": "Some Product"}
    result = recover_from_screenshot(row, "https://www.subzero-wolf.com/some-page")
    assert result.confidence == "MEDIUM"
    assert "official_domain" in result.evidence


def test_screenshot_low_when_unknown_domain_no_sku(mock_playwright):
    page = mock_playwright
    page.content.return_value = "<html><body>random page</body></html>"
    locator = MagicMock()
    locator.count.return_value = 1
    locator.first.bounding_box.return_value = {"x": 0, "y": 0, "width": 600, "height": 600}
    locator.first.is_visible.return_value = True
    locator.first.screenshot.return_value = _jpeg_bytes()
    page.locator.return_value = locator

    row = {"Brand": "Wolf", "Model/SKU": "ZZZ999", "Product Name": "Some Product"}
    result = recover_from_screenshot(row, "https://random-site.com/x")
    assert result.confidence == "LOW"


def test_screenshot_falls_back_to_bbox_crop_when_no_selector_matches(mock_playwright):
    page = mock_playwright
    page.content.return_value = "<html><body>Wolf MDD30TS</body></html>"
    no_match_locator = MagicMock()
    no_match_locator.count.return_value = 0
    page.locator.return_value = no_match_locator

    # Full-page screenshot returns valid PNG bytes (a 1200×1200 plain image).
    img = Image.new("RGB", (1200, 1200), "red")
    full_buf = io.BytesIO()
    img.save(full_buf, format="PNG")
    page.screenshot.return_value = full_buf.getvalue()

    # eval_on_selector_all returns one good <img> bbox candidate.
    page.eval_on_selector_all.return_value = [
        {
            "src": "https://example.com/hero.jpg",
            "x": 100, "y": 100, "width": 600, "height": 600,
        }
    ]
    page.viewport_size = {"width": 1280, "height": 800}

    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Product Name": "Warming Drawer"}
    result = recover_from_screenshot(row, "https://www.subzero-wolf.com/x")
    assert result.image_source == "page_screenshot"
    assert result.confidence == "HIGH"
    assert "bbox_crop" in result.evidence
    assert len(result.jpeg_bytes) > 0


def test_screenshot_filters_logo_url(mock_playwright):
    page = mock_playwright
    page.content.return_value = "<html><body>Wolf MDD30TS</body></html>"
    no_match_locator = MagicMock()
    no_match_locator.count.return_value = 0
    page.locator.return_value = no_match_locator
    img = Image.new("RGB", (1200, 1200), "red")
    full_buf = io.BytesIO()
    img.save(full_buf, format="PNG")
    page.screenshot.return_value = full_buf.getvalue()
    page.eval_on_selector_all.return_value = [
        # logo URL → filtered
        {"src": "https://example.com/header-logo.png", "x": 0, "y": 0, "width": 800, "height": 200},
        # tiny image → filtered
        {"src": "https://example.com/icon.png", "x": 50, "y": 50, "width": 80, "height": 80},
    ]
    page.viewport_size = {"width": 1280, "height": 800}

    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS"}
    result = recover_from_screenshot(row, "https://www.subzero-wolf.com/x")
    # All candidates filtered; no usable image found.
    assert result.confidence == "NONE"
    assert result.error == "no_usable_image_element"


def test_screenshot_returns_none_on_browser_unavailable():
    with patch("src.image_recovery.sync_playwright", side_effect=Exception("browser fail")):
        result = recover_from_screenshot({"Brand": "Wolf"}, "https://example.com")
    assert result.confidence == "NONE"
    assert result.error == "browser_unavailable"


def test_screenshot_returns_none_on_page_load_timeout(mock_playwright):
    page = mock_playwright
    page.goto.side_effect = Exception("Timeout 15000ms exceeded")
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS"}
    result = recover_from_screenshot(row, "https://www.subzero-wolf.com/x")
    assert result.confidence == "NONE"
    assert result.error == "page_load_timeout"
