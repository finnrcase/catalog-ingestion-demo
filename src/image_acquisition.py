"""Multi-stage product image acquisition pipeline.

The public entry point is recover_product_image(row, ...). It returns a stable
result object with source, confidence, evidence, and debug/provenance details.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.image_recovery import ImageRecoveryResult as LegacyImageRecoveryResult
from src.image_recovery import _download_jpeg_bytes
from src.pdf_image_recovery import recover_pdf_image
from src.product_page_images import ProductPageImageResult, extract_image_from_url
from src.web_product_lookup import lookup_official_product_image

CONFIDENCE_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

IMAGE_RECOVERY_DEBUG_COLUMNS = [
    "row_id",
    "product_name",
    "brand",
    "model_sku",
    "supplier",
    "product_url",
    "final_image_found",
    "final_image_source",
    "final_confidence",
    "final_evidence",
    "needs_image_review",
    "final_error",
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
    "pdf_path_exists",
    "pdf_opened",
    "pdf_page_number",
    "pdf_image_objects_count",
    "pdf_candidates_after_filter",
    "pdf_rejection_reasons",
    "pdf_adjacent_pages_scanned",
    "pdf_page_render_fallback_used",
    "pdf_page_render_full_used",
    "exported_to_zip",
    "exported_image_filename",
    "export_skip_reason",
]


@dataclass
class ImageRecoveryResult:
    image_found: bool = False
    image_source: str = "none"
    image_url: str | None = None
    local_image_path: str | None = None
    image_filename: str | None = None
    confidence: str = "NONE"
    evidence: str = ""
    needs_image_review: bool = True
    error: str | None = None
    debug: dict = field(default_factory=dict)
    jpeg_bytes: bytes = b""

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("jpeg_bytes", None)
        return data


def _str_val(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _initial_debug(row: dict) -> dict:
    return {
        "row_id": _str_val(row.get("row_id") or row.get("Row ID") or row.get("id")),
        "product_name": _str_val(row.get("Product Name")),
        "brand": _str_val(row.get("Brand")),
        "model_sku": _str_val(row.get("Model/SKU") or row.get("SKU")),
        "supplier": _str_val(row.get("Supplier")),
        "product_url": _str_val(row.get("Product URL") or row.get("Supplier URL") or row.get("Source URL")),
        "product_url_fetch_ran": False,
        "product_url_fetch_status": "",
        "product_url_images_found": 0,
        "product_url_selected_image": "",
        "product_url_rejection_reasons": [],
        "web_lookup_ran": False,
        "web_queries_used": [],
        "web_results_found": 0,
        "official_candidate_pages": [],
        "selected_product_page_url": "",
        "selected_product_page_domain": "",
        "selected_web_image_url": "",
        "web_confidence_reason": "",
        "web_rejection_reasons": [],
        "pdf_path_exists": "",
        "pdf_opened": "",
        "pdf_page_number": row.get("_source_page_number") or "",
        "pdf_image_objects_count": 0,
        "pdf_candidates_after_filter": 0,
        "pdf_rejection_reasons": [],
        "pdf_adjacent_pages_scanned": False,
        "pdf_page_render_fallback_used": False,
        "pdf_page_render_full_used": False,
        "exported_to_zip": False,
        "exported_image_filename": "",
        "export_skip_reason": "",
    }


def _from_page_result(result: ProductPageImageResult, debug: dict) -> ImageRecoveryResult:
    jpeg = _download_jpeg_bytes(result.image_url) if result.image_found else b""
    if result.image_found and not jpeg:
        return ImageRecoveryResult(
            image_found=False,
            image_source=result.image_source,
            image_url=result.image_url or None,
            confidence="NONE",
            evidence=";".join(result.evidence),
            needs_image_review=True,
            error="image_download_failed",
            debug=debug,
        )
    confidence = result.confidence if result.image_found else "NONE"
    return ImageRecoveryResult(
        image_found=bool(result.image_found and jpeg),
        image_source=result.image_source if result.image_found else "none",
        image_url=result.image_url or None,
        confidence=confidence,
        evidence=";".join(result.evidence),
        needs_image_review=confidence != "HIGH",
        error=result.error or None,
        debug=debug,
        jpeg_bytes=jpeg,
    )


def _from_legacy(result: LegacyImageRecoveryResult, debug: dict) -> ImageRecoveryResult:
    confidence = result.confidence or "NONE"
    return ImageRecoveryResult(
        image_found=confidence in {"HIGH", "MEDIUM", "LOW"} and bool(result.jpeg_bytes or result.image_url),
        image_source=result.image_source or "none",
        image_url=result.image_url or None,
        local_image_path=result.local_image_path or None,
        image_filename=result.local_image_filename or None,
        confidence=confidence,
        evidence=";".join(result.evidence or []),
        needs_image_review=result.needs_image_review,
        error=result.error or None,
        debug=debug,
        jpeg_bytes=result.jpeg_bytes,
    )


def _finalize(result: ImageRecoveryResult, debug: dict) -> ImageRecoveryResult:
    debug["final_image_found"] = result.image_found
    debug["final_image_source"] = result.image_source
    debug["final_confidence"] = result.confidence
    debug["final_evidence"] = result.evidence
    debug["needs_image_review"] = result.needs_image_review
    debug["final_error"] = result.error or ""
    result.debug = debug
    return result


def recover_product_image(
    row: dict,
    source_pdf_path: str | Path | None = None,
    page_number: int | None = None,
    config: dict | None = None,
) -> ImageRecoveryResult:
    config = config or {}
    debug = _initial_debug(row)
    held_low: ImageRecoveryResult | None = None

    for key in ("Product URL", "Supplier URL", "Source URL"):
        page_url = _str_val(row.get(key))
        if not page_url:
            continue
        debug["product_url_fetch_ran"] = True
        page_result = extract_image_from_url(page_url, row, source_prefix="product_url")
        pdebug = page_result.debug or {}
        debug["product_url_fetch_status"] = pdebug.get("fetch_status")
        debug["product_url_images_found"] = pdebug.get("images_found", 0)
        debug["product_url_selected_image"] = pdebug.get("selected_image", "")
        debug["product_url_rejection_reasons"] = pdebug.get("rejection_reasons", [])
        result = _from_page_result(page_result, debug)
        if result.confidence in {"HIGH", "MEDIUM"}:
            return _finalize(result, debug)
        if result.confidence == "LOW" and not config.get("prefer_pdf_over_low_url", True):
            return _finalize(result, debug)
        if result.confidence == "LOW":
            held_low = result
        break

    if config.get("use_web_lookup", True):
        web = lookup_official_product_image(row, session_cache=config.get("session_cache"))
        debug.update(web.debug)
        result = _from_page_result(web.image_result, debug)
        if result.confidence in {"HIGH", "MEDIUM"}:
            return _finalize(result, debug)
        if result.confidence == "LOW":
            held_low = result

    pdf_path = source_pdf_path
    if pdf_path:
        pdf_debug: dict = {}
        pdf_row = row.copy()
        if page_number:
            pdf_row["_source_page_number"] = page_number
        legacy = recover_pdf_image(pdf_row, pdf_path, debug=pdf_debug)
        debug["pdf_path_exists"] = pdf_debug.get("pdf_path_exists", "")
        debug["pdf_opened"] = pdf_debug.get("pdf_opened", "")
        debug["pdf_page_number"] = pdf_row.get("_source_page_number") or ""
        debug["pdf_image_objects_count"] = pdf_debug.get("pdf_image_objects_count", 0)
        debug["pdf_candidates_after_filter"] = pdf_debug.get("pdf_candidates_after_filter", 0)
        debug["pdf_rejection_reasons"] = pdf_debug.get("pdf_rejection_reasons", [])
        debug["pdf_adjacent_pages_scanned"] = pdf_debug.get("pdf_adjacent_pages_scanned", False)
        debug["pdf_page_render_fallback_used"] = pdf_debug.get("pdf_page_render_fallback_used", False)
        debug["pdf_page_render_full_used"] = pdf_debug.get("pdf_page_render_full", False)
        result = _from_legacy(legacy, debug)
        if result.confidence in {"HIGH", "MEDIUM", "LOW"}:
            return _finalize(result, debug)

    if held_low is not None:
        return _finalize(held_low, debug)

    return _finalize(ImageRecoveryResult(error="no_image_found", debug=debug), debug)


def image_recovery_debug_row(row: dict, result: ImageRecoveryResult, export_info: dict | None = None) -> dict:
    data = {col: "" for col in IMAGE_RECOVERY_DEBUG_COLUMNS}
    data.update(result.debug or {})
    export_info = export_info or {}
    data["exported_to_zip"] = export_info.get("exported_to_zip", data.get("exported_to_zip", False))
    data["exported_image_filename"] = export_info.get("exported_image_filename", data.get("exported_image_filename", ""))
    data["export_skip_reason"] = export_info.get("export_skip_reason", data.get("export_skip_reason", ""))
    data["row_id"] = data.get("row_id") or _str_val(row.get("row_id") or row.get("Row ID") or row.get("id"))
    data["product_name"] = data.get("product_name") or _str_val(row.get("Product Name"))
    data["brand"] = data.get("brand") or _str_val(row.get("Brand"))
    data["model_sku"] = data.get("model_sku") or _str_val(row.get("Model/SKU") or row.get("SKU"))
    data["supplier"] = data.get("supplier") or _str_val(row.get("Supplier"))
    data["product_url"] = data.get("product_url") or _str_val(row.get("Product URL"))
    data["final_image_found"] = result.image_found
    data["final_image_source"] = result.image_source
    data["final_confidence"] = result.confidence
    data["final_evidence"] = result.evidence
    data["needs_image_review"] = result.needs_image_review
    data["final_error"] = result.error or ""
    return {col: data.get(col, "") for col in IMAGE_RECOVERY_DEBUG_COLUMNS}
