"""
Canonical data model for SCH DesignOps Intake.

All column names, category options, status values, source-type constants,
and the base-row factory live here.  Every other module imports from this
file — changing the schema in one place keeps the app consistent.
"""

# ── Column definitions ─────────────────────────────────────────────────────────
# Internal column names used throughout the app and in CSV exports.
# Display labels shown in the review table are set separately in app.py's
# data_editor column_config so they can differ without renaming the data.

ALL_COLUMNS: list[str] = [
    "Include",
    "Project",
    "Room",             # display label → "Location"
    "Product Name",
    "Brand",
    "Dimensions",
    "Finish / Color",
    "Color",
    "Material",
    "Model/SKU",        # display label → "Serial / Model Number"
    "Product Category",
    "Quantity",
    "Price",
    "Supplier",         # display label → "Who We Bought It From"
    "Product URL",
    "Notes",
    "Source Type",
    "Status",
    "Import Type",
    "photo_only",
    "AI Category Confidence",
    "Category Source",
    "Image URL",
    "Local Image Path",
    "Image Filename",
    "Image Upload Status",
    "Dimension Source URL",
    "Dimension Confidence",
    "Dimension Source Type",
    "Dimension Lookup Status",
]

# ── Category options ───────────────────────────────────────────────────────────
# Title-case list shared by the manual-entry form, data editor, and AI prompt.

CATEGORIES: list[str] = [
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
    "Paint/Wallpaper",
    "Stone/Tile",
    "Seating",
    "Tables",
    "Gym Equipment",
    "Fabrics/Pillows",
    "Rugs",
    "Mirrors",
    "Beds/Mattresses",
    "Dressers/Drawers/Storage",
    "Accessories",
    "Art",
    "Artwork",
    "Bedding/Linens/Bath Linens",
]

# ── Status options ─────────────────────────────────────────────────────────────

STATUSES: list[str] = [
    "Needs Review",
    "Needs Enrichment",
    "Pending Extraction",
    "Ready for Review",
    "Ready for Programa",
    "Ignored",
    "Excluded",
]

# ── Source type constants ──────────────────────────────────────────────────────

SOURCE_MANUAL = "Manual"
SOURCE_URL    = "URL"
SOURCE_PDF    = "PDF"
SOURCE_PDF_AI = "PDF_AI"
SOURCE_PHOTO  = "Photo"

INTERNAL_IMAGE_COLUMNS: list[str] = [
    "Image URL",
    "Local Image Path",
    "Image Filename",
    "Image Upload Status",
]

# ── Important fields for confidence scoring ────────────────────────────────────
# A row missing any of these is flagged for review (unless it has a Model/SKU
# that can later be used to enrich the missing data).

IMPORTANT_FIELDS: list[str] = [
    "Product Name",
    "Brand",
    "Dimensions",
    "Quantity",
    "Supplier",
    "Room",
    "Product Category",
]

# ── Base row factory ───────────────────────────────────────────────────────────


def make_base_row(
    project: str = "",
    room: str = "",
    supplier: str = "",
    notes: str = "",
) -> dict:
    """
    Return a blank row dict with every column at its default value.
    Callers fill in the source-specific fields after calling this.

    Internal `_source_*` fields (added Phase 1 image recovery, 2026-05-08)
    record which uploaded PDF and page a row was parsed from. They are
    intentionally NOT in ALL_COLUMNS so the standard Programa CSV/XLSX
    export stays clean. They surface only in the debug export and in the
    manifest CSV inside the Programa ZIP.
    """
    base = {
        "Include":          True,
        "Project":          project,
        "Room":             room,
        "Product Name":     "",
        "Brand":            "",
        "Dimensions":       "",
        "Finish / Color":   "",
        "Color":            "",
        "Material":         "",
        "Model/SKU":        "",
        "Product Category": "",
        "Quantity":         1,
        "Price":            "",
        "Supplier":         supplier,
        "Product URL":      "",
        "Notes":            notes,
        "Source Type":           "",
        "Status":                "",
        "Import Type":           "",
        "photo_only":            False,
        "AI Category Confidence": 0,
        "Category Source":        "Unknown",
        "Image URL":              "",
        "Local Image Path":       "",
        "Image Filename":         "",
        "Image Upload Status":    "",
        "Dimension Source URL":   "",
        "Dimension Confidence":   "",
        "Dimension Source Type":  "",
        "Dimension Lookup Status": "",
    }
    base["_source_pdf_id"] = ""
    base["_source_page_number"] = None
    base["_source_filename"] = ""
    return base
