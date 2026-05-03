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
import re
import urllib.parse as _urlparse
from dataclasses import dataclass, field

try:
    from bs4 import BeautifulSoup as _BeautifulSoup
except ImportError:
    _BeautifulSoup = None


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

_SPEC_LABEL_KEYWORDS = frozenset({
    "width", "height", "depth", "dimensions", "overall dimensions",
    "product dimensions", "w×h×d", "w x h x d",
})


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


def _collect_json_strings(obj) -> list[str]:
    """Recursively collect all string leaf values from a parsed JSON object."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _collect_json_strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _collect_json_strings(v)]
    return []


def _parse_html_for_dimensions(
    html: str,
    *,
    is_appliance: bool = False,
) -> tuple[str | None, str | None]:
    """
    Extract (product_dimensions, cutout_dimensions) from an HTML page.
    Three passes: JSON-LD → spec tables/dl → visible text.
    Returns (None, None) if no complete 3D dimensions found.
    """
    from src.dimensions import has_complete_3d_dimensions

    product_dims: str | None = None
    cutout_dims: str | None = None

    soup = _BeautifulSoup(html, "html.parser") if _BeautifulSoup else None
    page_text: str | None = None  # lazily computed once

    def _get_page_text() -> str:
        nonlocal page_text
        if page_text is None:
            page_text = soup.get_text(" ") if soup else re.sub(r"<[^>]+>", " ", html)
        return page_text

    # ── Pass 1: JSON-LD ────────────────────────────────────────────────────────
    if soup:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = _json.loads(script.string or "")
                blob = "\n".join(_collect_json_strings(data))
            except Exception:
                continue
            candidates = _find_dimension_candidates(blob, include_cutout=is_appliance)
            for c in candidates:
                if has_complete_3d_dimensions(c):
                    product_dims = c
                    break
            if product_dims:
                break

    # ── Pass 2: spec tables / dl ───────────────────────────────────────────────
    if not product_dims and soup:

        # dl elements
        assembled: dict[str, str] = {}
        for dl in soup.find_all("dl"):
            items = dl.find_all(["dt", "dd"])
            label = ""
            for item in items:
                text = item.get_text(strip=True).lower()
                if item.name == "dt":
                    label = text
                elif item.name == "dd":
                    if label in _SPEC_LABEL_KEYWORDS:
                        assembled[label] = item.get_text(strip=True)
                        # Check if the dd value itself is a complete 3D string
                        if label in ("dimensions", "overall dimensions", "product dimensions"):
                            val = item.get_text(strip=True)
                            if has_complete_3d_dimensions(val):
                                product_dims = val
                                break
                    label = ""  # always reset after consuming a dd
            if product_dims:
                break

        if not product_dims:
            # Try assembling W/H/D from separate dl labels
            w = assembled.get("width", "")
            h = assembled.get("height", "")
            d = assembled.get("depth", "")
            if w and h and d:
                candidate = f"{w} W x {h} H x {d} D"
                if has_complete_3d_dimensions(candidate):
                    product_dims = candidate

        if not product_dims:
            # table th→td or td→td pairs
            for table in soup.find_all("table"):
                for row in table.find_all("tr"):
                    cells = row.find_all(["th", "td"])
                    if len(cells) >= 2:
                        label = cells[0].get_text(strip=True).lower()
                        value = cells[1].get_text(strip=True)
                        if label in _SPEC_LABEL_KEYWORDS and has_complete_3d_dimensions(value):
                            product_dims = value
                            break
                if product_dims:
                    break

    # ── Pass 3: visible text ───────────────────────────────────────────────────
    if not product_dims:
        text = _get_page_text()
        candidates = _find_dimension_candidates(text, include_cutout=is_appliance)
        for c in candidates:
            if has_complete_3d_dimensions(c):
                product_dims = c
                break

    # ── Cutout pass (appliances only) ──────────────────────────────────────────
    if is_appliance and product_dims:
        text = _get_page_text()
        cutout_candidates = _find_dimension_candidates(text, include_cutout=True)
        for c in cutout_candidates:
            if c == product_dims or not has_complete_3d_dimensions(c):
                continue
            pos = text.find(c)
            if pos >= 0 and "cutout" in text[max(0, pos - 20): pos].lower():
                cutout_dims = c
                break

    return product_dims, cutout_dims


def _parse_text_pages_for_dimensions(
    pages: list[str],
    *,
    is_appliance: bool = False,
    include_shipping_fallback: bool = False,
) -> tuple[str | None, str | None]:
    """
    Find dimensions in a list of page text strings (from PDF extraction).
    Stops at first page yielding a complete 3D result.
    Returns (product_dims, cutout_dims).
    """
    from src.dimensions import has_complete_3d_dimensions

    product_dims: str | None = None
    cutout_dims: str | None = None
    shipping_fallback: str | None = None

    for page_text in pages:
        candidates = _find_dimension_candidates(
            page_text, include_cutout=is_appliance
        )
        for c in candidates:
            if has_complete_3d_dimensions(c):
                product_dims = c
                break

        if product_dims:
            # Look for cutout on same page if appliance
            if is_appliance:
                cutout_candidates = _find_dimension_candidates(
                    page_text, include_cutout=True
                )
                for c in cutout_candidates:
                    if c == product_dims or not has_complete_3d_dimensions(c):
                        continue
                    pos = page_text.find(c)
                    if pos >= 0 and "cutout" in page_text[max(0, pos - 30): pos].lower():
                        cutout_dims = c
                        break
            break  # stop searching pages once product dims found

        # Collect shipping fallback while scanning (only when no product dims yet)
        if include_shipping_fallback and not shipping_fallback:
            shipping_candidates = _find_dimension_candidates(
                page_text, include_shipping=True
            )
            for c in shipping_candidates:
                if has_complete_3d_dimensions(c):
                    shipping_fallback = c
                    break

    # Use shipping fallback only when nothing better found
    if not product_dims and include_shipping_fallback and shipping_fallback:
        product_dims = shipping_fallback

    return product_dims, cutout_dims


def _parse_pdf_for_dimensions(
    pdf_bytes: bytes,
    *,
    is_appliance: bool = False,
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
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = [doc[i].get_text() for i in range(min(10, doc.page_count))]
        return _parse_text_pages_for_dimensions(
            pages,
            is_appliance=is_appliance,
            include_shipping_fallback=True,
        )
    except Exception:
        return None, None


def find_dimensions(row: dict) -> DimensionResult:
    """Perform full dimension lookup for a single intake row. Stub — implemented in Task 11."""
    return _make_not_found_result(failure_reason="not implemented")
