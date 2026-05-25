"""PDF product normalization and manufacturer URL lookup workflow."""

from __future__ import annotations

import hashlib
import re
from typing import Callable, Iterable

from src.intake_schema import SOURCE_PDF, SOURCE_PDF_AI
from src.official_product_lookup import ProductPageLookupResult, lookup_official_product_page

PDF_PRODUCT_URL_LOOKUP_NOTE = (
    "PDF rows use the extracted Model/SKU as the primary manufacturer lookup key. "
    "Product URL is saved only when an official-domain candidate validates as a "
    "real product page with SKU, brand, and product-name evidence."
)

_SPACE_RE = re.compile(r"\s+")
_MODEL_LABEL_RE = re.compile(
    r"^\s*(?:model|model\s*#|model\s*no\.?|sku|serial|serial\s*#|item\s*#|part\s*#|mfr\.?\s*#?)\s*[:#-]?\s*",
    re.IGNORECASE,
)


def _text(value: object) -> str:
    text = str(value or "").strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return _SPACE_RE.sub(" ", text)


def _normalize_model(value: object) -> str:
    text = _MODEL_LABEL_RE.sub("", _text(value))
    return text.strip(" .,;:")


def _first_text(row: dict, *keys: str) -> str:
    for key in keys:
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _infer_category(row: dict) -> str:
    existing = _text(row.get("Product Category") or row.get("Category") or row.get("Type"))
    if existing:
        return existing
    brand = _text(row.get("Brand")).lower()
    if brand in {"wolf", "sub-zero", "sub zero", "miele", "scotsman", "thermador", "monogram", "bosch"}:
        return "Appliances"
    haystack = f"{row.get('Product Name', '')} {row.get('Notes', '')}".lower()
    if re.search(r"\b(range|refrigerator|freezer|oven|microwave|dishwasher|ice maker|washer|dryer)\b", haystack):
        return "Appliances"
    if re.search(r"\b(sconce|lamp|chandelier|pendant|fixture|lighting)\b", haystack):
        return "Lighting"
    if re.search(r"\b(faucet|sink|toilet|tub|shower|drain)\b", haystack):
        return "Plumbing"
    if re.search(r"\b(chair|stool|sofa|sectional|bench)\b", haystack):
        return "Seating"
    if re.search(r"\b(table|desk|console|nightstand)\b", haystack):
        return "Tables"
    return ""


def _extraction_confidence(row: dict) -> int:
    existing = _text(row.get("Confidence Score"))
    if existing.isdigit():
        return max(0, min(100, int(existing)))
    score = 50
    if _text(row.get("Product Name")):
        score += 15
    if _text(row.get("Brand")):
        score += 15
    if _text(row.get("Model/SKU")):
        score += 20
    if _text(row.get("Dimensions")):
        score += 5
    if _text(row.get("Product Category")):
        score += 5
    return max(0, min(100, score))


def normalize_pdf_product_row(row: dict) -> dict:
    """Normalize PDF row fields while preserving source/extraction audit data."""
    out = dict(row)
    out["Product Name"] = _text(out.get("Product Name") or out.get("Description") or out.get("Item"))
    out["Brand"] = _first_text(out, "Brand", "Manufacturer", "Mfr", "Vendor")
    out["Model/SKU"] = _normalize_model(
        _first_text(out, "Model/SKU", "SKU", "Model", "Serial", "Serial Number", "Item #", "Part #")
    )
    out["Dimensions"] = _text(out.get("Dimensions"))
    out["Finish / Color"] = _text(out.get("Finish / Color") or out.get("Finish") or out.get("Color"))
    out["Material"] = _text(out.get("Material"))
    out["Product Category"] = _infer_category(out)
    out["Source Type"] = _text(out.get("Source Type")) or SOURCE_PDF
    out["_extracted_model_sku"] = _text(out.get("_extracted_model_sku")) or out["Model/SKU"]
    out["_extraction_confidence"] = _text(out.get("_extraction_confidence")) or _extraction_confidence(out)
    out.setdefault("_source_pdf_id", "")
    out.setdefault("_source_page_number", None)
    out.setdefault("_source_filename", "")
    return out


def _metadata_key(row: dict) -> tuple[str, str]:
    return (
        re.sub(r"[^a-z0-9]+", "", _text(row.get("Model/SKU")).lower()),
        re.sub(r"[^a-z0-9]+", " ", _text(row.get("Product Name")).lower()).strip(),
    )


def _attach_source_metadata(row: dict, source_rows: list[dict]) -> dict:
    if _text(row.get("_source_pdf_id")):
        return row
    model_key, name_key = _metadata_key(row)
    for source in source_rows:
        source_model_key, source_name_key = _metadata_key(source)
        if model_key and model_key == source_model_key:
            row["_source_pdf_id"] = source.get("_source_pdf_id", "")
            row["_source_page_number"] = source.get("_source_page_number")
            row["_source_filename"] = source.get("_source_filename", "")
            return row
        if name_key and name_key == source_name_key:
            row["_source_pdf_id"] = source.get("_source_pdf_id", "")
            row["_source_page_number"] = source.get("_source_page_number")
            row["_source_filename"] = source.get("_source_filename", "")
            return row
    return row


def normalize_pdf_product_rows(
    rows: Iterable[dict],
    *,
    source_rows: Iterable[dict] | None = None,
    source_pdf_bytes: bytes | None = None,
    source_filename: str = "",
) -> list[dict]:
    normalized = [normalize_pdf_product_row(row) for row in rows]
    parsed_sources = list(source_rows or [])
    for row in normalized:
        _attach_source_metadata(row, parsed_sources)
        if not _text(row.get("_source_pdf_id")) and source_pdf_bytes:
            row["_source_pdf_id"] = hashlib.sha1(source_pdf_bytes).hexdigest()[:12]
        if not _text(row.get("_source_filename")) and source_filename:
            row["_source_filename"] = source_filename
    return normalized


def enrich_pdf_rows_with_official_product_urls(
    rows: Iterable[dict],
    *,
    session_cache=None,
    lookup_fn: Callable[..., ProductPageLookupResult] = lookup_official_product_page,
) -> tuple[list[dict], list[str]]:
    """Confirm official manufacturer product pages and write Product URL."""
    enriched: list[dict] = []
    errors: list[str] = []
    for row in rows:
        out = dict(row)
        source = _text(out.get("Source Type"))
        model = _text(out.get("Model/SKU") or out.get("_extracted_model_sku"))
        brand = _text(out.get("Brand") or out.get("Supplier"))
        if source not in {SOURCE_PDF, SOURCE_PDF_AI} or _text(out.get("Product URL")) or not model or not brand:
            enriched.append(out)
            continue
        try:
            try:
                result = lookup_fn(out, session_cache=session_cache, validate_pages=True)
            except TypeError:
                result = lookup_fn(out, session_cache=session_cache)
        except Exception as exc:
            label = _text(out.get("Product Name")) or model
            errors.append(f"{label}: product URL lookup failed: {exc}")
            out["_product_url_lookup_status"] = "error"
            out["_product_url_lookup_error"] = str(exc)
            enriched.append(out)
            continue

        out["_product_url_lookup_confidence"] = result.confidence
        out["_product_url_lookup_reason"] = result.reason
        out["_product_url_lookup_queries"] = result.queries_used
        out["_product_url_lookup_candidates"] = result.candidate_pages
        if result.confidence in {"HIGH", "MEDIUM"} and result.selected_url:
            out["Product URL"] = result.selected_url
            out["_product_url_lookup_status"] = "confirmed"
        else:
            out["_product_url_lookup_status"] = "needs_manual_lookup"
        enriched.append(out)
    return enriched, errors
