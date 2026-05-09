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
    import fitz  # PyMuPDF — caller already guards the import at function entry

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
    # Belt-and-suspenders guard: with a 595×842 A4 page and 1%-area pre-filter,
    # any survivor renders to ~38 700 px² at 200 DPI, comfortably above this
    # 10 000 px² threshold. The check still defends against unusual page sizes
    # where 1% of the page area might be small in absolute pixels.
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
        return ImageRecoveryResult(
            image_source="pdf_crop",
            error=f"pdf_unreadable: file not found at {pdf_path}",
        )

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
        try:
            # 1. Try the recorded page first.
            target_idx = page_number - 1
            if target_idx >= doc.page_count or target_idx < 0:
                target_idx = 0

            target_page = doc[target_idx]

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
                # Same-page crop: read text only now that we know we'll use it.
                target_text = target_page.get_text("text") or ""
                # The row's _source_pdf_id + _source_page_number metadata is
                # itself MEDIUM evidence. SKU/name on page promotes to HIGH;
                # brand-only just adds an evidence string but stays at MEDIUM.
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
        except Exception as exc:
            _log.warning("[IMAGE RECOVERY] pdf_crop failed path=%s err=%s", pdf_path, exc)
            return ImageRecoveryResult(
                image_source="pdf_crop",
                error=f"pdf_render_error: {exc}",
            )
    finally:
        doc.close()


# ── recover_from_screenshot ───────────────────────────────────────────────────

try:
    from playwright.sync_api import sync_playwright  # type: ignore
except Exception:  # pragma: no cover - playwright optional at import
    sync_playwright = None  # type: ignore


_SCREENSHOT_SELECTORS = [
    "img[class*=product-image]",
    "[class*=product] img",
    "[class*=gallery] img",
    "[class*=hero] img",
    "[class*=media] img",
    'img[id*="product"]',
]
_SCREENSHOT_MIN_DIMENSION = 200            # px each side
_SCREENSHOT_PAGE_LOAD_TIMEOUT_MS = 15_000
_SCREENSHOT_LOGO_HINTS = ("logo", "icon", "sprite", "favicon")
_SCREENSHOT_ASPECT_RATIO_MIN = 0.25
_SCREENSHOT_ASPECT_RATIO_MAX = 4.0


def _is_logo_url(src: str) -> bool:
    s = (src or "").lower()
    return any(hint in s for hint in _SCREENSHOT_LOGO_HINTS)


def _bbox_passes_filters(bbox: dict) -> bool:
    w, h = bbox.get("width", 0), bbox.get("height", 0)
    if w < _SCREENSHOT_MIN_DIMENSION or h < _SCREENSHOT_MIN_DIMENSION:
        return False
    ratio = w / h if h else 0
    if ratio < _SCREENSHOT_ASPECT_RATIO_MIN or ratio > _SCREENSHOT_ASPECT_RATIO_MAX:
        return False
    return True


def _crop_jpeg_bytes_from_full_page(full_page_png: bytes, bbox: dict) -> bytes:
    """Crop bbox out of a full-page screenshot, return JPEG bytes."""
    with Image.open(io.BytesIO(full_page_png)) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        x, y = int(bbox["x"]), int(bbox["y"])
        w, h = int(bbox["width"]), int(bbox["height"])
        cropped = img.crop((x, y, x + w, y + h))
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()


def _score_screenshot_confidence(
    *,
    page_text: str,
    sku: str,
    product_name: str,
    brand: str,
    product_url: str,
) -> tuple[str, list[str]]:
    """Apply the screenshot confidence rules from the spec."""
    evidence: list[str] = []
    if sku and sku_appears_in_text(sku, page_text):
        evidence.append("sku_on_page")
        return "HIGH", evidence
    if product_name and product_name_appears_in_text(product_name, page_text):
        evidence.append("product_name_on_page")
        return "HIGH", evidence
    if is_official_domain(product_url, brand):
        evidence.append("official_domain")
        return "MEDIUM", evidence
    return "LOW", evidence


def recover_from_screenshot(row: dict, product_url: str) -> ImageRecoveryResult:
    """Open product_url in headless Chromium, capture and score a product image."""
    if sync_playwright is None:
        return ImageRecoveryResult(image_source="page_screenshot", error="browser_unavailable")
    if not product_url:
        return ImageRecoveryResult(image_source="page_screenshot", error="no_product_url")

    sku = _str_val(row.get("Model/SKU"))
    brand = _str_val(row.get("Brand"))
    product_name = _str_val(row.get("Product Name"))

    try:
        sp_ctx = sync_playwright()
    except Exception:
        return ImageRecoveryResult(
            image_source="page_screenshot",
            error="browser_unavailable",
        )

    try:
        with sp_ctx as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=_USER_AGENT)
                page = context.new_page()

                try:
                    page.goto(
                        product_url,
                        wait_until="networkidle",
                        timeout=_SCREENSHOT_PAGE_LOAD_TIMEOUT_MS,
                    )
                except Exception:
                    return ImageRecoveryResult(
                        image_source="page_screenshot",
                        error="page_load_timeout",
                    )

                page_text = page.content() or ""

                # 1) Element-selector pass
                for selector in _SCREENSHOT_SELECTORS:
                    try:
                        loc = page.locator(selector)
                        if loc.count() == 0:
                            continue
                        first = loc.first
                        if not first.is_visible():
                            continue
                        bbox = first.bounding_box()
                        if not bbox or not _bbox_passes_filters(bbox):
                            continue
                        jpeg = first.screenshot(type="jpeg", quality=85)
                        if not jpeg:
                            continue
                        confidence, evidence = _score_screenshot_confidence(
                            page_text=page_text,
                            sku=sku,
                            product_name=product_name,
                            brand=brand,
                            product_url=product_url,
                        )
                        evidence.append(f"selector:{selector}")
                        return ImageRecoveryResult(
                            image_source="page_screenshot",
                            confidence=confidence,
                            evidence=evidence,
                            jpeg_bytes=jpeg,
                        )
                    except Exception:
                        continue

                # 2) Bounding-box fallback over all <img>
                try:
                    candidates = page.eval_on_selector_all(
                        "img",
                        """
                        (els) => els.map(el => {
                            const r = el.getBoundingClientRect();
                            return {
                                src: el.currentSrc || el.src || "",
                                x: r.left, y: r.top,
                                width: r.width, height: r.height,
                            };
                        })
                        """,
                    ) or []
                except Exception:
                    candidates = []

                viewport = page.viewport_size or {"width": 1280, "height": 800}
                fold = viewport.get("height", 800)

                filtered = [
                    c for c in candidates
                    if not _is_logo_url(c.get("src", ""))
                    and _bbox_passes_filters(c)
                    and c.get("y", 0) < fold
                ]
                if not filtered:
                    return ImageRecoveryResult(
                        image_source="page_screenshot",
                        error="no_usable_image_element",
                    )

                filtered.sort(key=lambda c: c["width"] * c["height"], reverse=True)
                best = filtered[0]

                try:
                    full_png = page.screenshot(full_page=True, type="png")
                except Exception:
                    return ImageRecoveryResult(
                        image_source="page_screenshot",
                        error="screenshot_failed",
                    )

                try:
                    jpeg = _crop_jpeg_bytes_from_full_page(full_png, best)
                except Exception as exc:
                    return ImageRecoveryResult(
                        image_source="page_screenshot",
                        error=f"crop_failed: {exc}",
                    )

                confidence, evidence = _score_screenshot_confidence(
                    page_text=page_text,
                    sku=sku,
                    product_name=product_name,
                    brand=brand,
                    product_url=product_url,
                )
                evidence.append("bbox_crop")
                return ImageRecoveryResult(
                    image_source="page_screenshot",
                    confidence=confidence,
                    evidence=evidence,
                    jpeg_bytes=jpeg,
                )
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception:
        return ImageRecoveryResult(
            image_source="page_screenshot",
            error="browser_unavailable",
        )
