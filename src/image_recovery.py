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
from pathlib import Path

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
      HIGH   — SKU appears in image URL OR in fetched page text
               (page text not yet fetched in Phase 1; we rely on token-based
                SKU/product-name matching against the full URL string)
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


# ── recover_from_pdf_crop ─────────────────────────────────────────────────────

# Filtering thresholds for PDF image candidates.
_PDF_MIN_PIXEL_AREA = 100 * 100        # discard < 100×100 px
_PDF_MIN_PAGE_AREA_FRACTION = 0.01     # discard < 1% of page area
_PDF_ASPECT_RATIO_MIN = 0.25           # 1:4
_PDF_ASPECT_RATIO_MAX = 4.0            # 4:1
_PDF_RENDER_DPI = 200


def _crop_largest_image_on_pdf_page(page) -> bytes | None:
    """Return JPEG bytes of the largest non-icon image rect on `page`, or None."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None

    images = page.get_images(full=True)
    if not images:
        return None

    page_rect = page.rect
    page_area = page_rect.width * page_rect.height

    candidates: list[tuple[float, fitz.Rect]] = []
    for img_info in images:
        xref = img_info[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue
        for rect in rects:
            w, h = rect.width, rect.height
            if w <= 0 or h <= 0:
                continue
            if (w * h) < (page_area * _PDF_MIN_PAGE_AREA_FRACTION):
                continue
            ratio = w / h
            if ratio < _PDF_ASPECT_RATIO_MIN or ratio > _PDF_ASPECT_RATIO_MAX:
                continue
            candidates.append((w * h, rect))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[0], reverse=True)
    _, best_rect = candidates[0]

    # Render page at high DPI, crop best_rect from pixel-space.
    zoom = _PDF_RENDER_DPI / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, clip=best_rect, alpha=False)
    if pix.width * pix.height < _PDF_MIN_PIXEL_AREA:
        return None
    img_bytes = pix.tobytes("png")
    with Image.open(io.BytesIO(img_bytes)) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()


def recover_from_pdf_crop(row: dict, pdf_path: str | Path) -> ImageRecoveryResult:
    """
    Render the row's PDF page and crop the largest non-icon image region.

    Confidence rules:
      HIGH   — SKU OR product name appears as text on the same page as the crop
      MEDIUM — same-page crop with no SKU/name match (the row's _source_pdf_id
               + _source_page_number metadata is itself evidence that this PDF
               is the user-provided source for this row), OR crop comes from
               an adjacent ±1 page (capped at MEDIUM regardless of text)
      NONE   — PDF unreadable, or no usable images on this or adjacent pages

    LOW is not reachable from PDF crop in Phase 1.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ImageRecoveryResult(error="pymupdf_unavailable")

    pdf_path = str(pdf_path)
    if not Path(pdf_path).exists():
        return ImageRecoveryResult(image_source="pdf_crop", error="pdf_unreadable")

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        return ImageRecoveryResult(image_source="pdf_crop", error=f"pdf_unreadable: {exc}")

    page_number = row.get("_source_page_number")
    if not isinstance(page_number, int) or page_number < 1:
        page_number = 1

    sku = _str_val(row.get("Model/SKU"))
    brand = _str_val(row.get("Brand"))
    product_name = _str_val(row.get("Product Name"))

    try:
        # 1. Try the recorded page first.
        target_idx = page_number - 1
        if target_idx >= doc.page_count or target_idx < 0:
            target_idx = 0

        target_page = doc[target_idx]
        target_text = target_page.get_text("text") or ""

        jpeg = _crop_largest_image_on_pdf_page(target_page)
        is_adjacent = False

        if not jpeg:
            # 2. Fall back to ±1 adjacent pages.
            for offset in (-1, 1):
                idx = target_idx + offset
                if 0 <= idx < doc.page_count:
                    page = doc[idx]
                    j = _crop_largest_image_on_pdf_page(page)
                    if j:
                        jpeg = j
                        is_adjacent = True
                        break

        if not jpeg:
            return ImageRecoveryResult(image_source="pdf_crop", error="no_usable_images_in_pdf")

        # 3. Score confidence.
        evidence: list[str] = []

        if is_adjacent:
            evidence.append("adjacent_page_crop")
            confidence = "MEDIUM"
        else:
            # Same-page crop: the row's _source_pdf_id + _source_page_number
            # metadata is itself MEDIUM evidence. SKU/name on page promotes
            # to HIGH; brand-only just adds an evidence string but stays at MEDIUM.
            confidence = "MEDIUM"
            sku_hit = bool(sku and sku_appears_in_text(sku, target_text))
            name_hit = bool(product_name and product_name_appears_in_text(product_name, target_text))
            brand_hit = bool(brand and brand.lower() in target_text.lower())

            if sku_hit:
                evidence.append("sku_on_pdf_page")
                confidence = "HIGH"
            elif name_hit:
                evidence.append("product_name_on_pdf_page")
                confidence = "HIGH"
            elif brand_hit:
                evidence.append("brand_on_pdf_page")
                # confidence stays MEDIUM

        return ImageRecoveryResult(
            image_source="pdf_crop",
            confidence=confidence,
            evidence=evidence,
            jpeg_bytes=jpeg,
        )
    finally:
        doc.close()
