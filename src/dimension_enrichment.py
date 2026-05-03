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

import re
import urllib.parse as _urlparse
from dataclasses import dataclass, field


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

# Simple session-scoped cache — not a cross-process or persistent cache.
_discovered_domains: dict[str, str] = {}


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
    Checks BRAND_DOMAIN_TABLE first, then _discovered_domains cache,
    then optionally runs a discovery search via _search_fn(query) -> list[str].
    """
    brand_stripped = brand.strip()
    key = brand_stripped.lower()
    if not key:
        return None
    if key in BRAND_DOMAIN_TABLE:
        return BRAND_DOMAIN_TABLE[key]
    if key in _discovered_domains:
        return _discovered_domains[key]
    if _search_fn is None:
        return None
    try:
        urls = _search_fn(f'"{brand_stripped}" official website product specifications')
        if not urls:
            return None
        netloc = _urlparse.urlparse(urls[0]).netloc.lower()
        domain = netloc[4:] if netloc.startswith("www.") else netloc
        if domain:
            _discovered_domains[key] = domain
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
    r"\bdimensions?\s*[:\-]\s*([^\n]{5,100})",
    re.IGNORECASE,
)
# Inline W×H×D — supports fractions, decimals, integers; × or x or X or space
_INLINE_DIM = re.compile(
    r"[\d][\d ./]*\"?\s*[WwHhDd]\b[\s×xX]+[\d][\d ./]*\"?\s*[WwHhDd]\b[\s×xX]+[\d][\d ./]*\"?\s*[WwHhDd]\b",
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

    # Priority 4: cutout (optional)
    if include_cutout:
        for m in _CUTOUT_DIM_LABEL.finditer(text):
            _add(m.group(1))

    # Priority 5: shipping — only when flag set AND no higher-priority candidates found
    if include_shipping and not candidates:
        for m in _SHIPPING_DIM_LABEL.finditer(text):
            _add(m.group(1))

    return candidates


def find_dimensions(row: dict) -> DimensionResult:
    """Perform full dimension lookup for a single intake row. Stub — implemented in Task 11."""
    return _make_not_found_result(failure_reason="not implemented")
