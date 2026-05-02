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
