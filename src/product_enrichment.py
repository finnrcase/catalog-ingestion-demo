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
    "sku_match_location",
    "page_fetched",
    "dimensions_found",
    "image_candidates_count",
    "selected_image_url",
    "fields_filled",
    "budget_spent",
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


def _build_sku_lookup_queries(row: dict, manufacturer_domain: str = "") -> list[str]:
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))
    product_name = _str_val(row.get("Product Name"))
    queries: list[str] = []
    if brand and model:
        queries.extend([
            f'"{brand}" "{model}" dimensions image product page',
            f'"{brand}" "{model}" product',
        ])
    if manufacturer_domain and model:
        queries.extend([
            f'site:{manufacturer_domain} "{model}" dimensions image product page',
            f'site:{manufacturer_domain} "{model}" specifications',
            f'site:{manufacturer_domain} "{model}" spec sheet PDF',
            f'site:{manufacturer_domain} "{model}" product',
        ])
    if brand and model:
        queries.extend([
            f'"{brand}" "{model}" specifications',
            f'"{brand}" "{model}" spec sheet PDF',
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


def _fetch_page_html(url: str) -> str:
    """Fetch URL with httpx and return raw HTML. Empty string on error."""
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


def _fetch_page_html_budgeted(
    url: str,
    *,
    session_cache: "_SessionCache | None" = None,
    budget: "_SearchBudget | None" = None,
    debug: dict | None = None,
) -> str:
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
    html = _fetch_page_html(url)
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


def _check_image_content_type(url: str) -> bool:
    """Confirm url points to an image via HEAD, falling back to a GET byte-range request.

    Many manufacturer CDNs (Scene7, Imgix, Akamai) block HEAD with 405/403.
    If HEAD fails with a non-2xx status we retry with a small GET range request,
    which is universally supported and still confirms the content-type without
    downloading the whole image.
    """
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
    official = _domain_matches(page_domain, manufacturer_domain)

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
        if cache_key:
            existing_entry = _product_cache.get(cache_key) or {}
            if not existing_entry.get("image_url"):
                _product_cache.update(cache_key, {"image_url": None, "image_url__reason": debug["cloudinary_error"]})
        return None, debug

    debug["original_image_url"] = selected.url
    debug["image_confidence"] = selected.confidence

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

    raw_html = ""
    if session_cache is not None and product_url in session_cache.urls:
        raw_html = str(session_cache.urls.get(product_url) or "")
    if not raw_html:
        if budget is not None and not budget.can_fetch():
            debug = _image_debug_defaults()
            debug["budget_blocked"] = True
            debug["cloudinary_error"] = "budget blocked image page fetch"
            return (None, debug) if return_debug else None
        raw_html = _fetch_page_html(product_url)
        if budget is not None:
            budget.consume_fetch()
        if raw_html and session_cache is not None:
            session_cache.urls[product_url] = raw_html
    if not raw_html:
        _log.info("[IMAGE PIPELINE] fetch failed url=%s", product_url[:80])
        return (None, _image_debug_defaults()) if return_debug else None

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
        "sku_match_location": "",
        "page_fetched": False,
        "dimensions_found": False,
        "image_candidates_count": "",
        "selected_image_url": "",
        "fields_filled": "",
        "budget_spent": "",
    }
    debug.update(_image_debug_defaults())
    return debug


def _stamp_dimension_debug(row: dict, debug: dict) -> dict:
    updated = row.copy()
    merged = _dimension_debug_defaults(updated)
    merged.update(debug or {})
    for field in _ENRICHMENT_DEBUG_FIELDS:
        updated[field] = merged.get(field, "")
    return updated


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
    candidates = []
    queries_used: list[str] = []
    for query in _build_sku_lookup_queries(row, manufacturer_domain):
        if budget is not None and not budget.can_search():
            debug["budget_blocked"] = True
            debug["skipped_reason"] = "budget blocked SKU product search"
            break
        queries_used.append(query)
        debug["search_provider"] = "brave"
        debug["search_query_used"] = " | ".join(queries_used)
        results = search_product_candidates(query, brand, session_cache=session_cache)
        if budget is not None:
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

    def _candidate_rank(result) -> tuple[int, int, int, int]:
        url = _str_val(getattr(result, "url", ""))
        title = _str_val(getattr(result, "title", ""))
        description = _str_val(getattr(result, "description", ""))
        domain = _domain_of(url)
        sku_hit = 1 if model_norm and model_norm in _norm_token(" ".join([url, title, description])) else 0
        official_hit = 1 if _domain_matches(domain, manufacturer_domain) else 0
        weak_penalty = -1 if _reject_weak_product_url(url) else 0
        return (
            sku_hit,
            official_hit,
            int(getattr(result, "domain_score", 0) or 0),
            weak_penalty,
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
    html = _fetch_page_html_budgeted(url, session_cache=session_cache, budget=budget, debug=debug)
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
    if not product_url or _reject_weak_product_url(product_url):
        candidate_limit = max(5, getattr(budget, "max_urls", 5) if budget is not None else 5)
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
                    # Image is not an essential cache field. If it is missing,
                    # exploit the known product URL deterministically instead of
                    # letting a full cache hit block fresh image recovery.
                    if _needs_image_recovery(updated):
                        product_url = _str_val(updated.get("Product URL"))
                        if product_url:
                            enrichment_debug["fresh_extraction_forced"] = True
                            img, image_debug = _unpack_image_result(
                                _try_image_from_url(
                                    product_url,
                                    cache_key,
                                    session_cache=session_cache,
                                    budget=budget,
                                    row=updated,
                                    return_debug=True,
                                )
                            )
                            enrichment_debug.update(image_debug)
                            if img:
                                updated["Image URL"] = img
                                updated.update(image_debug)
                    product_url = _str_val(updated.get("Product URL"))
                    needs_cached_page_exploit = bool(product_url) and (
                        _needs_dimension_recovery(updated)
                        or _needs_image_recovery(updated)
                    )
                    if needs_cached_page_exploit:
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
                                    "enrichment_status": "complete",
                                    "enrichment_error": "",
                                },
                            ), None, verified_dim_result
                    _append_stage(enrichment_debug, "ENRICHMENT_COMPLETE")
                    return _stamp_dimension_debug(
                        updated,
                        {
                            **enrichment_debug,
                            "skipped_reason": "full cache hit",
                            "enrichment_status": "complete",
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

        domain_match = get_domain_for_brand(brand)
        manufacturer_domain = domain_match[0] if domain_match else ""
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
                {**enrichment_debug, "enrichment_status": "complete", "enrichment_error": ""},
            ), None, verified_dim_result

        # Legacy fallback: If cache (or the row itself) gives us a Product URL but
        # the verified-page pass could not accept it, still try the older
        # deterministic image-only path before generic search.
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
            parsed_domain = urllib.parse.urlparse(best.url).netloc
            if parsed_domain and not domain_match:
                record_discovered_domain(brand, parsed_domain)
            raw_html = _fetch_page_html(best.url)
            if raw_html and session_cache is not None:
                session_cache.urls[best.url] = raw_html
            page_text = _html_to_text(raw_html)

            if not raw_html:
                domain = urllib.parse.urlparse(best.url).netloc or best.url[:50]
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
        dim_result: _DimensionResult | None = None
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
        return _stamp_dimension_debug(
            updated,
            _merge_preserved_debug(
                updated,
                {**enrichment_debug, "enrichment_status": "complete", "enrichment_error": ""},
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

        try:
            updated, error, dim_result = enrich_row(
                r,
                enrichment_mode=enrichment_mode,
                session_cache=_session,
            )
            if error:
                errors.append(error)
                updated["enrichment_status"] = "failed"
                updated["enrichment_error"] = error
            elif not _str_val(updated.get("enrichment_status")):
                updated["enrichment_status"] = "complete"
                updated["enrichment_error"] = ""

            # Only write back columns that already exist in the DataFrame.
            # The intake schema guarantees all expected columns are present;
            # this guard prevents accidental column creation mid-iteration.
            for col, val in updated.items():
                if col in df.columns:
                    df.at[idx, col] = val

            if not error:
                _log_enrichment_outcome(updated)

                # Collect dimension diagnostics if lookup ran.
                # Diagnostic is built from DimensionResult directly (not from row dict),
                # so it is accurate even if dimension columns are absent from the DataFrame.
                if dim_result is not None:
                    dimension_diagnostics.append({
                        "row_index": int(idx),
                        "product_name": _str_val(updated.get("Product Name")),
                        "model_searched": _str_val(updated.get("Model/SKU")),
                        "domain_used": urllib.parse.urlparse(
                            dim_result.source_url
                        ).netloc or "",
                        "queries_tried": list(dim_result.queries_tried),
                        "urls_checked": list(dim_result.urls_checked),
                        "evidence_text": dim_result.evidence_text,
                        "confidence": dim_result.confidence if dim_result.confidence not in ("", "none", None) else "",
                        "status": dim_result.status,
                        "source_url": dim_result.source_url,
                        "failure_reason": dim_result.failure_reason,
                        **{field: dim_result.debug.get(field, "") for field in _ENRICHMENT_DEBUG_FIELDS},
                    })
        except Exception as exc:
            tb = traceback.format_exc()
            label = _str_val(r.get("Product Name")) or _str_val(r.get("Brand")) or _str_val(r.get("Model/SKU")) or str(idx)
            errors.append(f"Row '{label}': {exc}")
            failed_debug = _dimension_debug_defaults(r)
            _append_stage(failed_debug, "ENRICHMENT_STARTED")
            _append_stage(failed_debug, "ENRICHMENT_COMPLETE")
            failed_debug.update({
                "enrichment_status": "failed",
                "enrichment_error": str(exc),
                "debug_traceback": tb,
                "skipped_reason": str(exc),
            })
            for col, val in failed_debug.items():
                if col in df.columns:
                    df.at[idx, col] = val

        time.sleep(0.5)

    return df, errors, dimension_diagnostics


def recover_images_for_dataframe(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Targeted image recovery pass — runs only on rows that are missing Image URL.

    For each row without an image, attempts in order:
      1. Fetch the Product URL and extract an image via the standard pipeline.
      2. (Future) Brand/SKU-targeted image search as fallback.

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

    for idx, row in df.iterrows():
        if _str_val(row.get("Image URL")):
            continue  # already has image — skip

        r = row.to_dict()
        product_name = _str_val(r.get("Product Name"))
        brand = _str_val(r.get("Brand"))
        model_sku = _str_val(r.get("Model/SKU"))
        cache_key = _normalize_key(brand, model_sku) if brand and model_sku else ""

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
                    row=r,
                    return_debug=True,
                )
            )
            if image_url:
                source = "product_url"
        else:
            image_debug = _image_debug_defaults()

        diagnostics.append({
            "row_index": int(idx),
            "product_name": product_name,
            "brand": brand,
            "model_sku": model_sku,
            "product_url": product_url,
            "status": "found" if image_url else "not_found",
            "source": source,
            "image_url": image_url or "",
            **{k: image_debug.get(k, "") for k in _image_debug_defaults()},
        })

        if image_url and "Image URL" in df.columns:
            df.at[idx, "Image URL"] = image_url
            for field, value in image_debug.items():
                if field in df.columns:
                    df.at[idx, field] = value

        time.sleep(0.3)

    found = sum(1 for d in diagnostics if d["status"] == "found")
    _log.info("[RECOVER] complete — recovered %d / %d missing images", found, len(diagnostics))
    return df, diagnostics
