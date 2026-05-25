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
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field

import httpx
import pandas as pd
from dotenv import load_dotenv

from src.brand_lookup_registry import registry_domains_for_brand
from src.brave_search import BRAVE_API_KEY, search_product_candidates
from src.category_ai import _normalise_category
from src.dimension_enrichment import DimensionResult as _DimensionResult, find_dimensions as _find_dimensions
from src.dimensions import has_complete_3d_dimensions
from src.image_presence import row_has_image
from src.image_evidence import sku_appears_in_text
from src.measurement_parser import normalize_dimension_fields
from src.manufacturer_domains import get_domain_for_brand, record_discovered_domain, record_verified_domain
from src.official_product_lookup import lookup_official_product_page
from src.product_evidence import ProductEvidence, score_product_page
from src.product_images import extract_product_page_image
from src.product_lookup_cache import (
    ProductLookupCache as _ProductLookupCache,
    can_reuse_lookup,
    is_no_result,
    make_lookup_cache_key,
)
from src.product_page_specs import extract_product_page_specs
from src.product_resolver import resolve_product_page

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
_lookup_cache = _ProductLookupCache()

# Cache field mappings: cache field name → row column name
_CACHE_GENERAL_FIELDS: dict[str, str] = {
    "product_url": "Product URL",
    "finish": "Finish / Color",
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

MIN_USE_SCORE = 40   # below this: skip entirely, note in Notes
MIN_CONF_SCORE = 60  # 40–59: fill fields but force Review Required = True


@dataclass
class ProductPageCandidate:
    url: str
    title: str = ""
    description: str = ""
    html: str = ""
    query: str = ""
    search_rank: int = 0
    evidence: ProductEvidence = field(default_factory=ProductEvidence)
    rejected_candidates: list[dict] = field(default_factory=list)


def _str_val(v) -> str:
    """Safely convert a row cell value to a stripped string, handling None."""
    if v is None:
        return ""
    return str(v).strip()


def _qualifies(row: dict) -> bool:
    """True if this row should be sent through enrichment."""
    source = _str_val(row.get("Source Type", ""))
    if source == "URL":
        return False
    if source.endswith("_Enriched"):
        return False
    if not _str_val(row.get("Brand")):
        return False
    if not _str_val(row.get("Model/SKU")):
        return False
    # Qualify if any enrichable field is blank, OR if Dimensions exists but is
    # not full W×H×D (a partial dimension still needs enrichment).
    blank_or_incomplete = [
        f for f in _ENRICHABLE_FIELDS
        if not _str_val(row.get(f))
        or (f == "Dimensions" and not has_complete_3d_dimensions(_str_val(row.get(f))))
    ]
    return bool(blank_or_incomplete)


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


def build_search_queries(row: dict) -> list[str]:
    """Build precise product enrichment queries in priority order."""
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))
    product_name = _str_val(row.get("Product Name"))
    supplier = _str_val(row.get("Supplier"))
    category = _str_val(row.get("Product Category") or row.get("Category"))
    queries: list[str] = []

    def add(query: str) -> None:
        cleaned = " ".join(query.split()).strip()
        if cleaned and cleaned not in queries:
            queries.append(cleaned)

    if brand and model:
        add(f'"{brand}" "{model}"')
    if brand and product_name:
        add(f'"{brand}" "{product_name}"')
        add(f'"{brand}" "{product_name}" dimensions')
    if supplier and model:
        add(f'"{supplier}" "{model}"')
    if brand and product_name and category:
        add(f'"{brand}" "{product_name}" "{category}"')
    return queries


def _manufacturer_domains_for_search(row: dict) -> list[str]:
    brand = _str_val(row.get("Brand"))
    domains: list[str] = []
    direct = get_domain_for_brand(brand)
    if direct and direct[0] not in domains:
        domains.append(direct[0])
    for domain in registry_domains_for_brand(brand):
        if domain not in domains:
            domains.append(domain)
    return domains


def build_product_search_queries(row: dict, manufacturer_domain: str | None = None) -> list[str]:
    """Build page-verification search queries in strict priority order."""
    brand = _str_val(row.get("Brand"))
    sku = _str_val(row.get("Model/SKU") or row.get("SKU") or row.get("_extracted_model_sku"))
    product_name = _str_val(row.get("Product Name"))
    domain = _str_val(manufacturer_domain)
    if not domain:
        domains = _manufacturer_domains_for_search(row)
        domain = domains[0] if domains else ""

    queries: list[str] = []

    def add(query: str) -> None:
        cleaned = " ".join(query.split()).strip()
        if cleaned and cleaned not in queries:
            queries.append(cleaned)

    if domain and sku:
        add(f'site:{domain} "{sku}" specifications')
        add(f'site:{domain} "{sku}" product')
        add(f'site:{domain} "{sku}" images')
    if brand and sku:
        add(f'"{brand}" "{sku}" official product page')
    if brand and product_name and sku:
        add(f'"{brand}" "{product_name}" "{sku}"')
    if sku and product_name:
        add(f'"{sku}" "{product_name}"')
    return queries


def _candidate_debug_record(candidate: ProductPageCandidate, *, selected: bool = False) -> dict:
    evidence = candidate.evidence
    return {
        "url": candidate.url,
        "query": candidate.query,
        "title": candidate.title,
        "score": evidence.score,
        "confidence": evidence.confidence,
        "matched_sku": evidence.matched_sku,
        "matched_brand": evidence.matched_brand,
        "matched_product_name": evidence.matched_product_name,
        "official_domain": evidence.official_domain,
        "evidence_summary": evidence.evidence_summary,
        "rejection_reason": evidence.rejection_reason,
        "selected": selected,
    }


def _store_product_page_diagnostics(
    session_cache: "_SessionCache | None",
    row: dict,
    diagnostics: list[dict],
) -> None:
    if session_cache is None:
        return
    try:
        key = make_lookup_cache_key(row)
    except Exception:
        key = "|".join([
            _str_val(row.get("Brand")),
            _str_val(row.get("Model/SKU") or row.get("SKU")),
            _str_val(row.get("Product Name")),
        ])
    current = getattr(session_cache, "product_page_diagnostics", {})
    current[key] = diagnostics
    setattr(session_cache, "product_page_diagnostics", current)


def _search_results_for_query(
    query: str,
    brand: str,
    session_cache: "_SessionCache | None",
    budget: "_SearchBudget | None",
) -> list:
    cached = bool(session_cache is not None and query in session_cache.queries)
    if budget is not None and not cached:
        if not budget.can_search():
            return []
        budget.consume_search()
    return search_product_candidates(query, brand, session_cache=session_cache)


def _fetch_candidate_html(
    url: str,
    session_cache: "_SessionCache | None",
    budget: "_SearchBudget | None",
) -> str:
    if session_cache is not None and url in session_cache.urls:
        return session_cache.urls[url]
    if budget is not None:
        if not budget.can_fetch():
            return ""
        budget.consume_fetch()
    html = _fetch_page_html(url)
    if session_cache is not None:
        session_cache.urls[url] = html
    return html


def _find_best_product_page_with_diagnostics(
    row: dict,
    session_cache: "_SessionCache | None" = None,
    budget: "_SearchBudget | None" = None,
) -> tuple[ProductPageCandidate | None, list[dict], list[str], list[str]]:
    brand = _str_val(row.get("Brand"))
    domains = _manufacturer_domains_for_search(row)
    queries = build_product_search_queries(row, manufacturer_domain=domains[0] if domains else None)
    seen_urls: set[str] = set()
    all_candidates: list[ProductPageCandidate] = []
    diagnostics: list[dict] = []

    for query in queries:
        results = _search_results_for_query(query, brand, session_cache, budget)
        for rank, result in enumerate(results[:5], start=1):
            url = _str_val(getattr(result, "url", ""))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            html = _fetch_candidate_html(url, session_cache, budget)
            candidate = ProductPageCandidate(
                url=url,
                title=_str_val(getattr(result, "title", "")),
                description=_str_val(getattr(result, "description", "")),
                html=html,
                query=query,
                search_rank=rank,
            )
            if not html:
                candidate.evidence = ProductEvidence(
                    confidence="none",
                    domain=urllib.parse.urlparse(url).netloc.lower(),
                    evidence_summary="fetch_failed",
                    rejection_reason="fetch_failed",
                )
            else:
                candidate.evidence = score_product_page(
                    row,
                    url,
                    html,
                    title=candidate.title,
                    description=candidate.description,
                )
            all_candidates.append(candidate)
            diagnostics.append(_candidate_debug_record(candidate))

    eligible = [
        candidate
        for candidate in all_candidates
        if candidate.evidence.confidence in {"high", "medium"}
    ]
    if not eligible:
        _store_product_page_diagnostics(session_cache, row, diagnostics)
        return None, diagnostics, queries, domains

    rank = {"high": 2, "medium": 1}
    best = max(
        eligible,
        key=lambda candidate: (
            rank.get(candidate.evidence.confidence, 0),
            candidate.evidence.score,
            1 if candidate.evidence.official_domain else 0,
            -candidate.search_rank,
        ),
    )
    best.rejected_candidates = [
        _candidate_debug_record(candidate, selected=(candidate.url == best.url))
        for candidate in all_candidates
        if candidate.url != best.url
    ]
    diagnostics = [
        _candidate_debug_record(candidate, selected=(candidate.url == best.url))
        for candidate in all_candidates
    ]
    _store_product_page_diagnostics(session_cache, row, diagnostics)
    return best, diagnostics, queries, domains


def find_best_product_page(
    row: dict,
    session_cache: "_SessionCache | None" = None,
    budget: "_SearchBudget | None" = None,
) -> ProductPageCandidate | None:
    """Search, fetch, score, and return the best verified HIGH/MEDIUM page."""
    best, _diagnostics, _queries, _domains = _find_best_product_page_with_diagnostics(
        row,
        session_cache=session_cache,
        budget=budget,
    )
    return best


def has_enough_search_identity(row: dict) -> bool:
    return bool(
        (_str_val(row.get("Brand")) and (_str_val(row.get("Model/SKU")) or _str_val(row.get("Product Name"))))
        or (_str_val(row.get("Supplier")) and _str_val(row.get("Model/SKU")))
    )


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
                if has_complete_3d_dimensions(dim_extracted):
                    # Always accept complete 3D, even if row already had partial dims
                    updated["Dimensions"] = dim_extracted
                else:
                    # Partial found — note it, but do not fill
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


_VERIFIED_PAGE_FIELDS = (
    "Product Name",
    "Brand",
    "Model/SKU",
    "Dimensions",
    "Finish / Color",
    "Material",
    "Product Category",
)


def _needs_verified_page_extraction(row: dict) -> bool:
    for field in _VERIFIED_PAGE_FIELDS:
        value = _str_val(row.get(field))
        if not value:
            return True
        if field == "Dimensions" and not has_complete_3d_dimensions(value):
            return True
    return False


def _apply_verified_page_extraction(row: dict, extracted: dict, *, include_dimensions: bool = True) -> dict:
    """Fill blank fields from a verified page extraction without overwriting."""
    updated = row.copy()
    for field in _VERIFIED_PAGE_FIELDS:
        if field == "Dimensions" and not include_dimensions:
            continue
        if _str_val(updated.get(field)):
            continue
        value = _str_val(extracted.get(field))
        if not value and field == "Material":
            value = _str_val(extracted.get("materials"))
        if not value:
            continue
        if field == "Dimensions" and not has_complete_3d_dimensions(value):
            continue
        if field == "Product Category":
            value = _normalise_category(value) or value
        updated[field] = value
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


def extract_image_url(html: str) -> str | None:
    """
    Extract the best image URL from raw HTML using a multi-source fallback pipeline.

    Priority order:
      1. og:image meta tag (structured, authoritative)
      2. twitter:image meta tag (structured)
      3. JSON-LD Product "image" field (structured schema.org)
      4. Largest <img> by pixel area — checks src, srcset, data-src, data-original

    For structured sources (og/twitter/JSON-LD): accepts any absolute https URL;
    content-type validation happens later in enrich_row via _check_image_content_type.

    For <img> tags: applies extension pre-filter (_is_valid_image_url) and rejects
    tiny images (both dimensions < 100px) to skip icons and tracking pixels.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    if not html:
        return None

    # Priority 1: og:image (either attribute order)
    for pattern in (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    ):
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if _is_absolute_https(candidate):
                return candidate
            _log.info("[IMAGE INVALID — skipped] source=og:image url=%s (not absolute https)", candidate[:120])
            break

    # Priority 2: twitter:image (either attribute order)
    for pattern in (
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    ):
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if _is_absolute_https(candidate):
                return candidate
            _log.info("[IMAGE INVALID — skipped] source=twitter:image url=%s (not absolute https)", candidate[:120])
            break

    # Priority 3: JSON-LD Product "image" field
    for ld_m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.IGNORECASE | re.DOTALL,
    ):
        try:
            data = json.loads(ld_m.group(1))
            if isinstance(data, list):
                data = data[0] if data else {}
            candidate = data.get("image", "")
            if isinstance(candidate, list):
                candidate = candidate[0] if candidate else ""
            if isinstance(candidate, dict):
                candidate = candidate.get("url", "")
            candidate = str(candidate).strip()
            if candidate:
                if _is_absolute_https(candidate):
                    return candidate
                _log.info("[IMAGE INVALID — skipped] source=json-ld url=%s (not absolute https)", candidate[:120])
        except Exception:
            pass

    # Priority 4: largest <img> by pixel area.
    # Checks src, then srcset (last/largest descriptor), then data-src / data-original.
    best_url: str | None = None
    best_area = -1
    for img_m in re.finditer(r"<img([^>]+?)>", html, re.IGNORECASE):
        attrs = img_m.group(1)

        # Determine the candidate URL.
        # srcset wins when present (contains multiple resolutions; we take the highest).
        # Fall back to standard src, then lazy-load attributes.
        src: str | None = None
        srcset_m = re.search(r'\bsrcset=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        if srcset_m:
            # "url1 320w, url2 768w, url3 1200w" — last entry is highest-res
            parts = [p.strip().split() for p in srcset_m.group(1).split(",") if p.strip()]
            if parts and parts[-1]:
                src = parts[-1][0]
        if not src:
            for attr in ("src", "data-src", "data-original", "data-image"):
                src_m = re.search(rf'\b{attr}=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
                if src_m:
                    src = src_m.group(1).strip()
                    break

        if not src or not _is_valid_image_url(src):
            continue

        # Reject tiny images: icons, tracking pixels, sprites (< 100px on longest side)
        w_m = re.search(r'\bwidth=["\']?(\d+)', attrs, re.IGNORECASE)
        h_m = re.search(r'\bheight=["\']?(\d+)', attrs, re.IGNORECASE)
        w = int(w_m.group(1)) if w_m else None
        h = int(h_m.group(1)) if h_m else None
        if w is not None and h is not None and max(w, h) < 100:
            continue

        area = (w or 1) * (h or 1)
        if area > best_area:
            best_area = area
            best_url = src

    return best_url


def _try_image_from_url(product_url: str, cache_key: str = "") -> str | None:
    """Fetch product_url, extract the best image candidate, and validate via content-type.

    On success, updates the persistent cache so future cache hits carry the image.
    Returns the validated image URL, or None.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    raw_html = _fetch_page_html(product_url)
    if not raw_html:
        _log.info("[IMAGE PIPELINE] fetch failed url=%s", product_url[:80])
        return None

    candidate = extract_image_url(raw_html)
    if not candidate:
        _log.info("[IMAGE PIPELINE] no candidate found url=%s", product_url[:80])
        return None

    if not _check_image_content_type(candidate):
        _log.info("[IMAGE PIPELINE] content-type rejected candidate=%s", candidate[:80])
        if cache_key:
            _product_cache.update(cache_key, {"image_url": None})
        return None

    _log.info("[IMAGE PIPELINE] found url=%s img=%s", product_url[:60], candidate[:80])
    if cache_key:
        _product_cache.update(cache_key, {"image_url": candidate, "general_confidence": "medium"})
    return candidate


_CONF_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


def _is_manual_image(row: dict) -> bool:
    return _str_val(row.get("image_source")).lower() == "manual_upload"


def _budget_diagnostics(budget) -> dict:
    if budget is None:
        return {
            "API Budget Search Usage": "",
            "API Budget Fetch Usage": "",
            "API Budget AI Usage": "",
            "API Budget Stopped Reason": "",
        }
    if hasattr(budget, "diagnostics"):
        data = budget.diagnostics()
        return {
            "API Budget Search Usage": data.get("search_usage", ""),
            "API Budget Fetch Usage": data.get("fetch_usage", ""),
            "API Budget AI Usage": data.get("ai_usage", ""),
            "API Budget Stopped Reason": data.get("stopped_reason", ""),
        }
    return {
        "API Budget Search Usage": f"Used {getattr(budget, 'searches_used', 0)}/{getattr(budget, 'max_searches', 0)} search calls",
        "API Budget Fetch Usage": f"Used {getattr(budget, 'urls_used', 0)}/{getattr(budget, 'max_urls', 0)} page fetches",
        "API Budget AI Usage": "",
        "API Budget Stopped Reason": getattr(budget, "stopped_reason", ""),
    }


def _merge_budget_debug(debug: dict, budget) -> None:
    debug.update(_budget_diagnostics(budget))


def _apply_product_lookup_cache_entry(row: dict, cache_entry: dict, debug: dict) -> tuple[dict, _DimensionResult | None]:
    """Fill blank row fields from a HIGH/MEDIUM verified product lookup cache entry."""
    updated = row.copy()
    dim_result: _DimensionResult | None = None
    confidence = _str_val(cache_entry.get("confidence")).lower()
    product_url = _str_val(cache_entry.get("selected_product_url") or cache_entry.get("selected_product_page_url"))
    evidence = _str_val(cache_entry.get("evidence_summary") or cache_entry.get("evidence"))
    source_type = _str_val(cache_entry.get("source_type")) or "product_lookup_cache"

    debug.update({
        "product_lookup_cache_status": "hit",
        "AI Extraction Status": "Skipped AI: cached result",
        "API Budget Search Usage": "Used 0/0 search calls",
        "API Budget Fetch Usage": "Used 0/0 page fetches",
        "API Budget AI Usage": "Used 0/0 AI calls",
        "API Budget Stopped Reason": "Used cached result: no API cost",
        "selected_product_page_url": product_url,
        "selected_product_page_score": int(cache_entry.get("evidence_score") or 0),
        "selected_product_page_reason": evidence or "product_lookup_cache",
        "Product Resolution Confidence": confidence,
        "Product Resolution Evidence": evidence or "product_lookup_cache",
        "Product Resolution URL": product_url,
        "Search Diagnostics": "product_lookup_cache_hit",
        "web_lookup_error": "",
    })

    if product_url and not _str_val(updated.get("Product URL")):
        updated["Product URL"] = product_url

    if not _str_val(updated.get("Product Name")) and _str_val(cache_entry.get("product_name")):
        updated["Product Name"] = _str_val(cache_entry.get("product_name"))
    if not _str_val(updated.get("Finish / Color")) and _str_val(cache_entry.get("finish")):
        updated["Finish / Color"] = _str_val(cache_entry.get("finish"))
    if not _str_val(updated.get("Material")) and _str_val(cache_entry.get("material")):
        updated["Material"] = _str_val(cache_entry.get("material"))

    dimensions = _str_val(cache_entry.get("dimensions"))
    if dimensions and has_complete_3d_dimensions(dimensions) and not _str_val(updated.get("Dimensions")):
        updated["Dimensions"] = dimensions
        if _str_val(cache_entry.get("width_in")):
            updated["Width (in)"] = _str_val(cache_entry.get("width_in"))
        if _str_val(cache_entry.get("height_in")):
            updated["Height (in)"] = _str_val(cache_entry.get("height_in"))
        if _str_val(cache_entry.get("depth_in")):
            updated["Depth (in)"] = _str_val(cache_entry.get("depth_in"))
        updated["Dimension Source URL"] = product_url
        updated["Dimension Confidence"] = confidence
        updated["Dimension Source Type"] = source_type
        updated["Dimension Lookup Status"] = "found"
        updated["dimension_source_url"] = product_url
        updated["dimension_confidence"] = confidence
        updated["dimension_evidence"] = evidence
        debug.update({
            "dimensions_found": True,
            "dimension_source_url": product_url,
            "dimension_confidence": confidence,
            "dimension_evidence": evidence,
        })
        dim_result = _DimensionResult(
            dimensions=dimensions,
            width=_str_val(cache_entry.get("width_in")),
            height=_str_val(cache_entry.get("height_in")),
            depth=_str_val(cache_entry.get("depth_in")),
            source_url=product_url,
            confidence=confidence,
            source_type=source_type,
            status="found",
            evidence_text=evidence,
        )

    image_url = _str_val(cache_entry.get("image_url") or cache_entry.get("selected_image_url"))
    image_confidence = _str_val(cache_entry.get("image_confidence")).upper()
    if image_url and image_confidence in {"HIGH", "MEDIUM"}:
        debug.update({
            "selected_image_url": image_url,
            "selected_image_reason": evidence or "product_lookup_cache",
            "Image Recovery Confidence": image_confidence,
            "Image Recovery Source": "product_lookup_cache",
        })
        if not _is_manual_image(updated) and not row_has_image(updated):
            updated["Image URL"] = image_url
            updated["image_source"] = "product_lookup_cache"
            updated["confidence"] = image_confidence
            updated["evidence"] = evidence
            updated["needs_image_review"] = str(image_confidence != "HIGH" or confidence == "medium")

    if confidence == "medium":
        updated["Review Required"] = True
        updated["Suggested Action"] = "Cached medium-confidence product lookup — verify fields"

    updated["manufacturer_page_exact_sku"] = True
    return updated, dim_result


def _apply_official_product_lookup(
    row: dict,
    *,
    session_cache: "_SessionCache | None" = None,
    budget: "_SearchBudget | None" = None,
    force_refresh: bool = False,
) -> tuple[dict, _DimensionResult | None, dict]:
    """Incrementally fill product URL/image/specs from official brand registry lookup."""
    updated = row.copy()
    debug: dict = {
        "brand_registry_match": False,
        "brand_registry_domains_checked": [],
        "brand_search_queries_used": [],
        "candidate_pages_found": 0,
        "candidate_page_scores": [],
        "selected_product_page_url": "",
        "selected_product_page_score": 0,
        "selected_product_page_reason": "",
        "image_candidates_found": 0,
        "selected_image_url": "",
        "selected_image_reason": "",
        "dimensions_found": False,
        "dimension_source_url": "",
        "dimension_confidence": "",
        "dimension_evidence": "",
        "web_lookup_error": "",
        "Product Resolution Confidence": "",
        "Product Resolution Evidence": "",
        "Product Resolution URL": "",
        "Image Recovery Confidence": "",
        "Image Recovery Source": "",
        "Search Diagnostics": "",
        "product_lookup_cache_status": "miss",
        "AI Extraction Status": "",
        "API Budget Search Usage": "",
        "API Budget Fetch Usage": "",
        "API Budget AI Usage": "",
        "API Budget Stopped Reason": "",
    }
    def _stamp_debug_fields(target: dict) -> dict:
        for key, value in debug.items():
            if key in {
                "brand_registry_match",
                "brand_registry_domains_checked",
                "brand_search_queries_used",
                "candidate_pages_found",
                "candidate_page_scores",
                "selected_product_page_url",
                "selected_product_page_score",
                "selected_product_page_reason",
                "image_candidates_found",
                "selected_image_url",
                "selected_image_reason",
                "dimensions_found",
                "dimension_source_url",
                "dimension_confidence",
                "dimension_evidence",
                "web_lookup_error",
                "Product Resolution Confidence",
                "Product Resolution Evidence",
                "Product Resolution URL",
                "Image Recovery Confidence",
                "Image Recovery Source",
                "Search Diagnostics",
                "product_lookup_cache_status",
                "AI Extraction Status",
                "API Budget Search Usage",
                "API Budget Fetch Usage",
                "API Budget AI Usage",
                "API Budget Stopped Reason",
            }:
                target[key] = value
        return target

    dim_result: _DimensionResult | None = None
    key = make_lookup_cache_key(row)
    cached = _lookup_cache.get_for_row(row, force_refresh=force_refresh)

    page_url = ""
    selected_html = ""
    selected_evidence: ProductEvidence | None = None
    selected_resolution_candidate = None
    if cached:
        if can_reuse_lookup(cached):
            updated, dim_result = _apply_product_lookup_cache_entry(updated, cached, debug)
            return _stamp_debug_fields(updated), dim_result, debug
        if is_no_result(cached):
            debug.update({
                "product_lookup_cache_status": "searched_no_result",
                "AI Extraction Status": "Skipped AI: cached no-result",
                "API Budget Stopped Reason": "Used cached no-result: no API cost",
                "Product Resolution Confidence": _str_val(cached.get("confidence")) or "none",
                "Product Resolution Evidence": _str_val(cached.get("evidence_summary")) or "cached searched_no_result",
                "Search Diagnostics": "product_lookup_cache_searched_no_result",
                "web_lookup_error": "cached_searched_no_result",
            })
            _merge_budget_debug(debug, budget)
            if not debug.get("API Budget Stopped Reason"):
                debug["API Budget Stopped Reason"] = "Used cached no-result: no API cost"
            updated["manufacturer_page_exact_sku"] = False
            return _stamp_debug_fields(updated), None, debug
    elif force_refresh:
        debug["product_lookup_cache_status"] = "force_refresh"
    if not page_url:
        resolution = resolve_product_page(
            row,
            session_cache=session_cache,
            budget=budget,
        )
        page = resolution.selected
        domains = _manufacturer_domains_for_search(row)
        debug.update({
            "brand_registry_match": bool(domains),
            "brand_registry_domains_checked": domains,
            "brand_search_queries_used": resolution.queries_tried,
            "candidate_pages_found": len(resolution.diagnostics),
            "candidate_page_scores": resolution.diagnostics,
            "selected_product_page_url": page.url if page else "",
            "selected_product_page_score": page.evidence_score if page else 0,
            "selected_product_page_reason": (
                f"resolver_confidence:{page.confidence};score:{page.evidence_score}"
                if page else "no_verified_product_page"
            ),
            "web_lookup_error": "" if page else "no_verified_product_page",
            "Product Resolution Confidence": page.confidence if page else "none",
            "Product Resolution Evidence": (
                f"score={page.evidence_score};sku={page.matched_sku};brand={page.matched_brand};official={page.is_official_domain}"
                if page else "no_verified_product_page"
            ),
            "Product Resolution URL": page.url if page else "",
            "Search Diagnostics": resolution.diagnostics,
        })
        _merge_budget_debug(debug, budget)
        if page:
            page_url = page.url
            selected_html = page.html
            selected_resolution_candidate = page
            if page.is_official_domain:
                try:
                    record_verified_domain(
                        _str_val(row.get("Brand")),
                        page.domain,
                        confidence=page.confidence,
                        evidence_url=page.url,
                    )
                except Exception:
                    pass
            selected_evidence = ProductEvidence(
                confidence=page.confidence,
                score=page.evidence_score,
                matched_sku=page.matched_sku,
                matched_brand=page.matched_brand,
                matched_product_name=page.matched_product_name,
                official_domain=page.is_official_domain,
                domain=page.domain,
                evidence_summary=_str_val(debug.get("selected_product_page_reason")),
                rejection_reason=page.rejection_reason,
            )

    if not page_url:
        if not debug.get("AI Extraction Status"):
            debug["AI Extraction Status"] = "Skipped AI: no verified page"
        _merge_budget_debug(debug, budget)
        if not debug.get("API Budget Stopped Reason"):
            debug["API Budget Stopped Reason"] = debug.get("web_lookup_error") or "no_verified_product_page"
        _lookup_cache.record_no_result(
            row,
            confidence=_str_val(debug.get("Product Resolution Confidence")).lower() or "none",
            evidence_score=int(debug.get("selected_product_page_score") or 0),
            evidence_summary=_str_val(debug.get("Product Resolution Evidence")) or _str_val(debug.get("web_lookup_error")),
        )
        updated["manufacturer_page_exact_sku"] = False
        return _stamp_debug_fields(updated), None, debug

    if not _str_val(updated.get("Product URL")):
        updated["Product URL"] = page_url

    html = selected_html or _fetch_page_html(page_url)
    if not html:
        extracted_fields = getattr(selected_resolution_candidate, "extracted_fields", {}) or {}
        if not extracted_fields:
            debug["web_lookup_error"] = debug.get("web_lookup_error") or "selected_page_fetch_failed"
            _merge_budget_debug(debug, budget)
            return _stamp_debug_fields(updated), None, debug
        updated = _apply_verified_page_extraction(updated, {
            "Product Name": extracted_fields.get("Product Name", ""),
            "Brand": extracted_fields.get("Brand", ""),
            "Model/SKU": extracted_fields.get("Model/SKU", ""),
            "Dimensions": extracted_fields.get("Dimensions", ""),
            "Finish / Color": extracted_fields.get("Finish / Color", ""),
            "Material": extracted_fields.get("Material", ""),
            "Product Category": extracted_fields.get("Product Category", ""),
        })
        updated["manufacturer_page_exact_sku"] = bool(getattr(selected_resolution_candidate, "matched_sku", False))
        if extracted_fields.get("Image URL") and not row_has_image(updated) and extracted_fields.get("image_confidence") in {"HIGH", "MEDIUM"}:
            updated["Image URL"] = extracted_fields["Image URL"]
        _merge_budget_debug(debug, budget)
        return _stamp_debug_fields(updated), None, debug

    selected_reason = _str_val(debug.get("selected_product_page_reason"))
    sku_match = selected_evidence.matched_sku if selected_evidence else "sku_match" in selected_reason
    official = selected_evidence.official_domain if selected_evidence else any(
        token in selected_reason
        for token in ("brand_registry_domain", "official_brand_domain", "official_supplier_domain", "official_domain")
    )
    name_match = selected_evidence.matched_product_name if selected_evidence else "product_name_match" in selected_reason

    specs = extract_product_page_specs(
        html,
        page_url,
        updated,
        official_domain=official,
        sku_match=sku_match,
        product_name_match=name_match,
    )
    debug.update({
        "dimensions_found": bool(specs.dimensions),
        "dimension_source_url": specs.source_url,
        "dimension_confidence": specs.confidence,
        "dimension_evidence": specs.evidence,
    })

    row_sku = _str_val(updated.get("Model/SKU"))
    exact_sku_confirmation = bool(
        sku_match
        or (
            row_sku
            and _str_val(getattr(specs, "sku", ""))
            and sku_appears_in_text(row_sku, _str_val(getattr(specs, "sku", "")))
        )
    )
    page_confidence = selected_evidence.confidence if selected_evidence else ""
    # No caller currently opts into replacing non-empty PDF/user values. Keep
    # this explicit so a future overwrite path must prove HIGH confidence first.
    allow_verified_overwrite = False
    updated = _apply_manufacturer_page_fields(
        updated,
        specs,
        allow_overwrite=allow_verified_overwrite and page_confidence == "high",
    )
    resolver_fields = getattr(selected_resolution_candidate, "extracted_fields", {}) or {}
    if resolver_fields:
        updated = _apply_verified_page_extraction(updated, resolver_fields, include_dimensions=False)
    updated["manufacturer_page_exact_sku"] = exact_sku_confirmation

    existing_dims = _str_val(updated.get("Dimensions"))
    existing_dim_conf = _str_val(updated.get("Dimension Confidence")).lower() or "none"
    resolved_dimensions = specs.dimensions or _str_val(resolver_fields.get("Dimensions"))
    resolved_dim_confidence = specs.confidence or _str_val(resolver_fields.get("dimension_confidence")) or "none"
    if resolved_dimensions and resolved_dim_confidence in {"high", "medium"}:
        should_fill_dims = (
            not existing_dims
            or (
                allow_verified_overwrite
                and resolved_dim_confidence == "high"
                and _CONF_RANK.get(resolved_dim_confidence, 0) > _CONF_RANK.get(existing_dim_conf, 0)
            )
        )
        if should_fill_dims:
            updated["Dimensions"] = resolved_dimensions
            if specs.width or resolver_fields.get("width"):
                updated["Width (in)"] = specs.width or resolver_fields.get("width")
            if specs.height or resolver_fields.get("height"):
                updated["Height (in)"] = specs.height or resolver_fields.get("height")
            if specs.depth or resolver_fields.get("depth"):
                updated["Depth (in)"] = specs.depth or resolver_fields.get("depth")
            if specs.length:
                updated["Length (in)"] = specs.length
            if specs.diameter:
                updated["Diameter (in)"] = specs.diameter
            updated["Dimension Source URL"] = specs.source_url
            updated["Dimension Confidence"] = resolved_dim_confidence
            updated["Dimension Source Type"] = "official_product_page"
            updated["Dimension Lookup Status"] = "found"
            updated["dimension_source_url"] = specs.source_url
            updated["dimension_confidence"] = resolved_dim_confidence
            updated["dimension_evidence"] = specs.evidence or resolver_fields.get("dimension_evidence", "")
            updated["dimension_raw_text"] = specs.raw_text
            dim_result = _DimensionResult(
                dimensions=resolved_dimensions,
                width=specs.width or resolver_fields.get("width", ""),
                height=specs.height or resolver_fields.get("height", ""),
                depth=specs.depth or resolver_fields.get("depth", ""),
                length=specs.length,
                source_url=specs.source_url,
                confidence=resolved_dim_confidence,
                source_type="official_product_page",
                status="found",
                queries_tried=list(debug.get("brand_search_queries_used") or []),
                urls_checked=[page_url],
                evidence_text=specs.evidence or resolver_fields.get("dimension_evidence", "") or specs.raw_text,
            )

    for source, dest in (("finish", "Finish / Color"), ("material", "Material"), ("lead_time", "Lead Time"), ("weight", "Weight")):
        value = _str_val(getattr(specs, source))
        if value and not _str_val(updated.get(dest)):
            updated[dest] = value

    if _needs_verified_page_extraction(updated):
        if budget is not None and not getattr(budget, "can_ai_call", lambda: True)():
            debug["AI Extraction Status"] = f"Skipped AI: {getattr(budget, 'stopped_reason', '') or 'AI budget exhausted'}"
        else:
            if budget is not None and hasattr(budget, "consume_ai_call"):
                budget.consume_ai_call()
            debug["AI Extraction Status"] = "AI extraction used verified page text"
            extracted = _extract_with_claude(_html_to_text(html), updated)
            if extracted:
                updated = _apply_verified_page_extraction(updated, extracted)
    else:
        debug["AI Extraction Status"] = "Skipped AI: deterministic extraction complete"

    image = extract_product_page_image(
        html,
        page_url,
        updated,
        page_evidence=selected_evidence,
        source_prefix="official_site",
    )
    debug["image_candidates_found"] = image.debug.get("images_found", 0)
    if image.image_found:
        debug["selected_image_url"] = image.image_url
        debug["selected_image_reason"] = ";".join(image.evidence)
        debug["Image Recovery Confidence"] = image.confidence
        debug["Image Recovery Source"] = image.image_source
        if not _is_manual_image(updated) and not row_has_image(updated) and image.confidence in {"HIGH", "MEDIUM"}:
            updated["Image URL"] = image.image_url
            updated["image_source"] = image.image_source
            updated["confidence"] = image.confidence
            updated["evidence"] = ";".join(image.evidence)
            updated["needs_image_review"] = str(image.confidence != "HIGH")
    if (
        not image.image_found
        and
        resolver_fields.get("Image URL")
        and resolver_fields.get("image_confidence") in {"HIGH", "MEDIUM"}
        and not _is_manual_image(updated)
        and not row_has_image(updated)
    ):
        debug["selected_image_url"] = resolver_fields["Image URL"]
        debug["selected_image_reason"] = resolver_fields.get("image_evidence", "")
        debug["Image Recovery Confidence"] = resolver_fields.get("image_confidence", "")
        debug["Image Recovery Source"] = resolver_fields.get("image_source", "verified_product_page")
        updated["Image URL"] = resolver_fields["Image URL"]
        updated["image_source"] = resolver_fields.get("image_source", "verified_product_page")
        updated["confidence"] = resolver_fields.get("image_confidence", "")
        updated["evidence"] = resolver_fields.get("image_evidence", "")
        updated["needs_image_review"] = str(resolver_fields.get("image_confidence") != "HIGH")

    product_cache_key = ""
    brand_key = _str_val(updated.get("Brand"))
    model_key = _str_val(updated.get("Model/SKU"))
    if brand_key and model_key:
        product_cache_key = _normalize_key(brand_key, model_key)
    if product_cache_key:
        cache_fields: dict = {
            "product_url": page_url,
            "general_confidence": page_confidence if page_confidence in {"high", "medium"} else "medium",
        }
        if _str_val(updated.get("Finish / Color")):
            cache_fields["finish"] = _str_val(updated.get("Finish / Color"))
        existing_entry = _product_cache.get(product_cache_key) or {}
        if image.image_found and image.confidence in {"HIGH", "MEDIUM"} and not existing_entry.get("image_url"):
            cache_fields["image_url"] = image.image_url
        _product_cache.update(product_cache_key, cache_fields)

    image_conf_for_cache = _str_val(debug.get("Image Recovery Confidence")).upper()
    image_url_for_cache = (
        _str_val(debug.get("selected_image_url"))
        if image_conf_for_cache in {"HIGH", "MEDIUM"}
        else ""
    )
    dim_conf_for_cache = resolved_dim_confidence if resolved_dim_confidence in {"high", "medium"} else ""
    _lookup_cache.save_verified_lookup(
        updated,
        brand=_str_val(updated.get("Brand") or row.get("Brand")),
        sku=_str_val(updated.get("Model/SKU") or row.get("Model/SKU") or row.get("SKU")),
        product_name=_str_val(updated.get("Product Name") or row.get("Product Name")),
        selected_product_url=page_url,
        source_type=_str_val(getattr(selected_resolution_candidate, "source_type", "")) or "manufacturer_page",
        confidence=page_confidence if page_confidence in {"high", "medium"} else "medium",
        evidence_score=int(debug.get("selected_product_page_score") or 0),
        dimensions=resolved_dimensions if dim_conf_for_cache else "",
        width_in=specs.width or resolver_fields.get("width", ""),
        height_in=specs.height or resolver_fields.get("height", ""),
        depth_in=specs.depth or resolver_fields.get("depth", ""),
        finish=specs.finish or _str_val(updated.get("Finish / Color")),
        material=specs.material or _str_val(updated.get("Material")),
        image_url=image_url_for_cache,
        image_confidence=image_conf_for_cache if image_url_for_cache else "",
        evidence_summary=_str_val(debug.get("Product Resolution Evidence")) or _str_val(debug.get("selected_product_page_reason")),
        source_domain=urllib.parse.urlparse(page_url).netloc,
        **debug,
    )
    _merge_budget_debug(debug, budget)
    return _stamp_debug_fields(updated), dim_result, debug


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


def _should_write_manufacturer_value(updated: dict, field: str, allow_overwrite: bool = False) -> bool:
    existing = _str_val(updated.get(field))
    if not existing:
        return True
    return bool(allow_overwrite)


def _apply_manufacturer_page_fields(
    updated: dict,
    specs,
    *,
    allow_overwrite: bool = False,
) -> dict:
    """Fill Programa-required fields from a confirmed manufacturer page.

    Blank fields are safe to fill from a verified product page. Existing PDF or
    user-entered values are preserved unless a caller explicitly enables
    overwrite after its own high-confidence check.
    """
    field_map = (
        ("brand", "Brand"),
        ("product_name", "Product Name"),
        ("sku", "Model/SKU"),
        ("category", "Product Category"),
        ("color", "Color"),
        ("material", "Material"),
    )
    for source_attr, dest in field_map:
        value = _str_val(getattr(specs, source_attr, ""))
        if not value:
            continue
        if dest == "Product Category":
            value = _normalise_category(value) or value
        if _should_write_manufacturer_value(updated, dest, allow_overwrite):
            updated[dest] = value

    # Finish can come from either explicit finish or color. Keep the combined
    # SCH-facing field filled even when Color is also available separately.
    finish_value = _str_val(getattr(specs, "finish", "")) or _str_val(getattr(specs, "color", ""))
    if finish_value and _should_write_manufacturer_value(updated, "Finish / Color", allow_overwrite):
        updated["Finish / Color"] = finish_value

    description = _str_val(getattr(specs, "description", ""))
    if description:
        if _should_write_manufacturer_value(updated, "Description", allow_overwrite):
            updated["Description"] = description
        existing_notes = _str_val(updated.get("Notes"))
        note = f"[Manufacturer Description: {description}]"
        if note not in existing_notes:
            updated["Notes"] = f"{existing_notes} {note}".strip() if existing_notes else note

    qty = _str_val(updated.get("Quantity"))
    if qty in {"", "0"}:
        updated["Quantity"] = 1
    return updated


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
            updated, _debug = normalize_dimension_fields(row.copy())
            return updated, None, None

        mode = _normalize_mode(enrichment_mode)
        budget = _budget_for_mode(mode)
        brand = _str_val(row.get("Brand"))
        model_sku = _str_val(row.get("Model/SKU"))
        cache_key = _normalize_key(brand, model_sku) if brand and model_sku else ""
        force_refresh = session_cache.force_refresh if session_cache else False

        # ── Cache check ────────────────────────────────────────────────────────
        cache_fields_filled: list[str] = []
        fields_searched: list[str] = []
        product_cache_hit = "miss"

        if cache_key:
            cache_entry = _product_cache.get(cache_key)
            if cache_entry is not None:
                row, cache_fields_filled, still_missing = _apply_cache_to_row(
                    row, cache_entry, force_refresh
                )
                if not still_missing:
                    _log.info("[CACHE HIT: full] key=%s", cache_key)
                    product_cache_hit = "full"
                    updated = row.copy()
                    original = _str_val(updated.get("Source Type", ""))
                    if not original.endswith("_Enriched"):
                        updated["Source Type"] = f"{original}_Enriched" if original else "Enriched"
                    # Image is not an essential cache field; try recovering it when
                    # the cache explicitly has null (tried before, but now extraction
                    # logic is improved / CDN URLs were previously rejected by extension filter).
                    if not row_has_image(updated) and "image_url" in cache_entry and cache_entry["image_url"] is None:
                        product_url = _str_val(updated.get("Product URL"))
                        if product_url:
                            img = _try_image_from_url(product_url, cache_key)
                            if img:
                                updated["Image URL"] = img
                    return updated, None, None
                else:
                    product_cache_hit = "partial"
                    fields_searched.extend(still_missing)
                    _log.info("[CACHE HIT: partial] key=%s still_missing=%s", cache_key, still_missing)
            else:
                fields_searched.extend(_ESSENTIAL_CACHE_FIELDS)
                _log.info("[CACHE MISS] key=%s", cache_key)

        if not has_enough_search_identity(row):
            updated = row.copy()
            existing = _str_val(updated.get("Notes"))
            note = "[Enrichment: skipped web search; not enough identifying info]"
            if note not in existing:
                updated["Notes"] = f"{existing} {note}".strip() if existing else note
            updated, _debug = normalize_dimension_fields(updated)
            return updated, None, None

        updated, registry_dim_result, registry_debug = _apply_official_product_lookup(
            row,
            session_cache=session_cache,
            budget=budget,
            force_refresh=force_refresh,
        )
        if registry_debug.get("selected_product_page_url"):
            existing = _str_val(updated.get("Notes"))
            note = f"[Official lookup: {registry_debug.get('selected_product_page_reason', 'matched product page')}]"
            if note not in existing:
                updated["Notes"] = f"{existing} {note}".strip() if existing else note
        if registry_dim_result is not None and has_complete_3d_dimensions(_str_val(updated.get("Dimensions"))):
            updated, _debug = normalize_dimension_fields(updated)
            return updated, None, registry_dim_result

        if not registry_debug.get("selected_product_page_url"):
            existing = _str_val(updated.get("Notes"))
            note = "[Enrichment: no confident source found]"
            if note not in existing:
                updated["Notes"] = f"{existing} {note}".strip() if existing else note
            if registry_debug.get("product_lookup_cache_status") == "searched_no_result":
                updated, _debug = normalize_dimension_fields(updated)
                return updated, None, None

        # ── Dimension enrichment pass ──────────────────────────────────────────
        brand_val = _str_val(updated.get("Brand"))
        model_val = _str_val(updated.get("Model/SKU"))
        dims_val = _str_val(updated.get("Dimensions"))
        dim_result: _DimensionResult | None = None
        if brand_val and model_val and not has_complete_3d_dimensions(dims_val):
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

        updated, _debug = normalize_dimension_fields(updated)
        return updated, None, dim_result
    except Exception as exc:
        return row, str(exc), None


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

    from src.enrichment_cache import SessionCache as _SC
    _session = _SC(force_refresh=force_refresh)

    for idx, row in df.iterrows():
        r = row.to_dict()
        if not _qualifies(r):
            continue

        try:
            updated, error, dim_result = enrich_row(
                r,
                enrichment_mode=enrichment_mode,
                session_cache=_session,
            )
            if error:
                errors.append(error)
            else:
                # Ensure enrichment output columns survive even when an upstream
                # DataFrame was built without the full intake schema.
                for col, val in updated.items():
                    if col not in df.columns:
                        df[col] = pd.Series([None] * len(df), index=df.index, dtype=object)
                    elif str(df[col].dtype) == "string" and not isinstance(val, str):
                        df[col] = df[col].astype(object)
                    df.at[idx, col] = val

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
                    })
        except Exception as exc:
            label = _str_val(r.get("Product Name")) or _str_val(r.get("Brand")) or _str_val(r.get("Model/SKU")) or str(idx)
            errors.append(f"Row '{label}': {exc}")

        time.sleep(0.5)

    return df, errors, dimension_diagnostics


def recover_images_for_dataframe(
    df,
    pdf_lookup: dict[str, str] | None = None,
    session_id: str | None = None,
    enable_screenshot: bool = True,
):
    """
    Backward-compatible alias for src.image_recovery.recover_images_for_dataframe.

    Existing callers that pass only `df` continue to work — PDF crop is
    skipped (no pdf_lookup) and screenshot defaults to True. Real production
    callers in app.py and backend/main.py pass pdf_lookup + session_id.
    """
    from src.image_recovery import recover_images_for_dataframe as _impl
    return _impl(
        df,
        pdf_lookup=pdf_lookup,
        session_id=session_id,
        enable_screenshot=enable_screenshot,
    )
