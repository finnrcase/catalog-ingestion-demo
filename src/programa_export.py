"""
Programa import-file export for SCH DesignOps Intake.

Transforms the internal intake DataFrame into a clean CSV/XLSX compatible
with Programa's built-in "Import Products" feature.

Public API
----------
PROGRAMA_COLUMNS : list[str]
    Fixed 21-column output order for the Programa import file.

validate_for_export(rows) -> dict
    Returns a validation summary without modifying data.

build_programa_import_dataframe(rows) -> pd.DataFrame
    Returns a clean export-ready DataFrame (Include=True, Product Name required).

build_programa_debug_dataframe(rows) -> pd.DataFrame
    Same as above plus debug/confidence columns for internal review.

export_programa_csv(df) -> bytes
    Serialize DataFrame to UTF-8 CSV bytes.

export_programa_xlsx(df) -> bytes
    Serialize DataFrame to XLSX bytes (single sheet, no merged cells).
"""

from __future__ import annotations

import io
import re
import urllib.parse
import zipfile

import pandas as pd

from src.dimensions import (
    dimension_sanity_reason,
    extract_labeled_dimensions,
    has_complete_3d_dimensions,
)
from src.notes import remove_notes_row_prefix

# ── Constants ─────────────────────────────────────────────────────────────────

PROGRAMA_COLUMNS: list[str] = [
    "Section",
    "Product Name",
    "Brand",
    "SKU",
    "Model",
    "Dimensions",
    "Width (in)",
    "Height (in)",
    "Depth (in)",
    "Length (in)",
    "Quantity",
    "Price",
    "Supplier",
    "Product URL",
    "Image URL",
    "Finish",
    "Color",
    "Material",
    "Lead Time",
    "Notes",
    "Location",
]

CANONICAL_SECTIONS: list[str] = [
    "Appliances",
    "Lighting",
    "Plumbing",
    "Cabinetry",
    "Flooring",
    "Furniture",
    "Decor",
    "Hardware",
    "Exterior",
    "General",
]

_DEBUG_EXTRA_COLUMNS: list[str] = [
    "Confidence Score",
    "Source Type",
    "AI Category Confidence",
    "Category Source",
    "Local Image Path",
    "Image Filename",
    "Dimension Source URL",
    "Dimension Confidence",
    "Dimension Source Type",
    "Dimension Lookup Status",
    "enrichment_status",
    "enrichment_error",
    "stage_log",
    "debug_traceback",
]

_MATERIAL_TAG_RE = re.compile(r"\[Materials:\s*([^\]]+)\]", re.IGNORECASE)
_SYSTEM_TAG_RE = re.compile(r"\[[^\]]*\]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_HTTP_URL_RE = re.compile(r"https?://[^\s,)\]\"']+", re.IGNORECASE)
_SOURCE_LINKS_BLOCK_RE = re.compile(
    r"(?:\n{2,})?(?:Source links:\n.*|No verified source found during enrichment\.)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_TECHNICAL_NOISE_RE = re.compile(
    r"\b(?:invalid ipv6|traceback|stack trace|api key|secret|token|httpx|exception)\b",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_URL_WEAK_PAGE_RE = re.compile(
    r"(^|/)(?:sitemap|product-sitemap|search|category|categories|collections?|browse|tag|blog|support)(?:/|$)",
    re.IGNORECASE,
)
_RETAILER_DOMAINS = frozenset({
    "ajmadison.com",
    "appliancesconnection.com",
    "bestbuy.com",
    "build.com",
    "designerappliances.com",
    "ferguson.com",
    "homedepot.com",
    "lowes.com",
    "perigold.com",
    "wayfair.com",
})
_MANUAL_ARCHIVE_RE = re.compile(r"\b(manual|archive|manuals|install|installation|guide|document)\b", re.IGNORECASE)

_SECTION_ALIASES: dict[str, str] = {
    "": "General",
    "accessories": "Decor",
    "accessory": "Decor",
    "appliance": "Appliances",
    "appliances": "Appliances",
    "art": "Decor",
    "artwork": "Decor",
    "bath": "Plumbing",
    "bath linens": "Decor",
    "bathroom": "Plumbing",
    "bedding": "Decor",
    "bedding linens bath linens": "Decor",
    "beds": "Furniture",
    "beds mattresses": "Furniture",
    "cabinet": "Cabinetry",
    "cabinets": "Cabinetry",
    "cabinetry": "Cabinetry",
    "casework": "Cabinetry",
    "chairs": "Furniture",
    "decor": "Decor",
    "decoration": "Decor",
    "decorative": "Decor",
    "dresser": "Furniture",
    "dressers drawers storage": "Furniture",
    "electrical": "Lighting",
    "exterior": "Exterior",
    "fabric": "Decor",
    "fabrics pillows": "Decor",
    "flooring": "Flooring",
    "furniture": "Furniture",
    "general": "General",
    "gym equipment": "Furniture",
    "hardware": "Hardware",
    "lighting": "Lighting",
    "lights": "Lighting",
    "linen": "Decor",
    "linens": "Decor",
    "millwork": "Cabinetry",
    "mirror": "Decor",
    "mirrors": "Decor",
    "outdoor": "Exterior",
    "paint": "Decor",
    "paint wallpaper": "Decor",
    "pillow": "Decor",
    "pillows": "Decor",
    "plumbing": "Plumbing",
    "rug": "Decor",
    "rugs": "Decor",
    "seating": "Furniture",
    "sofa": "Furniture",
    "stone": "Flooring",
    "stone tile": "Flooring",
    "storage": "Furniture",
    "table": "Furniture",
    "tables": "Furniture",
    "tile": "Flooring",
    "wallpaper": "Decor",
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _str_val(v) -> str:
    """Safely coerce a cell value to a stripped string, treating None/NaN as blank."""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"nan", "none", "null"} else s


def _section_key(value: str) -> str:
    return _NON_ALNUM_RE.sub(" ", value.strip().lower()).strip()


def normalize_section(value: object) -> str:
    """Map any inferred category/section to the controlled Programa section list."""
    raw = _str_val(value)
    key = _section_key(raw)
    if not key:
        return "General"
    canonical_lookup = {_section_key(section): section for section in CANONICAL_SECTIONS}
    if key in canonical_lookup:
        return canonical_lookup[key]
    if key in _SECTION_ALIASES:
        return _SECTION_ALIASES[key]

    # Keyword fallback catches verbose AI labels like "decorative table lamp".
    keyword_map = [
        ("appliance", "Appliances"),
        ("light", "Lighting"),
        ("lamp", "Lighting"),
        ("plumb", "Plumbing"),
        ("sink", "Plumbing"),
        ("faucet", "Plumbing"),
        ("cabinet", "Cabinetry"),
        ("millwork", "Cabinetry"),
        ("floor", "Flooring"),
        ("tile", "Flooring"),
        ("stone", "Flooring"),
        ("chair", "Furniture"),
        ("seat", "Furniture"),
        ("sofa", "Furniture"),
        ("table", "Furniture"),
        ("bed", "Furniture"),
        ("dresser", "Furniture"),
        ("rug", "Decor"),
        ("mirror", "Decor"),
        ("pillow", "Decor"),
        ("decor", "Decor"),
        ("art", "Decor"),
        ("hardware", "Hardware"),
        ("knob", "Hardware"),
        ("pull", "Hardware"),
        ("exterior", "Exterior"),
        ("outdoor", "Exterior"),
    ]
    for needle, section in keyword_map:
        if needle in key:
            return section
    return "General"


def _extract_material_from_notes(notes: str) -> str:
    """Return the value from the first [Materials: ...] tag, or empty string."""
    notes = notes or ""
    m = _MATERIAL_TAG_RE.search(notes)
    return m.group(1).strip() if m else ""


def _clean_notes(notes: str) -> str:
    """Strip all [...] system tags and leading row-number prefixes from notes."""
    text = _SYSTEM_TAG_RE.sub("", notes)
    text = _SOURCE_LINKS_BLOCK_RE.sub("", text)
    text = " ".join(
        part.strip()
        for part in re.split(r"[\r\n]+", text)
        if part.strip() and not _TECHNICAL_NOISE_RE.search(part)
    )
    text = re.sub(r"\s{2,}", " ", text).strip()
    return remove_notes_row_prefix(text)


def _first_url(value: object) -> str:
    """Return the first clean http(s) URL from a cell/debug value."""
    text = _str_val(value)
    if not text:
        return ""
    match = _HTTP_URL_RE.search(text)
    if match:
        text = match.group(0)
    if not text.lower().startswith(("http://", "https://")):
        return ""
    text = text.rstrip(".,;")
    try:
        parsed = urllib.parse.urlparse(text)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if any(bad in text.lower() for bad in ("traceback", "invalid ipv6", "api key", "secret")):
        return ""
    return text


def _domain_from_url(value: object) -> str:
    url = _first_url(value)
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return ""
    host = (parsed.hostname or "").strip().lower()
    return host[4:] if host.startswith("www.") else host


def _domain_matches(domain: str, expected: str) -> bool:
    domain = str(domain or "").lower().lstrip(".")
    expected = str(expected or "").lower().lstrip(".")
    return bool(domain and expected and (domain == expected or domain.endswith(f".{expected}")))


def _is_retailer_url(url: object) -> bool:
    domain = _domain_from_url(url)
    return any(_domain_matches(domain, retailer) for retailer in _RETAILER_DOMAINS)


def _first_url_from_fields(row: dict, fields: tuple[str, ...]) -> str:
    for field in fields:
        url = _first_url(row.get(field))
        if url:
            return url
    return ""


def _source_kind_label(url: object, row: dict | None = None, source_hint: object = "") -> str:
    """Classify a user-facing source label. Never call retailers manufacturers."""
    row = row or {}
    url_text = _str_val(url)
    hint = _str_val(source_hint).replace("_", " ").lower()
    if _is_pdf_url(url_text):
        return "Manual/archive" if _MANUAL_ARCHIVE_RE.search(url_text) else "Spec sheet PDF"
    if "manual" in hint or "archive" in hint or "installation" in hint:
        return "Manual/archive"
    if "spec" in hint or "pdf" in hint:
        return "Spec sheet PDF"
    if "retailer" in hint or _is_retailer_url(url_text):
        return "Retailer"
    manufacturer_domain = _str_val(row.get("manufacturer_domain_used") or row.get("Manufacturer Domain"))
    if manufacturer_domain and _domain_matches(_domain_from_url(url_text), manufacturer_domain):
        return "Manufacturer"
    if "manufacturer" in hint:
        return "Manufacturer"
    return "Unknown"


def _source_label_parts(url: object = "", row: dict | None = None, source_hint: object = "", *values: object) -> str:
    parts: list[str] = []
    source_label = _source_kind_label(url, row, source_hint)
    if source_label != "Unknown":
        parts.append(source_label)
    for value in values:
        text = _str_val(value)
        if not text or text.lower() in {"none", "null", "nan"}:
            continue
        text = text.replace("_", " ").strip()
        if text.lower() in {"manufacturer page", "manufacturer pdf", "retailer page", "retailer pdf"}:
            continue
        if text.lower() in {"high", "medium", "low"}:
            text = f"{text.lower()} confidence"
        if text.lower() in {part.lower() for part in parts}:
            continue
        parts.append(text)
    return f" ({', '.join(parts)})" if parts else ""


def _manufacturer_url(row: dict) -> str:
    explicit = _first_url_from_fields(row, ("manufacturer_url", "Manufacturer URL", "manufacturer_source_url"))
    if explicit:
        return explicit
    domain = _str_val(row.get("manufacturer_domain_used") or row.get("Manufacturer Domain"))
    if not domain:
        return ""
    domain = domain.strip().lower()
    domain = re.sub(r"^https?://", "", domain).strip("/")
    if "." not in domain or any(ch.isspace() for ch in domain):
        return ""
    return f"https://{domain}"


def _append_dimension_context_note(notes: str, row: dict) -> str:
    """Keep non-product/extra dimensions visible but outside Programa W/H/D."""
    dimensions = _str_val(row.get("Dimensions"))
    parts = extract_labeled_dimensions(dimensions)
    additions: list[str] = []
    if parts.get("length"):
        additions.append(
            f"Additional dimension noted: Length {parts['length']} kept out of Programa W x H x D field."
        )
    rejected = _str_val(row.get("rejected_dimensions_reason"))
    if rejected:
        additions.append(f"Dimensions rejected before export: {rejected}.")
    if not additions:
        return notes
    existing = _str_val(notes)
    for addition in additions:
        if addition not in existing:
            existing = f"{existing} {addition}".strip() if existing else addition
    return existing


def _programa_dimension_text(row: dict) -> str:
    """Return main Programa dimensions as W x H x D only, never Length/Cutout."""
    if _dimension_rejection_reason(row):
        return ""
    raw = _str_val(row.get("Dimensions"))
    parts = extract_labeled_dimensions(raw)
    if parts.get("width") and parts.get("height") and parts.get("depth"):
        return f'{parts["width"]}"W x {parts["height"]}"H x {parts["depth"]}"D'
    return raw


def _build_source_notes_block(row: dict) -> str:
    """Build a clean, user-facing source summary for Programa Notes."""
    dimensions = _str_val(row.get("Dimensions"))
    image_url = _str_val(row.get("Image URL"))

    dimension_url = _first_url_from_fields(row, (
        "Dimension Source URL",
        "dimension_source_url",
        "dimension_source",
        "dimensions_source_url",
    ))
    image_source_url = _first_url_from_fields(row, (
        "image_source_url",
        "Image Source URL",
        "selected_image_url",
        "original_image_url",
        "cloudinary_url",
        "Image URL",
    ))
    product_page_url = _first_url_from_fields(row, (
        "Product URL",
        "selected_product_url",
        "product_url",
        "product_page_url",
    ))
    if product_page_url and (_product_url_rejection_reason(product_page_url, row) or _is_pdf_url(product_page_url)):
        product_page_url = ""

    spec_sheet_url = _first_url_from_fields(row, (
        "spec_sheet_url",
        "Spec Sheet URL",
        "spec_pdf_url",
        "Spec PDF URL",
    ))
    if not spec_sheet_url:
        for field in ("Dimension Source URL", "dimension_source_url", "Product URL", "selected_product_url"):
            candidate = _first_url(row.get(field))
            if candidate and _is_pdf_url(candidate):
                spec_sheet_url = candidate
                break

    manufacturer_url = _manufacturer_url(row)
    likely_dimension_source = dimension_url or spec_sheet_url or product_page_url
    likely_image_source = image_source_url or product_page_url

    lines: list[str] = []
    if has_complete_3d_dimensions(dimensions) and dimension_url:
        lines.append(
            "Dimensions source: "
            f"{dimension_url}{_source_label_parts(dimension_url, row, row.get('Dimension Source Type'), row.get('Dimension Confidence') or row.get('dimension_confidence'))}"
        )
    elif not has_complete_3d_dimensions(dimensions) and likely_dimension_source:
        lines.append(
            "Dimensions missing — check source: "
            f"{likely_dimension_source}{_source_label_parts(likely_dimension_source, row, row.get('Dimension Source Type'), row.get('Dimension Confidence') or row.get('dimension_confidence'))}"
        )

    if _is_public_https_image_url(image_url) and image_source_url:
        lines.append(
            "Image source: "
            f"{image_source_url}{_source_label_parts(image_source_url, row, 'image', row.get('image_confidence'))}"
        )
    elif not _is_public_https_image_url(image_url) and likely_image_source:
        lines.append(
            "Image missing — check source: "
            f"{likely_image_source}{_source_label_parts(likely_image_source, row, 'image', row.get('image_confidence'))}"
        )

    if product_page_url:
        lines.append(
            "Product page: "
            f"{product_page_url}{_source_label_parts(product_page_url, row, 'product_page', row.get('selected_product_url_confidence') or row.get('product_url_confidence'))}"
        )
    if spec_sheet_url:
        lines.append(f"Spec sheet: {spec_sheet_url}{_source_label_parts(spec_sheet_url, row, 'spec_sheet')}")
    if manufacturer_url:
        source_label = _source_kind_label(manufacturer_url, row, "manufacturer_url")
        lines.append(f"{source_label}: {manufacturer_url}")

    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if line not in seen:
            seen.add(line)
            deduped.append(line)
    if deduped:
        return "Source links:\n" + "\n".join(deduped)
    return "No verified source found during enrichment."


def append_source_links_to_notes(row: dict) -> dict:
    """Return row copy with a source-link Notes block appended for user recovery."""
    updated = dict(row)
    base_notes = _clean_notes(_str_val(updated.get("Notes")))
    source_block = _build_source_notes_block(updated)
    updated["Notes"] = f"{base_notes}\n\n{source_block}".strip() if base_notes else source_block
    return updated


def append_source_links_to_notes_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Append source-link notes for frontend preview rows without changing columns."""
    df = df.copy()
    if "Notes" not in df.columns:
        df["Notes"] = ""
    rows = [append_source_links_to_notes(row) for row in df.fillna("").to_dict("records")]
    return pd.DataFrame(rows, columns=list(df.columns))


def _to_row_list(rows) -> list[dict]:
    """Normalize supported row containers to a list of dictionaries."""
    if rows is None:
        return []
    if isinstance(rows, pd.DataFrame):
        return [r.to_dict() for _, r in rows.iterrows()]
    return list(rows)


def _is_included(row: dict) -> bool:
    """True when Include is truthy (True, 1, 'True', or absent)."""
    v = row.get("Include", True)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() not in {"false", "0", "no"}


def _is_photo_only(row: dict) -> bool:
    value = _str_val(row.get("photo_only")).lower()
    return (
        value in {"true", "1", "yes"}
        or _str_val(row.get("Source Type")) == "Photo"
        or _str_val(row.get("Import Type")).lower() in {"photo upload", "photo inventory upload"}
    )


_EXPORT_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
_EXPORT_NON_IMAGE_EXTENSIONS = (".html", ".htm", ".php", ".asp", ".aspx", ".cfm", ".pdf", ".txt", ".xml")


def _is_public_https_image_url(value) -> bool:
    url = _str_val(value).lower()
    if not url.startswith("https://"):
        return False
    path = url.split("?")[0].split("#")[0]
    if path.endswith(_EXPORT_IMAGE_EXTENSIONS):
        return True
    if path.endswith(_EXPORT_NON_IMAGE_EXTENSIONS):
        return False
    # CDN heuristic: no file extension in the last path segment → accept.
    # Enrichment already validated these URLs via HEAD content-type check.
    last_segment = path.rstrip("/").rsplit("/", 1)[-1]
    return "." not in last_segment


def _is_exportable(row: dict) -> bool:
    if not (_is_included(row) and _str_val(row.get("Product Name"))):
        return False
    if _is_photo_only(row):
        return _is_public_https_image_url(row.get("Image URL"))
    return True


def _norm_dedupe_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _str_val(value).lower())


def _product_identity_key(row: dict) -> tuple[str, str] | None:
    brand = _norm_dedupe_token(row.get("Brand"))
    sku = _norm_dedupe_token(row.get("Model/SKU") or row.get("SKU") or row.get("Model"))
    if brand and sku:
        return brand, sku
    return None


def _is_unresolved_charge(row: dict) -> bool:
    return _str_val(row.get("Import Type")).lower() in {"unresolved_charge", "manual_review_charge"}


def _missing_identity_reason(row: dict) -> str:
    if _is_unresolved_charge(row):
        return ""
    missing = []
    if not _str_val(row.get("Brand")):
        missing.append("manufacturer")
    if not _str_val(row.get("Model/SKU")):
        missing.append("model")
    return "missing " + " and ".join(missing) if missing else ""


def _contamination_reason(row: dict) -> str:
    model = _str_val(row.get("Model/SKU"))
    text = " ".join(
        _str_val(row.get(field))
        for field in ("Product Name", "Model/SKU", "Brand", "Notes")
    )
    if model and (_PHONE_RE.search(model) or _EMAIL_RE.search(model)):
        return "phone/email captured as model"
    if _EMAIL_RE.search(text):
        return "email/header text detected"
    if _PHONE_RE.search(text) and not _product_identity_key(row):
        return "phone/header text detected"
    return ""


def _has_product_source(row: dict) -> bool:
    return bool(
        _first_url_from_fields(row, (
            "Product URL",
            "selected_product_url",
            "Dimension Source URL",
            "dimension_source_url",
            "spec_sheet_url",
            "image_source_url",
        ))
    )


def _readiness_row_score(row: dict) -> tuple[int, int, list[str]]:
    """Score Programa readiness on fields SCH needs after export cleanup."""
    checks: list[tuple[str, bool]] = [
        ("dimensions", has_complete_3d_dimensions(_programa_dimension_text(row))),
        ("image", _is_public_https_image_url(row.get("Image URL"))),
        ("product URL/source", _has_product_source(row)),
        ("SKU/model", bool(_str_val(row.get("Model/SKU") or row.get("SKU") or row.get("Model")))),
        ("supplier", bool(_str_val(row.get("Supplier")))),
        ("room/location", bool(_str_val(row.get("Room") or row.get("Location")))),
    ]
    passed = sum(1 for _name, ok in checks if ok)
    missing = [name for name, ok in checks if not ok]
    return passed, len(checks), missing


def _room_key(row: dict) -> str:
    return _section_key(_str_val(row.get("Room") or row.get("Location")))


def _is_pdf_url(url: object) -> bool:
    path = _str_val(url).split("?", 1)[0].split("#", 1)[0].lower()
    return path.endswith(".pdf")


def _product_url_rejection_reason(url: object, row: dict | None = None) -> str:
    raw = _str_val(url)
    if not raw:
        return ""
    try:
        parsed = re.sub(r"^www\.", "", raw.strip(), flags=re.IGNORECASE)
        from urllib.parse import parse_qs, urlparse

        url_parts = urlparse(parsed)
        path = url_parts.path.lower().strip("/")
        query = parse_qs(url_parts.query)
    except Exception:
        return "invalid product URL"

    model_norm = _norm_dedupe_token((row or {}).get("Model/SKU"))
    url_norm = _norm_dedupe_token(raw)
    if "sitemap" in path:
        return "sitemap/category URL rejected"
    if "search" in path or any(key.lower() in {"q", "query", "search"} for key in query):
        return "search URL rejected"
    if _URL_WEAK_PAGE_RE.search(f"/{path}/") and not (model_norm and model_norm in url_norm):
        return "category/browse URL rejected"
    return ""


def _dimension_rejection_reason(row: dict) -> str:
    return dimension_sanity_reason(
        row.get("Dimensions"),
        row.get("Product Category") or row.get("Section"),
    )


def _row_richness_score(row: dict) -> int:
    score = 0
    product_name = _str_val(row.get("Product Name"))
    source_type = _str_val(row.get("Source Type")).lower()
    if product_name:
        score += min(20, len(product_name))
        if product_name == product_name.title():
            score += 3
    for field, weight in (
        ("Brand", 10),
        ("Model/SKU", 10),
        ("Room", 10),
        ("Supplier", 8),
        ("Product Category", 8),
        ("Finish / Color", 6),
        ("Material", 6),
        ("Notes", 3),
    ):
        if _str_val(row.get(field)):
            score += weight
    if _str_val(row.get("Dimensions")) and not _dimension_rejection_reason(row):
        score += 16 if has_complete_3d_dimensions(_str_val(row.get("Dimensions"))) else 7
    if _is_public_https_image_url(row.get("Image URL")):
        score += 14
    if _str_val(row.get("Product URL")) and not _product_url_rejection_reason(row.get("Product URL"), row):
        score += 10
    if "enrich" in source_type or "ai" in source_type:
        score += 6
    return score


def _dedupe_rows_for_export(row_list: list[dict]) -> tuple[list[dict], list[dict]]:
    groups: dict[tuple[str, str], list[tuple[int, dict]]] = {}
    passthrough: list[tuple[int, dict]] = []
    for idx, row in enumerate(row_list):
        if not _is_included(row):
            passthrough.append((idx, row))
            continue
        key = _product_identity_key(row)
        if not key:
            passthrough.append((idx, row))
            continue
        groups.setdefault(key, []).append((idx, row))

    kept: list[tuple[int, dict]] = list(passthrough)
    removed: list[dict] = []
    for group_rows in groups.values():
        if len(group_rows) == 1:
            kept.extend(group_rows)
            continue
        nonblank_rooms = sorted({_room_key(row) for _, row in group_rows if _room_key(row)})
        buckets: dict[str, list[tuple[int, dict]]] = {}
        if len(nonblank_rooms) > 1:
            for idx, row in group_rows:
                room = _room_key(row)
                if not room:
                    best_room = max(
                        nonblank_rooms,
                        key=lambda key: max(
                            _row_richness_score(r)
                            for _, r in group_rows
                            if _room_key(r) == key
                        ),
                    )
                    room = best_room
                buckets.setdefault(room, []).append((idx, row))
        else:
            buckets.setdefault("", []).extend(group_rows)

        for bucket_rows in buckets.values():
            scored_rows = [
                (idx, row, _row_richness_score(row))
                for idx, row in bucket_rows
            ]
            best_score = max(score for _, _, score in scored_rows)
            best_rows = [(idx, row) for idx, row, score in scored_rows if score == best_score]

            # Preserve equally rich duplicate rows because they may represent intentional
            # repeated products and the ZIP exporter has long supported unique filenames
            # for that case. Only suppress weaker fallback/raw rows.
            if len(best_rows) == len(bucket_rows):
                kept.extend((idx, row) for idx, row, _ in scored_rows)
                continue

            best_idx, best_row = min(best_rows, key=lambda item: item[0])
            kept.append((best_idx, best_row))
            for idx, row, score in scored_rows:
                if idx == best_idx:
                    continue
                if score == best_score:
                    kept.append((idx, row))
                    continue
                removed.append({
                    "index": idx,
                    "product_name": _str_val(row.get("Product Name")) or "(unnamed)",
                    "brand": _str_val(row.get("Brand")),
                    "sku": _str_val(row.get("Model/SKU")),
                    "kept_index": best_idx,
                    "reason": "duplicate brand/SKU/location; kept richer row",
                })

    return [row for _, row in sorted(kept, key=lambda item: item[0])], removed


def _prepare_rows_for_programa_export(rows) -> tuple[list[dict], dict]:
    row_list = _to_row_list(rows)
    deduped, duplicates_removed = _dedupe_rows_for_export(row_list)
    suspicious_dimensions_rejected: list[dict] = []
    rejected_product_urls: list[dict] = []
    pdf_product_urls: list[dict] = []
    cleaned: list[dict] = []

    for idx, row in enumerate(deduped):
        next_row = dict(row)
        dim_reason = _dimension_rejection_reason(next_row)
        if dim_reason:
            suspicious_dimensions_rejected.append({
                "index": idx,
                "product_name": _str_val(next_row.get("Product Name")) or "(unnamed)",
                "brand": _str_val(next_row.get("Brand")),
                "sku": _str_val(next_row.get("Model/SKU")),
                "dimensions": _str_val(next_row.get("Dimensions")),
                "reason": dim_reason,
            })
            next_row["Dimensions"] = ""
            next_row["Width (in)"] = ""
            next_row["Height (in)"] = ""
            next_row["Depth (in)"] = ""
            next_row["Length (in)"] = ""
            next_row["rejected_dimensions_reason"] = dim_reason

        product_url = _str_val(next_row.get("Product URL"))
        url_reason = _product_url_rejection_reason(product_url, next_row)
        if url_reason:
            rejected_product_urls.append({
                "index": idx,
                "product_name": _str_val(next_row.get("Product Name")) or "(unnamed)",
                "brand": _str_val(next_row.get("Brand")),
                "sku": _str_val(next_row.get("Model/SKU")),
                "url": product_url,
                "reason": url_reason,
            })
            next_row["Product URL"] = ""
        elif _is_pdf_url(product_url):
            pdf_product_urls.append({
                "index": idx,
                "product_name": _str_val(next_row.get("Product Name")) or "(unnamed)",
                "brand": _str_val(next_row.get("Brand")),
                "sku": _str_val(next_row.get("Model/SKU")),
                "url": product_url,
                "reason": "raw PDF/spec sheet used as Product URL",
            })
        cleaned.append(next_row)

    diagnostics = {
        "duplicates_removed": duplicates_removed,
        "suspicious_dimensions_rejected": suspicious_dimensions_rejected,
        "rejected_product_urls": rejected_product_urls,
        "pdf_product_urls": pdf_product_urls,
    }
    return cleaned, diagnostics


def _quantity_value(value):
    text = _str_val(value)
    if not text:
        return ""
    try:
        numeric = float(text)
        return int(numeric) if numeric.is_integer() else numeric
    except ValueError:
        return text


def _canonical_sku_display(value: object) -> str:
    """Normalize accidental SKU whitespace while preserving meaningful punctuation."""
    text = _str_val(value)
    if not text:
        return ""
    return re.sub(r"\s+", "", text)


def _row_to_programa_dict(row: dict) -> dict:
    """Map one internal intake row to Programa's import columns."""
    raw_notes = _str_val(row.get("Notes"))
    material = _str_val(row.get("Material")) or _extract_material_from_notes(raw_notes)
    row = dict(row)
    row["Notes"] = _append_dimension_context_note(raw_notes, row)
    row = append_source_links_to_notes(row)
    dimensions = _programa_dimension_text(row)
    parts = extract_labeled_dimensions(dimensions)
    finish_color = _str_val(row.get("Finish / Color"))
    color = _str_val(row.get("Color"))

    return {
        "Section": normalize_section(row.get("Product Category") or row.get("Section")),
        "Product Name": _str_val(row.get("Product Name")),
        "Brand": _str_val(row.get("Brand")),
        "SKU": _canonical_sku_display(row.get("Model/SKU")),
        "Model": "",
        "Dimensions": dimensions,
        "Width (in)": _str_val(parts.get("width")),
        "Height (in)": _str_val(parts.get("height")),
        "Depth (in)": _str_val(parts.get("depth")),
        "Length (in)": "",
        "Quantity": _quantity_value(row.get("Quantity")),
        "Price": _str_val(row.get("Price")),
        "Supplier": _str_val(row.get("Supplier")),
        "Product URL": "" if _product_url_rejection_reason(row.get("Product URL"), row) else _str_val(row.get("Product URL")),
        "Image URL": _str_val(row.get("Image URL")) if _is_public_https_image_url(row.get("Image URL")) else "",
        "Finish": finish_color,
        "Color": color,
        "Material": material,
        "Lead Time": _str_val(row.get("Lead Time")),
        "Notes": _str_val(row.get("Notes")),
        "Location": _str_val(row.get("Room")),
    }


def validate_for_export(rows) -> dict:
    """
    Return a validation summary dict without modifying source rows.

    Photo-only rows require Product Name, Product Category/Section, and Image URL.
    Standard rows require Product Name to appear in the export.
    """
    source_rows = _to_row_list(rows)
    row_list, export_diagnostics = _prepare_rows_for_programa_export(rows)
    included = [r for r in row_list if _is_included(r)]

    skipped: list[dict] = []
    blank_price_only_rows: list[dict] = []
    missing_model_manufacturer: list[dict] = []
    phone_email_header_contamination: list[dict] = []
    missing_section: list[dict] = []
    missing_dimensions = 0
    missing_product_url = 0
    missing_image_url = 0
    image_url_present = 0
    image_url_total = 0
    export_count = 0
    programa_ready_count = 0
    readiness_points = 0
    readiness_total = 0
    readiness_missing_fields: dict[str, int] = {}
    section_counts: dict[str, int] = {}
    section_equals_product_name: list[dict] = []
    section_too_long: list[dict] = []
    needs_enrichment: list[dict] = []

    for original_index, source_row in enumerate(source_rows):
        if _is_unresolved_charge(source_row):
            blank_price_only_rows.append({
                "index": original_index,
                "price": _str_val(source_row.get("Price")),
                "reason": "blank/price-only quote row",
            })
        identity_reason = _missing_identity_reason(source_row)
        if identity_reason and _is_included(source_row):
            missing_model_manufacturer.append({
                "index": original_index,
                "product_name": _str_val(source_row.get("Product Name")) or "(unnamed)",
                "brand": _str_val(source_row.get("Brand")),
                "sku": _str_val(source_row.get("Model/SKU")),
                "reason": identity_reason,
            })
        contamination_reason = _contamination_reason(source_row)
        if contamination_reason:
            phone_email_header_contamination.append({
                "index": original_index,
                "product_name": _str_val(source_row.get("Product Name")) or "(unnamed)",
                "brand": _str_val(source_row.get("Brand")),
                "sku": _str_val(source_row.get("Model/SKU")),
                "reason": contamination_reason,
            })

    for i, row in enumerate(included):
        name = _str_val(row.get("Product Name"))
        if not name:
            skipped.append({"index": i, "product_name": "(no name)"})
            continue
        image_url_total += 1
        if _is_public_https_image_url(row.get("Image URL")):
            image_url_present += 1
        raw_section = _str_val(row.get("Product Category") or row.get("Section"))
        section = normalize_section(raw_section)
        if _is_photo_only(row) and not _is_public_https_image_url(row.get("Image URL")):
            missing_image_url += 1
            skipped.append({"index": i, "product_name": name, "reason": "missing or invalid Image URL"})
            continue

        export_count += 1
        section_counts[section] = section_counts.get(section, 0) + 1
        if not raw_section:
            missing_section.append({"index": i, "product_name": name})
        if raw_section and _section_key(raw_section) == _section_key(name):
            section_equals_product_name.append({"index": i, "product_name": name, "section": raw_section})
        if len(raw_section) > 30:
            section_too_long.append({"index": i, "product_name": name, "section": raw_section})
        row_missing_dimensions = not has_complete_3d_dimensions(_programa_dimension_text(row))
        row_missing_image = not _is_public_https_image_url(row.get("Image URL"))
        if row_missing_dimensions:
            missing_dimensions += 1
        if not _str_val(row.get("Product URL")):
            missing_product_url += 1
        if _str_val(row.get("Image URL")) and row_missing_image:
            missing_image_url += 1
        elif not _str_val(row.get("Image URL")):
            missing_image_url += 1
        if not _is_photo_only(row) and (row_missing_dimensions or row_missing_image):
            reason_parts = []
            if row_missing_dimensions:
                reason_parts.append("missing full W x H x D dimensions")
            if row_missing_image:
                reason_parts.append("missing or invalid Image URL")
            needs_enrichment.append({"index": i, "product_name": name, "reason": "; ".join(reason_parts)})
        else:
            programa_ready_count += 1
        row_points, row_total, row_missing = _readiness_row_score(row)
        readiness_points += row_points
        readiness_total += row_total
        for field_name in row_missing:
            readiness_missing_fields[field_name] = readiness_missing_fields.get(field_name, 0) + 1

    readiness_score = round((readiness_points / readiness_total) * 100) if readiness_total else 0
    if not included:
        readiness_status = "Manual review"
    elif missing_dimensions or missing_image_url:
        readiness_status = "Needs enrichment"
    elif programa_ready_count == len(included) and (missing_product_url or missing_section):
        readiness_status = "Ready with warnings"
    elif programa_ready_count == len(included):
        readiness_status = "Ready"
    else:
        readiness_status = "Manual review"

    return {
        "skipped": skipped,
        "missing_section": missing_section,
        "missing_dimensions": missing_dimensions,
        "missing_product_url": missing_product_url,
        "missing_image_url": missing_image_url,
        "image_url_present": image_url_present,
        "image_url_total": image_url_total,
        "export_count": export_count,
        "programa_ready_count": programa_ready_count,
        "needs_enrichment": needs_enrichment,
        "unique_sections": sorted(section_counts),
        "section_counts": dict(sorted(section_counts.items())),
        "section_equals_product_name": section_equals_product_name,
        "section_too_long": section_too_long,
        "too_many_unique_sections": len(section_counts) > len(CANONICAL_SECTIONS),
        "canonical_sections": CANONICAL_SECTIONS,
        "duplicates_removed": export_diagnostics["duplicates_removed"],
        "duplicate_rows_removed": len(export_diagnostics["duplicates_removed"]),
        "suspicious_dimensions_rejected": export_diagnostics["suspicious_dimensions_rejected"],
        "rejected_product_urls": export_diagnostics["rejected_product_urls"],
        "pdf_product_urls": export_diagnostics["pdf_product_urls"],
        "blank_price_only_rows": blank_price_only_rows,
        "missing_model_manufacturer": missing_model_manufacturer,
        "phone_email_header_contamination": phone_email_header_contamination,
        "parsed_rows_count": len(source_rows),
        "export_rows_count": export_count,
        "readiness_score": readiness_score,
        "readiness_status": readiness_status,
        "readiness_missing_fields": dict(sorted(readiness_missing_fields.items())),
    }


def build_programa_import_dataframe(rows) -> pd.DataFrame:
    """Transform included intake rows with Product Name into a Programa import DataFrame."""
    row_list, _diagnostics = _prepare_rows_for_programa_export(rows)
    records = [
        _row_to_programa_dict(r)
        for r in row_list
        if _is_exportable(r)
    ]
    if not records:
        return pd.DataFrame(columns=PROGRAMA_COLUMNS)
    return pd.DataFrame(records, columns=PROGRAMA_COLUMNS)


def build_programa_debug_dataframe(rows) -> pd.DataFrame:
    """Build Programa import rows with debug/confidence columns appended."""
    row_list, _diagnostics = _prepare_rows_for_programa_export(rows)
    records = []
    for r in row_list:
        if not _is_exportable(r):
            continue
        mapped = _row_to_programa_dict(r)
        for col in _DEBUG_EXTRA_COLUMNS:
            mapped[col] = _str_val(r.get(col))
        records.append(mapped)
    if not records:
        return pd.DataFrame(columns=PROGRAMA_COLUMNS + _DEBUG_EXTRA_COLUMNS)
    return pd.DataFrame(records, columns=PROGRAMA_COLUMNS + _DEBUG_EXTRA_COLUMNS)


def export_programa_csv(df: pd.DataFrame) -> bytes:
    """Serialize a Programa import DataFrame to UTF-8 CSV bytes."""
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def export_programa_xlsx(df: pd.DataFrame) -> bytes:
    """Serialize a Programa import DataFrame to XLSX bytes."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Programa Import")
        ws = writer.book["Programa Import"]
        headers = [cell.value for cell in ws[1]]
        hyperlink_columns = {"Product URL", "Image URL", "Notes"}
        for header in hyperlink_columns:
            if header not in headers:
                continue
            col_idx = headers.index(header) + 1
            for row_idx in range(2, ws.max_row + 1):
                cell = ws.cell(row_idx, col_idx)
                url = _first_url(cell.value)
                if not url:
                    continue
                cell.hyperlink = url
                cell.style = "Hyperlink"
    return buf.getvalue()


_MANIFEST_COLUMNS: list[str] = [
    "Product Name",
    "Brand",
    "SKU/Model",
    "Product URL",
    "Original Image URL",
    "Local Image Filename",
    "Image Status",
    "Error",
]


def export_programa_zip(
    rows,
    include_images: bool = True,
    manual_images: dict | None = None,
) -> bytes:
    """
    Build a ZIP archive for the Programa export.

    Contents
    --------
    programa_import.csv  — the standard 21-column Programa import file
    images/              — one .jpg per successfully downloaded/uploaded product image
    manifest.csv         — per-row image status: URL, filename, status, errors

    Parameters
    ----------
    rows : list[dict] | pd.DataFrame
        Intake rows (same format accepted by build_programa_import_dataframe).
    include_images : bool
        If False, skip all image processing (manifest records "missing_image_url").
    manual_images : dict[int, bytes] | None
        Manually uploaded JPEG bytes keyed by 0-based index in `rows`.
        Manual images take priority over remote URL download.
        Status in manifest will be "manually_uploaded".
    """
    from src.image_assets import download_and_convert_image as _download_convert, build_image_filename

    manual_images = manual_images or {}
    source_row_list = _to_row_list(rows)
    row_list, _diagnostics = _prepare_rows_for_programa_export(source_row_list)

    df = build_programa_import_dataframe(rows)
    csv_bytes = export_programa_csv(df)

    # Track seen filenames for deduplication: "wolf_mdd30ts.jpg" -> count
    seen_filenames: dict[str, int] = {}

    def _unique_filename(filename: str) -> str:
        if filename not in seen_filenames:
            seen_filenames[filename] = 1
            return filename
        seen_filenames[filename] += 1
        if "." in filename:
            stem, ext = filename.rsplit(".", 1)
            return f"{stem}_{seen_filenames[filename]}.{ext}"
        return f"{filename}_{seen_filenames[filename]}"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("programa_import.csv", csv_bytes.decode("utf-8"))

        manifest_rows: list[dict] = []
        for i, r in enumerate(row_list):
            if not _is_exportable(r):
                continue

            image_url = _str_val(r.get("Image URL"))
            brand = _str_val(r.get("Brand"))
            sku = _str_val(r.get("Model/SKU"))
            product_name = _str_val(r.get("Product Name"))

            manifest_row: dict = {
                "Product Name": product_name,
                "Brand": brand,
                "SKU/Model": sku,
                "Product URL": _str_val(r.get("Product URL")),
                "Original Image URL": image_url,
                "Local Image Filename": "",
                "Image Status": "missing_image_url",
                "Error": "",
            }

            if include_images:
                manual_bytes = manual_images.get(i)
                if manual_bytes:
                    filename = _unique_filename(build_image_filename(brand, sku, product_name))
                    manifest_row["Local Image Filename"] = filename
                    manifest_row["Image Status"] = "manually_uploaded"
                    zf.writestr(f"images/{filename}", manual_bytes)
                elif _is_public_https_image_url(image_url):
                    result = _download_convert(
                        image_url,
                        brand=brand,
                        model_sku=sku,
                        product_name=product_name,
                    )
                    manifest_row["Image Status"] = result["image_status"]
                    manifest_row["Error"] = result.get("error", "")
                    if result["image_status"] == "downloaded" and result.get("jpeg_bytes"):
                        filename = _unique_filename(result["local_image_filename"])
                        manifest_row["Local Image Filename"] = filename
                        zf.writestr(f"images/{filename}", result["jpeg_bytes"])

            manifest_rows.append(manifest_row)

        if manifest_rows:
            manifest_df = pd.DataFrame(manifest_rows, columns=_MANIFEST_COLUMNS)
            mbuf = io.StringIO()
            manifest_df.to_csv(mbuf, index=False)
            zf.writestr("manifest.csv", mbuf.getvalue())

    return buf.getvalue()
