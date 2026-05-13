"""
Phase 1 confidence-gated image recovery pipeline.

Public API
----------
ImageRecoveryResult : dataclass
    Confidence + evidence carrier returned by every recovery source.

recover_from_url(row)
    Validates an existing Image URL on the row, downloads bytes, scores
    confidence based on SKU presence in URL and on official domain.

recover_from_pdf_crop(row, pdf_path)
    Renders the row's source PDF page with PyMuPDF, crops the largest
    non-icon image, scores confidence from page text.

recover_from_screenshot(row, product_url)
    Opens product_url in headless Chromium, captures a product image via
    element-selector priority list with full-page+bbox-crop fallback,
    scores confidence from rendered page text.

recover_image_for_row(row, pdf_lookup=None, session_id=None, enable_screenshot=True)
    Orchestrates all three sources in priority order (URL → PDF → screenshot),
    short-circuiting on HIGH and returning the best held result on lower tiers.

recover_images_for_dataframe(df, pdf_lookup=None, session_id=None, enable_screenshot=True)
    Runs the recovery pipeline on every row lacking a HIGH-confidence image,
    writes recovered files to .tmp/uploads/{session_id}/images/, and returns
    an annotated DataFrame plus a diagnostics list (jpeg_bytes never leaks).

cleanup_old_sessions(max_age_hours=24)
    Removes .tmp/uploads/<id> session directories older than max_age_hours
    and returns the count of directories deleted.

Sources for Phase 2 (image search) drop in alongside the existing three.
"""
from __future__ import annotations

import io
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pandas as pd
from PIL import Image, ImageOps

from src.image_assets import build_image_filename
from src.image_evidence import (
    is_official_domain,
    product_name_appears_in_text,
    sku_appears_in_text,
)
from src.image_presence import row_has_image, row_image_status
from src.product_page_images import extract_image_from_url
from src.web_product_lookup import lookup_official_product_image


_log = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (compatible; SCH-Intake/1.0)"


@dataclass
class ImageRecoveryResult:
    image_source: str = "none"
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
            image_source="product_url_html_image",
            confidence="NONE",
            error="invalid_content_type",
            image_url=url,
        )

    jpeg = _download_jpeg_bytes(url)
    if not jpeg:
        return ImageRecoveryResult(
            image_source="product_url_html_image",
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
        image_source="product_url_html_image",
        confidence=confidence,
        evidence=evidence,
        image_url=url,
        jpeg_bytes=jpeg,
    )


def _result_from_page_result(page_result) -> ImageRecoveryResult:
    if not page_result.image_found:
        return ImageRecoveryResult(
            image_source=page_result.image_source or "none",
            confidence="NONE",
            image_url=page_result.image_url or "",
            error=page_result.error or "no_usable_product_images",
        )
    jpeg = _download_jpeg_bytes(page_result.image_url)
    if not jpeg:
        return ImageRecoveryResult(
            image_source=page_result.image_source,
            confidence="NONE",
            evidence=list(page_result.evidence),
            image_url=page_result.image_url,
            error="image_download_failed",
        )
    return ImageRecoveryResult(
        image_source=page_result.image_source,
        confidence=page_result.confidence,
        evidence=list(page_result.evidence),
        image_url=page_result.image_url,
        jpeg_bytes=jpeg,
    )


def recover_from_product_page(row: dict, page_url: str, debug: dict | None = None) -> ImageRecoveryResult:
    page_result = extract_image_from_url(page_url, row, source_prefix="product_url")
    pdebug = page_result.debug or {}
    if debug is not None:
        debug["product_url_fetch_ran"] = True
        debug["product_url_fetch_status"] = pdebug.get("fetch_status")
        debug["product_url_images_found"] = pdebug.get("images_found", 0)
        debug["product_url_selected_image"] = pdebug.get("selected_image", "")
        debug["product_url_rejection_reasons"] = list(pdebug.get("rejection_reasons", []))
    return _result_from_page_result(page_result)


def recover_from_official_lookup(row: dict, debug: dict | None = None) -> ImageRecoveryResult:
    web = lookup_official_product_image(row)
    if debug is not None:
        debug.update(web.debug)
    return _result_from_page_result(web.image_result)


# ── recover_from_pdf_crop ─────────────────────────────────────────────────────

# Filtering thresholds for PDF image candidates.
_PDF_MIN_PIXEL_AREA = 100 * 100        # discard < 100×100 px
_PDF_MIN_PAGE_AREA_FRACTION = 0.01     # discard < 1% of page area
_PDF_ASPECT_RATIO_MIN = 0.25           # 1:4
_PDF_ASPECT_RATIO_MAX = 4.0            # 4:1
_PDF_RENDER_DPI = 200


def _crop_largest_image_on_pdf_page(page, debug: dict | None = None) -> bytes | None:
    """Return JPEG bytes of the largest non-icon image rect on `page`, or None.

    When `debug` is provided, populate it with diagnostic counters so callers
    can see why candidates were rejected.
    """
    import fitz  # PyMuPDF — caller already guards the import at function entry

    images = page.get_images(full=True)
    if debug is not None:
        debug["pdf_image_objects_count"] = len(images)
    if not images:
        return None

    page_rect = page.rect
    page_area = page_rect.width * page_rect.height

    candidates: list[tuple[float, fitz.Rect]] = []
    rejection_reasons: list[str] = [] if debug is not None else []
    for img_info in images:
        xref = img_info[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception as exc:
            if debug is not None:
                rejection_reasons.append(f"xref={xref}: get_image_rects raised {exc}")
            continue
        if not rects:
            if debug is not None:
                rejection_reasons.append(f"xref={xref}: no rects returned")
            continue
        for rect in rects:
            w, h = rect.width, rect.height
            if w <= 0 or h <= 0:
                if debug is not None:
                    rejection_reasons.append(f"xref={xref}: zero-area rect ({w}x{h})")
                continue
            if (w * h) < (page_area * _PDF_MIN_PAGE_AREA_FRACTION):
                if debug is not None:
                    pct = (w * h) / page_area * 100 if page_area else 0
                    rejection_reasons.append(
                        f"xref={xref}: too small ({w:.0f}x{h:.0f}={pct:.2f}% of page)"
                    )
                continue
            ratio = w / h
            if ratio < _PDF_ASPECT_RATIO_MIN or ratio > _PDF_ASPECT_RATIO_MAX:
                if debug is not None:
                    rejection_reasons.append(
                        f"xref={xref}: extreme aspect ratio {ratio:.2f}"
                    )
                continue
            candidates.append((w * h, rect))

    if debug is not None:
        debug["pdf_candidates_after_filter"] = len(candidates)
        if rejection_reasons:
            debug.setdefault("pdf_rejection_reasons", []).extend(rejection_reasons)

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
        if debug is not None:
            debug.setdefault("pdf_rejection_reasons", []).append(
                f"best candidate rendered to {pix.width}x{pix.height}px, below "
                f"{_PDF_MIN_PIXEL_AREA}px² minimum"
            )
        return None
    img_bytes = pix.tobytes("png")
    with Image.open(io.BytesIO(img_bytes)) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()


def _render_pdf_page_to_jpeg(page, dpi: int = _PDF_RENDER_DPI) -> bytes | None:
    """Render an entire PDF page at `dpi` and return JPEG bytes, or None on failure.

    Used as a last-resort fallback when no embedded images can be extracted —
    many vendor spec sheets flatten product photos into page graphics that
    page.get_images() doesn't expose. Rendering the page itself always works
    when the page is otherwise readable.
    """
    try:
        import fitz  # caller has already guarded the import
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        png_bytes = pix.tobytes("png")
    except Exception:
        return None
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue()
    except Exception:
        return None


_PAGE_RENDER_CONTENT_THRESHOLD = 240  # gray pixels < this are "content"
_PAGE_RENDER_MIN_BBOX_FRACTION = 0.05  # bbox must cover ≥5% of page
_PAGE_RENDER_MAX_BBOX_FRACTION = 0.95  # > this means we found nothing distinct


def _crop_largest_non_white_region(jpeg_bytes: bytes) -> bytes | None:
    """Crop the bounding box of all non-near-white pixels from a rendered page.

    Returns JPEG bytes of the cropped region, or None if the rendered page is
    nearly empty (bbox covers <5% of the page) or essentially all-content
    (bbox covers >95%, which means we couldn't isolate the photo from
    surrounding text). In the latter case the caller should fall through to
    the full-page render rather than this content-crop result.
    """
    try:
        with Image.open(io.BytesIO(jpeg_bytes)) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            gray = img.convert("L")
            # Binary mask: white pixels become 0, content pixels become 255.
            mask = gray.point(lambda px: 255 if px < _PAGE_RENDER_CONTENT_THRESHOLD else 0)
            bbox = mask.getbbox()
            if not bbox:
                return None
            left, top, right, bottom = bbox
            bbox_area = max(0, (right - left) * (bottom - top))
            page_area = img.width * img.height
            if page_area == 0:
                return None
            frac = bbox_area / page_area
            if frac < _PAGE_RENDER_MIN_BBOX_FRACTION:
                return None
            if frac > _PAGE_RENDER_MAX_BBOX_FRACTION:
                return None
            cropped = img.crop(bbox)
            buf = io.BytesIO()
            cropped.save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue()
    except Exception:
        return None


def recover_from_pdf_crop(
    row: dict,
    pdf_path: str | Path,
    debug: dict | None = None,
) -> ImageRecoveryResult:
    """
    Render the row's PDF page and crop the largest non-icon image region.

    Confidence rules:
      HIGH   — SKU OR product name appears as text on the same page as the crop
               (only via the embedded-image extraction path)
      MEDIUM — same-page embedded-image crop with no SKU/name match, OR
               crop comes from an adjacent ±1 page, OR
               page-render content crop, OR
               full-page render fallback
      NONE   — PDF unreadable

    Fallback chain when the embedded-image path fails (page.get_images()
    returns 0 candidates or all are filtered out):
      1. Try ±1 adjacent pages with the embedded-image path.
      2. Render the target page at 200 DPI and crop the largest non-near-white
         region (page_render_content_crop).
      3. Otherwise return the full-page render itself (page_render_full).

    Steps 2-3 always cap confidence at MEDIUM and add `needs_review` semantics.
    Many vendor spec sheets flatten product photos into page graphics that
    page.get_images() doesn't expose; the page-render fallback ensures the
    user always gets a usable image candidate when the PDF is readable.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        if debug is not None:
            debug["error"] = "pymupdf_unavailable"
        return ImageRecoveryResult(error="pymupdf_unavailable")

    pdf_path = str(pdf_path)
    if debug is not None:
        debug["pdf_path"] = pdf_path
    if not Path(pdf_path).exists():
        if debug is not None:
            debug["pdf_path_exists"] = False
        return ImageRecoveryResult(
            image_source="none",
            error=f"pdf_unreadable: file not found at {pdf_path}",
        )
    if debug is not None:
        debug["pdf_path_exists"] = True

    try:
        doc = fitz.open(pdf_path)
        if debug is not None:
            debug["pdf_opened"] = True
    except Exception as exc:
        if debug is not None:
            debug["pdf_opened"] = False
            debug["error"] = f"pdf_unreadable: {exc}"
        return ImageRecoveryResult(image_source="none", error=f"pdf_unreadable: {exc}")

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

            jpeg = _crop_largest_image_on_pdf_page(target_page, debug=debug)
            is_adjacent = False
            adjacent_pages_scanned = False
            page_render_used = False
            page_render_full = False

            if not jpeg:
                # 2. Fall back to ±1 adjacent pages.
                adjacent_pages_scanned = True
                for offset in (-1, 1):
                    idx = target_idx + offset
                    if 0 <= idx < doc.page_count:
                        page = doc[idx]
                        # Don't pollute primary-page debug counters with adjacent
                        # ones; capture as a side-channel list instead.
                        j = _crop_largest_image_on_pdf_page(page)
                        if j:
                            jpeg = j
                            is_adjacent = True
                            break

            if not jpeg:
                # 3. Render the target page itself and try a content-region crop.
                rendered = _render_pdf_page_to_jpeg(target_page)
                if rendered:
                    cropped = _crop_largest_non_white_region(rendered)
                    if cropped:
                        jpeg = cropped
                        page_render_used = True
                    else:
                        # 4. Last resort: hand back the full page render.
                        jpeg = rendered
                        page_render_used = True
                        page_render_full = True

            if debug is not None:
                debug["pdf_adjacent_pages_scanned"] = adjacent_pages_scanned
                debug["pdf_page_render_fallback_used"] = page_render_used
                debug["pdf_page_render_full"] = page_render_full

            if not jpeg:
                # Even page rendering failed (corrupt page, encrypted, etc.)
                return ImageRecoveryResult(
                    image_source="none",
                    error="no_usable_images_in_pdf",
                )

            # 5. Score confidence.
            evidence: list[str] = []

            if page_render_used:
                # Page-render fallbacks are MEDIUM at best — we have less
                # certainty about what the cropped region actually contains.
                evidence.append("page_render_full" if page_render_full else "page_render_content_crop")
                source = "pdf_page_render_full" if page_render_full else "pdf_page_render_content_crop"
                confidence = "MEDIUM"
            elif is_adjacent:
                evidence.append("adjacent_page_crop")
                source = "pdf_adjacent_page_image"
                confidence = "MEDIUM"
            else:
                source = "pdf_embedded_image"
                # Same-page embedded image: read text now that we know we'll use it.
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
                image_source=source,
                confidence=confidence,
                evidence=evidence,
                jpeg_bytes=jpeg,
            )
        except Exception as exc:
            _log.warning("[IMAGE RECOVERY] pdf_crop failed path=%s err=%s", pdf_path, exc)
            if debug is not None:
                debug["error"] = f"pdf_render_error: {exc}"
            return ImageRecoveryResult(
                image_source="none",
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
        img = ImageOps.exif_transpose(img)
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


def recover_from_screenshot(
    row: dict,
    product_url: str,
    debug: dict | None = None,
) -> ImageRecoveryResult:
    """Open product_url in headless Chromium, capture and score a product image.

    When `debug` is provided, populate it with screenshot diagnostics
    (`screenshot_selector_matched`, `screenshot_candidates_found`).
    """
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
                        if debug is not None:
                            debug["screenshot_selector_matched"] = selector
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
                if debug is not None:
                    debug["screenshot_candidates_found"] = len(filtered)
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


# ── recover_image_for_row orchestrator ────────────────────────────────────────

_CONFIDENCE_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


def _better(a: ImageRecoveryResult | None, b: ImageRecoveryResult) -> ImageRecoveryResult:
    """Return whichever result has higher confidence; on tie keep `a` (first wins)."""
    if a is None:
        return b
    # Strict >: ties keep a (the held-first result wins).
    if _CONFIDENCE_RANK.get(b.confidence, -1) > _CONFIDENCE_RANK.get(a.confidence, -1):
        return b
    return a


def recover_image_for_row(
    row: dict,
    pdf_lookup: dict[str, str] | None = None,
    session_id: str | None = None,  # forward-plumbing for recover_images_for_dataframe (Task 7) — not used here
    enable_screenshot: bool = True,
    debug: dict | None = None,
) -> ImageRecoveryResult:
    """
    Try sources in priority order:
      1) Product/Supplier/Source URL page extraction
      2) Official manufacturer/supplier web lookup
      3) Existing Image URL validation
      4) PDF native image extraction and page-render fallback

    On a confidence tie between two non-HIGH results, the source held first wins
    (URL > PDF > Screenshot). This means PDF MEDIUM beats Screenshot MEDIUM, and
    URL MEDIUM beats PDF MEDIUM, etc.

    When `debug` is provided, populate it with per-source diagnostics so the
    caller can produce an Image Recovery Debug Report.
    """
    held: ImageRecoveryResult | None = None

    if debug is not None:
        debug.setdefault("pdf_path_exists", None)
        debug.setdefault("pdf_opened", None)
        debug.setdefault("pdf_image_objects_count", 0)
        debug.setdefault("pdf_candidates_after_filter", 0)
        debug.setdefault("pdf_rejection_reasons", [])
        debug.setdefault("pdf_adjacent_pages_scanned", False)
        debug.setdefault("pdf_page_render_fallback_used", False)
        debug.setdefault("pdf_page_render_full", False)
        debug.setdefault("product_url_fetch_ran", False)
        debug.setdefault("product_url_fetch_status", "")
        debug.setdefault("product_url_images_found", 0)
        debug.setdefault("product_url_selected_image", "")
        debug.setdefault("product_url_rejection_reasons", [])
        debug.setdefault("web_lookup_ran", False)
        debug.setdefault("web_queries_used", [])
        debug.setdefault("web_results_found", 0)
        debug.setdefault("official_candidate_pages", [])
        debug.setdefault("selected_product_page_url", "")
        debug.setdefault("selected_product_page_domain", "")
        debug.setdefault("selected_web_image_url", "")
        debug.setdefault("web_confidence_reason", "")
        debug.setdefault("web_rejection_reasons", [])
        debug.setdefault("brand_registry_match", False)
        debug.setdefault("brand_registry_domains_checked", [])
        debug.setdefault("brand_search_queries_used", [])
        debug.setdefault("candidate_pages_found", 0)
        debug.setdefault("candidate_page_scores", [])
        debug.setdefault("selected_product_page_score", 0)
        debug.setdefault("selected_product_page_reason", "")
        debug.setdefault("image_candidates_found", 0)
        debug.setdefault("selected_image_url", "")
        debug.setdefault("selected_image_reason", "")

    # 1) Product/Supplier/Source URL page extraction.
    for page_key in ("Product URL", "Supplier URL", "Source URL"):
        page_url = _str_val(row.get(page_key))
        if not page_url:
            continue
        page_result = recover_from_product_page(row, page_url, debug=debug)
        if page_result.confidence == "HIGH":
            if debug is not None:
                _record_final_debug(debug, page_result)
            return page_result
        if page_result.confidence in ("MEDIUM", "LOW"):
            held = page_result
        break

    # 2) Official manufacturer/supplier web lookup.
    web_result = recover_from_official_lookup(row, debug=debug)
    if web_result.confidence == "HIGH":
        if debug is not None:
            _record_final_debug(debug, web_result)
        return web_result
    if web_result.confidence in ("MEDIUM", "LOW"):
        held = _better(held, web_result)

    # 3) Existing image URL on row.
    url_val = _str_val(row.get("Image URL"))
    if url_val:
        url_result = recover_from_url(row)
        if url_result.confidence == "HIGH":
            if debug is not None:
                _record_final_debug(debug, url_result)
            return url_result
        if url_result.confidence in ("MEDIUM", "LOW"):
            held = url_result

    # 4) PDF crop/render fallback.
    pdf_id = _str_val(row.get("_source_pdf_id"))
    pdf_path = (pdf_lookup or {}).get(pdf_id) if pdf_id else None
    if pdf_id and pdf_path:
        pdf_result = recover_from_pdf_crop(row, pdf_path, debug=debug)
        if pdf_result.confidence == "HIGH":
            if debug is not None:
                _record_final_debug(debug, pdf_result)
            return pdf_result
        if pdf_result.confidence in ("MEDIUM", "LOW"):
            held = _better(held, pdf_result)

    final = held if held is not None else ImageRecoveryResult()
    if debug is not None:
        _record_final_debug(debug, final)
    return final


def _record_final_debug(debug: dict, result: "ImageRecoveryResult") -> None:
    debug["final_image_source"] = result.image_source
    debug["final_confidence"] = result.confidence
    debug["final_evidence"] = list(result.evidence)
    debug["final_error"] = result.error


# ── recover_images_for_dataframe ──────────────────────────────────────────────

# Relative to process cwd. Both uvicorn (FastAPI) and streamlit run from
# the repo root, so this resolves to <repo>/.tmp/uploads at runtime.
# `local_image_path` values stored in the dataframe are absolute (via
# Path.resolve()), so consumers don't need to know cwd to find files.
# Anchor to repo root via __file__ (always absolute) so cwd drift in
# deployment doesn't divert recovered images away from the PDFs the
# backend stored under the same .tmp/uploads/{sid}/ tree.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TMP_ROOT = _REPO_ROOT / ".tmp" / "uploads"


def _session_image_dir(session_id: str) -> Path:
    return _TMP_ROOT / session_id / "images"


_RECOVERY_COLUMNS = [
    "image_source", "confidence", "evidence", "needs_image_review",
    "local_image_filename", "local_image_path",
]


def _ensure_recovery_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in _RECOVERY_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    if "Image URL" not in df.columns:
        df["Image URL"] = ""
    return df


def _row_already_high(row: pd.Series) -> bool:
    row_dict = row.to_dict()
    if _str_val(row.get("image_source")).lower() == "manual_upload" and row_has_image(row_dict):
        return True
    return _str_val(row.get("confidence")).upper() == "HIGH" and row_has_image(row_dict)


def recover_images_for_dataframe(
    df: pd.DataFrame,
    pdf_lookup: dict[str, str] | None = None,
    session_id: str | None = None,
    enable_screenshot: bool = True,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Run the recovery pipeline on rows that don't already carry a HIGH-confidence
    image. Writes recovered files to .tmp/uploads/{session_id}/images/ and
    annotates rows with image_source / confidence / evidence / needs_image_review
    / local_image_filename / local_image_path.

    Returns (updated_df, diagnostics_list). Diagnostics never include jpeg_bytes.
    """
    df = df.copy()
    df = _ensure_recovery_columns(df)
    diagnostics: list[dict] = []

    if not session_id:
        session_id = "default"
    images_dir = _session_image_dir(session_id)

    for idx, row in df.iterrows():
        if _row_already_high(row):
            continue

        row_dict = row.to_dict()
        debug: dict = {}
        result = recover_image_for_row(
            row_dict,
            pdf_lookup=pdf_lookup,
            session_id=session_id,
            enable_screenshot=enable_screenshot,
            debug=debug,
        )

        # Persist evidence on the row regardless of confidence.
        df.at[idx, "image_source"] = result.image_source
        df.at[idx, "confidence"] = result.confidence
        df.at[idx, "evidence"] = ";".join(result.evidence)
        # CONTRACT: needs_image_review is stored as the string "True" or "False",
        # not the Python boolean. Pandas Arrow-backed StringDtype rejects bool
        # assignment to a string-typed column (and _ensure_recovery_columns
        # initializes this column with ""). Downstream readers must compare against
        # the string ("True" / "False"), not bool() — bool("False") evaluates to
        # True because non-empty strings are truthy.
        df.at[idx, "needs_image_review"] = str(result.needs_image_review)

        if result.image_url:
            df.at[idx, "Image URL"] = result.image_url

        # Only write files for HIGH/MEDIUM. LOW results are diagnostic only.
        if result.confidence in ("HIGH", "MEDIUM") and result.jpeg_bytes:
            try:
                images_dir.mkdir(parents=True, exist_ok=True)
                filename = build_image_filename(
                    brand=_str_val(row_dict.get("Brand")),
                    model_sku=_str_val(row_dict.get("Model/SKU")),
                    product_name=_str_val(row_dict.get("Product Name")),
                )
                # Deduplicate against files already in this session dir.
                target = images_dir / filename
                counter = 2
                stem = Path(filename).stem
                ext = Path(filename).suffix.lstrip(".") or "jpg"
                while target.exists():
                    filename = f"{stem}_{counter}.{ext}"
                    target = images_dir / filename
                    counter += 1

                target.write_bytes(result.jpeg_bytes)
                df.at[idx, "local_image_filename"] = filename
                df.at[idx, "Image Filename"] = filename
                df.at[idx, "local_image_path"] = str(target.resolve())
                df.at[idx, "recovered_image_path"] = str(target.resolve())
            except Exception as exc:
                _log.warning("[IMAGE RECOVERY] disk write failed idx=%s err=%s", idx, exc)

        row_after = df.loc[idx].to_dict()
        local_path_after = _str_val(row_after.get("local_image_path") or row_after.get("recovered_image_path"))
        source_pdf_id = _str_val(row_dict.get("_source_pdf_id"))
        source_pdf_path = (pdf_lookup or {}).get(source_pdf_id, "") if source_pdf_id else ""
        image_url_after = _str_val(row_after.get("Image URL"))
        confidence_after = result.confidence
        local_path_exists = bool(local_path_after and Path(local_path_after).exists())
        exported_to_zip = bool(
            confidence_after in {"HIGH", "MEDIUM"}
            and (local_path_exists or image_url_after.lower().startswith("https://"))
        )
        if exported_to_zip:
            export_skip_reason = ""
        elif confidence_after == "LOW":
            export_skip_reason = "low_confidence_skipped_by_default"
        elif not row_has_image(row_after):
            export_skip_reason = "missing_image"
        elif local_path_after and not local_path_exists:
            export_skip_reason = "local_image_path_missing"
        else:
            export_skip_reason = "not_export_ready"

        diagnostics.append({
            "row_index": int(idx),
            "row_id": _str_val(row_dict.get("row_id") or row_dict.get("Row ID") or idx),
            "product_name": _str_val(row_dict.get("Product Name")),
            "brand": _str_val(row_dict.get("Brand")),
            "model_sku": _str_val(row_dict.get("Model/SKU")),
            "supplier": _str_val(row_dict.get("Supplier")),
            "product_url": _str_val(row_dict.get("Product URL")),
            "product_url_present": bool(_str_val(row_dict.get("Product URL"))),
            "_source_pdf_id": _str_val(row_dict.get("_source_pdf_id")),
            "_source_page_number": row_dict.get("_source_page_number"),
            "page_number": row_dict.get("_source_page_number"),
            "_source_filename": _str_val(row_dict.get("_source_filename")),
            "source_pdf_path": source_pdf_path,
            "source_pdf_path_exists": bool(source_pdf_path and Path(source_pdf_path).exists()),
            "image_source": result.image_source,
            "confidence": result.confidence,
            "evidence": list(result.evidence),
            "error": result.error,
            "local_image_path": local_path_after,
            "image_filename": _str_val(row_after.get("Image Filename") or row_after.get("local_image_filename")),
            "image_url": image_url_after,
            "row_has_image": row_has_image(row_after),
            "exported_to_zip": exported_to_zip,
            "export_skip_reason": export_skip_reason,
            "final_ui_status": row_image_status(row_after),
            # Per-source diagnostics for the Image Recovery Debug Report.
            "pdf_path": debug.get("pdf_path", ""),
            "pdf_path_exists": debug.get("pdf_path_exists"),
            "pdf_opened": debug.get("pdf_opened"),
            "pdf_image_objects_count": debug.get("pdf_image_objects_count", 0),
            "pdf_images_found": debug.get("pdf_image_objects_count", 0),
            "pdf_candidates_after_filter": debug.get("pdf_candidates_after_filter", 0),
            "pdf_rejection_reasons": list(debug.get("pdf_rejection_reasons", [])),
            "pdf_adjacent_pages_scanned": bool(debug.get("pdf_adjacent_pages_scanned", False)),
            "pdf_page_render_fallback_used": bool(debug.get("pdf_page_render_fallback_used", False)),
            "pdf_page_render_full": bool(debug.get("pdf_page_render_full", False)),
            "product_url_fetch_ran": bool(debug.get("product_url_fetch_ran", False)),
            "product_url_fetch_status": debug.get("product_url_fetch_status", ""),
            "product_url_images_found": debug.get("product_url_images_found", 0),
            "product_url_selected_image": debug.get("product_url_selected_image", ""),
            "product_url_rejection_reasons": list(debug.get("product_url_rejection_reasons", [])),
            "web_lookup_ran": bool(debug.get("web_lookup_ran", False)),
            "web_queries_used": list(debug.get("web_queries_used", [])),
            "web_results_found": debug.get("web_results_found", 0),
            "official_candidate_pages": list(debug.get("official_candidate_pages", [])),
            "selected_product_page_url": debug.get("selected_product_page_url", ""),
            "selected_product_page_domain": debug.get("selected_product_page_domain", ""),
            "selected_web_image_url": debug.get("selected_web_image_url", ""),
            "web_confidence_reason": debug.get("web_confidence_reason", ""),
            "web_rejection_reasons": list(debug.get("web_rejection_reasons", [])),
            "brand_registry_match": debug.get("brand_registry_match", False),
            "brand_registry_domains_checked": list(debug.get("brand_registry_domains_checked", [])),
            "brand_search_queries_used": list(debug.get("brand_search_queries_used", [])),
            "candidate_pages_found": debug.get("candidate_pages_found", 0),
            "candidate_page_scores": list(debug.get("candidate_page_scores", [])),
            "selected_product_page_score": debug.get("selected_product_page_score", 0),
            "selected_product_page_reason": debug.get("selected_product_page_reason", ""),
            "image_candidates_found": debug.get("image_candidates_found", 0),
            "selected_image_url": debug.get("selected_image_url", ""),
            "selected_image_reason": debug.get("selected_image_reason", ""),
            "screenshot_ran": bool(debug.get("screenshot_ran", False)),
            "screenshot_url_attempted": debug.get("screenshot_url_attempted", ""),
            "screenshot_selector_matched": debug.get("screenshot_selector_matched", ""),
            "screenshot_candidates_found": debug.get("screenshot_candidates_found", 0),
        })

    return df, diagnostics


# ── Debug report builder ──────────────────────────────────────────────────────

_DEBUG_REPORT_COLUMNS = [
    "row_index",
    "row_id",
    "product_name",
    "brand",
    "model_sku",
    "supplier",
    "product_url",
    "product_url_present",
    "_source_pdf_id",
    "_source_page_number",
    "page_number",
    "_source_filename",
    "source_pdf_path",
    "source_pdf_path_exists",
    "pdf_path",
    "pdf_path_exists",
    "pdf_opened",
    "pdf_image_objects_count",
    "pdf_images_found",
    "pdf_candidates_after_filter",
    "pdf_rejection_reasons",
    "pdf_adjacent_pages_scanned",
    "pdf_page_render_fallback_used",
    "pdf_page_render_full",
    "product_url_fetch_ran",
    "product_url_fetch_status",
    "product_url_images_found",
    "product_url_selected_image",
    "product_url_rejection_reasons",
    "web_lookup_ran",
    "web_queries_used",
    "web_results_found",
    "official_candidate_pages",
    "selected_product_page_url",
    "selected_product_page_domain",
    "selected_web_image_url",
    "web_confidence_reason",
    "web_rejection_reasons",
    "brand_registry_match",
    "brand_registry_domains_checked",
    "brand_search_queries_used",
    "candidate_pages_found",
    "candidate_page_scores",
    "selected_product_page_score",
    "selected_product_page_reason",
    "image_candidates_found",
    "selected_image_url",
    "selected_image_reason",
    "screenshot_ran",
    "screenshot_url_attempted",
    "screenshot_selector_matched",
    "screenshot_candidates_found",
    "image_source",
    "confidence",
    "evidence",
    "error",
    "local_image_path",
    "image_filename",
    "image_url",
    "row_has_image",
    "exported_to_zip",
    "export_skip_reason",
    "final_ui_status",
]


def build_image_recovery_debug_dataframe(diagnostics: list[dict]) -> pd.DataFrame:
    """Convert the diagnostics list from recover_images_for_dataframe into a
    fixed-column DataFrame suitable for CSV download. Joins list-valued fields
    (`evidence`, `pdf_rejection_reasons`) with semicolons so each cell stays
    flat. Missing fields default to empty/0/False.
    """
    if not diagnostics:
        return pd.DataFrame(columns=_DEBUG_REPORT_COLUMNS)

    rows: list[dict] = []
    for d in diagnostics:
        row = {col: d.get(col, "") for col in _DEBUG_REPORT_COLUMNS}
        # Flatten list-valued fields for CSV friendliness.
        if isinstance(row.get("evidence"), list):
            row["evidence"] = ";".join(row["evidence"])
        if isinstance(row.get("pdf_rejection_reasons"), list):
            row["pdf_rejection_reasons"] = " | ".join(row["pdf_rejection_reasons"])
        for key in (
            "product_url_rejection_reasons",
            "web_queries_used",
            "official_candidate_pages",
            "web_rejection_reasons",
        ):
            if isinstance(row.get(key), list):
                row[key] = " | ".join(str(v) for v in row[key])
        rows.append(row)
    return pd.DataFrame(rows, columns=_DEBUG_REPORT_COLUMNS)


# ── cleanup_old_sessions ──────────────────────────────────────────────────────

def cleanup_old_sessions(max_age_hours: int = 24) -> int:
    """Delete .tmp/uploads/<id> dirs older than max_age_hours. Returns count deleted."""
    import shutil

    root = _TMP_ROOT
    if not root.exists():
        return 0
    threshold = time.time() - max_age_hours * 3600
    deleted = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime < threshold:
                shutil.rmtree(child, ignore_errors=True)
                if not child.exists():
                    deleted += 1
        except Exception as exc:
            _log.warning("[IMAGE RECOVERY] cleanup failed dir=%s err=%s", child, exc)
    return deleted
