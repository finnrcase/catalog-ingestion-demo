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

from src.dimensions import extract_labeled_dimensions, has_complete_3d_dimensions
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
    # Phase 1 image recovery internal source metadata
    "_source_pdf_id",
    "_source_page_number",
    "_source_filename",
    # Phase 1 image recovery confidence/evidence
    "image_source",
    "confidence",
    "evidence",
    "needs_image_review",
]

_MATERIAL_TAG_RE = re.compile(r"\[Materials:\s*([^\]]+)\]", re.IGNORECASE)
_SYSTEM_TAG_RE = re.compile(r"\[[^\]]*\]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

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
    dimensions = _str_val(row.get("Dimensions"))
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
        "Product URL": _str_val(row.get("Product URL")),
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
    row_list = _to_row_list(rows)
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
    }


def build_programa_import_dataframe(rows) -> pd.DataFrame:
    """Transform included intake rows with Product Name into a Programa import DataFrame."""
    records = [
        _row_to_programa_dict(r)
        for r in _to_row_list(rows)
        if _is_exportable(r)
    ]
    if not records:
        return pd.DataFrame(columns=PROGRAMA_COLUMNS)
    return pd.DataFrame(records, columns=PROGRAMA_COLUMNS)


def build_programa_debug_dataframe(rows) -> pd.DataFrame:
    """Build Programa import rows with debug/confidence columns appended.

    Internal ``_source_*`` columns preserve their original Python type (int,
    str, …) so callers can distinguish unset (None) from numeric zero.
    All other debug columns are coerced to stripped strings via ``_str_val``.
    """
    records = []
    for r in _to_row_list(rows):
        if not _is_exportable(r):
            continue
        mapped = _row_to_programa_dict(r)
        for col in _DEBUG_EXTRA_COLUMNS:
            if col.startswith("_source_"):
                # Preserve raw type (int page numbers, etc.)
                mapped[col] = r.get(col)
            else:
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
    "Image Source",
    "Confidence",
    "Evidence",
    "Needs Image Review",
    "Error",
]


def export_programa_zip(
    rows,
    include_images: bool = True,
    manual_images: dict | None = None,
    session_id: str | None = None,
) -> bytes:
    """
    Build a ZIP archive for the Programa export.

    Image-resolution priority per row:
      1. local_image_path (validated under .tmp/uploads/{session_id}/images/, .jpg)
      2. manual_images[i] bytes
      3. row's Image URL (download via download_and_convert_image)
      4. otherwise: status "missing_image_url"

    LOW-confidence rows are never copied to images/, even if local_image_path
    is set; manifest records "low_confidence_skipped".
    """
    from src.image_assets import download_and_convert_image as _download_convert, build_image_filename

    manual_images = manual_images or {}
    row_list = _to_row_list(rows)

    df = build_programa_import_dataframe(rows)
    csv_bytes = export_programa_csv(df)

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

    def _validate_local_path(path_str: str) -> tuple[bool, str]:
        """Return (ok, reason). Reason is empty when ok."""
        if not session_id:
            return False, "no_session_id"
        try:
            from pathlib import Path as _P
            p = _P(path_str).resolve()
        except Exception:
            return False, "invalid_path"
        if not p.exists():
            return False, "file_not_found"
        if p.suffix.lower() not in (".jpg", ".jpeg"):
            return False, "wrong_extension"
        if p.stat().st_size <= 0:
            return False, "empty_file"
        try:
            allowed_root = (_P(".tmp") / "uploads" / session_id / "images").resolve()
        except Exception:
            return False, "invalid_session_dir"
        try:
            p.relative_to(allowed_root)
        except ValueError:
            return False, "path_outside_session_dir"
        return True, ""

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
            confidence = _str_val(r.get("confidence")).upper()
            local_path = _str_val(r.get("local_image_path"))
            local_filename = _str_val(r.get("local_image_filename"))

            # needs_image_review is stored as string "True"/"False" by image_recovery
            # (Task 7 contract — pandas string dtype constraint). Compare against the
            # string explicitly; do not call bool() on it (bool("False") is True).
            _nir_raw = str(r.get("needs_image_review", "")).strip().lower()

            manifest_row: dict = {
                "Product Name": product_name,
                "Brand": brand,
                "SKU/Model": sku,
                "Product URL": _str_val(r.get("Product URL")),
                "Original Image URL": image_url,
                "Local Image Filename": "",
                "Image Status": "missing_image_url",
                "Image Source": _str_val(r.get("image_source")),
                "Confidence": confidence,
                "Evidence": _str_val(r.get("evidence")),
                "Needs Image Review": "false" if _nir_raw == "false" else "true",
                "Error": "",
            }

            if not include_images:
                manifest_rows.append(manifest_row)
                continue

            # LOW-confidence rows are never written to images/.
            if confidence == "LOW":
                manifest_row["Image Status"] = "low_confidence_skipped"
                manifest_rows.append(manifest_row)
                continue

            wrote_image = False

            # 1) local_image_path
            if local_path:
                ok, reason = _validate_local_path(local_path)
                if ok:
                    filename = _unique_filename(local_filename or build_image_filename(brand, sku, product_name))
                    from pathlib import Path as _P
                    manifest_row["Local Image Filename"] = filename
                    manifest_row["Image Status"] = "downloaded"
                    zf.writestr(f"images/{filename}", _P(local_path).read_bytes())
                    wrote_image = True
                else:
                    manifest_row["Image Status"] = "invalid_local_path"
                    manifest_row["Error"] = reason

            # 2) manual_images
            if not wrote_image and manual_images.get(i):
                filename = _unique_filename(build_image_filename(brand, sku, product_name))
                manifest_row["Local Image Filename"] = filename
                manifest_row["Image Status"] = "manually_uploaded"
                manifest_row["Error"] = ""
                zf.writestr(f"images/{filename}", manual_images[i])
                wrote_image = True

            # 3) remote URL download
            if not wrote_image and _is_public_https_image_url(image_url):
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
