"""PDF image recovery adapter with production source labels."""

from __future__ import annotations

from pathlib import Path

from src.image_recovery import ImageRecoveryResult, recover_from_pdf_crop


def recover_pdf_image(row: dict, pdf_path: str | Path, debug: dict | None = None) -> ImageRecoveryResult:
    result = recover_from_pdf_crop(row, pdf_path, debug=debug)
    evidence = set(result.evidence or [])
    if "page_render_content_crop" in evidence:
        result.image_source = "pdf_page_render_content_crop"
    elif "page_render_full" in evidence:
        result.image_source = "pdf_page_render_full"
    elif "adjacent_page_crop" in evidence:
        result.image_source = "pdf_adjacent_page_image"
    elif result.confidence in {"HIGH", "MEDIUM", "LOW"}:
        result.image_source = "pdf_embedded_image"
    return result
