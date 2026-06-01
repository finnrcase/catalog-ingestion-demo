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
_URL_WEAK_PAGE_RE = re.compile(
    r"(^|/)(?:sitemap|product-sitemap|search|category|categories|collections?|browse|tag|blog|support)(?:/|$)",
    re.IGNORECASE,
)

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
    text = re.sub(r"\s{2,}", " ", text).strip()
    return remove_notes_row_prefix(text)


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


def _row_to_programa_dict(row: dict) -> dict:
    """Map one internal intake row to Programa's import columns."""
    dimensions = "" if _dimension_rejection_reason(row) else _str_val(row.get("Dimensions"))
    parts = extract_labeled_dimensions(dimensions)
    finish_color = _str_val(row.get("Finish / Color"))
    color = _str_val(row.get("Color"))
    material = _str_val(row.get("Material")) or _extract_material_from_notes(_str_val(row.get("Notes")))

    return {
        "Section": normalize_section(row.get("Product Category") or row.get("Section")),
        "Product Name": _str_val(row.get("Product Name")),
        "Brand": _str_val(row.get("Brand")),
        "SKU": _str_val(row.get("Model/SKU")),
        "Model": "",
        "Dimensions": dimensions,
        "Width (in)": _str_val(parts.get("width")),
        "Height (in)": _str_val(parts.get("height")),
        "Depth (in)": _str_val(parts.get("depth")),
        "Length (in)": _str_val(parts.get("length")),
        "Quantity": _quantity_value(row.get("Quantity")),
        "Price": _str_val(row.get("Price")),
        "Supplier": _str_val(row.get("Supplier")),
        "Product URL": "" if _product_url_rejection_reason(row.get("Product URL"), row) else _str_val(row.get("Product URL")),
        "Image URL": _str_val(row.get("Image URL")) if _is_public_https_image_url(row.get("Image URL")) else "",
        "Finish": finish_color,
        "Color": color,
        "Material": material,
        "Lead Time": _str_val(row.get("Lead Time")),
        "Notes": _clean_notes(_str_val(row.get("Notes"))),
        "Location": _str_val(row.get("Room")),
    }


def validate_for_export(rows) -> dict:
    """
    Return a validation summary dict without modifying source rows.

    Photo-only rows require Product Name, Product Category/Section, and Image URL.
    Standard rows require Product Name to appear in the export.
    """
    row_list, export_diagnostics = _prepare_rows_for_programa_export(rows)
    included = [r for r in row_list if _is_included(r)]

    skipped: list[dict] = []
    missing_section: list[dict] = []
    missing_dimensions = 0
    missing_product_url = 0
    missing_image_url = 0
    image_url_present = 0
    image_url_total = 0
    export_count = 0
    section_counts: dict[str, int] = {}
    section_equals_product_name: list[dict] = []
    section_too_long: list[dict] = []

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
        if not has_complete_3d_dimensions(_str_val(row.get("Dimensions"))):
            missing_dimensions += 1
        if not _str_val(row.get("Product URL")):
            missing_product_url += 1
        if _str_val(row.get("Image URL")) and not _is_public_https_image_url(row.get("Image URL")):
            missing_image_url += 1
        elif not _str_val(row.get("Image URL")):
            missing_image_url += 1

    return {
        "skipped": skipped,
        "missing_section": missing_section,
        "missing_dimensions": missing_dimensions,
        "missing_product_url": missing_product_url,
        "missing_image_url": missing_image_url,
        "image_url_present": image_url_present,
        "image_url_total": image_url_total,
        "export_count": export_count,
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
