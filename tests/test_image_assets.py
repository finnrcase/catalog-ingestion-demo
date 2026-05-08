"""Tests for src/image_assets.py — download, convert, ZIP export."""
from __future__ import annotations

import io
import zipfile

import pytest
from PIL import Image
from unittest.mock import MagicMock, patch


def _make_image_bytes(fmt: str = "JPEG", size: tuple = (100, 100), color: str = "red") -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# ── build_image_filename ──────────────────────────────────────────────────────

from src.image_assets import build_image_filename


def test_build_filename_brand_and_sku():
    assert build_image_filename("Wolf", "MDD30TS") == "wolf_mdd30ts.jpg"


def test_build_filename_normalises_spaces():
    assert build_image_filename("Sub Zero", "ID 36R") == "sub_zero_id_36r.jpg"


def test_build_filename_strips_special_chars():
    assert build_image_filename("Miele", "CVA-7440 CTS!") == "miele_cva_7440_cts.jpg"


def test_build_filename_falls_back_to_product_name():
    assert build_image_filename(product_name="Handmade Bowl") == "handmade_bowl.jpg"


def test_build_filename_all_empty_defaults_to_product():
    assert build_image_filename() == "product.jpg"


# ── download_and_convert_image ────────────────────────────────────────────────

from src.image_assets import download_and_convert_image


def _mock_get(content: bytes, content_type: str = "image/jpeg"):
    resp = MagicMock()
    resp.content = content
    resp.headers = {"content-type": content_type}
    resp.raise_for_status = MagicMock()
    return resp


def test_download_jpeg_returns_downloaded_status():
    jpeg = _make_image_bytes("JPEG")
    with patch("src.image_assets.httpx.get", return_value=_mock_get(jpeg)):
        result = download_and_convert_image("https://example.com/photo.jpg", brand="Wolf", model_sku="MDD30TS")
    assert result["image_status"] == "downloaded"
    assert result["local_image_filename"] == "wolf_mdd30ts.jpg"
    assert result["original_image_url"] == "https://example.com/photo.jpg"
    assert result["error"] == ""
    assert len(result["jpeg_bytes"]) > 0


def test_download_png_converts_to_jpeg():
    png = _make_image_bytes("PNG")
    with patch("src.image_assets.httpx.get", return_value=_mock_get(png, "image/png")):
        result = download_and_convert_image("https://example.com/photo.png", brand="Wolf", model_sku="MDD30TS")
    assert result["image_status"] == "downloaded"
    # Verify output is valid JPEG
    img = Image.open(io.BytesIO(result["jpeg_bytes"]))
    assert img.format == "JPEG"


def test_download_webp_converts_to_jpeg():
    webp = _make_image_bytes("WEBP")
    with patch("src.image_assets.httpx.get", return_value=_mock_get(webp, "image/webp")):
        result = download_and_convert_image("https://example.com/photo.webp", brand="Miele", model_sku="CVA7440")
    assert result["image_status"] == "downloaded"
    assert result["local_image_filename"] == "miele_cva7440.jpg"
    img = Image.open(io.BytesIO(result["jpeg_bytes"]))
    assert img.format == "JPEG"


def test_download_rgba_image_converts_to_rgb_jpeg():
    """RGBA (transparent PNG) must be converted to RGB before saving as JPEG."""
    img = Image.new("RGBA", (50, 50), (255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    rgba_bytes = buf.getvalue()
    with patch("src.image_assets.httpx.get", return_value=_mock_get(rgba_bytes, "image/png")):
        result = download_and_convert_image("https://example.com/photo.png", brand="Wolf")
    assert result["image_status"] == "downloaded"
    out_img = Image.open(io.BytesIO(result["jpeg_bytes"]))
    assert out_img.mode == "RGB"


def test_download_rejects_html_content_type():
    with patch("src.image_assets.httpx.get", return_value=_mock_get(b"<html>", "text/html")):
        result = download_and_convert_image("https://example.com/product.html")
    assert result["image_status"] == "invalid_content_type"
    assert result["jpeg_bytes"] == b""
    assert "text/html" in result["error"]


def test_download_fetch_error_returns_fetch_error_status():
    with patch("src.image_assets.httpx.get", side_effect=Exception("connection timeout")):
        result = download_and_convert_image("https://example.com/photo.jpg")
    assert result["image_status"] == "fetch_error"
    assert "connection timeout" in result["error"]
    assert result["jpeg_bytes"] == b""


def test_download_empty_url_returns_skipped():
    result = download_and_convert_image("")
    assert result["image_status"] == "skipped"
    assert result["jpeg_bytes"] == b""


def test_download_saves_to_disk(tmp_path):
    jpeg = _make_image_bytes("JPEG")
    with patch("src.image_assets.httpx.get", return_value=_mock_get(jpeg)):
        result = download_and_convert_image(
            "https://example.com/photo.jpg",
            brand="Wolf", model_sku="MDD30TS",
            output_dir=tmp_path,
        )
    assert result["image_status"] == "downloaded"
    saved = tmp_path / "wolf_mdd30ts.jpg"
    assert saved.exists()
    assert result["local_image_path"] == str(saved)


# ── ZIP export ────────────────────────────────────────────────────────────────

from src.programa_export import export_programa_zip


def _make_exportable_row(**overrides) -> dict:
    base = {
        "Include": True,
        "Product Name": "Wolf Warming Drawer",
        "Product Category": "Appliances",
        "Brand": "Wolf",
        "Model/SKU": "WWD30",
        "Dimensions": "29.875 in W x 10.375 in H x 22.75 in D",
        "Product URL": "https://wolfappliance.com/wwd30",
        "Image URL": "https://wolfappliance.com/img/wwd30.jpg",
        "Quantity": 1,
    }
    return {**base, **overrides}


def test_zip_contains_programa_csv():
    rows = [_make_exportable_row()]
    with patch("src.image_assets.httpx.get", return_value=_mock_get(_make_image_bytes())):
        zip_bytes = export_programa_zip(rows)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert "programa_import.csv" in zf.namelist()


def test_zip_contains_manifest():
    rows = [_make_exportable_row()]
    with patch("src.image_assets.httpx.get", return_value=_mock_get(_make_image_bytes())):
        zip_bytes = export_programa_zip(rows)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert "manifest.csv" in zf.namelist()
        manifest_text = zf.read("manifest.csv").decode("utf-8")
    assert "Wolf Warming Drawer" in manifest_text
    assert "wolf_wwd30.jpg" in manifest_text


def test_zip_contains_downloaded_image():
    rows = [_make_exportable_row()]
    with patch("src.image_assets.httpx.get", return_value=_mock_get(_make_image_bytes())):
        zip_bytes = export_programa_zip(rows)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    image_files = [n for n in names if n.startswith("images/")]
    assert len(image_files) == 1
    assert image_files[0] == "images/wolf_wwd30.jpg"


def test_zip_manifest_shows_missing_image_url_for_rows_without_image():
    row = _make_exportable_row()
    row["Image URL"] = ""
    zip_bytes = export_programa_zip([row])
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        manifest_text = zf.read("manifest.csv").decode("utf-8")
    assert "missing_image_url" in manifest_text


def test_zip_manifest_shows_failed_status_on_bad_url():
    row = _make_exportable_row()
    row["Image URL"] = "https://example.com/broken.jpg"
    with patch("src.image_assets.httpx.get", side_effect=Exception("timeout")):
        zip_bytes = export_programa_zip([row])
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        manifest_text = zf.read("manifest.csv").decode("utf-8")
    assert "fetch_error" in manifest_text


def test_zip_without_images_skips_download():
    rows = [_make_exportable_row()]
    with patch("src.image_assets.httpx.get") as mock_get:
        zip_bytes = export_programa_zip(rows, include_images=False)
    mock_get.assert_not_called()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    assert "programa_import.csv" in names
    assert not any(n.startswith("images/") for n in names)


def test_zip_preserves_original_image_url_in_manifest():
    rows = [_make_exportable_row()]
    with patch("src.image_assets.httpx.get", return_value=_mock_get(_make_image_bytes())):
        zip_bytes = export_programa_zip(rows)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        manifest_text = zf.read("manifest.csv").decode("utf-8")
    assert "https://wolfappliance.com/img/wwd30.jpg" in manifest_text


# ── manual_images ─────────────────────────────────────────────────────────────

def test_zip_manual_image_is_included_in_zip():
    """Bytes passed via manual_images appear in images/ even without a remote URL."""
    row = _make_exportable_row()
    row["Image URL"] = ""
    jpeg = _make_image_bytes("JPEG")
    zip_bytes = export_programa_zip([row], manual_images={0: jpeg})
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        image_files = [n for n in zf.namelist() if n.startswith("images/")]
    assert len(image_files) == 1


def test_zip_manual_image_status_is_manually_uploaded():
    row = _make_exportable_row()
    row["Image URL"] = ""
    jpeg = _make_image_bytes("JPEG")
    zip_bytes = export_programa_zip([row], manual_images={0: jpeg})
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        manifest_text = zf.read("manifest.csv").decode("utf-8")
    assert "manually_uploaded" in manifest_text


def test_zip_manual_image_preferred_over_remote_url():
    """When both a remote URL and manual bytes are supplied, manual bytes win."""
    row = _make_exportable_row()
    manual_jpeg = _make_image_bytes("JPEG", color="blue")
    with patch("src.image_assets.httpx.get") as mock_get:
        zip_bytes = export_programa_zip([row], manual_images={0: manual_jpeg})
    mock_get.assert_not_called()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        manifest_text = zf.read("manifest.csv").decode("utf-8")
    assert "manually_uploaded" in manifest_text


# ── duplicate filename deduplication ─────────────────────────────────────────

def test_zip_duplicate_filenames_are_deduplicated():
    """Two rows with same brand+SKU must produce two distinct filenames."""
    rows = [_make_exportable_row(), _make_exportable_row()]
    jpeg = _make_image_bytes("JPEG")
    with patch("src.image_assets.httpx.get", return_value=_mock_get(jpeg)):
        zip_bytes = export_programa_zip(rows)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        image_files = [n for n in zf.namelist() if n.startswith("images/")]
    assert len(image_files) == 2
    assert len(set(image_files)) == 2  # no duplicates


def test_zip_deduplicated_second_filename_has_numeric_suffix():
    rows = [_make_exportable_row(), _make_exportable_row()]
    jpeg = _make_image_bytes("JPEG")
    with patch("src.image_assets.httpx.get", return_value=_mock_get(jpeg)):
        zip_bytes = export_programa_zip(rows)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        image_files = sorted(n for n in zf.namelist() if n.startswith("images/"))
    assert image_files[0] == "images/wolf_wwd30.jpg"
    assert image_files[1] == "images/wolf_wwd30_2.jpg"
