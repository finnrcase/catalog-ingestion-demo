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
    "original_image_url",
    "cloudinary_url",
    "image_confidence",
    "image_source_url",
    "image_candidate_diagnostics",
    "cloudinary_status",
    "cloudinary_error",
    "programa_image_ready",
    "Dimension Source URL",
    "Dimension Confidence",
    "Dimension Source Type",
    "Dimension Lookup Status",
    "Width (in)",
    "Height (in)",
    "Depth (in)",
    "Length (in)",
    "skipped_by_source_type",
    "skipped_by_missing_brand",
    "skipped_by_missing_model",
    "cache_hit",
    "cache_had_blank_dimensions",
    "cache_had_blank_image",
    "fresh_extraction_forced",
    "product_url",
    "product_url_confidence",
    "page_fetch_attempted",
    "page_fetch_success",
    "spec_table_found",
    "json_ld_found",
    "next_data_found",
    "shopify_json_found",
    "spec_pdf_links_found",
    "spec_pdf_fetched",
    "dimensions_before_enrichment",
    "dimensions_extracted",
    "dimension_confidence",
    "dimension_source",
    "dimension_source_url",
    "dimension_parse_method",
    "partial_dimensions_found",
    "rejected_dimensions_reason",
    "final_dimensions",
    "final_dimension_writeback_success",
    "skipped_reason",
    "budget_blocked",
    "deterministic_product_name",
    "ai_product_name",
    "final_product_name",
    "deterministic_supplier",
    "ai_supplier",
    "final_supplier",
    "deterministic_model_sku",
    "ai_model_sku",
    "final_model_sku",
    "deterministic_dimensions",
    "ai_dimensions",
    "ai_used",
    "ai_skipped_reason",
    "parse_confidence",
    "missing_critical_fields_before_ai",
    "missing_critical_fields_after_ai",
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
    "original_image_url",
    "cloudinary_url",
    "image_confidence",
    "image_source_url",
    "image_candidate_diagnostics",
    "cloudinary_status",
    "cloudinary_error",
    "programa_image_ready",
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
    """
    return {
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
        "original_image_url":      "",
        "cloudinary_url":          "",
        "image_confidence":        "",
        "image_source_url":        "",
        "image_candidate_diagnostics": "",
        "cloudinary_status":       "",
        "cloudinary_error":        "",
        "programa_image_ready":    False,
        "Dimension Source URL":   "",
        "Dimension Confidence":   "",
        "Dimension Source Type":  "",
        "Dimension Lookup Status": "",
        "Width (in)":             "",
        "Height (in)":            "",
        "Depth (in)":             "",
        "Length (in)":            "",
        "skipped_by_source_type":  False,
        "skipped_by_missing_brand": False,
        "skipped_by_missing_model": False,
        "cache_hit":               "",
        "cache_had_blank_dimensions": False,
        "cache_had_blank_image":    False,
        "fresh_extraction_forced":  False,
        "product_url":            "",
        "product_url_confidence": "",
        "page_fetch_attempted":   False,
        "page_fetch_success":     False,
        "spec_table_found":       False,
        "json_ld_found":          False,
        "next_data_found":        False,
        "shopify_json_found":     False,
        "spec_pdf_links_found":   0,
        "spec_pdf_fetched":       False,
        "dimensions_before_enrichment": "",
        "dimensions_extracted":   "",
        "dimension_confidence":   "",
        "dimension_source":       "",
        "dimension_source_url":   "",
        "dimension_parse_method": "",
        "partial_dimensions_found": "",
        "rejected_dimensions_reason": "",
        "final_dimensions":       "",
        "final_dimension_writeback_success": False,
        "skipped_reason":         "",
        "budget_blocked":         False,
        "deterministic_product_name": "",
        "ai_product_name":        "",
        "final_product_name":     "",
        "deterministic_supplier": "",
        "ai_supplier":            "",
        "final_supplier":         "",
        "deterministic_model_sku": "",
        "ai_model_sku":           "",
        "final_model_sku":        "",
        "deterministic_dimensions": "",
        "ai_dimensions":          "",
        "ai_used":                False,
        "ai_skipped_reason":      "",
        "parse_confidence":       "",
        "missing_critical_fields_before_ai": "",
        "missing_critical_fields_after_ai": "",
    }
