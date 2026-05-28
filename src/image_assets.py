"""
Image download, validation, and JPEG conversion for Programa export.

Public API
----------
build_image_filename(brand, model_sku, product_name) -> str
    Return a clean deterministic .jpg filename.

download_and_convert_image(url, brand, model_sku, product_name, output_dir) -> dict
    Download url, validate content-type, convert to JPEG, optionally save to disk.
    Returns dict with keys:
      original_image_url, local_image_filename, local_image_path,
      image_status, error, jpeg_bytes
    image_status values: "downloaded" | "invalid_content_type" | "fetch_error" |
                         "conversion_error" | "skipped"
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path

import httpx
from PIL import Image, ImageOps

_log = logging.getLogger(__name__)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def build_image_filename(
    brand: str = "",
    model_sku: str = "",
    product_name: str = "",
) -> str:
    """Return a deterministic .jpg filename derived from brand + model/SKU."""
    parts = [brand, model_sku or product_name]
    raw = "_".join(p.strip() for p in parts if p.strip())
    if not raw:
        raw = "product"
    slug = _NON_ALNUM_RE.sub("_", raw.lower()).strip("_")
    if not slug:
        slug = "product"
    return f"{slug}.jpg"


def _download_bytes(url: str) -> tuple[bytes, str]:
    """Fetch url and return (body_bytes, normalised_content_type). Raises on HTTP error."""
    resp = httpx.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SCH-Intake/1.0)"},
        timeout=15,
        follow_redirects=True,
    )
    resp.raise_for_status()
    ct = resp.headers.get("content-type", "").lower().split(";")[0].strip()
    return resp.content, ct


def _convert_to_jpeg(img_bytes: bytes) -> bytes:
    """Convert any PIL-supported image format to an RGB JPEG byte string."""
    with Image.open(io.BytesIO(img_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()


def download_and_convert_image(
    url: str,
    brand: str = "",
    model_sku: str = "",
    product_name: str = "",
    output_dir: str | Path | None = None,
) -> dict:
    """
    Download an image URL, validate content-type, convert to JPEG.

    If output_dir is given the JPEG is also saved to disk at
    output_dir/<filename>.  The JPEG bytes are always returned in the result
    so callers can include them in an in-memory ZIP without a disk round-trip.

    Returns
    -------
    dict with keys:
      original_image_url   : str   — the input url
      local_image_filename : str   — e.g. "wolf_mdd30ts.jpg" (empty on failure)
      local_image_path     : str   — full path on disk (empty if not saved)
      image_status         : str   — see module docstring
      error                : str   — description of failure (empty on success)
      jpeg_bytes           : bytes — JPEG content (empty on failure)
    """
    filename = build_image_filename(brand, model_sku, product_name)
    result: dict = {
        "original_image_url": url or "",
        "local_image_filename": "",
        "local_image_path": "",
        "image_status": "skipped",
        "error": "",
        "jpeg_bytes": b"",
    }

    if not (url or "").strip():
        return result

    # Download
    try:
        raw_bytes, content_type = _download_bytes(url)
    except Exception as exc:
        result["image_status"] = "fetch_error"
        result["error"] = str(exc)
        _log.warning("[IMAGE ASSETS] fetch_error url=%s err=%s", url[:80], exc)
        return result

    # Validate
    if not content_type.startswith("image/"):
        result["image_status"] = "invalid_content_type"
        result["error"] = f"Expected image/*, got {content_type!r}"
        _log.warning("[IMAGE ASSETS] invalid_content_type url=%s ct=%s", url[:80], content_type)
        return result

    # Convert to JPEG
    try:
        jpeg_bytes = _convert_to_jpeg(raw_bytes)
    except Exception as exc:
        result["image_status"] = "conversion_error"
        result["error"] = f"Conversion failed: {exc}"
        _log.warning("[IMAGE ASSETS] conversion_error url=%s err=%s", url[:80], exc)
        return result

    result["local_image_filename"] = filename
    result["image_status"] = "downloaded"
    result["jpeg_bytes"] = jpeg_bytes

    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        dest = out / filename
        dest.write_bytes(jpeg_bytes)
        result["local_image_path"] = str(dest)
        _log.info("[IMAGE ASSETS] saved url=%s → %s", url[:60], dest)

    return result
