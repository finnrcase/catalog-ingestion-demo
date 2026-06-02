"""
Dimension lookup pipeline for SCH DesignOps Intake.

Searches manufacturer and retailer sources for product W/H/D dimensions.
Called by product_enrichment.enrich_row() after the regular enrichment pass.

Public API
----------
find_dimensions(row: dict) -> DimensionResult
    Perform full dimension lookup for a single intake row.

BRAND_DOMAIN_TABLE : dict[str, str]
    Known brand -> official domain mappings. Add new entries here.
"""

from __future__ import annotations

import json as _json
import logging as _logging
import re
import urllib.parse as _urlparse
from dataclasses import dataclass, field

try:
    from bs4 import BeautifulSoup as _BeautifulSoup
except ImportError:
    _BeautifulSoup = None

try:
    import httpx as _httpx
except ImportError:
    _httpx = None

try:
    from src.brave_search import search_product_candidates as _brave_candidates
except ImportError:
    _brave_candidates = None

from src.dimensions import dimension_sanity_reason
from src.enrichment_cache import ManufacturerDomainCache, SessionCache, SearchBudget

_log = _logging.getLogger(__name__)


def _brave_search_urls(
    query: str,
    limit: int = 5,
    brand: str = "",
    session_cache: "SessionCache | None" = None,
    budget: "SearchBudget | None" = None,
) -> list[str]:
    """Call Brave Search and return up to `limit` result URLs.
    Checks session cache first (no budget consumed). Skips call if budget exhausted."""
    if _brave_candidates is None:
        return []
    # Session cache hit — free, no budget consumed
    if session_cache is not None and query in session_cache.queries:
        return [r.url for r in session_cache.queries[query][:limit]]
    # Budget check before real API call
    if budget is not None and not budget.can_search():
        return []
    try:
        results = _brave_candidates(query, brand, session_cache=session_cache)
        if budget is not None:
            budget.consume_search()
        return [r.url for r in results[:limit]]
    except Exception:
        return []


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class DimensionResult:
    # Persisted to intake row
    dimensions: str = ""
    width: str = ""
    height: str = ""
    depth: str = ""
    length: str = ""
    source_url: str = ""
    confidence: str = "none"      # "high" | "medium" | "low" | "none"
    source_type: str = "none"     # "manufacturer_page" | "manufacturer_pdf"
                                  # | "retailer_page" | "retailer_pdf" | "none"
    status: str = "not_found"     # "found" | "not_found" | "low_confidence_skipped"
    # Diagnostics — API response only, not persisted
    queries_tried: list[str] = field(default_factory=list)
    urls_checked: list[str] = field(default_factory=list)
    evidence_text: str = ""
    failure_reason: str = ""
    debug: dict = field(default_factory=dict)


# ── Constants ──────────────────────────────────────────────────────────────────

BRAND_DOMAIN_TABLE: dict[str, str] = {
    "scotsman": "scotsman-ice.com",
    "kohler": "kohler.com",
    "kallista": "kallista.com",
    "miele": "mieleusa.com",
    "wolf": "subzero-wolf.com",
    "sub-zero": "subzero-wolf.com",
    "thermador": "thermador.com",
    "dacor": "dacor.com",
    "samsung": "samsung.com",
    "ge": "geappliances.com",
    "ge appliances": "geappliances.com",
    "bosch": "bosch-home.com",
    "fisher & paykel": "fisherpaykel.com",
    "frigidaire": "frigidaire.com",
    "lg": "lg.com",
    "whirlpool": "whirlpool.com",
    "kitchenaid": "kitchenaid.com",
    "viking": "vikingrange.com",
}

RETAILER_DOMAINS: list[str] = [
    "build.com",
    "ajmadison.com",
    "bestbuy.com",
    "homedepot.com",
    "lowes.com",
    "wayfair.com",
    "ferguson.com",
    "appliancesconnection.com",
]

_APPLIANCE_CATEGORIES: frozenset[str] = frozenset({
    "Appliances", "Appliance", "Kitchen Appliances",
    "Kitchen Appliance", "Built-in Appliances",
})

# Best-effort manufacturer domain cache (lazy-loads from runtime storage).
_mfr_cache = ManufacturerDomainCache()

_SPEC_LABEL_KEYWORDS = frozenset({
    "width", "height", "depth", "dimensions", "overall dimensions",
    "product dimensions", "w×h×d", "w x h x d",
})
_SPEC_PDF_KEYWORDS = re.compile(
    r"spec|specification|dimension|install|installation|manual|guide|product[-_ ]?sheet|submittal|technical",
    re.IGNORECASE,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_not_found_result(
    queries_tried: list[str] | None = None,
    urls_checked: list[str] | None = None,
    failure_reason: str = "",
) -> DimensionResult:
    return DimensionResult(
        status="not_found",
        confidence="none",
        source_type="none",
        queries_tried=queries_tried or [],
        urls_checked=urls_checked or [],
        failure_reason=failure_reason,
    )


def _normalize_model_variants(model: str) -> list[str]:
    """Return up to 4 model variants to try in order: exact, no-spaces, dashes, suffix-stripped."""
    # Strip whitespace and non-printable characters
    model = "".join(c for c in model.strip() if c.isprintable())
    if not model:
        return []
    seen: list[str] = [model]

    no_spaces = re.sub(r"\s+", "", model)
    if no_spaces not in seen:
        seen.append(no_spaces)

    with_dashes = re.sub(r"\s+", "-", model)
    if with_dashes not in seen:
        seen.append(with_dashes)

    # Suffix strip: last dash/space token of 1–3 chars
    tokens = re.split(r"[-\s]+", model)
    if len(tokens) > 1 and 1 <= len(tokens[-1]) <= 3:
        without_suffix = model[: -len(tokens[-1])].rstrip(" -")
        if without_suffix and without_suffix not in seen:
            seen.append(without_suffix)
    elif len(tokens) == 1:
        # No delimiters: strip a trailing 1–3 alpha color-code suffix only when
        # the string has the simple form  <alpha><digits><alpha 1-3>
        # (e.g. "HV48SS" → "HV48").  More complex models like "SCN60PA1SU"
        # (alpha–digit–alpha–digit–alpha) are left untouched.
        m = re.match(r"^([A-Za-z]+\d+)([A-Za-z]{1,3})$", model)
        if m:
            without_suffix = m.group(1)
            if without_suffix and without_suffix not in seen:
                seen.append(without_suffix)

    return seen


def _get_manufacturer_domain(
    brand: str,
    *,
    _search_fn=None,
) -> str | None:
    """
    Return official manufacturer domain for a brand, or None.
    Checks BRAND_DOMAIN_TABLE first, then persistent ManufacturerDomainCache,
    then optionally runs a discovery search via _search_fn(query) -> list[str].
    """
    brand_stripped = brand.strip()
    key = brand_stripped.lower()
    if not key:
        return None
    # 1. Hardcoded table (fastest, authoritative)
    if key in BRAND_DOMAIN_TABLE:
        return BRAND_DOMAIN_TABLE[key]
    # 2. Persistent discovered cache
    cached = _mfr_cache.get(key)
    if cached:
        return cached["domain"]
    # 3. Live discovery search
    if _search_fn is None:
        return None
    try:
        urls = _search_fn(f'"{brand_stripped}" official website product specifications')
        if not urls:
            return None
        netloc = _urlparse.urlparse(urls[0]).netloc.lower()
        domain = netloc[4:] if netloc.startswith("www.") else netloc
        if domain:
            _mfr_cache.set(key, domain, source="discovered")
            return domain
    except Exception:
        pass
    return None


def _generate_queries(
    brand: str,
    model: str,
    domain: str | None,
    product_name: str = "",
    sku: str = "",
) -> list[str]:
    """
    Return search queries in priority order: manufacturer site-targeted (phase 1),
    general brand (phase 2), final fallbacks (phase 4).
    Retailer queries (phase 3) are generated separately by _generate_retailer_queries.
    Bounded to <= 9 queries (deduplication may reduce further).
    """
    queries: list[str] = []
    seen: set[str] = set()

    def _add(q: str) -> None:
        if q not in seen:
            seen.add(q)
            queries.append(q)

    # Phase 1 — manufacturer site-targeted
    if domain:
        _add(f'site:{domain} "{model}" dimensions')
        _add(f'site:{domain} "{model}" specifications')
        _add(f'site:{domain} "{model}" spec sheet')
        _add(f'site:{domain} "{model}" installation guide')

    # Phase 2 — general brand queries
    _add(f'"{brand}" "{model}" "dimensions"')
    _add(f'"{brand}" "{model}" "specifications"')

    # Phase 4 — final fallbacks
    if product_name:
        _add(f'"{brand}" "{product_name}" dimensions')
    if sku:
        _add(f'"{sku}" dimensions specifications')
    _add(f'"{brand}" "{model}" dimensions')

    return queries


def _generate_retailer_queries(brand: str, model: str) -> list[str]:
    """Return one site: query per trusted retailer domain (phase 3)."""
    return [
        f'site:{domain} "{brand}" "{model}" dimensions'
        for domain in RETAILER_DOMAINS
    ]


# ── Dimension text patterns ────────────────────────────────────────────────────

_NUM = r"\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?"
_PRODUCT_DIM_LABELS = re.compile(
    r"(?:product|overall)\s+dimensions?\s*[:\-]\s*([^\n]{5,100})",
    re.IGNORECASE,
)
_CUTOUT_DIM_LABEL = re.compile(
    r"cutout\s+dimensions?\s*[:\-]\s*([^\n]{5,100})",
    re.IGNORECASE,
)
_SHIPPING_DIM_LABEL = re.compile(
    r"shipping\s+(?:dimensions?|size)\s*[:\-]\s*([^\n]{5,100})",
    re.IGNORECASE,
)
# Bare "Dimensions:" — matched after the priority labels above exclude their ranges
_DIM_LABEL = re.compile(
    r"\b(?:dimensions?|size|measurements?|appliance\s+dimensions?)\s*[:\-]\s*([^\n]{5,120})",
    re.IGNORECASE,
)
# Inline W×H×D — supports fractions, decimals, integers; × or x or X or space
_INLINE_DIM = re.compile(
    r"[\d][\d ./]*\"?\s*[WwHhDd]\b[\s×xX]+[\d][\d ./]*\"?\s*[WwHhDd]\b[\s×xX]+[\d][\d ./]*\"?\s*[WwHhDd]\b",
)
_UNIT = r"(?:[\"“”″']|\bin(?:ches)?\b|\bcm\b|\bmm\b)"
_UNLABELED_TRIPLE = re.compile(
    rf"(?<![\w/])(?P<a>{_NUM})\s*(?P<ua>{_UNIT})?\s*[×xX]\s*"
    rf"(?P<b>{_NUM})\s*(?P<ub>{_UNIT})?\s*[×xX]\s*"
    rf"(?P<c>{_NUM})\s*(?P<uc>{_UNIT})?(?![\w/])",
    re.IGNORECASE,
)
_DIMENSION_CONTEXT = re.compile(
    r"\b(dimensions?|size|measurements?|measures|overall|product|appliance|"
    r"width|height|depth|specifications?|w\s*[×x]\s*d\s*[×x]\s*h|"
    r"w\s*[×x]\s*h\s*[×x]\s*d)\b",
    re.IGNORECASE,
)
_REJECTED_DIMENSION_CONTEXT = re.compile(
    r"\b(shipping|package|packaged|carton|box|cutout|rough[- ]?in|opening)\b",
    re.IGNORECASE,
)


def _fraction_to_decimal(s: str) -> str:
    """Convert fraction strings like '14 7/8' → '14.875'. Returns input unchanged if not parseable."""
    s = s.strip()
    if not s:
        return s
    # Mixed number: "14 7/8"
    m = re.match(r"^(\d+)\s+(\d+)/(\d+)$", s)
    if m:
        whole, num, den = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if den == 0:
            return s
        val = whole + num / den
        # Format: strip trailing zeros, at most 6 decimal places
        result = f"{val:.6f}".rstrip("0").rstrip(".")
        return result
    # Simple fraction: "3/4"
    m = re.match(r"^(\d+)/(\d+)$", s)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        if den == 0:
            return s
        val = num / den
        result = f"{val:.6f}".rstrip("0").rstrip(".")
        return result
    # Already a decimal or integer
    try:
        float(s)
        # Return as-is if it's already a clean decimal string
        return s
    except ValueError:
        return s


def _number_to_float(s: str) -> float | None:
    text = _fraction_to_decimal(str(s or "").strip())
    try:
        return float(text)
    except ValueError:
        return None


def _format_decimal(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _normalize_unit(unit: str | None) -> str:
    unit = str(unit or "").strip().lower()
    if unit in {"cm", "mm"}:
        return unit
    if unit in {'"', "“", "”", "″", "'", "in", "inch", "inches"}:
        return "in"
    return ""


def _value_to_inches_text(value: str, unit: str | None = None) -> str:
    """Return a compact inch value string, converting cm/mm when explicit."""
    unit_norm = _normalize_unit(unit)
    if not unit_norm:
        unit_m = re.search(rf"(?P<num>{_NUM})\s*(?P<unit>{_UNIT})", str(value or ""), re.IGNORECASE)
        if unit_m:
            unit_norm = _normalize_unit(unit_m.group("unit"))
            value = unit_m.group("num")
    num = _number_to_float(value)
    if num is None:
        return str(value or "").strip()
    if unit_norm == "cm":
        return _format_decimal(num / 2.54)
    if unit_norm == "mm":
        return _format_decimal(num / 25.4)
    return str(value).strip()


def _format_labeled_value(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    unit_m = re.search(rf"(?P<num>{_NUM})\s*(?P<unit>{_UNIT})?", text, re.IGNORECASE)
    if not unit_m:
        return f"{text} {label}"
    number = _value_to_inches_text(unit_m.group("num"), unit_m.group("unit"))
    return f'{number}"{label}'


def _format_unlabeled_triple(match: re.Match) -> str:
    """Interpret verified-source unlabeled triples as W x D x H."""
    unit = match.group("ua") or match.group("ub") or match.group("uc") or "in"
    w = _value_to_inches_text(match.group("a"), unit)
    d = _value_to_inches_text(match.group("b"), unit)
    h = _value_to_inches_text(match.group("c"), unit)
    return f'{w}"W x {d}"D x {h}"H'


def _spec_label_key(label: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(label or "").lower()).strip()
    if not text:
        return ""
    if any(word in text for word in ("shipping", "package", "packaged", "carton", "box")):
        return "shipping"
    if any(word in text for word in ("cutout", "rough in", "opening")):
        return "cutout"
    if "width" in text or text in {"w"} or text.endswith(" w"):
        return "width"
    if "height" in text or text in {"h"} or text.endswith(" h"):
        return "height"
    if "depth" in text or text in {"d"} or text.endswith(" d"):
        return "depth"
    if any(word in text for word in ("dimension", "measurement", "measurements", "size")):
        return "dimensions"
    if "w x d x h" in text or "w x h x d" in text:
        return "dimensions"
    return ""


def _find_dimension_candidates(
    text: str,
    *,
    include_cutout: bool = False,
    include_shipping: bool = False,
) -> list[str]:
    """
    Return candidate dimension strings from plain text, in priority order.
    Shipping excluded by default; cutout excluded unless include_cutout=True.
    Shipping only included when include_shipping=True AND no other candidates found.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(s: str) -> None:
        s = s.strip().rstrip(".,;:")
        if s and s not in seen:
            seen.add(s)
            candidates.append(s)

    # Priority 1: "Product Dimensions" / "Overall Dimensions"
    for m in _PRODUCT_DIM_LABELS.finditer(text):
        _add(m.group(1))

    # Priority 2: bare "Dimensions:" — exclude matches already captured by
    # product/overall/cutout/shipping labels (use span containment, not proximity)
    excluded_ranges = [
        (m.start(), m.end())
        for pat in (_PRODUCT_DIM_LABELS, _CUTOUT_DIM_LABEL, _SHIPPING_DIM_LABEL)
        for m in pat.finditer(text)
    ]
    for m in _DIM_LABEL.finditer(text):
        if any(r_start <= m.start() < r_end for r_start, r_end in excluded_ranges):
            continue
        _add(m.group(1))

    # Priority 3: inline W×H×D pattern — skip if inside a cutout/shipping label span
    cutout_ranges = [(m.start(), m.end()) for m in _CUTOUT_DIM_LABEL.finditer(text)]
    shipping_ranges = [(m.start(), m.end()) for m in _SHIPPING_DIM_LABEL.finditer(text)]
    excluded_ranges = cutout_ranges + shipping_ranges
    for m in _INLINE_DIM.finditer(text):
        in_excluded = any(r_start <= m.start() < r_end for r_start, r_end in excluded_ranges)
        if not in_excluded:
            _add(m.group(0))

    # Priority 3b: verified-source unlabeled triples near dimension context.
    # These remain local to enrichment; has_complete_3d_dimensions stays strict.
    for m in _UNLABELED_TRIPLE.finditer(text):
        in_excluded = any(r_start <= m.start() < r_end for r_start, r_end in excluded_ranges)
        if in_excluded:
            continue
        context = text[max(0, m.start() - 70): min(len(text), m.end() + 30)]
        if not _DIMENSION_CONTEXT.search(context):
            continue
        if _REJECTED_DIMENSION_CONTEXT.search(context):
            continue
        _add(_format_unlabeled_triple(m))

    # Priority 4: cutout (optional)
    if include_cutout:
        for m in _CUTOUT_DIM_LABEL.finditer(text):
            _add(m.group(1))

    # Priority 5: shipping — only when flag set AND no higher-priority candidates found
    if include_shipping and not candidates:
        for m in _SHIPPING_DIM_LABEL.finditer(text):
            _add(m.group(1))

    return candidates


def _dimension_parts(dimensions: str) -> dict[str, str]:
    from src.dimensions import extract_labeled_dimensions

    text = str(dimensions or "")
    parts = extract_labeled_dimensions(text)

    before_label = re.compile(
        rf"\b(?P<label>width|wide|w|height|high|h|depth|deep|d|length|long|l)\b"
        rf"\s*(?:[:=]|is|of)?\s*(?P<num>{_NUM})\s*(?P<unit>{_UNIT})?",
        re.IGNORECASE,
    )
    after_label = re.compile(
        rf"(?P<num>{_NUM})\s*(?P<unit>{_UNIT})?\s*(?P<label>[WwHhDdLl])(?![a-zA-Z])",
        re.IGNORECASE,
    )
    key_map = {
        "w": "width", "wide": "width", "width": "width",
        "h": "height", "high": "height", "height": "height",
        "d": "depth", "deep": "depth", "depth": "depth",
        "l": "length", "long": "length", "length": "length",
    }
    for pattern in (before_label, after_label):
        for match in pattern.finditer(text):
            key = key_map.get(match.group("label").lower())
            if not key:
                continue
            value = _value_to_inches_text(match.group("num"), match.group("unit"))
            if value and (not parts.get(key) or _normalize_unit(match.group("unit")) in {"cm", "mm"}):
                parts[key] = value
    return parts


def _dimension_part_count(dimensions: str) -> int:
    parts = _dimension_parts(dimensions)
    return sum(1 for key in ("width", "height", "depth") if parts.get(key))


def _format_dimension_parts(parts: dict[str, str]) -> str:
    labels = (("width", "W"), ("height", "H"), ("depth", "D"), ("length", "L"))
    bits = [_format_labeled_value(parts[key], label) for key, label in labels if parts.get(key)]
    return " x ".join(bits)


def _best_dimension_candidate(candidates: list[str]) -> str | None:
    best = ""
    best_score = 0
    for candidate in candidates:
        count = _dimension_part_count(candidate)
        if count > best_score:
            best = candidate
            best_score = count
        if count >= 3:
            break
    return best or None


def _collect_json_strings(obj) -> list[str]:
    """Recursively collect all string leaf values from a parsed JSON object."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _collect_json_strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _collect_json_strings(v)]
    return []


def _collect_json_dimension_strings(obj) -> list[str]:
    """Collect key-aware dimension strings from JSON so labels are preserved."""
    collected: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            label_key = _spec_label_key(str(key))
            if label_key in {"width", "height", "depth", "dimensions", "cutout", "shipping"}:
                if isinstance(value, (str, int, float)):
                    collected.append(f"{key}: {value}")
                elif isinstance(value, dict):
                    strings = _collect_json_strings(value)
                    if strings:
                        collected.append(f"{key}: {' '.join(strings)}")
            collected.extend(_collect_json_dimension_strings(value))
    elif isinstance(obj, list):
        for value in obj:
            collected.extend(_collect_json_dimension_strings(value))
    return collected


def _candidate_strings_from_text(text: str, *, include_cutout: bool = False) -> list[str]:
    candidates = _find_dimension_candidates(text, include_cutout=include_cutout)
    if candidates:
        return candidates
    parts = _dimension_parts(text)
    candidate = _format_dimension_parts(parts)
    if candidate and _DIMENSION_CONTEXT.search(text) and not _REJECTED_DIMENSION_CONTEXT.search(text):
        return [candidate]
    return []


def _parse_html_for_dimensions(
    html: str,
    *,
    is_appliance: bool = False,
    debug: dict | None = None,
) -> tuple[str | None, str | None]:
    """
    Extract (product_dimensions, cutout_dimensions) from an HTML page.
    Three passes: JSON-LD → spec tables/dl → visible text.
    Returns the best product dimensions found. Complete W/H/D wins; a useful
    partial is returned only after all deterministic sources are checked.
    """
    product_dims: str | None = None
    cutout_dims: str | None = None
    best_partial_dims: str | None = None
    best_partial_count = 0
    best_partial_method = ""

    soup = _BeautifulSoup(html, "html.parser") if _BeautifulSoup else None
    page_text: str | None = None  # lazily computed once
    candidates_seen: list[str] = []

    if debug is not None:
        debug.setdefault("json_ld_found", False)
        debug.setdefault("spec_table_found", False)
        debug.setdefault("next_data_found", False)
        debug.setdefault("shopify_json_found", False)
        debug.setdefault("embedded_json_found", False)
        debug.setdefault("dimension_parse_method", "")
        debug.setdefault("partial_dimensions_found", "")
        debug.setdefault("rejected_dimensions_reason", "")

    def _get_page_text() -> str:
        nonlocal page_text
        if page_text is None:
            page_text = soup.get_text(" ") if soup else re.sub(r"<[^>]+>", " ", html)
        return page_text

    def _remember(candidates: list[str], method: str) -> str | None:
        nonlocal best_partial_dims, best_partial_count, best_partial_method
        candidates_seen.extend(candidates)
        best = _best_dimension_candidate(candidates)
        if not best:
            return None
        part_count = _dimension_part_count(best)
        if part_count >= 3:
            if debug is not None and not debug.get("dimension_parse_method"):
                debug["dimension_parse_method"] = method
            return best
        if part_count > best_partial_count:
            best_partial_dims = best
            best_partial_count = part_count
            best_partial_method = method
            if debug is not None and not debug.get("partial_dimensions_found"):
                debug["partial_dimensions_found"] = best
        return None

    # ── Pass 1: JSON-LD ────────────────────────────────────────────────────────
    if soup:
        for script in soup.find_all("script", type="application/ld+json"):
            if debug is not None:
                debug["json_ld_found"] = True
            try:
                data = _json.loads(script.string or "")
                blob = "\n".join(_collect_json_dimension_strings(data) + _collect_json_strings(data))
            except Exception:
                continue
            candidates = _candidate_strings_from_text(blob, include_cutout=is_appliance)
            product_dims = _remember(candidates, "json_ld")
            if product_dims:
                break

    # ── Pass 1b: embedded app JSON such as Next.js / Shopify / window config ───
    if not product_dims and soup:
        for script in soup.find_all("script"):
            script_id = str(script.get("id") or "")
            text = script.string or script.get_text(" ") or ""
            if not text:
                continue
            is_next = script_id == "__NEXT_DATA__"
            is_shopify = "Shopify" in text or "shopify" in text
            is_embedded_product_json = bool(
                re.search(r"\b(window|product|config|__INITIAL_STATE__|__APOLLO_STATE__)\b", text)
                and any(k in text.lower() for k in ("dimension", "width", "height", "depth", "size"))
            )
            if is_next and debug is not None:
                debug["next_data_found"] = True
            if is_shopify and debug is not None:
                debug["shopify_json_found"] = True
            if is_embedded_product_json and debug is not None:
                debug["embedded_json_found"] = True
            if not is_next and not is_shopify and not is_embedded_product_json and not any(k in text.lower() for k in ("dimension", "width", "height", "depth", "size")):
                continue
            try:
                parsed = _json.loads(text)
                blob = "\n".join(_collect_json_dimension_strings(parsed) + _collect_json_strings(parsed))
            except Exception:
                blob = text
            candidates = _candidate_strings_from_text(blob, include_cutout=is_appliance)
            method = "next_data" if is_next else "shopify_json" if is_shopify else "embedded_json"
            product_dims = _remember(candidates, method)
            if product_dims:
                break

    # ── Pass 2: spec tables / dl ───────────────────────────────────────────────
    if not product_dims and soup:

        # dl elements
        assembled: dict[str, str] = {}
        for dl in soup.find_all("dl"):
            if debug is not None:
                debug["spec_table_found"] = True
            items = dl.find_all(["dt", "dd"])
            label = ""
            for item in items:
                text = item.get_text(strip=True).lower()
                if item.name == "dt":
                    label = text
                elif item.name == "dd":
                    label_key = _spec_label_key(label)
                    if label_key in {"shipping", "cutout"}:
                        if debug is not None and not debug.get("rejected_dimensions_reason"):
                            debug["rejected_dimensions_reason"] = f"rejected {label_key} dimensions"
                    if label_key in {"width", "height", "depth", "dimensions"}:
                        assembled[label_key] = item.get_text(strip=True)
                        # Check if the dd value itself is a complete 3D string
                        if label_key == "dimensions":
                            val = item.get_text(strip=True)
                            product_dims = _remember([val], "definition_list")
                            if product_dims:
                                break
                    label = ""  # always reset after consuming a dd
            if product_dims:
                break

        if not product_dims:
            # Try assembling W/H/D from separate dl labels
            w = assembled.get("width", "")
            h = assembled.get("height", "")
            d = assembled.get("depth", "")
            candidate = _format_dimension_parts({"width": w, "height": h, "depth": d})
            if candidate:
                product_dims = _remember([candidate], "definition_list_parts")

        if not product_dims:
            # table th→td or td→td pairs
            for table in soup.find_all("table"):
                if debug is not None:
                    debug["spec_table_found"] = True
                table_parts: dict[str, str] = {}
                for row in table.find_all("tr"):
                    cells = row.find_all(["th", "td"])
                    if len(cells) >= 2:
                        label = cells[0].get_text(strip=True).lower()
                        value = cells[1].get_text(strip=True)
                        label_key = _spec_label_key(label)
                        if label_key in {"shipping", "cutout"}:
                            if debug is not None and not debug.get("rejected_dimensions_reason"):
                                debug["rejected_dimensions_reason"] = f"rejected {label_key} dimensions"
                            continue
                        if label_key in {"width", "height", "depth", "dimensions"}:
                            if label_key in ("width", "height", "depth"):
                                table_parts[label_key] = value
                            else:
                                product_dims = _remember([value], "spec_table")
                            if product_dims:
                                break
                if not product_dims and table_parts:
                    product_dims = _remember([_format_dimension_parts(table_parts)], "spec_table_parts")
                if product_dims:
                    break

    # ── Pass 3: visible text ───────────────────────────────────────────────────
    if not product_dims:
        text = _get_page_text()
        candidates = _candidate_strings_from_text(text, include_cutout=is_appliance)
        product_dims = _remember(candidates, "visible_text")
        if not product_dims and debug is not None:
            if _find_dimension_candidates(text, include_shipping=True):
                debug["rejected_dimensions_reason"] = debug.get("rejected_dimensions_reason") or "shipping/package dimensions only"
            elif _find_dimension_candidates(text, include_cutout=True):
                debug["rejected_dimensions_reason"] = debug.get("rejected_dimensions_reason") or "cutout/opening dimensions only"

    if not product_dims and best_partial_dims:
        product_dims = best_partial_dims
        if debug is not None:
            debug["dimension_parse_method"] = debug.get("dimension_parse_method") or best_partial_method
            debug["partial_dimensions_found"] = debug.get("partial_dimensions_found") or best_partial_dims

    # ── Cutout pass (appliances only) ──────────────────────────────────────────
    if is_appliance and product_dims:
        text = _get_page_text()
        cutout_candidates = _find_dimension_candidates(text, include_cutout=True)
        for c in cutout_candidates:
            if c == product_dims or _dimension_part_count(c) < 2:
                continue
            pos = text.find(c)
            if pos >= 0 and "cutout" in text[max(0, pos - 30): pos].lower():
                cutout_dims = c
                break

    if debug is not None:
        debug["dimensions_extracted"] = product_dims or ""
        debug["dimension_part_count"] = _dimension_part_count(product_dims or "")
        debug["dimension_candidates_seen"] = candidates_seen[:8]
        if product_dims and 0 < _dimension_part_count(product_dims) < 3:
            debug["partial_dimensions_found"] = product_dims

    return product_dims, cutout_dims


def _parse_text_pages_for_dimensions(
    pages: list[str],
    *,
    is_appliance: bool = False,
    include_shipping_fallback: bool = False,
    debug: dict | None = None,
) -> tuple[str | None, str | None]:
    """
    Find dimensions in a list of page text strings (from PDF extraction).
    Stops at first page yielding a complete 3D result.
    Returns (product_dims, cutout_dims).
    """
    product_dims: str | None = None
    cutout_dims: str | None = None
    best_partial_dims: str | None = None
    best_partial_count = 0
    shipping_fallback: str | None = None

    for page_text in pages:
        candidates = _candidate_strings_from_text(page_text, include_cutout=is_appliance)
        candidate = _best_dimension_candidate(candidates)

        if candidate:
            part_count = _dimension_part_count(candidate)
            if debug is not None:
                if 0 < part_count < 3 and not debug.get("partial_dimensions_found"):
                    debug["partial_dimensions_found"] = candidate
            if part_count < 3:
                if part_count > best_partial_count:
                    best_partial_dims = candidate
                    best_partial_count = part_count
                # Keep scanning pages. A one/two-axis partial is useful, but it
                # is not a Programa-ready dimension result.
                continue
            product_dims = candidate
            if debug is not None:
                debug["dimension_parse_method"] = debug.get("dimension_parse_method") or "pdf_text"
            # Look for cutout on same page if appliance
            if is_appliance:
                cutout_candidates = _find_dimension_candidates(
                    page_text, include_cutout=True
                )
                for c in cutout_candidates:
                    if c == product_dims or _dimension_part_count(c) < 2:
                        continue
                    pos = page_text.find(c)
                    if pos >= 0 and "cutout" in page_text[max(0, pos - 30): pos].lower():
                        cutout_dims = c
                        break
            break  # stop searching pages once complete product dims found

        # Collect shipping fallback while scanning (only when no product dims yet)
        if include_shipping_fallback and not shipping_fallback:
            shipping_candidates = _find_dimension_candidates(
                page_text, include_shipping=True
            )
            shipping_fallback = _best_dimension_candidate(shipping_candidates)
        elif debug is not None and not debug.get("rejected_dimensions_reason"):
            if _find_dimension_candidates(page_text, include_shipping=True):
                debug["rejected_dimensions_reason"] = "shipping/package dimensions only"
            elif _find_dimension_candidates(page_text, include_cutout=True):
                debug["rejected_dimensions_reason"] = "cutout/opening dimensions only"

    if not product_dims and best_partial_dims:
        product_dims = best_partial_dims
        if debug is not None:
            debug["dimension_parse_method"] = debug.get("dimension_parse_method") or "pdf_text_partial"
            debug["partial_dimensions_found"] = debug.get("partial_dimensions_found") or best_partial_dims

    # Use shipping fallback only when nothing better found
    if not product_dims and include_shipping_fallback and shipping_fallback:
        product_dims = shipping_fallback
        if debug is not None:
            debug["dimension_parse_method"] = debug.get("dimension_parse_method") or "shipping_fallback"

    return product_dims, cutout_dims


def _parse_pdf_for_dimensions(
    pdf_bytes: bytes,
    *,
    is_appliance: bool = False,
    debug: dict | None = None,
) -> tuple[str | None, str | None]:
    """
    Extract dimension text from a PDF using PyMuPDF. Scans first 10 pages.
    Returns (product_dims, cutout_dims). Returns (None, None) on any error.
    """
    try:
        import fitz  # PyMuPDF  # noqa: PLC0415
    except ImportError:
        return None, None

    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            pages = [doc[i].get_text() for i in range(min(10, doc.page_count))]
    except Exception:
        return None, None
    return _parse_text_pages_for_dimensions(
        pages,
        is_appliance=is_appliance,
        include_shipping_fallback=False,
        debug=debug,
    )


# ── URL fetch + parser routing ─────────────────────────────────────────────────

_PDF_EXTENSIONS = frozenset({".pdf"})
_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SCH-Intake/1.0)"}
_REQUEST_TIMEOUT_S: int = 12
_HOSTNAME_RE = re.compile(r"^[a-z0-9.-]+$", re.IGNORECASE)


def _url_validation_error(url: object) -> str:
    raw = str(url or "").strip()
    if not raw:
        return "empty URL"
    if any(ch.isspace() for ch in raw):
        return "malformed URL: contains whitespace"
    try:
        parsed = _urlparse.urlparse(raw)
        hostname = parsed.hostname  # raises ValueError for malformed IPv6 brackets
    except ValueError as exc:
        return f"Invalid IPv6 URL: {exc}"
    except Exception as exc:
        return f"malformed URL: {exc}"
    if parsed.scheme.lower() not in {"http", "https"}:
        return f"malformed URL: unsupported scheme {parsed.scheme or '<missing>'}"
    if not parsed.netloc or not hostname:
        return "malformed URL: missing host"
    host = hostname.strip().lower()
    if ":" not in host and (not _HOSTNAME_RE.match(host) or "." not in host):
        return f"malformed domain: {host}"
    return ""


def _record_invalid_url_debug(
    debug: dict | None,
    url: object,
    *,
    source: str,
    step: str,
    reason: str = "",
    query: str = "",
    fallback_attempted: bool = True,
) -> None:
    if debug is None:
        return
    raw = str(url or "").strip()
    debug["invalid_url_error"] = reason or _url_validation_error(raw)
    debug["invalid_url_generated"] = raw
    debug["invalid_url_source"] = source
    debug["invalid_url_step"] = step
    debug["invalid_url_search_query"] = query
    debug["invalid_url_fallback_attempted"] = "yes" if fallback_attempted else "no"
    if not debug.get("skipped_reason"):
        debug["skipped_reason"] = "invalid URL skipped before fetch"
    _log.warning(
        "Invalid dimension URL skipped step=%s source=%s query=%s url=%r reason=%s fallback=%s",
        step,
        source,
        query,
        raw,
        debug["invalid_url_error"],
        fallback_attempted,
    )


def _fetch_and_parse_url(
    url: str,
    *,
    is_appliance: bool = False,
    debug: dict | None = None,
) -> tuple[str | None, str | None, str]:
    """
    Fetch a URL and route to the correct parser.
    Returns (product_dims, cutout_dims, source_type_suffix).
    source_type_suffix is "page" or "pdf".
    Both dimension values are None on fetch failure.
    """
    suffix = "page"
    invalid_reason = _url_validation_error(url)
    if invalid_reason:
        _record_invalid_url_debug(
            debug,
            url,
            source="dimension_source",
            step="dimension_url_fetch",
            reason=invalid_reason,
            fallback_attempted=True,
        )
        return None, None, suffix
    if _urlparse.urlparse(url).path.lower().endswith(tuple(_PDF_EXTENSIONS)):
        suffix = "pdf"

    if _httpx is None:
        return None, None, suffix

    try:
        resp = _httpx.get(url, headers=_REQUEST_HEADERS, timeout=_REQUEST_TIMEOUT_S, follow_redirects=True)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "").lower()
        if "pdf" in content_type:
            suffix = "pdf"
            if debug is not None:
                debug["spec_pdf_fetched"] = True
            return (*_parse_pdf_for_dimensions(resp.content, is_appliance=is_appliance, debug=debug), suffix)
        if "html" not in content_type and suffix == "pdf":
            # No HTML signal from content-type; URL extension suggests PDF
            if debug is not None:
                debug["spec_pdf_fetched"] = True
            return (*_parse_pdf_for_dimensions(resp.content, is_appliance=is_appliance, debug=debug), suffix)
        return (*_parse_html_for_dimensions(resp.text, is_appliance=is_appliance, debug=debug), suffix)
    except Exception:  # network errors, redirects, and parser failures all return no-result
        return None, None, suffix


def _extract_spec_pdf_links(html: str, base_url: str, model: str = "") -> list[str]:
    """Return product/spec/install/dimension PDF links from an already verified product page."""
    if not html:
        return []
    links: list[tuple[int, str]] = []
    seen: set[str] = set()

    def _add(raw_url: str, label: str = "") -> None:
        if not raw_url:
            return
        resolved = _urlparse.urljoin(base_url, raw_url.strip())
        if _url_validation_error(resolved):
            return
        parsed = _urlparse.urlparse(resolved)
        haystack = f"{resolved} {label}".lower()
        if ".pdf" not in parsed.path.lower() and ".pdf" not in haystack:
            return
        if not _SPEC_PDF_KEYWORDS.search(haystack):
            return
        model_norm = re.sub(r"[^a-z0-9]+", "", model.lower())
        haystack_norm = re.sub(r"[^a-z0-9]+", "", haystack)
        score = 1 + (2 if model_norm and model_norm in haystack_norm else 0)
        key = resolved.split("#")[0]
        if key not in seen:
            seen.add(key)
            links.append((score, key))

    if _BeautifulSoup:
        soup = _BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["a", "link"]):
            href = tag.get("href") or tag.get("src") or ""
            label = tag.get_text(" ") if hasattr(tag, "get_text") else ""
            _add(str(href), label)
    else:
        for href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
            _add(href)

    ranked = sorted(links, key=lambda item: item[0], reverse=True)
    return [url for _score, url in ranked]


def _domain_matches(url: str, domain: str | None) -> bool:
    if not domain:
        return False
    try:
        netloc = _urlparse.urlparse(url).netloc.lower()
    except ValueError:
        return False
    domain = domain.lower().lstrip(".")
    return bool(netloc == domain or netloc.endswith(f".{domain}"))


def _model_in_url(url: str, model: str) -> bool:
    model_norm = re.sub(r"[^a-z0-9]+", "", model.lower())
    url_norm = re.sub(r"[^a-z0-9]+", "", url.lower())
    return bool(model_norm and model_norm in url_norm)


def _confidence_for_dimension(
    dimensions: str,
    *,
    is_manufacturer: bool,
    exact_model_evidence: bool,
) -> str:
    part_count = _dimension_part_count(dimensions)
    if part_count >= 3:
        if is_manufacturer and exact_model_evidence:
            return "high"
        return "medium"
    if part_count == 2:
        return "medium"
    if part_count == 1:
        return "low"
    return "none"


def _build_dimension_result(
    *,
    dimensions: str,
    cutout_dims: str | None,
    source_url: str,
    source_type: str,
    confidence: str,
    queries_tried: list[str],
    urls_checked: list[str],
    debug: dict,
    is_appliance: bool = False,
) -> DimensionResult:
    from src.dimensions import extract_labeled_dimensions

    parts = extract_labeled_dimensions(dimensions)
    evidence = dimensions
    sanity_reason = dimension_sanity_reason(dimensions, "Appliances" if is_appliance else "")
    if sanity_reason:
        confidence = "none"
        debug["rejected_dimensions_reason"] = sanity_reason
    if cutout_dims:
        evidence += f" | Cutout: {cutout_dims}"
    debug = dict(debug)
    debug["dimension_source_url"] = source_url
    debug["dimension_confidence"] = confidence
    debug["dimensions_extracted"] = dimensions
    if 0 < _dimension_part_count(dimensions) < 3:
        debug["partial_dimensions_found"] = dimensions
    return DimensionResult(
        dimensions=dimensions,
        width=_fraction_to_decimal(parts.get("width", "")),
        height=_fraction_to_decimal(parts.get("height", "")),
        depth=_fraction_to_decimal(parts.get("depth", "")),
        length=_fraction_to_decimal(parts.get("length", "")),
        source_url=source_url,
        confidence=confidence,
        source_type=source_type,
        status="found" if confidence in ("high", "medium") else "low_confidence_skipped",
        queries_tried=list(queries_tried),
        urls_checked=list(urls_checked),
        evidence_text=evidence,
        failure_reason="",
        debug=debug,
    )


def _fetch_and_parse_url_with_debug(
    url: str,
    *,
    is_appliance: bool = False,
    debug: dict | None = None,
) -> tuple[str | None, str | None, str]:
    """Call _fetch_and_parse_url with debug while staying compatible with older test doubles."""
    try:
        return _fetch_and_parse_url(url, is_appliance=is_appliance, debug=debug)
    except TypeError as exc:
        if "debug" not in str(exc):
            raise
        return _fetch_and_parse_url(url, is_appliance=is_appliance)


# ── Confidence assignment ──────────────────────────────────────────────────────

def _assign_confidence(
    model_variant: str,
    primary_model: str,
    *,
    is_manufacturer: bool,
) -> str:
    """
    Assign confidence tier based on model match quality and source authority.

    The caller passes the specific model variant that was matched on the source page.
    Comparison is case-insensitive and strips spaces/dashes for normalization.

    - Normalized exact match + manufacturer → "high"
    - Normalized exact match + retailer → "medium"
    - Space/dash-normalized variant (still the same model) → "medium" regardless of source
    - Suffix-stripped partial match → "low" regardless of source
    """
    primary_clean = primary_model.strip().lower()
    variant_clean = model_variant.strip().lower()

    # Exact string match (no normalization)
    if variant_clean == primary_clean:
        if is_manufacturer:
            return "high"
        return "medium"

    # Normalized match (spaces/dashes removed)
    primary_norm = re.sub(r"[\s-]+", "", primary_clean)
    variant_norm = re.sub(r"[\s-]+", "", variant_clean)

    if variant_norm == primary_norm:
        # normalized variant (spaces/dashes removed) — medium regardless of source authority
        return "medium"

    # No match (includes partial/suffix-stripped)
    return "low"


def find_dimensions(
    row: dict,
    session_cache: "SessionCache | None" = None,
    budget: "SearchBudget | None" = None,
) -> DimensionResult:
    """
    Perform full dimension lookup for one intake row.
    Returns DimensionResult with status "found", "not_found", or "low_confidence_skipped".
    Only rows with Brand + Model/SKU that are missing complete 3D dimensions are processed.
    """
    from src.dimensions import has_complete_3d_dimensions

    brand = (row.get("Brand") or "").strip()
    model = (row.get("Model/SKU") or "").strip()
    product_name = (row.get("Product Name") or "").strip()
    category = (row.get("Product Category") or "").strip()
    current_dims = (row.get("Dimensions") or "").strip()
    product_url = (row.get("Product URL") or "").strip()
    is_appliance = category in _APPLIANCE_CATEGORIES
    debug: dict = {
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
        "dimensions_before_enrichment": current_dims,
        "dimensions_extracted": "",
        "dimension_confidence": "none",
        "dimension_source": "",
        "dimension_source_url": "",
        "dimension_parse_method": "",
        "partial_dimensions_found": "",
        "rejected_dimensions_reason": "",
        "final_dimensions": current_dims,
        "final_dimension_writeback_success": False,
        "skipped_reason": "",
        "budget_blocked": False,
        "pages_found": 0,
        "pages_fully_parsed": 0,
        "dimensions_successfully_extracted": False,
        "dimensions_blocked_by_budget": False,
        "dimensions_blocked_by_parser": False,
        "spec_sheets_found": 0,
        "spec_sheets_parsed": 0,
        "image_only_success": False,
        "invalid_url_error": "",
        "invalid_url_generated": "",
        "invalid_url_source": "",
        "invalid_url_step": "",
        "invalid_url_normalized_brand": re.sub(r"[^a-z0-9]+", "", brand.lower()),
        "invalid_url_normalized_model": re.sub(r"[^a-z0-9]+", "", model.lower()),
        "invalid_url_search_query": "",
        "invalid_url_fallback_attempted": "",
    }

    if has_complete_3d_dimensions(current_dims):
        result = _make_not_found_result(failure_reason="dimensions already complete")
        result.debug = {**debug, "skipped_reason": "dimensions already complete"}
        return result
    if not brand:
        result = _make_not_found_result(failure_reason="brand required for dimension lookup")
        result.debug = {**debug, "skipped_reason": "brand required for dimension lookup"}
        return result
    if not model:
        result = _make_not_found_result(failure_reason="model/sku required for dimension lookup")
        result.debug = {**debug, "skipped_reason": "model/sku required for dimension lookup"}
        return result

    domain = _get_manufacturer_domain(
        brand,
        _search_fn=lambda q: _brave_search_urls(q, limit=5, brand=brand, session_cache=session_cache, budget=budget),
    )
    model_variants = _normalize_model_variants(model)

    queries_tried: list[str] = []
    urls_checked: list[str] = []
    low_confidence_result: DimensionResult | None = None

    def _accept_or_remember(result: DimensionResult) -> DimensionResult | None:
        nonlocal low_confidence_result
        result.debug.setdefault("dimensions_before_enrichment", current_dims)
        result.debug["dimensions_extracted"] = result.dimensions
        result.debug["dimension_confidence"] = result.confidence
        result.debug["dimension_source"] = result.source_url
        result.debug["dimension_source_url"] = result.source_url
        result.debug["dimensions_successfully_extracted"] = result.status == "found"
        if 0 < _dimension_part_count(result.dimensions) < 3:
            result.debug["partial_dimensions_found"] = result.dimensions
        result.debug["final_dimensions"] = result.dimensions if result.status == "found" else current_dims
        result.debug["final_dimension_writeback_success"] = result.status == "found"
        if result.confidence == "low":
            if low_confidence_result is None:
                low_confidence_result = result
            return None
        if result.status == "found":
            return result
        return None

    def _try_verified_product_url() -> DimensionResult | None:
        """Exploit an existing product URL and linked spec PDFs before any search."""
        if not product_url:
            return None
        local_debug = dict(debug)
        local_debug["page_fetch_attempted"] = True
        urls_checked.append(product_url)
        invalid_reason = _url_validation_error(product_url)
        if invalid_reason:
            _record_invalid_url_debug(
                local_debug,
                product_url,
                source="existing_product_url",
                step="dimension_product_url_fetch",
                reason=invalid_reason,
                fallback_attempted=True,
            )
            debug.update(local_debug)
            return None
        page_html = ""
        source_suffix = "page"

        cached_content = session_cache.urls.get(product_url) if session_cache is not None else None
        if cached_content:
            page_html = str(cached_content)
            local_debug["page_fetch_success"] = True
            local_debug["pages_fully_parsed"] = int(local_debug.get("pages_fully_parsed") or 0) + 1
            product_dims, cutout_dims = _parse_html_for_dimensions(
                page_html, is_appliance=is_appliance, debug=local_debug
            )
        else:
            if budget is not None and not budget.can_fetch():
                local_debug["budget_blocked"] = True
                local_debug["dimensions_blocked_by_budget"] = True
                local_debug["skipped_reason"] = "budget blocked product URL fetch"
                return None
            product_dims = None
            cutout_dims = None
            if _httpx is not None:
                try:
                    resp = _httpx.get(
                        product_url,
                        headers=_REQUEST_HEADERS,
                        timeout=_REQUEST_TIMEOUT_S,
                        follow_redirects=True,
                    )
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type", "").lower()
                    if "pdf" in content_type or _urlparse.urlparse(product_url).path.lower().endswith(tuple(_PDF_EXTENSIONS)):
                        source_suffix = "pdf"
                        local_debug["spec_pdf_fetched"] = True
                        local_debug["spec_sheets_parsed"] = int(local_debug.get("spec_sheets_parsed") or 0) + 1
                        product_dims, cutout_dims = _parse_pdf_for_dimensions(
                            resp.content,
                            is_appliance=is_appliance,
                            debug=local_debug,
                        )
                    else:
                        page_html = resp.text
                        if session_cache is not None:
                            session_cache.urls[product_url] = page_html
                        product_dims, cutout_dims = _parse_html_for_dimensions(
                            page_html,
                            is_appliance=is_appliance,
                            debug=local_debug,
                        )
                    local_debug["page_fetch_success"] = True
                    local_debug["pages_fully_parsed"] = int(local_debug.get("pages_fully_parsed") or 0) + 1
                except Exception:
                    local_debug["page_fetch_success"] = False
            if budget is not None:
                budget.consume_fetch()

        is_manufacturer = _domain_matches(product_url, domain)
        exact_evidence = _model_in_url(product_url, model)
        source_type = f"{'manufacturer' if is_manufacturer else 'retailer'}_{source_suffix}"
        if product_dims:
            confidence = _confidence_for_dimension(
                product_dims,
                is_manufacturer=is_manufacturer,
                exact_model_evidence=exact_evidence,
            )
            local_debug["product_url_confidence"] = "high" if is_manufacturer and exact_evidence else "medium"
            result = _build_dimension_result(
                dimensions=product_dims,
                cutout_dims=cutout_dims,
                source_url=product_url,
                source_type=source_type,
                confidence=confidence,
                queries_tried=queries_tried,
                urls_checked=urls_checked,
                debug=local_debug,
                is_appliance=is_appliance,
            )
            accepted = _accept_or_remember(result)
            if accepted:
                return accepted

        # If we have the product page HTML, inspect linked spec/install PDFs.
        if not page_html and session_cache is not None and product_url in session_cache.urls:
            page_html = str(session_cache.urls.get(product_url) or "")
        if page_html:
            pdf_links = _extract_spec_pdf_links(page_html, product_url, model)
            local_debug["spec_pdf_links_found"] = len(pdf_links)
            local_debug["spec_sheets_found"] = len(pdf_links)
            for pdf_url in pdf_links[:3]:
                if budget is not None and not budget.can_fetch():
                    local_debug["budget_blocked"] = True
                    local_debug["dimensions_blocked_by_budget"] = True
                    local_debug["skipped_reason"] = "budget blocked spec PDF fetch"
                    break
                urls_checked.append(pdf_url)
                pdf_debug = dict(local_debug)
                pdf_debug["spec_pdf_fetched"] = True
                pdf_debug["spec_sheets_parsed"] = int(pdf_debug.get("spec_sheets_parsed") or 0) + 1
                pdf_dims, pdf_cutout, pdf_suffix = _fetch_and_parse_url_with_debug(
                    pdf_url, is_appliance=is_appliance, debug=pdf_debug
                )
                if budget is not None:
                    budget.consume_fetch()
                if not pdf_dims:
                    pdf_debug["dimensions_blocked_by_parser"] = True
                    continue
                confidence = _confidence_for_dimension(
                    pdf_dims,
                    is_manufacturer=is_manufacturer,
                    exact_model_evidence=_model_in_url(pdf_url, model) or exact_evidence,
                )
                result = _build_dimension_result(
                    dimensions=pdf_dims,
                    cutout_dims=pdf_cutout,
                    source_url=pdf_url,
                    source_type=f"{'manufacturer' if is_manufacturer else 'retailer'}_{pdf_suffix}",
                    confidence=confidence,
                    queries_tried=queries_tried,
                    urls_checked=urls_checked,
                    debug=pdf_debug,
                    is_appliance=is_appliance,
                )
                accepted = _accept_or_remember(result)
                if accepted:
                    return accepted
        return None

    direct_result = _try_verified_product_url()
    if direct_result:
        return direct_result

    def _try_queries(query_list: list[str], is_manufacturer: bool) -> DimensionResult | None:
        for query in query_list:
            queries_tried.append(query)
            search_urls = _brave_search_urls(query, limit=5, brand=brand, session_cache=session_cache, budget=budget)
            debug["pages_found"] = int(debug.get("pages_found") or 0) + len(search_urls)
            for url in search_urls:
                invalid_reason = _url_validation_error(url)
                if invalid_reason:
                    _record_invalid_url_debug(
                        debug,
                        url,
                        source="brave_search_result",
                        step="dimension_search_result_fetch",
                        reason=invalid_reason,
                        query=query,
                        fallback_attempted=True,
                    )
                    continue
                urls_checked.append(url)
                if budget is not None and not budget.can_fetch():
                    debug["budget_blocked"] = True
                    debug["dimensions_blocked_by_budget"] = True
                    debug["skipped_reason"] = "budget blocked page/spec fetch"
                    break
                url_debug = dict(debug)
                url_debug["page_fetch_attempted"] = True
                if _urlparse.urlparse(url).path.lower().endswith(tuple(_PDF_EXTENSIONS)):
                    url_debug["spec_sheets_found"] = int(url_debug.get("spec_sheets_found") or 0) + 1
                product_dims, cutout_dims, src_suffix = _fetch_and_parse_url_with_debug(
                    url, is_appliance=is_appliance, debug=url_debug
                )
                if budget is not None:
                    budget.consume_fetch()
                url_debug["pages_fully_parsed"] = int(url_debug.get("pages_fully_parsed") or 0) + 1
                if src_suffix == "pdf":
                    url_debug["spec_sheets_parsed"] = int(url_debug.get("spec_sheets_parsed") or 0) + 1
                url_debug["page_fetch_success"] = bool(product_dims or cutout_dims)
                if not product_dims or _dimension_part_count(product_dims) == 0:
                    url_debug["dimensions_blocked_by_parser"] = True
                    continue
                matched_variant = None
                for v in model_variants:
                    if v.lower() in product_dims.lower() or v.lower() in url.lower():
                        matched_variant = v
                        break
                src_type_key = "manufacturer" if is_manufacturer else "retailer"
                source_type = f"{src_type_key}_{src_suffix}"
                if matched_variant is None:
                    conf = "low"
                else:
                    conf = _assign_confidence(
                        matched_variant,
                        model,
                        is_manufacturer=is_manufacturer,
                    )
                part_count = _dimension_part_count(product_dims)
                if part_count == 2 and conf == "high":
                    conf = "medium"
                elif part_count == 1:
                    conf = "low"
                result = _build_dimension_result(
                    dimensions=product_dims,
                    cutout_dims=cutout_dims,
                    source_url=url,
                    source_type=source_type,
                    confidence=conf,
                    queries_tried=queries_tried,
                    urls_checked=urls_checked,
                    debug=url_debug,
                    is_appliance=is_appliance,
                )
                accepted = _accept_or_remember(result)
                if accepted is None:
                    continue
                return accepted
        return None

    for i, variant in enumerate(model_variants):
        # Pass sku only on primary variant to avoid duplicate Phase-4 fallback queries
        sku_arg = model if i == 0 else ""
        variant_queries = _generate_queries(brand, variant, domain, product_name, sku_arg)
        result = _try_queries(variant_queries, is_manufacturer=bool(domain))
        if result:
            return result

    retailer_queries = _generate_retailer_queries(brand, model)
    result = _try_queries(retailer_queries, is_manufacturer=False)
    if result:
        return result

    if low_confidence_result:
        low_confidence_result.queries_tried = list(queries_tried)
        low_confidence_result.urls_checked = list(urls_checked)
        # Spec: do not fill dimension fields for low confidence — preserve evidence_text for audit
        low_confidence_result.dimensions = ""
        low_confidence_result.width = ""
        low_confidence_result.height = ""
        low_confidence_result.depth = ""
        low_confidence_result.length = ""
        low_confidence_result.debug["partial_dimensions_found"] = (
            low_confidence_result.debug.get("partial_dimensions_found")
            or low_confidence_result.evidence_text
        )
        low_confidence_result.debug["final_dimensions"] = current_dims
        low_confidence_result.debug["final_dimension_writeback_success"] = False
        return low_confidence_result

    result = _make_not_found_result(
        queries_tried=queries_tried,
        urls_checked=urls_checked,
        failure_reason=f"no dimensions found after {len(queries_tried)} queries and {len(urls_checked)} URLs checked",
    )
    result.debug = {
        **debug,
        "queries_tried": list(queries_tried),
        "urls_checked": list(urls_checked),
        "final_dimensions": current_dims,
        "skipped_reason": debug.get("skipped_reason", ""),
        "rejected_dimensions_reason": debug.get("rejected_dimensions_reason", ""),
    }
    return result
