"""
Product enrichment orchestrator for SCH DesignOps Intake.

Automatically fills blank fields (Product Name, Dimensions, Finish / Color,
Product Category, Product URL, Notes/materials) using Brave Search + httpx +
Claude Haiku. Never overwrites existing data.

Public API
----------
enrich_row(row: dict) -> tuple[dict, str | None, DimensionResult | None]
enrich_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[dict]]
"""

import json
import io
import logging
import os
import re
import time
import traceback
import urllib.parse
from datetime import datetime, timezone

import httpx
import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from src.brand_sources import brand_source_score, domains_for_brand, is_low_priority_domain
from src.brave_search import BRAVE_API_KEY, search_product_candidates
from src.category_ai import _normalise_category
from src.dimension_enrichment import DimensionResult as _DimensionResult, find_dimensions as _find_dimensions
from src.dimensions import extract_labeled_dimensions, has_complete_3d_dimensions
from src.image_assets import download_and_convert_image
from src.image_uploader import upload_image as _upload_image_to_cloudinary
from src.manufacturer_domains import get_domain_for_brand, record_discovered_domain
from src.product_image_extraction import (
    ImageCandidate,
    extract_product_image_candidates,
    select_best_product_image,
    top_candidate_diagnostics,
)
from src.source_memory import (
    apply_product_source_to_row,
    increment_source_failure,
    lookup_product_source,
    preferred_domain_hint,
    save_successful_source_from_row,
    storage_backend_name,
)
from src.url_utils import validate_http_url

try:
    import html2text as _html2text
except ImportError:
    _html2text = None

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None

load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

from src.enrichment_cache import (
    SessionCache as _SessionCache,
    SearchBudget as _SearchBudget,
    ProductEnrichmentCache as _ProductEnrichmentCache,
    normalize_key as _normalize_key,
    normalize_mode as _normalize_mode,
    budget_for_mode as _budget_for_mode,
    confidence_ok as _confidence_ok,
)

# Module-level singleton — lazy-loads on first access
_product_cache = _ProductEnrichmentCache()
_log = logging.getLogger(__name__)

# Cache field mappings: cache field name → row column name
_CACHE_GENERAL_FIELDS: dict[str, str] = {
    "product_url": "Product URL",
    "product_name": "Product Name",
    "brand": "Brand",
    "finish": "Finish / Color",
    "material": "Material",
    "product_category": "Product Category",
    "image_url": "Image URL",
}
_CACHE_DIM_FIELDS: dict[str, str] = {
    "dimensions": "Dimensions",
    "width_in": "Width (in)",
    "height_in": "Height (in)",
    "depth_in": "Depth (in)",
    "length_in": "Length (in)",
}
_ESSENTIAL_CACHE_FIELDS: list[str] = ["dimensions", "product_url"]

_ENRICHABLE_FIELDS: list = [
    "Product Name",
    "Dimensions",
    "Finish / Color",
    "Product Category",
    "Product URL",
]

_ENRICHMENT_DEBUG_FIELDS: list[str] = [
    "skipped_by_source_type",
    "skipped_by_missing_brand",
    "skipped_by_missing_model",
    "cache_hit",
    "cache_had_blank_dimensions",
    "cache_had_blank_image",
    "fresh_extraction_forced",
    "product_url",
    "product_url_confidence",
    "page_fetch_attempted",
    "page_fetch_success",
    "spec_table_found",
    "json_ld_found",
    "next_data_found",
    "shopify_json_found",
    "spec_pdf_links_found",
    "spec_pdf_fetched",
    "dimensions_before_enrichment",
    "dimensions_extracted",
    "dimension_confidence",
    "dimension_source",
    "dimension_source_url",
    "dimension_parse_method",
    "dimension_raw_text",
    "dimension_raw_snippet",
    "partial_dimensions_found",
    "rejected_dimensions_reason",
    "final_dimensions",
    "final_dimension_writeback_success",
    "skipped_reason",
    "budget_blocked",
    "enrichment_status",
    "enrichment_error",
    "stage_log",
    "debug_traceback",
    "original_image_url",
    "cloudinary_url",
    "image_confidence",
    "image_source_url",
    "image_failure_reason",
    "image_candidate_diagnostics",
    "cloudinary_status",
    "cloudinary_error",
    "programa_image_ready",
    "sku_used_for_lookup",
    "manufacturer_domain_used",
    "search_provider",
    "search_query_used",
    "product_url_candidates",
    "selected_product_url",
    "selected_product_url_confidence",
    "was_searched",
    "selected_strategy",
    "retry_recommended",
    "dimensions_status",
    "image_status",
    "source_status",
    "product_component_type",
    "round_attempted",
    "sku_match_location",
    "page_fetched",
    "dimensions_found",
    "image_candidates_count",
    "selected_image_url",
    "fields_filled",
    "budget_spent",
    "budget_stop_reason",
    "total_budget_remaining",
    "search_budget_remaining",
    "openai_budget_remaining",
    "reason_row_skipped",
    "skip_reason",
    "skip_threshold_hit",
    "budget_skip_threshold",
    "pages_found",
    "pages_fully_parsed",
    "dimensions_successfully_extracted",
    "dimensions_blocked_by_budget",
    "dimensions_blocked_by_parser",
    "spec_sheets_found",
    "spec_sheets_parsed",
    "image_only_success",
    "duplicate_model_skipped",
    "duplicate_model_source_index",
    "searches_avoided",
    "useful_fields_found",
    "stored_source_hit",
    "stored_source_used",
    "stored_source_updated",
    "stored_source_rejected_reason",
    "source_memory_backend",
    "duplicate_searches_avoided_from_cache",
    "knowledge_base_hit",
    "knowledge_base_miss",
    "knowledge_base_source_used",
    "knowledge_base_updated",
    "knowledge_base_rejected_reason",
    "preferred_domain_used",
    "invalid_url_error",
    "invalid_url_generated",
    "invalid_url_source",
    "invalid_url_step",
    "invalid_url_normalized_brand",
    "invalid_url_normalized_model",
    "invalid_url_search_query",
    "invalid_url_fallback_attempted",
]

MIN_USE_SCORE = 40   # below this: skip entirely, note in Notes
MIN_CONF_SCORE = 60  # 40–59: fill fields but force Review Required = True


def _str_val(v) -> str:
    """Safely convert a row cell value to a stripped string, handling None."""
    if v is None:
        return ""
    return str(v).strip()


def _norm_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _str_val(value).lower())


def _domain_of(url: str) -> str:
    try:
        domain = urllib.parse.urlparse(url).netloc.lower()
        return domain[4:] if domain.startswith("www.") else domain
    except Exception:
        return ""


def _domain_matches(candidate_domain: str, official_domain: str) -> bool:
    domain = str(candidate_domain or "").lower()
    official = str(official_domain or "").lower()
    return bool(domain and official and (domain == official or domain.endswith("." + official)))


def _dimension_part_count(value: object) -> int:
    parts = extract_labeled_dimensions(value)
    return sum(1 for key in ("width", "height", "depth") if parts.get(key))


def _dimension_confidence_from_parts(value: object, domain_score: int) -> str:
    count = _dimension_part_count(value)
    if count >= 3:
        return "high" if domain_score >= 80 else "medium"
    if count == 2:
        return "medium"
    if count == 1:
        return "low"
    return ""


def _has_good_image(row: dict) -> bool:
    """True when the row already has a usable hosted image URL."""
    return _is_absolute_https(_str_val(row.get("Image URL")))


def _needs_dimension_recovery(row: dict) -> bool:
    dims = _str_val(row.get("Dimensions"))
    return not dims or not has_complete_3d_dimensions(dims)


def _needs_image_recovery(row: dict) -> bool:
    return not _has_good_image(row)


def _append_stage(debug: dict, stage: str) -> None:
    if not stage:
        return
    existing = [
        part.strip()
        for part in _str_val(debug.get("stage_log")).split(";")
        if part.strip()
    ]
    if stage not in existing:
        existing.append(stage)
        debug["stage_log"] = "; ".join(existing)
    _log.info("[%s]", stage)


def _missing_enrichment_fields(row: dict) -> list[str]:
    missing: list[str] = []
    for field in _ENRICHABLE_FIELDS:
        if field == "Dimensions":
            if _needs_dimension_recovery(row):
                missing.append(field)
        elif not _str_val(row.get(field)):
            missing.append(field)
    if _needs_image_recovery(row):
        missing.append("Image URL")
    return missing


def _qualifies(row: dict) -> bool:
    """True if this row should be sent through enrichment."""
    source = _str_val(row.get("Source Type", ""))
    if not _str_val(row.get("Brand")):
        return False
    if not _str_val(row.get("Model/SKU")):
        return False
    # Previously enriched rows should still get another deterministic pass when
    # dimensions or images are missing. They should not keep re-running only for
    # cosmetic/general blanks.
    if source.endswith("_Enriched"):
        return _needs_dimension_recovery(row) or _needs_image_recovery(row)
    return bool(_missing_enrichment_fields(row))


def _skip_debug(row: dict) -> dict:
    source = _str_val(row.get("Source Type", ""))
    brand_missing = not _str_val(row.get("Brand"))
    model_missing = not _str_val(row.get("Model/SKU"))
    source_blocked = False
    if source.endswith("_Enriched") and not (_needs_dimension_recovery(row) or _needs_image_recovery(row)):
        source_blocked = True
    return {
        "skipped_by_source_type": source_blocked,
        "skipped_by_missing_brand": brand_missing,
        "skipped_by_missing_model": model_missing,
    }


def _build_search_query(row: dict) -> str:
    """Build a Brave Search query; prioritise spec sheets when dimensions are needed."""
    dims = _str_val(row.get("Dimensions"))
    needs_dims = not dims or not has_complete_3d_dimensions(dims)

    parts = [
        _str_val(row.get("Brand")),
        _str_val(row.get("Model/SKU")),
        _str_val(row.get("Product Name")),
    ]
    suffix = (
        "dimensions width height depth spec sheet official"
        if needs_dims
        else "specifications official"
    )
    query = " ".join(p for p in parts if p) + " " + suffix
    domain_match = get_domain_for_brand(_str_val(row.get("Brand")))
    if domain_match:
        domain, _source = domain_match
        return f"site:{domain} {query}"
    return query


def _sku_lookup_variants(model: object) -> list[str]:
    """Generate exact and normalized SKU/model variants for low-cost lookup."""
    raw = _str_val(model)
    if not raw:
        return []
    seen: list[str] = []

    def add(value: object) -> None:
        text = _str_val(value)
        if text and text not in seen:
            seen.append(text)

    add(raw)
    no_spaces = re.sub(r"\s+", "", raw)
    add(no_spaces)
    add(re.sub(r"[/\\]+", "", no_spaces))
    add(re.sub(r"[-\s]+", "", raw))
    add(re.sub(r"\s+", "-", raw))
    if "/" in no_spaces or "\\" in no_spaces:
        parts = re.split(r"[/\\]+", no_spaces)
        base = parts[0]
        suffix = parts[-1]
        add(base)
        if base and suffix and len(suffix) <= 3:
            add(f"{base}{suffix}")
            if base.upper().endswith("RID") and suffix.upper() in {"R", "L"}:
                add(f"{base[:-3]}{suffix}")
            elif base.upper().endswith("ID") and len(suffix) == 1:
                add(base[:-2])
    tokens = re.split(r"[-\s]+", raw)
    if len(tokens) > 1 and 1 <= len(tokens[-1]) <= 3:
        add(raw[: -len(tokens[-1])].rstrip(" -"))
    return seen


def _quoted_or_clause(values: list[str], limit: int = 5) -> str:
    values = [value for value in values if value][:limit]
    if not values:
        return ""
    if len(values) == 1:
        return f'"{values[0]}"'
    return "(" + " OR ".join(f'"{value}"' for value in values) + ")"


def _build_sku_lookup_queries(row: dict, manufacturer_domain: str = "") -> list[str]:
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))
    product_name = _str_val(row.get("Product Name"))
    category = _str_val(row.get("Product Category"))
    variants = _sku_lookup_variants(model)
    variant_clause = _quoted_or_clause(variants)
    context = " ".join(
        part
        for part in (
            product_name,
            category,
        )
        if part
    )
    queries: list[str] = []
    source_domains = domains_for_brand(brand, manufacturer_domain)
    primary_domain = source_domains[0] if source_domains else ""
    if primary_domain and variant_clause:
        queries.append(
            f"site:{primary_domain} {variant_clause} dimensions specifications spec sheet product"
        )
    if primary_domain and model:
        queries.extend([
            f'site:{primary_domain} "{model}" product',
            f'site:{primary_domain} "{model}" spec sheet PDF',
            f'site:{primary_domain} "{model}" installation guide',
            f'site:{primary_domain} "{model}" installation guide PDF',
            f'site:{primary_domain} "{model}" dimensions',
            f'site:{primary_domain} "{model}" specification',
            f'site:{primary_domain} "{model}" specifications',
            f'site:{primary_domain} "{model}" "Width" "Height" "Depth"',
        ])
    for extra_domain in source_domains[1:3]:
        if model:
            queries.extend([
                f'site:{extra_domain} "{model}" dimensions',
                f'site:{extra_domain} "{model}" spec sheet PDF',
            ])
    if primary_domain and context and variants:
        queries.append(f"site:{primary_domain} {variants[0]} {context} dimensions")
    if brand and model:
        queries.extend([
            f'"{brand}" "{model}" dimensions',
            f'"{brand}" "{model}" specification',
            f'"{brand}" "{model}" specifications',
            f'"{brand}" "{model}" spec sheet PDF',
            f'"{brand}" "{model}" spec sheet pdf',
            f'"{brand}" "{model}" installation guide',
            f'"{brand}" "{model}" installation guide pdf',
            f'"{brand}" "{model}" product page',
            f'"{model}" PDF dimensions',
        ])
    for variant in variants[1:4]:
        if brand and variant:
            queries.append(f'"{brand}" "{variant}" dimensions specifications')
    if brand and model:
        queries.extend([
            f'"{brand}" "{model}" dimensions image product page',
        ])
    if brand and model and product_name:
        queries.append(f'"{brand}" "{model}" "{product_name}"')
        queries.append(f'"{brand}" "{product_name}"')
    # Dedupe while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for query in queries:
        if query not in seen:
            seen.add(query)
            deduped.append(query)
    return deduped


def _apply_enrichment(
    row: dict,
    extracted: dict,
    source_url: str,
    domain_score: int,
) -> dict:
    """
    Apply extracted fields to a row copy, filling blank fields only.
    Sets Source Type suffix and confidence flags. Never overwrites existing data.
    """
    updated = row.copy()

    for field in _ENRICHABLE_FIELDS:
        if field == "Product URL":
            if not _str_val(updated.get("Product URL")):
                updated["Product URL"] = source_url
            continue

        if field == "Dimensions":
            dim_extracted = _str_val(extracted.get("Dimensions"))
            if dim_extracted:
                extracted_part_count = _dimension_part_count(dim_extracted)
                existing_part_count = _dimension_part_count(updated.get("Dimensions"))
                if extracted_part_count >= 3:
                    # Always accept complete 3D, even if row already had partial dims.
                    updated["Dimensions"] = dim_extracted
                    updated["Dimension Confidence"] = _dimension_confidence_from_parts(dim_extracted, domain_score)
                    updated["Dimension Source URL"] = source_url
                    updated["Dimension Lookup Status"] = "found"
                elif extracted_part_count >= 2 and existing_part_count < extracted_part_count:
                    # Useful partial dimensions should not be lost just because
                    # one axis is missing. Store them as medium confidence.
                    updated["Dimensions"] = dim_extracted
                    updated["Dimension Confidence"] = "medium"
                    updated["Dimension Source URL"] = source_url
                    updated["Dimension Lookup Status"] = "found"
                else:
                    # Ambiguous/single-axis partial found — note it, but do not
                    # fill the primary dimensions field.
                    existing_notes = _str_val(updated.get("Notes"))
                    partial_note = (
                        f"[Partial dimension found: {dim_extracted}; "
                        "full W x H x D still needed]"
                    )
                    if partial_note not in existing_notes:
                        updated["Notes"] = (
                            f"{existing_notes} {partial_note}".strip()
                            if existing_notes else partial_note
                        )
            continue

        # Never overwrite non-empty fields for all other enrichable fields
        if _str_val(updated.get(field)):
            continue

        value = _str_val(extracted.get(field))
        if not value:
            continue

        if field == "Product Category":
            value = _normalise_category(value)

        if value:
            updated[field] = value

    # Materials → Notes (only if not already expressed in Finish / Color)
    materials = _str_val(extracted.get("materials"))
    finish = _str_val(updated.get("Finish / Color")).lower()
    if materials and materials.lower() not in finish:
        existing_notes = _str_val(updated.get("Notes"))
        mat_tag = f"[Materials: {materials}]"
        updated["Notes"] = f"{existing_notes} {mat_tag}".strip() if existing_notes else mat_tag

    # Source Type suffix
    original = _str_val(updated.get("Source Type"))
    if original and not original.endswith("_Enriched"):
        updated["Source Type"] = f"{original}_Enriched"
    elif not original:
        updated["Source Type"] = "Enriched"

    # Confidence flagging
    if domain_score < MIN_CONF_SCORE:
        updated["Review Required"] = True
        updated["Suggested Action"] = "Enriched from low-confidence source — verify fields"

    return updated


def _fetch_page_html(
    url: str,
    *,
    debug: dict | None = None,
    source: str = "direct",
    step: str = "page_fetch",
    row: dict | None = None,
    query: str = "",
    fallback_attempted: bool = True,
) -> str:
    """Fetch URL with httpx and return raw HTML. Empty string on error."""
    invalid_reason = _url_validation_error(url)
    if invalid_reason:
        _stamp_invalid_url_debug(
            debug,
            url,
            source=source,
            step=step,
            row=row,
            query=query,
            fallback_attempted=fallback_attempted,
            reason=invalid_reason,
        )
        return ""
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SCH-Intake/1.0)"},
            timeout=10,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text
    except Exception:
        return ""


def _fetch_page_html_with_debug(url: str, **kwargs) -> str:
    """Call _fetch_page_html with diagnostics while tolerating older test doubles."""
    try:
        return _fetch_page_html(url, **kwargs)
    except TypeError as exc:
        if "unexpected keyword argument" in str(exc):
            return _fetch_page_html(url)
        raise


def _fetch_page_html_budgeted(
    url: str,
    *,
    session_cache: "_SessionCache | None" = None,
    budget: "_SearchBudget | None" = None,
    debug: dict | None = None,
    source: str = "product_page",
    step: str = "page_fetch",
    row: dict | None = None,
    query: str = "",
) -> str:
    invalid_reason = _url_validation_error(url)
    if invalid_reason:
        if debug is not None:
            debug["page_fetch_attempted"] = False
            debug["page_fetch_success"] = False
            debug["page_fetched"] = False
        _stamp_invalid_url_debug(
            debug,
            url,
            source=source,
            step=step,
            row=row,
            query=query,
            fallback_attempted=True,
            reason=invalid_reason,
        )
        return ""
    if session_cache is not None and url in session_cache.urls:
        if debug is not None:
            debug["page_fetch_success"] = bool(session_cache.urls.get(url))
            debug["page_fetched"] = bool(session_cache.urls.get(url))
        return str(session_cache.urls.get(url) or "")
    if budget is not None and not budget.can_fetch():
        if debug is not None:
            debug["budget_blocked"] = True
            debug["skipped_reason"] = "budget blocked product page fetch"
        return ""
    html = _fetch_page_html_with_debug(
        url,
        debug=debug,
        source=source,
        step=step,
        row=row,
        query=query,
    )
    if budget is not None:
        budget.consume_fetch()
    if html and session_cache is not None:
        session_cache.urls[url] = html
    if debug is not None:
        debug["page_fetch_attempted"] = True
        debug["page_fetch_success"] = bool(html)
        debug["page_fetched"] = bool(html)
    return html


def _html_to_text(html: str) -> str:
    """Convert raw HTML to plain text (max 6 000 chars). Returns empty string for empty input."""
    if not html:
        return ""
    if _html2text is not None:
        h = _html2text.HTML2Text()
        h.ignore_links = True
        h.ignore_images = True
        text = h.handle(html)
    else:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s{2,}", " ", text)
    return text[:6000].strip()


def _first_json_ld_product(html: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text() or ""
        if not text.strip():
            continue
        try:
            data = json.loads(text)
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if isinstance(node, dict) and isinstance(node.get("@graph"), list):
                nodes.extend(node["@graph"])
            if isinstance(node, dict) and "product" in str(node.get("@type", "")).lower():
                return node
    return {}


def _extract_meta_content(soup: BeautifulSoup, *keys: str) -> str:
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and _str_val(tag.get("content")):
            return _str_val(tag.get("content"))
    return ""


def _extract_finish_material_from_text(text: str) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    finish = ""
    material = ""
    boundary = r"(?=\s+(?:finish|color|colour|materials?|dimensions?|width|height|depth)\s*[:\-]|$|[|;,])"
    finish_match = re.search(rf"\b(?:finish|color|colour)\s*[:\-]\s*(.+?){boundary}", text, re.IGNORECASE)
    material_match = re.search(rf"\bmaterials?\s*[:\-]\s*(.+?){boundary}", text, re.IGNORECASE)
    if finish_match:
        finish = finish_match.group(1).strip()
    if material_match:
        material = material_match.group(1).strip()
    return finish, material


def _extract_verified_page_fields(html: str, row: dict, page_url: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    product = _first_json_ld_product(html)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    og_title = _extract_meta_content(soup, "og:title", "twitter:title")
    description = _extract_meta_content(soup, "description", "og:description", "twitter:description")
    visible_text = _html_to_text(html)
    finish, material = _extract_finish_material_from_text(visible_text)
    brand_value = ""
    product_brand = product.get("brand") if isinstance(product, dict) else ""
    if isinstance(product_brand, dict):
        brand_value = _str_val(product_brand.get("name"))
    elif product_brand:
        brand_value = _str_val(product_brand)

    name = _str_val(product.get("name") if isinstance(product, dict) else "") or og_title or title
    if name and "|" in name:
        name = name.split("|", 1)[0].strip()

    category = _str_val(product.get("category") if isinstance(product, dict) else "")
    if not category and _str_val(row.get("Product Category")):
        category = _str_val(row.get("Product Category"))

    fields = {
        "Product Name": name,
        "Brand": brand_value,
        "Finish / Color": finish,
        "Material": material,
        "Product Category": _normalise_category(category) if category else "",
        "Description": description,
    }
    # Avoid writing generic title-only names when they do not contain any row token.
    if fields["Product Name"] and _str_val(row.get("Product Name")):
        page_name_norm = _norm_token(fields["Product Name"])
        row_tokens = [
            _norm_token(t)
            for t in re.findall(r"[A-Za-z0-9]{4,}", _str_val(row.get("Product Name")))
        ]
        if row_tokens and not any(t and t in page_name_norm for t in row_tokens):
            fields["Product Name"] = ""
    return fields


def _fill_blank_fields_from_verified_page(row: dict, fields: dict, page_url: str, confidence: str) -> tuple[dict, list[str]]:
    updated = row.copy()
    filled: list[str] = []
    for column in ("Product Name", "Brand", "Finish / Color", "Material", "Product Category"):
        value = _str_val(fields.get(column))
        if not value or _str_val(updated.get(column)):
            continue
        updated[column] = value
        filled.append(column)
    description = _str_val(fields.get("Description"))
    if description and not _str_val(updated.get("Notes")):
        updated["Notes"] = f"[Description: {description[:500]}]"
        filled.append("Notes")
    existing_product_url = _str_val(updated.get("Product URL"))
    if not existing_product_url or _reject_weak_product_url(existing_product_url):
        updated["Product URL"] = page_url
        filled.append("Product URL")
    original = _str_val(updated.get("Source Type"))
    if original and not original.endswith("_Enriched"):
        updated["Source Type"] = f"{original}_Enriched"
    elif not original:
        updated["Source Type"] = "Enriched"
    if confidence == "medium":
        updated["Review Required"] = True
    return updated, filled


def _fetch_page_text(url: str) -> str:
    """Fetch URL and return plain text (max 6 000 chars). Empty string on error."""
    return _html_to_text(_fetch_page_html(url))


_IMAGE_FILE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def _is_valid_image_url(url: str) -> bool:
    """True if url is an absolute https URL whose path ends in a recognized image extension.
    Used as a pre-filter for <img> tags to reduce noise before content-type validation."""
    if not url or not isinstance(url, str):
        return False
    url_lower = url.lower()
    if not url_lower.startswith("https://"):
        return False
    path = url_lower.split("?")[0].split("#")[0]
    return path.endswith(_IMAGE_FILE_EXTENSIONS)


def _is_absolute_https(url: str) -> bool:
    """True if url is an absolute https URL."""
    return bool(url) and isinstance(url, str) and url.lower().startswith("https://")


def _url_validation_error(url: object) -> str:
    """Return a reason when a URL should not be fetched."""
    return validate_http_url(url)


def _stamp_invalid_url_debug(
    debug: dict | None,
    url: object,
    *,
    source: str,
    step: str,
    row: dict | None = None,
    query: str = "",
    fallback_attempted: bool = True,
    reason: str = "",
) -> None:
    """Record invalid URL diagnostics without surfacing raw errors to normal UI."""
    if debug is None:
        return
    raw = _str_val(url)
    reason = reason or _url_validation_error(raw)
    row = row or {}
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU") or row.get("SKU"))
    debug["invalid_url_error"] = reason
    debug["invalid_url_generated"] = raw
    debug["invalid_url_source"] = source
    debug["invalid_url_step"] = step
    debug["invalid_url_normalized_brand"] = _norm_token(brand)
    debug["invalid_url_normalized_model"] = _norm_token(model)
    debug["invalid_url_search_query"] = query or _str_val(debug.get("search_query_used"))
    debug["invalid_url_fallback_attempted"] = "yes" if fallback_attempted else "no"
    if not _str_val(debug.get("skipped_reason")):
        debug["skipped_reason"] = "invalid URL skipped before fetch"
    _log.warning(
        "Invalid enrichment URL skipped step=%s source=%s brand=%s model=%s query=%s url=%r reason=%s fallback=%s",
        step,
        source,
        _norm_token(brand),
        _norm_token(model),
        debug["invalid_url_search_query"],
        raw,
        reason,
        fallback_attempted,
    )


def _check_image_content_type(url: str) -> bool:
    """Confirm url points to an image via HEAD, falling back to a GET byte-range request.

    Many manufacturer CDNs (Scene7, Imgix, Akamai) block HEAD with 405/403.
    If HEAD fails with a non-2xx status we retry with a small GET range request,
    which is universally supported and still confirms the content-type without
    downloading the whole image.
    """
    if _url_validation_error(url):
        return False
    _ua = {"User-Agent": "Mozilla/5.0 (compatible; SCH-Intake/1.0)"}
    try:
        resp = httpx.head(url, headers=_ua, timeout=5, follow_redirects=True)
        if 200 <= resp.status_code < 300:
            ct = resp.headers.get("content-type", "").lower()
            return ct.startswith("image/")
        # HEAD blocked (405, 403, etc.) — try a minimal GET
        resp2 = httpx.get(
            url,
            headers={**_ua, "Range": "bytes=0-1023"},
            timeout=8,
            follow_redirects=True,
        )
        if 200 <= resp2.status_code < 300 or resp2.status_code == 206:
            ct = resp2.headers.get("content-type", "").lower()
            return ct.startswith("image/")
        return False
    except Exception:
        return False


_WEAK_PAGE_RE = re.compile(r"\b(sitemap|search|category|collections?|browse|tag|blog|support)$", re.IGNORECASE)


def _page_title_and_description(html: str) -> tuple[str, str]:
    if not html:
        return "", ""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    description = ""
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    if meta:
        description = _str_val(meta.get("content"))
    return title, description


def _model_match_locations(model: str, url: str, html: str, title: str = "", description: str = "") -> list[str]:
    model_norm = _norm_token(model)
    if not model_norm:
        return []
    locations: list[str] = []
    checks = {
        "url": url,
        "title": title,
        "description": description,
        "html": html[:250_000],
    }
    for name, value in checks.items():
        if model_norm in _norm_token(value):
            locations.append(name)
    return locations


def _product_name_similarity_score(row_name: str, page_text: str) -> int:
    tokens = [t for t in re.findall(r"[a-z0-9]{4,}", row_name.lower()) if t not in {"with", "and", "the", "product"}]
    if not tokens:
        return 0
    haystack = page_text.lower()
    hits = sum(1 for token in tokens if token in haystack)
    return int((hits / max(1, len(tokens))) * 15)


def _reject_weak_product_url(url: str) -> str:
    invalid_reason = _url_validation_error(url)
    if invalid_reason:
        return invalid_reason
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/").lower()
    if not path:
        return "homepage"
    if "sitemap" in path:
        return "sitemap page"
    if path.endswith(".pdf"):
        return "spec PDF, not product page"
    if "/search" in path or "search" in urllib.parse.parse_qs(parsed.query):
        return "search result page"
    if _WEAK_PAGE_RE.search(path):
        return "category/browse page"
    return ""


def _score_verified_product_page(
    row: dict,
    url: str,
    html: str,
    *,
    manufacturer_domain: str = "",
    title: str = "",
    description: str = "",
) -> dict:
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))
    page_domain = _domain_of(url)
    official_domains = domains_for_brand(brand, manufacturer_domain)
    weak_reason = _reject_weak_product_url(url)
    if weak_reason:
        return {
            "score": 0,
            "confidence": "none",
            "official_domain": False,
            "matched_sku": False,
            "matched_brand": False,
            "sku_match_location": "",
            "rejection_reason": weak_reason,
        }

    locations = _model_match_locations(model, url, html, title, description)
    matched_sku = bool(locations)
    brand_norm = _norm_token(brand)
    brand_text = _norm_token(" ".join([url, title, description, html[:80_000]]))
    matched_brand = bool(brand_norm and brand_norm in brand_text)
    official = any(_domain_matches(page_domain, official_domain) for official_domain in official_domains)

    if brand and not matched_brand and not official:
        return {
            "score": 0,
            "confidence": "none",
            "official_domain": official,
            "matched_sku": matched_sku,
            "matched_brand": False,
            "sku_match_location": ",".join(locations),
            "rejection_reason": "brand not found on candidate page",
        }
    if model and not matched_sku:
        return {
            "score": 20 if official and matched_brand else 0,
            "confidence": "low",
            "official_domain": official,
            "matched_sku": False,
            "matched_brand": matched_brand,
            "sku_match_location": "",
            "rejection_reason": "exact SKU/model not found",
        }

    score = 0
    if matched_sku:
        score += 50
    if matched_brand:
        score += 15
    if official:
        score += 25
    score += _product_name_similarity_score(_str_val(row.get("Product Name")), " ".join([title, description, html[:80_000]]))
    if re.search(r'application/ld\+json|@type["\']?\s*:\s*["\']Product', html, re.IGNORECASE):
        score += 10
    if re.search(r"<table|<dl|dimensions?|specifications?|width|height|depth", html, re.IGNORECASE):
        score += 10
    if extract_product_image_candidates(html, url, row):
        score += 10
    score = min(100, score)
    confidence = "high" if official and matched_sku and score >= 80 else ("medium" if matched_sku and score >= 60 else "low")
    return {
        "score": score,
        "confidence": confidence,
        "official_domain": official,
        "matched_sku": matched_sku,
        "matched_brand": matched_brand,
        "sku_match_location": ",".join(locations),
        "rejection_reason": "",
    }


def extract_image_url(html: str) -> str | None:
    """
    Compatibility wrapper: return the top scored image candidate URL.

    The enrichment path uses the richer candidate/scoring API below so it can
    keep diagnostics and Cloudinary upload status. This wrapper preserves the
    older public test surface and simple callers.
    """
    candidates = extract_product_image_candidates(html, "", {})
    for source_type in ("og:image", "twitter:image", "json_ld_image"):
        for candidate in candidates:
            if candidate.source_type == source_type and not candidate.rejection_reason:
                return candidate.url
    for candidate in candidates:
        if not candidate.rejection_reason:
            return candidate.url
    return None


def _cloudinary_configured() -> bool:
    return bool(
        os.getenv("CLOUDINARY_CLOUD_NAME")
        and os.getenv("CLOUDINARY_API_KEY")
        and os.getenv("CLOUDINARY_API_SECRET")
    )


def _image_debug_defaults() -> dict:
    return {
        "original_image_url": "",
        "cloudinary_url": "",
        "image_confidence": "",
        "image_source_url": "",
        "image_failure_reason": "",
        "image_candidate_diagnostics": "",
        "cloudinary_status": "",
        "cloudinary_error": "",
        "programa_image_ready": False,
        "Image Upload Status": "",
    }


def _candidate_debug_json(candidates: list[ImageCandidate]) -> str:
    try:
        return json.dumps(top_candidate_diagnostics(candidates, limit=3), ensure_ascii=True)
    except Exception:
        return "[]"


def _upload_candidate_image(candidate: ImageCandidate, row: dict) -> tuple[str | None, dict]:
    """
    Convert/upload the accepted candidate through Cloudinary when configured.

    If Cloudinary is unavailable or fails, the caller can still keep the original
    validated HTTPS image URL and gets explicit debug fields explaining why.
    """
    debug = {
        "original_image_url": candidate.url,
        "cloudinary_url": "",
        "cloudinary_status": "skipped_unconfigured",
        "cloudinary_error": "",
        "programa_image_ready": False,
    }

    if not _cloudinary_configured():
        debug["cloudinary_error"] = "Cloudinary env vars are not configured."
        return None, debug

    converted = download_and_convert_image(
        candidate.url,
        brand=_str_val(row.get("Brand")),
        model_sku=_str_val(row.get("Model/SKU")),
        product_name=_str_val(row.get("Product Name")),
    )
    if converted.get("image_status") != "downloaded":
        debug["cloudinary_status"] = converted.get("image_status") or "conversion_failed"
        debug["cloudinary_error"] = converted.get("error") or "Image conversion failed."
        return None, debug

    try:
        upload_file = io.BytesIO(converted.get("jpeg_bytes") or b"")
        upload_file.name = converted.get("local_image_filename") or "product.jpg"
        cloudinary_url = _upload_image_to_cloudinary(upload_file)
    except Exception as exc:
        cloudinary_url = None
        debug["cloudinary_error"] = str(exc)

    if cloudinary_url and _is_absolute_https(cloudinary_url):
        debug["cloudinary_url"] = cloudinary_url
        debug["cloudinary_status"] = "uploaded"
        debug["programa_image_ready"] = True
        return cloudinary_url, debug

    debug["cloudinary_status"] = "failed"
    debug["cloudinary_error"] = debug["cloudinary_error"] or "Cloudinary upload failed or returned no secure_url."
    return None, debug


def _extract_image_from_html(
    raw_html: str,
    product_url: str,
    row: dict,
    cache_key: str = "",
) -> tuple[str | None, dict]:
    debug = _image_debug_defaults()
    _append_stage(debug, "SEARCHING_IMAGES")
    debug["image_source_url"] = product_url
    candidates = extract_product_image_candidates(raw_html, product_url, row)
    debug["image_candidate_diagnostics"] = _candidate_debug_json(candidates)

    if not candidates:
        debug["cloudinary_status"] = "not_attempted"
        debug["cloudinary_error"] = "No image candidates found on verified product page."
        debug["image_failure_reason"] = debug["cloudinary_error"]
        if cache_key:
            existing_entry = _product_cache.get(cache_key) or {}
            if not existing_entry.get("image_url"):
                _product_cache.update(cache_key, {"image_url": None, "image_url__reason": "no image candidates found on page"})
        return None, debug

    selected = select_best_product_image(
        candidates,
        content_type_checker=_check_image_content_type,
    )
    debug["image_candidate_diagnostics"] = _candidate_debug_json(candidates)
    if selected is None:
        debug["cloudinary_status"] = "not_attempted"
        debug["cloudinary_error"] = "No HIGH/MEDIUM image candidate passed validation."
        debug["image_failure_reason"] = debug["cloudinary_error"]
        if cache_key:
            existing_entry = _product_cache.get(cache_key) or {}
            if not existing_entry.get("image_url"):
                _product_cache.update(cache_key, {"image_url": None, "image_url__reason": debug["cloudinary_error"]})
        return None, debug

    debug["original_image_url"] = selected.url
    debug["image_confidence"] = selected.confidence
    debug["image_failure_reason"] = ""

    cloudinary_url, upload_debug = _upload_candidate_image(selected, row)
    debug.update(upload_debug)
    final_url = cloudinary_url or selected.url
    debug["programa_image_ready"] = bool(_is_absolute_https(final_url))
    debug["Image Upload Status"] = (
        "cloudinary_uploaded"
        if cloudinary_url
        else f"using_original_url:{debug.get('cloudinary_status') or 'not_uploaded'}"
    )

    if cache_key:
        existing_entry = _product_cache.get(cache_key) or {}
        if not existing_entry.get("image_url"):
            _product_cache.update(
                cache_key,
                {
                    "image_url": final_url,
                    "original_image_url": selected.url,
                    "image_confidence": selected.confidence,
                    "image_source_url": product_url,
                    "cloudinary_url": cloudinary_url or "",
                    "general_confidence": "medium",
                },
            )
    return final_url, debug


def _try_image_from_url(
    product_url: str,
    cache_key: str = "",
    session_cache: "_SessionCache | None" = None,
    budget: "_SearchBudget | None" = None,
    row: dict | None = None,
    return_debug: bool = False,
) -> str | tuple[str | None, dict] | None:
    """Fetch product_url, extract the best image candidate, and validate via content-type.

    On success, updates the persistent cache so future cache hits carry the image.
    Returns the validated image URL, or None.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    debug = _image_debug_defaults()
    invalid_reason = _url_validation_error(product_url)
    if invalid_reason:
        debug["cloudinary_status"] = "not_attempted"
        debug["cloudinary_error"] = "invalid product URL before image fetch"
        debug["image_failure_reason"] = debug["cloudinary_error"]
        debug["programa_image_ready"] = False
        _stamp_invalid_url_debug(
            debug,
            product_url,
            source="product_url",
            step="image_page_fetch",
            row=row or {},
            fallback_attempted=True,
            reason=invalid_reason,
        )
        return (None, debug) if return_debug else None

    raw_html = ""
    if session_cache is not None and product_url in session_cache.urls:
        raw_html = str(session_cache.urls.get(product_url) or "")
    if not raw_html:
        if budget is not None and not budget.can_fetch():
            debug["budget_blocked"] = True
            debug["cloudinary_error"] = "budget blocked image page fetch"
            debug["image_failure_reason"] = debug["cloudinary_error"]
            return (None, debug) if return_debug else None
        raw_html = _fetch_page_html_with_debug(
            product_url,
            debug=debug,
            source="product_url",
            step="image_page_fetch",
            row=row or {},
        )
        if budget is not None:
            budget.consume_fetch()
        if raw_html and session_cache is not None:
            session_cache.urls[product_url] = raw_html
    if not raw_html:
        _log.info("[IMAGE PIPELINE] fetch failed url=%s", product_url[:80])
        debug["image_failure_reason"] = debug.get("image_failure_reason") or "image source page fetch failed"
        return (None, debug) if return_debug else None

    final_url, debug = _extract_image_from_html(raw_html, product_url, row or {}, cache_key)
    if final_url:
        _log.info("[IMAGE PIPELINE] found url=%s img=%s", product_url[:60], final_url[:80])
    else:
        _log.info("[IMAGE PIPELINE] no accepted candidate url=%s", product_url[:80])
    return (final_url, debug) if return_debug else final_url


def _unpack_image_result(result) -> tuple[str | None, dict]:
    if isinstance(result, tuple):
        image_url = result[0] if result else None
        debug = result[1] if len(result) > 1 and isinstance(result[1], dict) else {}
        return image_url, debug
    return result, {}


def _build_image_recovery_queries(row: dict, manufacturer_domain: str = "") -> list[str]:
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))
    product_name = _str_val(row.get("Product Name"))
    domains = domains_for_brand(brand, manufacturer_domain)
    queries: list[str] = []
    if brand and model:
        queries.extend([
            f'"{brand}" "{model}" product image',
            f'"{brand}" "{model}" appliance image',
            f'"{brand}" "{model}" official product page',
        ])
        for domain in domains[:2]:
            queries.extend([
                f'site:{domain} "{model}" image',
                f'site:{domain} "{model}" product',
            ])
    if brand and model and product_name:
        queries.append(f'"{brand}" "{model}" "{product_name}" image')
    seen: set[str] = set()
    deduped: list[str] = []
    for query in queries:
        if query and query not in seen:
            seen.add(query)
            deduped.append(query)
    return deduped


def _try_image_recovery_searches(
    row: dict,
    *,
    cache_key: str = "",
    manufacturer_domain: str = "",
    session_cache: "_SessionCache | None" = None,
    budget: "_SearchBudget | None" = None,
) -> tuple[str | None, dict]:
    brand = _str_val(row.get("Brand"))
    debug = _image_debug_defaults()
    queries_used: list[str] = []
    urls_checked: list[str] = []
    candidates_debug: list[dict] = []
    for query in _build_image_recovery_queries(row, manufacturer_domain):
        query_cached = session_cache is not None and query in session_cache.queries
        if budget is not None and not budget.can_search() and not query_cached:
            debug["budget_blocked"] = True
            debug["image_failure_reason"] = "image search budget exhausted"
            break
        queries_used.append(query)
        results = search_product_candidates(query, brand, session_cache=session_cache)
        if budget is not None and not query_cached:
            budget.consume_search()
        for result in results:
            url = _str_val(getattr(result, "url", ""))
            if not url or url in urls_checked:
                continue
            urls_checked.append(url)
            image_url, image_debug = _unpack_image_result(
                _try_image_from_url(
                    url,
                    cache_key,
                    session_cache=session_cache,
                    budget=budget,
                    row=row,
                    return_debug=True,
                )
            )
            candidates_debug.append({
                "page_url": url,
                "title": _str_val(getattr(result, "title", "")),
                "domain_score": int(getattr(result, "domain_score", 0) or 0),
                "image_url": image_url or "",
                "image_confidence": image_debug.get("image_confidence", ""),
                "failure_reason": image_debug.get("image_failure_reason") or image_debug.get("cloudinary_error", ""),
            })
            if image_url:
                image_debug["image_source_url"] = image_debug.get("image_source_url") or url
                image_debug["search_query_used"] = " | ".join(queries_used)
                image_debug["product_url_candidates"] = json.dumps(candidates_debug[:5], ensure_ascii=True)
                image_debug["budget_spent"] = _budget_summary(budget)
                return image_url, image_debug
            if budget is not None and not budget.can_fetch():
                debug["budget_blocked"] = True
                debug["image_failure_reason"] = "image page fetch budget exhausted"
                break
        if budget is not None and (not budget.can_search() or not budget.can_fetch()):
            break
    debug["search_query_used"] = " | ".join(queries_used)
    debug["product_url_candidates"] = json.dumps(candidates_debug[:5], ensure_ascii=True)
    debug["budget_spent"] = _budget_summary(budget)
    debug["image_failure_reason"] = debug.get("image_failure_reason") or "no validated product image found"
    return None, debug


def _cache_has_blank_dimension(cache_entry: dict | None) -> bool:
    if not cache_entry:
        return False
    null_fields = cache_entry.get("null_fields") or {}
    return (
        ("dimensions" in cache_entry and not _str_val(cache_entry.get("dimensions")))
        or "dimensions" in null_fields
    )


def _cache_has_blank_image(cache_entry: dict | None) -> bool:
    if not cache_entry:
        return False
    null_fields = cache_entry.get("null_fields") or {}
    return (
        ("image_url" in cache_entry and not _str_val(cache_entry.get("image_url")))
        or "image_url" in null_fields
    )


def _build_extraction_prompt(page_text: str, row: dict) -> str:
    """Build the Claude Haiku prompt listing which fields are blank and need filling."""
    dims = _str_val(row.get("Dimensions"))
    needs_dims = not dims or not has_complete_3d_dimensions(dims)

    # Non-dimension fields that are blank
    blank = [
        f for f in ["Product Name", "Finish / Color", "Product Category"]
        if not _str_val(row.get(f))
    ]
    if needs_dims:
        blank.append("Dimensions")

    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))

    if needs_dims:
        dim_instruction = (
            "\n\nFor Dimensions: look for the exact product specification "
            "listing width, height, and depth. "
            'Format your answer as: 36"W x 34.5"H x 24"D '
            "(always include the W, H, and D labels). "
            "Return the combined string ONLY if all three of width, height, "
            "and depth are explicitly stated on the page. "
            'If any one of them is missing, return "".'
        )
        dims_note = ""
    else:
        dim_instruction = ""
        dims_note = "\n\nNote: Dimensions are already complete — do not extract or overwrite them."

    return (
        f"You are extracting product specification data for {brand} model {model}.\n\n"
        f"The following fields are currently blank or incomplete and need to be filled:\n"
        f"{', '.join(blank)}\n\n"
        "Also extract: materials (short description of primary construction materials, "
        "e.g. 'Solid Oak', 'Stainless Steel')"
        + dim_instruction + dims_note + "\n\n"
        "Return ONLY a JSON object. No prose. No markdown fences. Example:\n"
        '{"Product Name": "Wolf 30\\" Drawer Microwave Oven", '
        '"Dimensions": "29 7/8\\" W x 23 1/2\\" D x 11 7/8\\" H", '
        '"Finish / Color": "Stainless Steel", '
        '"Product Category": "Appliances", '
        '"materials": "Stainless steel exterior"}\n\n'
        "Rules:\n"
        "- Only include the fields listed above as blank/incomplete, plus 'materials'.\n"
        "- If a field is not clearly stated in the page, return \"\" for that field.\n"
        "- Never invent values not present in the page.\n"
        "- For Dimensions: only return a value when width AND height AND depth "
        "are all explicitly stated. Never infer from product name alone.\n"
        "- Product Category must be one of: Paint/Wallpaper, Stone/Tile, Seating, "
        "Hardware, Flooring, Tables, Gym Equipment, Fabrics/Pillows, Lighting, Rugs, "
        "Mirrors, Beds/Mattresses, Dressers/Drawers/Storage, Appliances, Accessories, "
        "Artwork, Bedding/Linens/Bath Linens.\n\n"
        f"PAGE TEXT:\n---\n{page_text}\n---"
    )


def _extract_with_claude(page_text: str, row: dict) -> dict:
    """Call Claude Haiku to extract missing fields from page text. Returns {} on any failure."""
    if not ANTHROPIC_API_KEY or _anthropic is None:
        return {}
    try:
        client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": _build_extraction_prompt(page_text, row)}],
        )
        text = msg.content[0].text.strip()
        text = re.sub(r"```(?:json)?\s*|```", "", text).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return {}
        return json.loads(text[start : end + 1])
    except Exception:
        return {}


def _apply_cache_to_row(
    row: dict,
    cache_entry: dict,
    force_refresh: bool,
) -> tuple[dict, list[str], list[str]]:
    """
    Fill row from cache entry where confidence is high/medium.
    Returns (updated_row, cache_fields_filled, still_missing_essentials).
    Null cache values are skipped unless force_refresh=True.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    updated = row.copy()
    filled: list[str] = []
    all_cache_fields = {**_CACHE_GENERAL_FIELDS, **_CACHE_DIM_FIELDS}

    for cache_field, row_col in all_cache_fields.items():
        cached_val = cache_entry.get(cache_field)
        if cached_val is None:
            if not force_refresh:
                _log.debug("[CACHED NULL SKIPPED] field=%s", cache_field)
            continue  # null = searched before, no result; skip unless force_refresh
        if cache_field == "product_url" and _reject_weak_product_url(_str_val(cached_val)):
            _log.debug("[CACHED PRODUCT URL REJECTED] field=%s url=%s", cache_field, cached_val)
            continue
        if not _confidence_ok(cache_entry, cache_field):
            continue
        if not _str_val(updated.get(row_col)):
            updated[row_col] = cached_val
            filled.append(cache_field)

    missing = []
    for f in _ESSENTIAL_CACHE_FIELDS:
        row_col = all_cache_fields[f]  # KeyError if f is not mapped — fail loud, not silent
        val = _str_val(updated.get(row_col))
        if not val or (f == "dimensions" and not has_complete_3d_dimensions(val)):
            missing.append(f)
    return updated, filled, missing


def _dimension_debug_defaults(row: dict) -> dict:
    dims_before = _str_val(row.get("Dimensions"))
    product_url = _str_val(row.get("Product URL"))
    debug = {
        "skipped_by_source_type": False,
        "skipped_by_missing_brand": False,
        "skipped_by_missing_model": False,
        "cache_hit": "",
        "cache_had_blank_dimensions": False,
        "cache_had_blank_image": False,
        "fresh_extraction_forced": False,
        "product_url": product_url,
        "product_url_confidence": "",
        "page_fetch_attempted": False,
        "page_fetch_success": False,
        "spec_table_found": False,
        "json_ld_found": False,
        "next_data_found": False,
        "shopify_json_found": False,
        "spec_pdf_links_found": 0,
        "spec_pdf_fetched": False,
        "dimensions_before_enrichment": dims_before,
        "dimensions_extracted": "",
        "dimension_confidence": "",
        "dimension_source": "",
        "dimension_source_url": "",
        "dimension_parse_method": "",
        "dimension_raw_text": "",
        "dimension_raw_snippet": "",
        "partial_dimensions_found": "",
        "rejected_dimensions_reason": "",
        "final_dimensions": dims_before,
        "final_dimension_writeback_success": False,
        "skipped_reason": "",
        "budget_blocked": False,
        "enrichment_status": "",
        "enrichment_error": "",
        "stage_log": "",
        "debug_traceback": "",
        "sku_used_for_lookup": _str_val(row.get("Model/SKU")),
        "manufacturer_domain_used": "",
        "search_provider": "",
        "search_query_used": "",
        "product_url_candidates": "",
        "selected_product_url": "",
        "selected_product_url_confidence": "",
        "was_searched": "no",
        "selected_strategy": "",
        "retry_recommended": "",
        "dimensions_status": "",
        "image_status": "",
        "source_status": "",
        "product_component_type": _product_component_type(row),
        "round_attempted": "",
        "sku_match_location": "",
        "page_fetched": False,
        "dimensions_found": False,
        "image_candidates_count": "",
        "selected_image_url": "",
        "fields_filled": "",
        "budget_spent": "",
        "budget_stop_reason": "",
        "total_budget_remaining": "",
        "search_budget_remaining": "",
        "openai_budget_remaining": "",
        "reason_row_skipped": "",
        "skip_reason": "",
        "skip_threshold_hit": False,
        "budget_skip_threshold": "",
        "pages_found": 0,
        "pages_fully_parsed": 0,
        "dimensions_successfully_extracted": False,
        "dimensions_blocked_by_budget": False,
        "dimensions_blocked_by_parser": False,
        "spec_sheets_found": 0,
        "spec_sheets_parsed": 0,
        "image_only_success": False,
        "duplicate_model_skipped": False,
        "duplicate_model_source_index": "",
        "searches_avoided": 0,
        "useful_fields_found": "",
        "stored_source_hit": False,
        "stored_source_used": "",
        "stored_source_updated": False,
        "stored_source_rejected_reason": "",
        "source_memory_backend": storage_backend_name(),
        "duplicate_searches_avoided_from_cache": 0,
        "knowledge_base_hit": False,
        "knowledge_base_miss": False,
        "knowledge_base_source_used": "",
        "knowledge_base_updated": False,
        "knowledge_base_rejected_reason": "",
        "preferred_domain_used": "",
        "invalid_url_error": "",
        "invalid_url_generated": "",
        "invalid_url_source": "",
        "invalid_url_step": "",
        "invalid_url_normalized_brand": "",
        "invalid_url_normalized_model": "",
        "invalid_url_search_query": "",
        "invalid_url_fallback_attempted": "",
    }
    debug.update(_image_debug_defaults())
    return debug


def _stamp_dimension_debug(row: dict, debug: dict) -> dict:
    updated = _apply_status_fields(row)
    merged = _dimension_debug_defaults(updated)
    merged.update(debug or {})
    for field in _ENRICHMENT_DEBUG_FIELDS:
        updated[field] = merged.get(field, "")
    return _apply_status_fields(updated)


def _merge_preserved_debug(row: dict, debug: dict) -> dict:
    merged = dict(debug or {})
    for field in _ENRICHMENT_DEBUG_FIELDS:
        if not _str_val(merged.get(field)) and _str_val(row.get(field)):
            merged[field] = row.get(field)
    return merged


def _mark_enrichment_failed(row: dict, debug: dict, reason: str) -> dict:
    """Mark an attempted enrichment as an explicit reviewable failure."""
    updated = row.copy()
    reason = _str_val(reason) or "no verified product match"
    _append_stage(debug, "ENRICHMENT_COMPLETE")
    existing_notes = _str_val(updated.get("Notes"))
    failure_tag = f"[ENRICHMENT_FAILED: {reason}]"
    legacy_tag = "[Enrichment: no confident source found]"
    notes_to_add = [failure_tag]
    if "no confident source" in reason.lower():
        notes_to_add.append(legacy_tag)
    for note in notes_to_add:
        if note not in existing_notes:
            existing_notes = f"{existing_notes} {note}".strip() if existing_notes else note
    updated["Notes"] = existing_notes
    updated["Review Required"] = True
    updated["Suggested Action"] = f"ENRICHMENT_FAILED: {reason}"
    if not _str_val(updated.get("Status")):
        updated["Status"] = "Needs Review"
    return _stamp_dimension_debug(
        updated,
        {
            **(debug or {}),
            "skipped_reason": reason,
            "enrichment_status": "failed",
            "enrichment_error": reason,
        },
    )


def _log_enrichment_outcome(row: dict) -> None:
    """Structured per-row enrichment log for debugging production quote runs."""
    import logging as _logging

    _logging.getLogger(__name__).info(
        "[ENRICHMENT ROW] manufacturer=%s model=%s query=%s matched_url=%s image_extracted=%s dimensions_extracted=%s failure_reason=%s",
        _str_val(row.get("Brand")),
        _str_val(row.get("Model/SKU")),
        _str_val(row.get("search_query_used")),
        _str_val(row.get("selected_product_url")) or _str_val(row.get("Product URL")),
        bool(_str_val(row.get("Image URL")) or _str_val(row.get("selected_image_url"))),
        bool(_str_val(row.get("Dimensions")) or _str_val(row.get("dimensions_extracted"))),
        _str_val(row.get("skipped_reason")) or _str_val(row.get("Suggested Action")),
    )


def _budget_summary(budget: "_SearchBudget | None") -> str:
    if budget is None:
        return ""
    return f"searches={budget.searches_used}/{budget.max_searches}; fetches={budget.urls_used}/{budget.max_urls}"


def _budget_searches_used(value: object) -> int:
    match = re.search(r"searches=(\d+)/\d+", _str_val(value))
    return int(match.group(1)) if match else 0


def _budget_fetches_used(value: object) -> int:
    match = re.search(r"fetches=(\d+)/\d+", _str_val(value))
    return int(match.group(1)) if match else 0


def _budget_fetches_remaining(budget: "_SearchBudget | None") -> int:
    if budget is None:
        return 999_999
    return max(0, int(getattr(budget, "max_urls", 0)) - int(getattr(budget, "urls_used", 0)))


def _hard_cost_cap_for_mode(mode: str) -> float:
    if _normalize_mode(mode) == "max_accuracy":
        return float(os.getenv("ENRICHMENT_MAX_ACCURACY_HARD_COST_USD", "5.00") or "5.00")
    if _normalize_mode(mode) == "deep":
        return float(os.getenv("ENRICHMENT_DEEP_HARD_COST_USD", "1.00") or "1.00")
    return float(os.getenv("ENRICHMENT_HARD_COST_USD", "0.25") or "0.25")


def _brave_cost_per_search() -> float:
    return float(os.getenv("BRAVE_SEARCH_COST_USD", "0.006") or "0.006")


def _row_enrichment_key(row: dict) -> str:
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))
    if not brand or not model:
        return ""
    try:
        return _normalize_key(brand, model)
    except ValueError:
        return ""


def _row_richness_score(row: dict) -> int:
    score = 0
    for field in ("Product URL", "Image URL", "Dimensions", "Product Name", "Supplier", "Room", "Finish / Color", "Material"):
        if _str_val(row.get(field)):
            score += 1
    if has_complete_3d_dimensions(row.get("Dimensions")):
        score += 3
    if _reject_weak_product_url(_str_val(row.get("Product URL"))):
        score -= 2
    return score


def _merge_duplicate_enrichment(original: dict, source: dict, source_index: int | str) -> dict:
    """Copy enrichment-only values from source into a duplicate model row."""
    updated = original.copy()
    filled: list[str] = []

    def fill_if_blank(column: str) -> None:
        value = _str_val(source.get(column))
        if value and not _str_val(updated.get(column)):
            updated[column] = source.get(column)
            filled.append(column)

    def force_enrichment_value(column: str) -> None:
        value = _str_val(source.get(column))
        if value and _str_val(updated.get(column)) != value:
            updated[column] = source.get(column)
            filled.append(column)

    if _str_val(source.get("Dimensions")) and not has_complete_3d_dimensions(updated.get("Dimensions")):
        updated["Dimensions"] = source.get("Dimensions")
        filled.append("Dimensions")
    elif _str_val(source.get("Dimensions")) and has_complete_3d_dimensions(source.get("Dimensions")):
        # Same brand/model duplicate rows should share the same verified product
        # dimensions, even when one row had stale partial data.
        updated["Dimensions"] = source.get("Dimensions")
    for column in ("Width (in)", "Height (in)", "Depth (in)"):
        force_enrichment_value(column)
    # Programa should not inherit length into the primary W/H/D handoff.
    if _str_val(source.get("Length (in)")) and not _str_val(updated.get("Length (in)")):
        updated["Length (in)"] = source.get("Length (in)")

    if _str_val(source.get("Image URL")) and _needs_image_recovery(updated):
        updated["Image URL"] = source.get("Image URL")
        filled.append("Image URL")
    elif _str_val(source.get("Image URL")) and _str_val(updated.get("Image URL")) != _str_val(source.get("Image URL")):
        updated["Image URL"] = source.get("Image URL")
        filled.append("Image URL")

    product_url = _str_val(source.get("Product URL"))
    if product_url and (
        not _str_val(updated.get("Product URL"))
        or _reject_weak_product_url(_str_val(updated.get("Product URL")))
        or _str_val(updated.get("Product URL")) != product_url
    ):
        updated["Product URL"] = product_url
        filled.append("Product URL")

    for column in (
        "Dimension Source URL",
        "Dimension Confidence",
        "Dimension Source Type",
        "Dimension Lookup Status",
        "original_image_url",
        "cloudinary_url",
        "image_confidence",
        "image_source_url",
        "image_candidate_diagnostics",
        "cloudinary_status",
        "cloudinary_error",
        "programa_image_ready",
        "selected_product_url",
        "selected_product_url_confidence",
        "selected_image_url",
    ):
        force_enrichment_value(column)

    for column in (
        "Product Category",
        "Finish / Color",
        "Material",
    ):
        fill_if_blank(column)

    merged_fields = [
        part.strip()
        for part in f"{_str_val(source.get('fields_filled'))}, {', '.join(filled)}".split(",")
        if part.strip()
    ]
    updated["fields_filled"] = ", ".join(dict.fromkeys(merged_fields))
    updated["useful_fields_found"] = updated["fields_filled"]
    updated["duplicate_model_skipped"] = True
    updated["duplicate_model_source_index"] = str(source_index)
    updated["searches_avoided"] = max(1, _budget_searches_used(source.get("budget_spent")))
    updated["budget_spent"] = "searches=0/0; fetches=0/0"
    updated["cache_hit"] = _str_val(updated.get("cache_hit")) or "duplicate_model"
    updated["skipped_reason"] = "duplicate brand/model reused enrichment result"
    updated["enrichment_error"] = ""
    updated["enrichment_status"] = _classify_enrichment_status(updated)
    return updated


def _classify_enrichment_status(row: dict, failed: bool = False) -> str:
    if failed or _str_val(row.get("enrichment_error")):
        return "failed"
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))
    if not brand or not model:
        return "needs_review"
    missing_dims = _needs_dimension_recovery(row)
    missing_image = _needs_image_recovery(row)
    if missing_dims or missing_image:
        if _str_val(row.get("Product URL")) or _str_val(row.get("Image URL")) or _str_val(row.get("Dimensions")):
            return "partially_enriched"
        return "needs_review"
    return "complete"


def _product_component_type(row: dict) -> str:
    text = " ".join(
        _str_val(row.get(field))
        for field in ("Product Name", "Description", "Notes", "Product Category", "Model/SKU")
    ).lower()
    if re.search(r"\b(trim|panel|kit|blower|filter|accessor(?:y|ies)|component|insert|sleeve|handle|grille)\b", text):
        return "accessory/component"
    model = _str_val(row.get("Model/SKU"))
    if model.isdigit() and 4 <= len(model) <= 7:
        return "accessory/component"
    return "primary_product"


def _dimension_status(row: dict) -> str:
    if has_complete_3d_dimensions(row.get("Dimensions")):
        return "complete"
    if _str_val(row.get("partial_dimensions_found")) or _str_val(row.get("Dimensions")):
        return "partial"
    if _str_val(row.get("rejected_dimensions_reason")):
        return "failed"
    if _str_val(row.get("Dimension Lookup Status")) in {"low_confidence_skipped"}:
        return "partial"
    if _str_val(row.get("Dimension Lookup Status")) == "not_found":
        return "missing"
    return "missing"


def _image_status(row: dict) -> str:
    if _has_good_image(row):
        return "found"
    if _str_val(row.get("selected_image_url")):
        return "fallback"
    if _str_val(row.get("cloudinary_error")) or _str_val(row.get("Image Upload Status")).lower() == "failed":
        return "failed"
    return "missing"


def _source_status(row: dict) -> str:
    source_type = " ".join(
        _str_val(row.get(field))
        for field in ("Dimension Source Type", "source_type", "selected_strategy")
    ).lower()
    product_url = _str_val(row.get("selected_product_url") or row.get("Product URL"))
    domain = _domain_of(product_url)
    manufacturer_domain = _str_val(row.get("manufacturer_domain_used"))
    if "manufacturer" in source_type or _domain_matches(domain, manufacturer_domain):
        return "manufacturer"
    if "retailer" in source_type:
        return "dealer"
    if product_url:
        return "fallback"
    if _str_val(row.get("skipped_reason")).lower().startswith("skipped"):
        return "skipped"
    return "manual"


def _apply_status_fields(row: dict) -> dict:
    updated = dict(row)
    updated["dimensions_status"] = _dimension_status(updated)
    updated["image_status"] = _image_status(updated)
    updated["source_status"] = _source_status(updated)
    updated["product_component_type"] = _product_component_type(updated)
    if updated["dimensions_status"] in {"missing", "partial"}:
        updated["retry_recommended"] = "dimension-focused retry"
    elif updated["image_status"] in {"missing", "failed"}:
        updated["retry_recommended"] = "image-only retry"
    else:
        updated["retry_recommended"] = ""
    return updated


def _search_sku_product_pages(
    row: dict,
    manufacturer_domain: str,
    *,
    session_cache: "_SessionCache | None",
    budget: "_SearchBudget | None",
    debug: dict,
) -> list:
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))
    official_domains = domains_for_brand(brand, manufacturer_domain)
    candidates = []
    queries_used: list[str] = []
    for query in _build_sku_lookup_queries(row, manufacturer_domain):
        query_cached = session_cache is not None and query in session_cache.queries
        if budget is not None and not budget.can_search() and not query_cached:
            debug["budget_blocked"] = True
            debug["budget_stop_reason"] = "SKU product search budget exhausted"
            break
        queries_used.append(query)
        debug["search_provider"] = "brave"
        debug["was_searched"] = "yes"
        debug["selected_strategy"] = (
            "manufacturer"
            if any(query.startswith(f"site:{domain}") for domain in official_domains)
            else "fallback"
        )
        debug["search_query_used"] = " | ".join(queries_used)
        results = search_product_candidates(query, brand, session_cache=session_cache)
        if budget is not None and not query_cached:
            budget.consume_search()
        candidates.extend(results or [])
        # Keep walking the SKU-specific fallbacks until the per-row search
        # budget is exhausted. Verification happens after results are fetched;
        # stopping at the first search-result page is what caused valid late
        # quote rows to export with blank URLs/images/dimensions.
    # Dedupe by URL.
    seen: set[str] = set()
    deduped = []
    for result in candidates:
        if result.url in seen:
            continue
        seen.add(result.url)
        deduped.append(result)

    model_norm = _norm_token(model)

    def _candidate_rank(result) -> tuple[int, int, int, int, int, int, int]:
        url = _str_val(getattr(result, "url", ""))
        title = _str_val(getattr(result, "title", ""))
        description = _str_val(getattr(result, "description", ""))
        haystack = " ".join([url, title, description]).lower()
        domain = _domain_of(url)
        sku_hit = 1 if model_norm and model_norm in _norm_token(" ".join([url, title, description])) else 0
        official_hit = 1 if any(_domain_matches(domain, official) for official in official_domains) else 0
        spec_hit = 1 if any(token in haystack for token in ("dimension", "specification", "spec-sheet", "spec sheet", "installation", ".pdf")) else 0
        weak_penalty = -1 if _reject_weak_product_url(url) else 0
        low_priority_penalty = -1 if is_low_priority_domain(domain) else 0
        return (
            sku_hit,
            official_hit,
            spec_hit,
            brand_source_score(domain, brand, manufacturer_domain),
            int(getattr(result, "domain_score", 0) or 0),
            weak_penalty,
            low_priority_penalty,
        )

    deduped.sort(key=_candidate_rank, reverse=True)
    debug["product_url_candidates"] = json.dumps(
        [{"url": r.url, "title": r.title, "domain_score": r.domain_score} for r in deduped[:5]],
        ensure_ascii=True,
    )
    return deduped


def _verify_candidate_url(
    row: dict,
    url: str,
    *,
    manufacturer_domain: str,
    session_cache: "_SessionCache | None",
    budget: "_SearchBudget | None",
    debug: dict,
    title_hint: str = "",
    description_hint: str = "",
) -> tuple[str, dict]:
    html = _fetch_page_html_budgeted(
        url,
        session_cache=session_cache,
        budget=budget,
        debug=debug,
        source="product_url_candidate",
        step="candidate_page_fetch",
        row=row,
        query=_str_val(debug.get("search_query_used")),
    )
    if not html:
        return "", {
            "confidence": "none",
            "score": 0,
            "rejection_reason": debug.get("skipped_reason") or "could not fetch product page",
            "sku_match_location": "",
        }
    title, description = _page_title_and_description(html)
    evidence = _score_verified_product_page(
        row,
        url,
        html,
        manufacturer_domain=manufacturer_domain,
        title=title or title_hint,
        description=description or description_hint,
    )
    return html, evidence


def _write_verified_cache(
    cache_key: str,
    row: dict,
    *,
    manufacturer_domain: str,
    page_url: str,
    page_confidence: str,
    fields: dict,
    image_debug: dict,
    dim_result: "_DimensionResult | None",
) -> None:
    if not cache_key:
        return
    cache_fields = {
        "brand": _str_val(row.get("Brand")),
        "sku": _str_val(row.get("Model/SKU")),
        "product_name": _str_val(row.get("Product Name")),
        "manufacturer_domain": manufacturer_domain,
        "product_url": page_url,
        "verified_product_url": page_url,
        "product_url_confidence": page_confidence,
        "source_timestamp": datetime.now(timezone.utc).isoformat(),
        "general_confidence": "high" if page_confidence == "high" else "medium",
    }
    if _str_val(fields.get("Finish / Color")):
        cache_fields["finish"] = _str_val(fields.get("Finish / Color"))
    if _str_val(fields.get("Material")):
        cache_fields["material"] = _str_val(fields.get("Material"))
    if _str_val(fields.get("Product Category")):
        cache_fields["product_category"] = _str_val(fields.get("Product Category"))
    if _str_val(row.get("Image URL")):
        cache_fields["image_url"] = _str_val(row.get("Image URL"))
        cache_fields["cloudinary_url"] = _str_val(image_debug.get("cloudinary_url"))
        cache_fields["original_image_url"] = _str_val(image_debug.get("original_image_url"))
        cache_fields["image_confidence"] = _str_val(image_debug.get("image_confidence"))
    if dim_result is not None and dim_result.status == "found":
        cache_fields.update({
            "dimensions": dim_result.dimensions,
            "width_in": dim_result.width or None,
            "height_in": dim_result.height or None,
            "depth_in": dim_result.depth or None,
            "length_in": dim_result.length or None,
            "dimension_source_url": dim_result.source_url,
            "dimension_confidence": dim_result.confidence,
        })
    _product_cache.update(cache_key, cache_fields)


def _try_verified_page_enrichment(
    row: dict,
    *,
    cache_key: str,
    manufacturer_domain: str,
    session_cache: "_SessionCache | None",
    budget: "_SearchBudget | None",
    debug: dict,
) -> tuple[dict, _DimensionResult | None, dict]:
    updated = row.copy()
    debug["sku_used_for_lookup"] = _str_val(row.get("Model/SKU"))
    debug["manufacturer_domain_used"] = manufacturer_domain
    debug["budget_spent"] = _budget_summary(budget)
    product_url = _str_val(updated.get("Product URL"))
    candidates_to_check: list[tuple[str, str, str]] = []

    if product_url:
        candidates_to_check.append((product_url, "", ""))
        debug["selected_strategy"] = "existing_product_url"
    if not product_url or _reject_weak_product_url(product_url):
        candidate_limit = max(1, min(3, getattr(budget, "max_urls", 3) if budget is not None else 3))
        for result in _search_sku_product_pages(
            row,
            manufacturer_domain,
            session_cache=session_cache,
            budget=budget,
            debug=debug,
        )[:candidate_limit]:
            candidates_to_check.append((result.url, result.title, result.description))

    selected_url = ""
    selected_html = ""
    selected_evidence: dict = {}
    candidate_debug: list[dict] = []
    best_rank: tuple[int, int, int] | None = None
    for url, title_hint, description_hint in candidates_to_check:
        html, evidence = _verify_candidate_url(
            row,
            url,
            manufacturer_domain=manufacturer_domain,
            session_cache=session_cache,
            budget=budget,
            debug=debug,
            title_hint=title_hint,
            description_hint=description_hint,
        )
        candidate_debug.append({"url": url, **evidence})
        if evidence.get("confidence") in {"high", "medium"}:
            confidence_rank = 2 if evidence.get("confidence") == "high" else 1
            official_rank = 1 if evidence.get("official_domain") else 0
            evidence_rank = int(evidence.get("score") or 0)
            rank = (confidence_rank, official_rank, evidence_rank)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                selected_url = url
                selected_html = html
                selected_evidence = evidence
            if evidence.get("confidence") == "high" and evidence.get("official_domain"):
                break
            # Once we have a verified page, preserve room for parsing dimensions
            # and linked spec PDFs instead of spending every fetch on alternates.
            if selected_url and _budget_fetches_remaining(budget) <= 2:
                debug["skipped_reason"] = debug.get("skipped_reason") or "reserved fetch budget for dimension/spec parsing"
                break
        if budget is not None and not budget.can_fetch():
            debug["budget_blocked"] = True
            break

    if candidate_debug:
        debug["product_url_candidates"] = json.dumps(candidate_debug[:5], ensure_ascii=True)
    if not selected_url:
        debug["selected_product_url_confidence"] = "none"
        debug["sku_match_location"] = candidate_debug[0].get("sku_match_location", "") if candidate_debug else ""
        if candidate_debug and not debug.get("skipped_reason"):
            debug["skipped_reason"] = candidate_debug[0].get("rejection_reason", "")
        debug["budget_spent"] = _budget_summary(budget)
        return updated, None, debug

    debug["selected_product_url"] = selected_url
    debug["selected_product_url_confidence"] = _str_val(selected_evidence.get("confidence"))
    debug["product_url_confidence"] = debug["selected_product_url_confidence"]
    debug["sku_match_location"] = _str_val(selected_evidence.get("sku_match_location"))
    debug["product_url"] = selected_url

    fields = _extract_verified_page_fields(selected_html, updated, selected_url)
    updated, fields_filled = _fill_blank_fields_from_verified_page(
        updated,
        fields,
        selected_url,
        debug["selected_product_url_confidence"],
    )

    dim_result: _DimensionResult | None = None
    if _needs_dimension_recovery(updated):
        _append_stage(debug, "SEARCHING_DIMENSIONS")
        dim_result = _find_dimensions(updated, session_cache=session_cache, budget=budget)
        debug.update(dim_result.debug or {})
        if dim_result.status == "found" and dim_result.confidence in {"high", "medium"}:
            updated["Dimensions"] = dim_result.dimensions
            if dim_result.width:
                updated["Width (in)"] = dim_result.width
            if dim_result.height:
                updated["Height (in)"] = dim_result.height
            if dim_result.depth:
                updated["Depth (in)"] = dim_result.depth
            if dim_result.length:
                updated["Length (in)"] = dim_result.length
            fields_filled.append("Dimensions")
        debug["dimensions_found"] = bool(dim_result.status == "found")
        debug["dimension_confidence"] = dim_result.confidence if dim_result.confidence not in ("", "none", None) else ""
        debug["dimension_source_url"] = dim_result.source_url
        debug["Dimension Source URL"] = dim_result.source_url
    else:
        debug["dimensions_found"] = bool(_str_val(updated.get("Dimensions")))

    image_debug: dict = {}
    if _needs_image_recovery(updated):
        image_url, image_debug = _extract_image_from_html(selected_html, selected_url, updated, cache_key)
        debug.update(image_debug)
        try:
            debug["image_candidates_count"] = len(extract_product_image_candidates(selected_html, selected_url, updated))
        except Exception:
            debug["image_candidates_count"] = ""
        if image_url:
            updated["Image URL"] = image_url
            updated.update(image_debug)
            debug["selected_image_url"] = image_url
            fields_filled.append("Image URL")
            if _needs_dimension_recovery(updated):
                debug["image_only_success"] = True

    debug["fields_filled"] = ", ".join(dict.fromkeys(fields_filled))
    debug["budget_spent"] = _budget_summary(budget)
    _write_verified_cache(
        cache_key,
        updated,
        manufacturer_domain=manufacturer_domain,
        page_url=selected_url,
        page_confidence=debug["selected_product_url_confidence"],
        fields=fields,
        image_debug=image_debug,
        dim_result=dim_result,
    )
    try:
        saved_source = save_successful_source_from_row(updated, notes="Verified product page enrichment.")
        if saved_source:
            debug["stored_source_updated"] = True
            debug["knowledge_base_updated"] = True
            debug["stored_source_used"] = (
                debug.get("stored_source_used")
                or saved_source.get("product_page_url")
                or saved_source.get("manufacturer_url")
                or saved_source.get("dimension_source_url")
                or saved_source.get("spec_sheet_url")
                or saved_source.get("image_source_url")
                or ""
            )
            debug["knowledge_base_source_used"] = debug["stored_source_used"]
    except Exception as exc:
        debug["stored_source_rejected_reason"] = f"source memory save failed: {exc}"
        debug["knowledge_base_rejected_reason"] = debug["stored_source_rejected_reason"]
    return updated, dim_result, debug


def enrich_row(
    row: dict,
    enrichment_mode: str = "standard",
    session_cache: "_SessionCache | None" = None,
    use_web_enrichment: bool = True,
) -> tuple[dict, str | None, _DimensionResult | None]:
    """
    Enrich a single row using Brave Search + httpx + Claude.

    Returns (updated_row, None, dim_result_or_none) on success or graceful no-result.
    Returns (row_as_given, error_string, None) only on unexpected exceptions.
    dim_result_or_none is the DimensionResult when a dimension lookup ran, else None.
    """
    try:
        import logging as _logging
        _log = _logging.getLogger(__name__)

        if not use_web_enrichment:
            _log.info("[WEB ENRICHMENT DISABLED] skipping search and cache for row")
            return row.copy(), None, None

        mode = _normalize_mode(enrichment_mode)
        budget = _budget_for_mode(mode)
        remaining_searches = getattr(session_cache, "remaining_searches", None)
        if remaining_searches is not None:
            budget.max_searches = min(budget.max_searches, max(0, int(remaining_searches)))
        remaining_fetches = getattr(session_cache, "remaining_fetches", None)
        if remaining_fetches is not None:
            budget.max_urls = min(budget.max_urls, max(0, int(remaining_fetches)))
        brand = _str_val(row.get("Brand"))
        model_sku = _str_val(row.get("Model/SKU"))
        cache_key = _normalize_key(brand, model_sku) if brand and model_sku else ""
        force_refresh = session_cache.force_refresh if session_cache else False
        enrichment_debug = _dimension_debug_defaults(row)
        _append_stage(enrichment_debug, "ENRICHMENT_STARTED")

        # ── Cache check ────────────────────────────────────────────────────────
        cache_fields_filled: list[str] = []
        fields_searched: list[str] = []
        product_cache_hit = "miss"
        cache_entry: dict | None = None

        if cache_key:
            cache_entry = _product_cache.get(cache_key)
            if cache_entry is not None:
                enrichment_debug["cache_had_blank_dimensions"] = _cache_has_blank_dimension(cache_entry)
                enrichment_debug["cache_had_blank_image"] = _cache_has_blank_image(cache_entry)
                row, cache_fields_filled, still_missing = _apply_cache_to_row(
                    row, cache_entry, force_refresh
                )
                if not still_missing:
                    _log.info("[CACHE HIT: full] key=%s", cache_key)
                    product_cache_hit = "full"
                    enrichment_debug["cache_hit"] = "full"
                    updated = row.copy()
                    original = _str_val(updated.get("Source Type", ""))
                    if not original.endswith("_Enriched"):
                        updated["Source Type"] = f"{original}_Enriched" if original else "Enriched"
                    product_url = _str_val(updated.get("Product URL"))
                    needs_cached_page_exploit = bool(product_url) and (
                        _needs_dimension_recovery(updated)
                        or _needs_image_recovery(updated)
                    )
                    if needs_cached_page_exploit:
                        enrichment_debug["fresh_extraction_forced"] = True
                        domain_match_for_cache = get_domain_for_brand(brand)
                        verified_updated, verified_dim_result, verified_debug = _try_verified_page_enrichment(
                            updated,
                            cache_key=cache_key,
                            manufacturer_domain=domain_match_for_cache[0] if domain_match_for_cache else "",
                            session_cache=session_cache,
                            budget=budget,
                            debug=enrichment_debug,
                        )
                        enrichment_debug.update(verified_debug)
                        if _str_val(enrichment_debug.get("selected_product_url")):
                            if verified_dim_result is not None:
                                verified_updated["Dimension Source URL"] = verified_dim_result.source_url
                                verified_updated["Dimension Confidence"] = (
                                    verified_dim_result.confidence if verified_dim_result.confidence not in ("", "none", None) else ""
                                )
                                verified_updated["Dimension Source Type"] = (
                                    verified_dim_result.source_type if verified_dim_result.source_type not in ("", "none", None) else ""
                                )
                                verified_updated["Dimension Lookup Status"] = verified_dim_result.status
                            _append_stage(enrichment_debug, "ENRICHMENT_COMPLETE")
                            return _stamp_dimension_debug(
                                verified_updated,
                                {
                                    **enrichment_debug,
                                    "skipped_reason": "full cache hit with product page refresh",
                                    "enrichment_status": _classify_enrichment_status(verified_updated),
                                    "enrichment_error": "",
                                },
                            ), None, verified_dim_result
                    _append_stage(enrichment_debug, "ENRICHMENT_COMPLETE")
                    return _stamp_dimension_debug(
                        updated,
                        {
                            **enrichment_debug,
                            "skipped_reason": "full cache hit",
                            "enrichment_status": _classify_enrichment_status(updated),
                            "enrichment_error": "",
                        },
                    ), None, None
                else:
                    product_cache_hit = "partial"
                    enrichment_debug["cache_hit"] = "partial"
                    fields_searched.extend(still_missing)
                    _log.info("[CACHE HIT: partial] key=%s still_missing=%s", cache_key, still_missing)
            else:
                enrichment_debug["cache_hit"] = "miss"
                fields_searched.extend(_ESSENTIAL_CACHE_FIELDS)
                _log.info("[CACHE MISS] key=%s", cache_key)
        else:
            enrichment_debug["cache_hit"] = "unavailable"

        stored_source = None
        if cache_key and not force_refresh:
            try:
                stored_source = lookup_product_source(brand, model_sku)
            except Exception as exc:
                enrichment_debug["stored_source_rejected_reason"] = f"source memory lookup failed: {exc}"
                enrichment_debug["knowledge_base_rejected_reason"] = enrichment_debug["stored_source_rejected_reason"]
                stored_source = None
            if stored_source:
                before_missing = set(_missing_enrichment_fields(row))
                row, source_fields_filled = apply_product_source_to_row(row, stored_source)
                after_missing = set(_missing_enrichment_fields(row))
                enrichment_debug["stored_source_hit"] = True
                enrichment_debug["stored_source_used"] = _str_val(row.get("stored_source_used"))
                enrichment_debug["knowledge_base_hit"] = True
                enrichment_debug["knowledge_base_source_used"] = enrichment_debug["stored_source_used"]
                enrichment_debug["cache_hit"] = "stored_source"
                enrichment_debug["fields_filled"] = ", ".join(dict.fromkeys(cache_fields_filled + source_fields_filled))
                enrichment_debug["searches_avoided"] = max(1, int(enrichment_debug.get("searches_avoided") or 0))
                enrichment_debug["duplicate_searches_avoided_from_cache"] = 1
                if after_missing and before_missing != after_missing and (
                    _needs_dimension_recovery(row) or _needs_image_recovery(row)
                ):
                    enrichment_debug["fresh_extraction_forced"] = True
                try:
                    save_successful_source_from_row(row, notes="Stored source reused before paid search.")
                except Exception as exc:
                    enrichment_debug["stored_source_rejected_reason"] = f"source memory reuse update failed: {exc}"
                    enrichment_debug["knowledge_base_rejected_reason"] = enrichment_debug["stored_source_rejected_reason"]
                if not _needs_dimension_recovery(row) and not _needs_image_recovery(row):
                    updated = row.copy()
                    original = _str_val(updated.get("Source Type", ""))
                    if not original.endswith("_Enriched"):
                        updated["Source Type"] = f"{original}_Enriched" if original else "Enriched"
                    _append_stage(enrichment_debug, "ENRICHMENT_COMPLETE")
                    return _stamp_dimension_debug(
                        updated,
                        {
                            **enrichment_debug,
                            "skipped_reason": "stored source cache hit",
                            "enrichment_status": _classify_enrichment_status(updated),
                            "enrichment_error": "",
                        },
                    ), None, None
            else:
                enrichment_debug["knowledge_base_miss"] = True

        domain_match = get_domain_for_brand(brand)
        manufacturer_domain = domain_match[0] if domain_match else ""
        if not manufacturer_domain:
            try:
                manufacturer_domain = preferred_domain_hint(brand, _str_val(row.get("Product Category")))
                if manufacturer_domain:
                    enrichment_debug["manufacturer_domain_used"] = manufacturer_domain
                    enrichment_debug["stored_source_used"] = enrichment_debug.get("stored_source_used") or f"preferred-domain:{manufacturer_domain}"
                    enrichment_debug["preferred_domain_used"] = manufacturer_domain
            except Exception as exc:
                enrichment_debug["stored_source_rejected_reason"] = f"preferred domain hint failed: {exc}"
                enrichment_debug["knowledge_base_rejected_reason"] = enrichment_debug["stored_source_rejected_reason"]
        cached_or_existing_product_url = _str_val(row.get("Product URL"))
        if cached_or_existing_product_url and (
            _needs_dimension_recovery(row) or _needs_image_recovery(row)
        ) and (
            product_cache_hit in {"full", "partial"} or (
                cache_entry is not None and (
                    enrichment_debug["cache_had_blank_dimensions"]
                    or enrichment_debug["cache_had_blank_image"]
                )
            )
        ):
            enrichment_debug["fresh_extraction_forced"] = True

        # Verified product-page pass. This is the preferred low-cost path:
        # Product URL if present, otherwise one SKU/model-anchored manufacturer
        # lookup, then extract URL/image/dimensions/spec fields from that page.
        verified_updated, verified_dim_result, verified_debug = _try_verified_page_enrichment(
            row,
            cache_key=cache_key,
            manufacturer_domain=manufacturer_domain,
            session_cache=session_cache,
            budget=budget,
            debug=enrichment_debug,
        )
        enrichment_debug.update(verified_debug)
        if _str_val(enrichment_debug.get("selected_product_url")):
            if verified_dim_result is not None:
                verified_updated["Dimension Source URL"] = verified_dim_result.source_url
                verified_updated["Dimension Confidence"] = (
                    verified_dim_result.confidence if verified_dim_result.confidence not in ("", "none", None) else ""
                )
                verified_updated["Dimension Source Type"] = (
                    verified_dim_result.source_type if verified_dim_result.source_type not in ("", "none", None) else ""
                )
                verified_updated["Dimension Lookup Status"] = verified_dim_result.status
            _append_stage(enrichment_debug, "ENRICHMENT_COMPLETE")
            return _stamp_dimension_debug(
                verified_updated,
                {**enrichment_debug, "enrichment_status": _classify_enrichment_status(verified_updated), "enrichment_error": ""},
            ), None, verified_dim_result
        if stored_source and _str_val(row.get("Product URL")) and not _str_val(enrichment_debug.get("selected_product_url")):
            try:
                increment_source_failure(
                    brand,
                    model_sku,
                    _str_val(row.get("Product URL")),
                    _str_val(enrichment_debug.get("skipped_reason")) or "stored source did not verify during extraction",
                )
            except Exception as exc:
                enrichment_debug["stored_source_rejected_reason"] = f"source memory failure update failed: {exc}"
                enrichment_debug["knowledge_base_rejected_reason"] = enrichment_debug["stored_source_rejected_reason"]

        # Legacy fallback: If cache (or the row itself) gives us a Product URL but
        # the verified-page pass could not accept it, still exploit that URL in
        # Programa priority order: dimensions first, image second.
        legacy_dim_result: _DimensionResult | None = None
        if cached_or_existing_product_url and (
            _needs_dimension_recovery(row) or _needs_image_recovery(row)
        ):
            if product_cache_hit in {"full", "partial"} or (
                cache_entry is not None and (
                    enrichment_debug["cache_had_blank_dimensions"]
                    or enrichment_debug["cache_had_blank_image"]
                )
            ):
                enrichment_debug["fresh_extraction_forced"] = True
            if _needs_dimension_recovery(row):
                _append_stage(enrichment_debug, "SEARCHING_DIMENSIONS")
                legacy_dim_result = _find_dimensions(row, session_cache=session_cache, budget=budget)
                enrichment_debug.update(legacy_dim_result.debug or {})
                if legacy_dim_result.status == "found" and legacy_dim_result.confidence in ("high", "medium"):
                    row = row.copy()
                    row["Dimensions"] = legacy_dim_result.dimensions
                    if legacy_dim_result.width:
                        row["Width (in)"] = legacy_dim_result.width
                    if legacy_dim_result.height:
                        row["Height (in)"] = legacy_dim_result.height
                    if legacy_dim_result.depth:
                        row["Depth (in)"] = legacy_dim_result.depth
                    if legacy_dim_result.length:
                        row["Length (in)"] = legacy_dim_result.length
                    row["Dimension Source URL"] = legacy_dim_result.source_url
                    row["Dimension Confidence"] = legacy_dim_result.confidence
                    row["Dimension Source Type"] = legacy_dim_result.source_type
                    row["Dimension Lookup Status"] = legacy_dim_result.status
            if _needs_image_recovery(row):
                img, image_debug = _unpack_image_result(
                    _try_image_from_url(
                        cached_or_existing_product_url,
                        cache_key,
                        session_cache=session_cache,
                        budget=budget,
                        row=row,
                        return_debug=True,
                    )
                )
                enrichment_debug.update(image_debug)
                if img:
                    row = {**row, **image_debug, "Image URL": img}
                    if _needs_dimension_recovery(row):
                        enrichment_debug["image_only_success"] = True

        if _needs_image_recovery(row) and mode in {"deep", "max_accuracy"} and brand and model_sku:
            img, image_debug = _try_image_recovery_searches(
                row,
                cache_key=cache_key,
                manufacturer_domain=manufacturer_domain,
                session_cache=session_cache,
                budget=budget,
            )
            enrichment_debug.update(image_debug)
            if img:
                row = {**row, **image_debug, "Image URL": img}
                enrichment_debug["selected_image_url"] = img
                if _needs_dimension_recovery(row):
                    enrichment_debug["image_only_success"] = True

        # Fast mode: if manufacturer domain is known, skip general Brave search
        # (dimension lookup will handle targeted search via the known domain)
        needs_general_fields = any(
            not _str_val(row.get(f))
            for f in ("Product Name", "Finish / Color", "Product Category")
        )
        skip_general_search = (mode == "fast") or (
            bool(cached_or_existing_product_url) and not needs_general_fields
        )

        query = _build_search_query(row)
        results = []
        if not skip_general_search and budget.can_search():
            _log.info("[LIVE SEARCH] query=%s", query[:80])
            results = search_product_candidates(query, brand, session_cache=session_cache)
            budget.consume_search()
        elif not budget.can_search():
            _log.info("[BUDGET EXHAUSTED] skipping general search for key=%s", cache_key)

        if not results or results[0].domain_score < MIN_USE_SCORE:
            failure_reason = _str_val(enrichment_debug.get("skipped_reason")) or "no confident source found"
            updated = _mark_enrichment_failed(
                row,
                enrichment_debug,
                failure_reason,
            )
        else:
            best = results[0]
            parsed_domain = ""
            try:
                parsed_domain = urllib.parse.urlparse(best.url).netloc
            except ValueError as exc:
                _stamp_invalid_url_debug(
                    enrichment_debug,
                    best.url,
                    source="general_search_result",
                    step="general_search_domain_parse",
                    row=row,
                    query=_str_val(enrichment_debug.get("search_query_used")),
                    fallback_attempted=True,
                    reason=f"Invalid IPv6 URL: {exc}",
                )
            if parsed_domain and not domain_match:
                record_discovered_domain(brand, parsed_domain)
            raw_html = _fetch_page_html_with_debug(
                best.url,
                debug=enrichment_debug,
                source="general_search_result",
                step="general_search_page_fetch",
                row=row,
                query=_str_val(enrichment_debug.get("search_query_used")),
            )
            if raw_html and session_cache is not None:
                session_cache.urls[best.url] = raw_html
            page_text = _html_to_text(raw_html)

            if not raw_html:
                try:
                    domain = urllib.parse.urlparse(best.url).netloc or best.url[:50]
                except ValueError:
                    domain = best.url[:50]
                updated = _mark_enrichment_failed(
                    row,
                    enrichment_debug,
                    f"could not fetch {domain}",
                )
            else:
                extracted = _extract_with_claude(page_text, row)
                updated = _apply_enrichment(row, extracted, best.url, best.domain_score)

                # ── Image extraction (opportunistic — no extra Brave call) ─────
                image_url_found, image_debug = _extract_image_from_html(raw_html, best.url, updated, cache_key)
                enrichment_debug.update(image_debug)
                if image_url_found:
                    _log.info("[IMAGE FOUND] key=%s url=%s", cache_key, image_url_found[:80])
                    if not _str_val(updated.get("Image URL")):
                        updated["Image URL"] = image_url_found
                        updated.update(image_debug)
                else:
                    _log.info("[IMAGE MISSING] key=%s", cache_key)

                # ── Cache write-back (general fields found) ────────────────────
                if cache_key:
                    _cache_fields: dict = {}
                    if _str_val(updated.get("Product URL")):
                        _cache_fields["product_url"] = _str_val(updated.get("Product URL"))
                    if _str_val(updated.get("Finish / Color")):
                        _cache_fields["finish"] = _str_val(updated.get("Finish / Color"))
                    if _cache_fields:
                        _cache_fields["general_confidence"] = "medium"
                        _product_cache.update(cache_key, _cache_fields)
                    # Image cache write-back — never overwrite an existing valid entry
                    existing_entry = _product_cache.get(cache_key) or {}
                    if not existing_entry.get("image_url"):
                        if image_url_found:
                            _product_cache.update(cache_key, {
                                "image_url": image_url_found,
                                "original_image_url": image_debug.get("original_image_url") or image_url_found,
                                "image_confidence": image_debug.get("image_confidence") or "medium",
                                "image_source_url": best.url,
                                "cloudinary_url": image_debug.get("cloudinary_url") or "",
                                "general_confidence": "medium",
                            })
                        else:
                            _product_cache.update(cache_key, {"image_url": None, "image_url__reason": "not found on page"})

        # ── Dimension enrichment pass ──────────────────────────────────────────
        brand_val = _str_val(updated.get("Brand"))
        model_val = _str_val(updated.get("Model/SKU"))
        dims_val = _str_val(updated.get("Dimensions"))
        dim_result: _DimensionResult | None = legacy_dim_result
        if brand_val and model_val and not has_complete_3d_dimensions(dims_val):
            _append_stage(enrichment_debug, "SEARCHING_DIMENSIONS")
            dim_result = _find_dimensions(updated, session_cache=session_cache, budget=budget)
            if dim_result.status == "found" and dim_result.confidence in ("high", "medium"):
                updated["Dimensions"] = dim_result.dimensions
                if dim_result.width:
                    updated["Width (in)"] = dim_result.width
                if dim_result.height:
                    updated["Height (in)"] = dim_result.height
                if dim_result.depth:
                    updated["Depth (in)"] = dim_result.depth
                if dim_result.length:
                    updated["Length (in)"] = dim_result.length
                # ── Cache write-back (dimension fields) ────────────────────
                if cache_key:
                    _conf_rank = {"high": 2, "medium": 1, "low": 0, "none": -1}
                    existing_entry = _product_cache.get(cache_key) or {}
                    existing_dim_conf = existing_entry.get("dimension_confidence", "none")
                    new_dim_conf = dim_result.confidence or "none"
                    if _conf_rank.get(new_dim_conf, -1) >= _conf_rank.get(existing_dim_conf, -1):
                        _product_cache.update(cache_key, {
                            "dimensions": dim_result.dimensions,
                            "width_in": dim_result.width or None,
                            "height_in": dim_result.height or None,
                            "depth_in": dim_result.depth or None,
                            "length_in": dim_result.length or None,
                            "dimension_source_url": dim_result.source_url,
                            "dimension_confidence": dim_result.confidence,
                        })
                if "Cutout:" in dim_result.evidence_text:
                    cutout_part = dim_result.evidence_text.split("Cutout:")[-1].strip()
                    if cutout_part:
                        existing_notes = _str_val(updated.get("Notes"))
                        tag = f"[Cutout Dimensions: {cutout_part}]"
                        if tag not in existing_notes:
                            updated["Notes"] = f"{existing_notes} {tag}".strip() if existing_notes else tag
            updated["Dimension Source URL"] = dim_result.source_url
            updated["Dimension Confidence"] = dim_result.confidence if dim_result.confidence not in ("", "none", None) else ""
            updated["Dimension Source Type"] = dim_result.source_type if dim_result.source_type not in ("", "none", None) else ""
            updated["Dimension Lookup Status"] = dim_result.status
            updated = _stamp_dimension_debug(updated, {**enrichment_debug, **dim_result.debug})
        else:
            updated = _stamp_dimension_debug(
                updated,
                {
                    **enrichment_debug,
                    "skipped_reason": "dimensions already complete or missing brand/model",
                    "final_dimensions": _str_val(updated.get("Dimensions")),
                },
            )

        _append_stage(enrichment_debug, "ENRICHMENT_COMPLETE")
        try:
            saved_source = save_successful_source_from_row(updated, notes="Enrichment result saved after fallback extraction.")
            if saved_source:
                enrichment_debug["stored_source_updated"] = True
                enrichment_debug["knowledge_base_updated"] = True
                enrichment_debug["stored_source_used"] = (
                    enrichment_debug.get("stored_source_used")
                    or saved_source.get("product_page_url")
                    or saved_source.get("manufacturer_url")
                    or saved_source.get("dimension_source_url")
                    or saved_source.get("spec_sheet_url")
                    or saved_source.get("image_source_url")
                    or ""
                )
                enrichment_debug["knowledge_base_source_used"] = enrichment_debug["stored_source_used"]
        except Exception as exc:
            enrichment_debug["stored_source_rejected_reason"] = f"source memory save failed: {exc}"
            enrichment_debug["knowledge_base_rejected_reason"] = enrichment_debug["stored_source_rejected_reason"]
        return _stamp_dimension_debug(
                updated,
                _merge_preserved_debug(
                    updated,
                    {**enrichment_debug, "enrichment_status": _classify_enrichment_status(updated), "enrichment_error": ""},
                ),
            ), None, dim_result
    except Exception as exc:
        tb = traceback.format_exc()
        _log.exception("Enrichment failed for row brand=%s model=%s", _str_val(row.get("Brand")), _str_val(row.get("Model/SKU")))
        return _stamp_dimension_debug(
            row,
            {
                "skipped_reason": str(exc),
                "enrichment_status": "failed",
                "enrichment_error": str(exc),
                "debug_traceback": tb,
                "stage_log": "ENRICHMENT_STARTED; ENRICHMENT_COMPLETE",
            },
        ), str(exc), None


def enrich_dataframe(
    df: pd.DataFrame,
    enrichment_mode: str = "standard",
    force_refresh: bool = False,
    use_web_enrichment: bool = True,
    enrichment_budget_usd: float | None = 0.25,
) -> tuple[pd.DataFrame, list[str], list[dict]]:
    """
    Enrich all qualifying rows in df. Returns (updated_df, error_list, dimension_diagnostics).
    Exceptions in individual rows are caught and logged; the row is left unchanged.
    """
    df = df.copy()
    errors: list[str] = []
    dimension_diagnostics: list[dict] = []

    if not use_web_enrichment:
        return df, errors, dimension_diagnostics

    for field in _ENRICHMENT_DEBUG_FIELDS:
        if field not in df.columns:
            df[field] = pd.Series([None] * len(df), index=df.index, dtype=object)
        else:
            df[field] = df[field].astype(object)

    from src.enrichment_cache import SessionCache as _SC
    _session = _SC(force_refresh=force_refresh)

    mode = _normalize_mode(enrichment_mode)
    search_cost = _brave_cost_per_search()
    mode_cap_usd = _hard_cost_cap_for_mode(mode)
    try:
        requested_cap_usd = float(enrichment_budget_usd) if enrichment_budget_usd is not None else mode_cap_usd
    except (TypeError, ValueError):
        requested_cap_usd = mode_cap_usd
    hard_cap_usd = max(0.0, min(mode_cap_usd, requested_cap_usd))
    max_run_searches = max(0, int(hard_cap_usd / search_cost)) if search_cost > 0 else 999_999
    total_searches_used = 0
    total_fetches_used = 0
    unique_products_searched = 0
    dimension_diagnostic_keys: set[tuple[str, str, str, str]] = set()

    grouped_indices: dict[str, list] = {}
    unique_counter = 0
    for idx, row in df.iterrows():
        r = row.to_dict()
        if not _qualifies(r):
            debug = _dimension_debug_defaults(r)
            _append_stage(debug, "ENRICHMENT_STARTED")
            debug.update(_skip_debug(r))
            debug["skipped_reason"] = "row does not qualify for enrichment"
            debug["enrichment_status"] = "skipped"
            debug["enrichment_error"] = debug["skipped_reason"]
            _append_stage(debug, "ENRICHMENT_COMPLETE")
            for col, val in debug.items():
                if col in df.columns:
                    df.at[idx, col] = val
            continue
        key = _row_enrichment_key(r)
        if not key:
            unique_counter += 1
            key = f"__row_{idx}_{unique_counter}"
        grouped_indices.setdefault(key, []).append(idx)

    representative_for_key: dict[str, object] = {
        key: max(indices, key=lambda i: _row_richness_score(df.loc[i].to_dict()))
        for key, indices in grouped_indices.items()
    }
    searched_keys: set[str] = set()

    def _group_rep(key: str) -> object:
        return representative_for_key[key]

    def _group_indices(key: str) -> list:
        return grouped_indices.get(key, [])

    def _group_incomplete(key: str, *, dimensions_only: bool = False, image_only: bool = False) -> bool:
        rep = df.loc[_group_rep(key)].to_dict()
        if not _qualifies(rep):
            return False
        if _str_val(rep.get("enrichment_status")).lower() == "failed":
            return False
        if dimensions_only:
            return _needs_dimension_recovery(rep)
        if image_only:
            return _needs_image_recovery(rep)
        return _needs_dimension_recovery(rep) or _needs_image_recovery(rep) or not _str_val(rep.get("Product URL"))

    def _ordered_keys(keys: list[str], *, accessories_last: bool = True) -> list[str]:
        def rank(key: str) -> tuple[int, int, str]:
            row = df.loc[_group_rep(key)].to_dict()
            accessory = 1 if _product_component_type(row) == "accessory/component" and accessories_last else 0
            return (accessory, -_row_richness_score(row), key)
        return sorted(keys, key=rank)

    def _stamp_group_without_paid_search(key: str, reason: str) -> None:
        rep_idx = _group_rep(key)
        rep = df.loc[rep_idx].to_dict()
        debug = _dimension_debug_defaults(rep)
        _append_stage(debug, "ENRICHMENT_STARTED")
        _append_stage(debug, "ENRICHMENT_COMPLETE")
        debug.update({
            "round_attempted": "round_0_cache_direct",
            "was_searched": "no",
            "selected_strategy": "cache/direct",
            "skipped_reason": reason,
            "enrichment_status": _classify_enrichment_status(rep),
            "enrichment_error": "",
            "budget_spent": "searches=0/0; fetches=0/0",
        })
        stamped = _stamp_dimension_debug(rep, debug)
        for idx in _group_indices(key):
            row_to_write = stamped if idx == rep_idx else _merge_duplicate_enrichment(df.loc[idx].to_dict(), stamped, rep_idx)
            for col, val in row_to_write.items():
                if col in df.columns:
                    df.at[idx, col] = val

    def _apply_enriched_group(key: str, updated: dict, error: str | None, dim_result: _DimensionResult | None) -> None:
        nonlocal unique_products_searched
        rep_idx = _group_rep(key)
        if error:
            errors.append(error)
            updated["enrichment_status"] = "failed"
            updated["enrichment_error"] = error
        else:
            updated["enrichment_status"] = _classify_enrichment_status(updated)
            updated["enrichment_error"] = ""
        updated["useful_fields_found"] = _str_val(updated.get("fields_filled"))
        updated = _apply_status_fields(updated)
        searches_used = _budget_searches_used(updated.get("budget_spent"))
        if searches_used > 0 and key not in searched_keys:
            unique_products_searched += 1
            searched_keys.add(key)

        for col, val in updated.items():
            if col in df.columns:
                df.at[rep_idx, col] = val

        if not error:
            _log_enrichment_outcome(updated)
            if dim_result is not None:
                diagnostic_key = (
                    key,
                    _str_val(dim_result.status),
                    _str_val(dim_result.source_url),
                    _str_val(dim_result.failure_reason),
                )
                if diagnostic_key not in dimension_diagnostic_keys:
                    dimension_diagnostic_keys.add(diagnostic_key)
                    dimension_diagnostics.append({
                        "row_index": int(rep_idx),
                        "product_name": _str_val(updated.get("Product Name")),
                        "model_searched": _str_val(updated.get("Model/SKU")),
                        "domain_used": _domain_of(dim_result.source_url),
                        "queries_tried": list(dim_result.queries_tried),
                        "urls_checked": list(dim_result.urls_checked),
                        "evidence_text": dim_result.evidence_text,
                        "confidence": dim_result.confidence if dim_result.confidence not in ("", "none", None) else "",
                        "status": dim_result.status,
                        "source_url": dim_result.source_url,
                        "failure_reason": dim_result.failure_reason,
                        **{field: dim_result.debug.get(field, "") for field in _ENRICHMENT_DEBUG_FIELDS},
                    })

        for idx in _group_indices(key):
            if idx == rep_idx:
                continue
            duplicate = _merge_duplicate_enrichment(df.loc[idx].to_dict(), updated, rep_idx)
            duplicate = _apply_status_fields(duplicate)
            for col, val in duplicate.items():
                if col in df.columns:
                    df.at[idx, col] = val

    def _attempt_group(
        key: str,
        *,
        round_name: str,
        search_limit: int,
        fetch_limit: int,
        budget_reason: str = "Skipped due to enrichment budget cap",
    ) -> None:
        nonlocal total_searches_used, total_fetches_used
        rep_idx = _group_rep(key)
        remaining_searches = max_run_searches - total_searches_used
        allowed_searches = max(0, min(search_limit, remaining_searches))
        remaining_cost = max(0.0, hard_cap_usd - (total_searches_used * search_cost))
        if search_limit > 0 and allowed_searches <= 0:
            for idx in _group_indices(key):
                r = df.loc[idx].to_dict()
                blocked_debug = _dimension_debug_defaults(r)
                _append_stage(blocked_debug, "ENRICHMENT_STARTED")
                _append_stage(blocked_debug, "ENRICHMENT_COMPLETE")
                blocked_debug.update({
                    "budget_blocked": True,
                    "round_attempted": round_name,
                    "was_searched": "no",
                    "selected_strategy": "skipped",
                    "skipped_reason": budget_reason,
                    "reason_row_skipped": budget_reason,
                    "skip_reason": budget_reason,
                    "skip_threshold_hit": True,
                    "enrichment_status": _classify_enrichment_status(r),
                    "enrichment_error": "",
                    "budget_spent": "searches=0/0; fetches=0/0",
                    "budget_stop_reason": (
                        f"required_searches={search_limit}; remaining_searches={remaining_searches}; "
                        f"spent=${total_searches_used * search_cost:.4f}; cap=${hard_cap_usd:.4f}"
                    ),
                    "total_budget_remaining": f"{remaining_cost:.4f}",
                    "search_budget_remaining": str(max(0, remaining_searches)),
                    "openai_budget_remaining": "not_applicable_standard_enrichment",
                    "budget_skip_threshold": f"allowed_searches={allowed_searches}; required_searches={search_limit}",
                })
                stamped = _stamp_dimension_debug(r, blocked_debug)
                for col, val in stamped.items():
                    if col in df.columns:
                        df.at[idx, col] = val
            return

        setattr(_session, "remaining_searches", allowed_searches)
        setattr(_session, "remaining_fetches", max(0, fetch_limit))
        try:
            representative = df.loc[rep_idx].to_dict()
            updated, error, dim_result = enrich_row(
                representative,
                enrichment_mode=enrichment_mode,
                session_cache=_session,
            )
            updated["round_attempted"] = round_name
            if round_name == "round_0_cache_direct" and (error or _str_val(updated.get("enrichment_status")).lower() == "failed"):
                error = None
                updated["enrichment_status"] = _classify_enrichment_status(updated)
                updated["enrichment_error"] = ""
                updated["skipped_reason"] = "Direct/cache source incomplete; queued for fair paid search"
            searches_used = _budget_searches_used(updated.get("budget_spent"))
            fetches_used = _budget_fetches_used(updated.get("budget_spent"))
            total_searches_used += searches_used
            total_fetches_used += fetches_used
            updated["total_budget_remaining"] = f"{max(0.0, hard_cap_usd - (total_searches_used * search_cost)):.4f}"
            updated["search_budget_remaining"] = str(max(0, max_run_searches - total_searches_used))
            updated["openai_budget_remaining"] = "not_applicable_standard_enrichment"
            _apply_enriched_group(key, updated, error, dim_result)
        except Exception as exc:
            tb = traceback.format_exc()
            label_row = df.loc[rep_idx].to_dict()
            label = _str_val(label_row.get("Product Name")) or _str_val(label_row.get("Brand")) or _str_val(label_row.get("Model/SKU")) or str(rep_idx)
            errors.append(f"Row '{label}': {exc}")
            for idx in _group_indices(key):
                r = df.loc[idx].to_dict()
                failed_debug = _dimension_debug_defaults(r)
                _append_stage(failed_debug, "ENRICHMENT_STARTED")
                _append_stage(failed_debug, "ENRICHMENT_COMPLETE")
                failed_debug.update({
                    "round_attempted": round_name,
                    "enrichment_status": "failed",
                    "enrichment_error": str(exc),
                    "debug_traceback": tb,
                    "skipped_reason": str(exc),
                })
                stamped = _stamp_dimension_debug(r, failed_debug)
                for col, val in stamped.items():
                    if col in df.columns:
                        df.at[idx, col] = val

    all_keys = _ordered_keys(list(grouped_indices))

    # Round 0: free/cache/direct pass for every unique product. This exploits
    # existing Product URLs and exact product memory without spending Brave calls.
    for key in all_keys:
        rep = df.loc[_group_rep(key)].to_dict()
        cache_key = _row_enrichment_key(rep)
        try:
            has_cache_hint = bool(cache_key and (_product_cache.get(cache_key) or lookup_product_source(_str_val(rep.get("Brand")), _str_val(rep.get("Model/SKU")))))
        except Exception:
            has_cache_hint = bool(cache_key and _product_cache.get(cache_key))
        has_direct_url = bool(_str_val(rep.get("Product URL")))
        if has_cache_hint or has_direct_url:
            _attempt_group(key, round_name="round_0_cache_direct", search_limit=0, fetch_limit=3)
        else:
            _stamp_group_without_paid_search(key, "No cached/product URL source; queued for fair paid search")

    # Round 1: one manufacturer/spec-sheet focused paid search per incomplete
    # unique product before any product gets retries.
    for key in _ordered_keys([key for key in all_keys if _group_incomplete(key)]):
        _attempt_group(key, round_name="round_1_manufacturer_spec", search_limit=1, fetch_limit=5)
        time.sleep(0.05)

    # Round 2: dimension-focused retry only for rows still missing complete W/H/D.
    for key in _ordered_keys([key for key in all_keys if _group_incomplete(key, dimensions_only=True)]):
        if max_run_searches - total_searches_used <= 0:
            _attempt_group(key, round_name="round_2_dimension_retry", search_limit=1, fetch_limit=0)
            continue
        _attempt_group(key, round_name="round_2_dimension_retry", search_limit=1, fetch_limit=4)
        time.sleep(0.05)

    # Round 3: dedicated image recovery for rows still missing images. Deep and
    # Max Accuracy keep going even when dimensions are also still incomplete.
    for key in _ordered_keys([
        key
        for key in all_keys
        if _group_incomplete(key, image_only=True)
    ]):
        remaining = max_run_searches - total_searches_used
        if mode == "max_accuracy":
            search_limit = min(3, remaining)
            fetch_limit = 8
        elif mode == "deep":
            search_limit = min(2, remaining)
            fetch_limit = 5
        else:
            search_limit = 1
            fetch_limit = 3
        _attempt_group(key, round_name="round_3_image_retry", search_limit=search_limit, fetch_limit=fetch_limit)
        time.sleep(0.05)

    df.attrs["enrichment_budget_cap_usd"] = hard_cap_usd
    df.attrs["enrichment_search_cost_usd"] = search_cost
    df.attrs["unique_products_searched"] = unique_products_searched
    df.attrs["duplicate_rows_skipped_for_enrichment"] = max(0, sum(len(indices) - 1 for indices in grouped_indices.values()))
    df.attrs["brave_searches_used"] = total_searches_used
    df.attrs["page_fetches_used"] = total_fetches_used
    return df, errors, dimension_diagnostics


def recover_images_for_dataframe(
    df: pd.DataFrame,
    enrichment_mode: str = "standard",
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Targeted image recovery pass — runs only on rows that are missing Image URL.

    For each row without an image, attempts in order:
      1. Fetch the Product URL and extract an image via the standard pipeline.
      2. Brand/SKU-targeted image/product-page search as fallback.

    Returns (updated_df, diagnostics) where diagnostics is a list of per-row dicts:
      {row_index, product_name, status ("found"|"not_found"), source, image_url}
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    df = df.copy()
    if "Image URL" not in df.columns:
        df["Image URL"] = ""
    for field in _image_debug_defaults():
        if field not in df.columns:
            df[field] = pd.Series([None] * len(df), index=df.index, dtype=object)
        else:
            df[field] = df[field].astype(object)
    diagnostics: list[dict] = []
    session_cache = _SessionCache()
    budget = _budget_for_mode(enrichment_mode)

    for idx, row in df.iterrows():
        if _str_val(row.get("Image URL")):
            continue  # already has image — skip

        r = row.to_dict()
        product_name = _str_val(r.get("Product Name"))
        brand = _str_val(r.get("Brand"))
        model_sku = _str_val(r.get("Model/SKU"))
        cache_key = _normalize_key(brand, model_sku) if brand and model_sku else ""
        manufacturer_domain = ""
        domain_match = get_domain_for_brand(brand) if brand else None
        if domain_match:
            manufacturer_domain = domain_match[0]
        if not manufacturer_domain:
            manufacturer_domain = preferred_domain_hint(brand, _str_val(r.get("Product Category")))

        image_url: str | None = None
        source = "none"

        # Step 1: fetch the cached/available Product URL
        product_url = _str_val(r.get("Product URL"))
        if product_url:
            _log.info("[RECOVER] row=%s trying product_url=%s", idx, product_url[:80])
            image_url, image_debug = _unpack_image_result(
                _try_image_from_url(
                    product_url,
                    cache_key,
                    session_cache=session_cache,
                    budget=budget,
                    row=r,
                    return_debug=True,
                )
            )
            if image_url:
                source = "product_url"
        else:
            image_debug = _image_debug_defaults()

        if not image_url and brand and model_sku:
            _log.info("[RECOVER] row=%s trying image-specific search brand=%s model=%s", idx, brand, model_sku)
            image_url, image_debug = _try_image_recovery_searches(
                r,
                cache_key=cache_key,
                manufacturer_domain=manufacturer_domain,
                session_cache=session_cache,
                budget=budget,
            )
            if image_url:
                source = "image_search"

        diagnostics.append({
            "row_index": int(idx),
            "product_name": product_name,
            "brand": brand,
            "model_sku": model_sku,
            "product_url": product_url,
            "status": "found" if image_url else "not_found",
            "source": source,
            "image_url": image_url or "",
            "image_source_url": image_debug.get("image_source_url", ""),
            "image_confidence": image_debug.get("image_confidence", ""),
            "image_failure_reason": image_debug.get("image_failure_reason") or image_debug.get("cloudinary_error", ""),
            "budget_spent": _budget_summary(budget),
            **{k: image_debug.get(k, "") for k in _image_debug_defaults()},
        })

        if image_url and "Image URL" in df.columns:
            df.at[idx, "Image URL"] = image_url
            for field, value in image_debug.items():
                if field in df.columns:
                    df.at[idx, field] = value
            if image_debug.get("image_source_url") and "image_source_url" in df.columns:
                df.at[idx, "image_source_url"] = image_debug["image_source_url"]
            try:
                saved = save_successful_source_from_row({**r, **image_debug, "Image URL": image_url}, notes="Dedicated image recovery result.")
                if saved:
                    for field, value in {
                        "stored_source_updated": True,
                        "knowledge_base_updated": True,
                        "knowledge_base_source_used": saved.get("image_source_url") or saved.get("product_page_url") or "",
                    }.items():
                        if field in df.columns:
                            df.at[idx, field] = value
            except Exception:
                pass

        time.sleep(0.3)

    found = sum(1 for d in diagnostics if d["status"] == "found")
    _log.info("[RECOVER] complete — recovered %d / %d missing images", found, len(diagnostics))
    return df, diagnostics
