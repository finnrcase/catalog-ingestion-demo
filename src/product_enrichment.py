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

import httpx
import pandas as pd
from dotenv import load_dotenv

from src.brave_search import BRAVE_API_KEY, search_product_candidates
from src.category_ai import _normalise_category
from src.dimension_enrichment import DimensionResult as _DimensionResult, find_dimensions as _find_dimensions
from src.dimensions import has_complete_3d_dimensions
from src.manufacturer_domains import get_domain_for_brand, record_discovered_domain

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

# Cache field mappings: cache field name → row column name
_CACHE_GENERAL_FIELDS: dict[str, str] = {
    "product_url": "Product URL",
    "finish": "Finish / Color",
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


def _fetch_page_text(url: str) -> str:
    """Fetch URL with httpx and return plain text (max 6 000 chars). Empty string on error."""
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SCH-Intake/1.0)"},
            timeout=10,
            follow_redirects=True,
        )
        resp.raise_for_status()

        if _html2text is not None:
            h = _html2text.HTML2Text()
            h.ignore_links = True
            h.ignore_images = True
            text = h.handle(resp.text)
        else:
            text = re.sub(r"<[^>]+>", " ", resp.text)
            text = re.sub(r"\s{2,}", " ", text)

        return text[:6000].strip()
    except Exception:
        return ""


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


def enrich_row(
    row: dict,
    enrichment_mode: str = "standard",
    session_cache: "_SessionCache | None" = None,
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
                    return updated, None, None
                else:
                    product_cache_hit = "partial"
                    fields_searched.extend(still_missing)
                    _log.info("[CACHE HIT: partial] key=%s still_missing=%s", cache_key, still_missing)
            else:
                fields_searched.extend(_ESSENTIAL_CACHE_FIELDS)
                _log.info("[CACHE MISS] key=%s", cache_key)

        # Fast mode: if manufacturer domain is known, skip general Brave search
        # (dimension lookup will handle targeted search via the known domain)
        skip_general_search = (mode == "fast")

        query = _build_search_query(row)
        domain_match = get_domain_for_brand(brand)
        results = []
        if not skip_general_search and budget.can_search():
            _log.info("[LIVE SEARCH] query=%s", query[:80])
            results = search_product_candidates(query, brand, session_cache=session_cache)
            budget.consume_search()
        elif not budget.can_search():
            _log.info("[BUDGET EXHAUSTED] skipping general search for key=%s", cache_key)

        if not results or results[0].domain_score < MIN_USE_SCORE:
            updated = row.copy()
            existing = _str_val(updated.get("Notes"))
            note = "[Enrichment: no confident source found]"
            if note not in existing:
                updated["Notes"] = f"{existing} {note}".strip() if existing else note
        else:
            best = results[0]
            parsed_domain = urllib.parse.urlparse(best.url).netloc
            if parsed_domain and not domain_match:
                record_discovered_domain(brand, parsed_domain)
            page_text = _fetch_page_text(best.url)

            if not page_text:
                updated = row.copy()
                existing = _str_val(updated.get("Notes"))
                domain = urllib.parse.urlparse(best.url).netloc or best.url[:50]
                note = f"[Enrichment: could not fetch {domain}]"
                if note not in existing:
                    updated["Notes"] = f"{existing} {note}".strip() if existing else note
            else:
                extracted = _extract_with_claude(page_text, row)
                updated = _apply_enrichment(row, extracted, best.url, best.domain_score)
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

        return updated, None, dim_result
    except Exception as exc:
        return row, str(exc), None


def enrich_dataframe(
    df: pd.DataFrame,
    enrichment_mode: str = "standard",
    force_refresh: bool = False,
) -> tuple[pd.DataFrame, list[str], list[dict]]:
    """
    Enrich all qualifying rows in df. Returns (updated_df, error_list, dimension_diagnostics).
    Exceptions in individual rows are caught and logged; the row is left unchanged.
    """
    df = df.copy()
    errors: list[str] = []
    dimension_diagnostics: list[dict] = []

    from src.enrichment_cache import SessionCache as _SC
    _session = _SC(force_refresh=force_refresh)

    for idx, row in df.iterrows():
        r = row.to_dict()
        if not _qualifies(r):
            continue

        try:
            updated, error, dim_result = enrich_row(r, enrichment_mode=enrichment_mode, session_cache=_session)
            if error:
                errors.append(error)
            else:
                # Only write back columns that already exist in the DataFrame.
                # The intake schema guarantees all expected columns are present;
                # this guard prevents accidental column creation mid-iteration.
                for col, val in updated.items():
                    if col in df.columns:
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
