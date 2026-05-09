"""Tests for src/image_recovery.py — Phase 1 confidence-gated image recovery."""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

from PIL import Image

from src.image_recovery import (
    ImageRecoveryResult,
    recover_from_url,
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
