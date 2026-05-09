"""
Phase 1 confidence-gated image recovery pipeline.

Public API
----------
ImageRecoveryResult : dataclass
    Confidence + evidence carrier returned by every recovery source.

recover_from_url(row)
    Validates an existing Image URL on the row, downloads bytes, scores
    confidence based on SKU presence in URL path and on official domain.

Sources for Phase 2 (image search) drop in alongside the existing three.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

import httpx
from PIL import Image, ImageOps

from src.image_evidence import (
    is_official_domain,
    product_name_appears_in_text,
    sku_appears_in_text,
)


_log = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (compatible; SCH-Intake/1.0)"


@dataclass
class ImageRecoveryResult:
    image_source: str = "none"   # "url" | "pdf_crop" | "page_screenshot" | "manual_upload" | "none"
    confidence: str = "NONE"     # "HIGH" | "MEDIUM" | "LOW" | "NONE"
    evidence: list[str] = field(default_factory=list)
    image_url: str = ""
    local_image_filename: str = ""
    local_image_path: str = ""
    jpeg_bytes: bytes = b""
    error: str = ""

    @property
    def needs_image_review(self) -> bool:
        return self.confidence != "HIGH"


# ── Internal helpers ──────────────────────────────────────────────────────────

# These helpers (_check_image_content_type, _download_jpeg_bytes) intentionally
# duplicate logic that also exists in src/product_enrichment.py and src/image_assets.py.
# The duplication is removed in Task 8 of the Phase 1 plan, when
# product_enrichment.recover_images_for_dataframe becomes a thin delegator to
# this module. Until then, fixes to the HEAD+GET-Range probe pattern need to
# be applied to all three locations.

def _str_val(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"nan", "none", "null"} else s


def _check_image_content_type(url: str) -> bool:
    """HEAD then GET-Range fallback to confirm Content-Type starts with image/."""
    if not url:
        return False
    headers = {"User-Agent": _USER_AGENT}
    try:
        resp = httpx.head(url, headers=headers, timeout=5, follow_redirects=True)
        if 200 <= resp.status_code < 300:
            ct = resp.headers.get("content-type", "").lower()
            return ct.startswith("image/")
        # Some CDNs (Scene7, Akamai) reject HEAD; try a tiny GET.
        resp2 = httpx.get(
            url,
            headers={**headers, "Range": "bytes=0-1023"},
            timeout=8,
            follow_redirects=True,
        )
        if 200 <= resp2.status_code < 300 or resp2.status_code == 206:
            ct = resp2.headers.get("content-type", "").lower()
            return ct.startswith("image/")
        return False
    except Exception:
        return False


def _download_jpeg_bytes(url: str) -> bytes:
    """Download an image URL and return JPEG-encoded bytes (RGB), or b'' on failure."""
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as exc:
        _log.warning("[IMAGE RECOVERY] download failed url=%s err=%s", url[:80], exc)
        return b""
    try:
        with Image.open(io.BytesIO(resp.content)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue()
    except Exception as exc:
        _log.warning("[IMAGE RECOVERY] decode failed url=%s err=%s", url[:80], exc)
        return b""


# ── recover_from_url ──────────────────────────────────────────────────────────

def recover_from_url(row: dict) -> ImageRecoveryResult:
    """
    Validate the row's existing Image URL and download bytes.

    Confidence rules:
      HIGH   — SKU appears in image URL path OR in fetched page text
               (page text not yet fetched in Phase 1; we rely on URL path
                + product name match against the URL)
      MEDIUM — image URL is on the brand's official domain, no SKU evidence
      LOW    — URL valid but unrelated/unknown domain, no SKU evidence
    """
    url = _str_val(row.get("Image URL"))
    if not url:
        return ImageRecoveryResult()

    if not _check_image_content_type(url):
        return ImageRecoveryResult(
            image_source="url",
            confidence="NONE",
            error="invalid_content_type",
            image_url=url,
        )

    jpeg = _download_jpeg_bytes(url)
    if not jpeg:
        return ImageRecoveryResult(
            image_source="url",
            confidence="NONE",
            error="download_failed",
            image_url=url,
        )

    sku = _str_val(row.get("Model/SKU"))
    brand = _str_val(row.get("Brand"))
    product_name = _str_val(row.get("Product Name"))

    evidence: list[str] = []
    confidence = "LOW"

    # SKU in URL is the strongest URL-side signal we have without a page fetch.
    if sku and sku_appears_in_text(sku, url):
        evidence.append("sku_in_image_url")
        confidence = "HIGH"

    if confidence != "HIGH" and product_name and product_name_appears_in_text(product_name, url):
        evidence.append("product_name_in_image_url")
        confidence = "HIGH"

    if confidence != "HIGH" and is_official_domain(url, brand):
        evidence.append("official_domain")
        confidence = "MEDIUM"

    return ImageRecoveryResult(
        image_source="url",
        confidence=confidence,
        evidence=evidence,
        image_url=url,
        jpeg_bytes=jpeg,
    )
