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

_DEBUG_EXTRA_COLUMNS: list[str] = [
    "Confidence Score",
    "Source Type",
    "AI Category Confidence",
    "Category Source",
    "Local Image Path",
]

_MATERIAL_TAG_RE = re.compile(r"\[Materials:\s*([^\]]+)\]", re.IGNORECASE)
_SYSTEM_TAG_RE = re.compile(r"\[[^\]]*\]")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _str_val(v) -> str:
    """Safely coerce a cell value to a stripped string, treating None/NaN as blank."""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"nan", "none", "null"} else s


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


def _is_exportable(row: dict) -> bool:
    if not (_is_included(row) and _str_val(row.get("Product Name"))):
        return False
    if _is_photo_only(row):
        return bool(_str_val(row.get("Product Category")) and _str_val(row.get("Image URL")))
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
        "Section": _str_val(row.get("Product Category")) or "General",
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
        "Image URL": _str_val(row.get("Image URL")),
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
    export_count = 0

    for i, row in enumerate(included):
        name = _str_val(row.get("Product Name"))
        if not name:
            skipped.append({"index": i, "product_name": "(no name)"})
            continue
        if _is_photo_only(row) and (
            not _str_val(row.get("Product Category")) or not _str_val(row.get("Image URL"))
        ):
            skipped.append({"index": i, "product_name": name})
            continue

        export_count += 1
        if not _str_val(row.get("Product Category")):
            missing_section.append({"index": i, "product_name": name})
        if not has_complete_3d_dimensions(_str_val(row.get("Dimensions"))):
            missing_dimensions += 1
        if not _str_val(row.get("Product URL")):
            missing_product_url += 1
        if not _str_val(row.get("Image URL")):
            missing_image_url += 1

    return {
        "skipped": skipped,
        "missing_section": missing_section,
        "missing_dimensions": missing_dimensions,
        "missing_product_url": missing_product_url,
        "missing_image_url": missing_image_url,
        "export_count": export_count,
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
    """Build Programa import rows with debug/confidence columns appended."""
    records = []
    for r in _to_row_list(rows):
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
