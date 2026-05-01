import pandas as pd

from src.intake_schema import (
    ALL_COLUMNS,
    SOURCE_MANUAL,
    SOURCE_PDF,
    SOURCE_PHOTO,
    SOURCE_URL,
    make_base_row,
)

# Re-export so existing code that does `from src.intake import COLUMNS` still works.
COLUMNS = ALL_COLUMNS


# ── Row factories ──────────────────────────────────────────────────────────────


def create_url_rows(
    urls: list[str], project: str, room: str, supplier: str, notes: str
) -> list[dict]:
    rows = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        row = make_base_row(project, room, supplier, notes)
        row["Product URL"] = url
        row["Source Type"] = SOURCE_URL
        row["Status"] = "Needs Review"
        rows.append(row)
    return rows


def create_pdf_rows(
    pdf_files: list, project: str, room: str, supplier: str, notes: str
) -> list[dict]:
    rows = []
    for pdf in pdf_files:
        row = make_base_row(project, room, supplier, notes)
        clean_name = pdf.name.removesuffix(".pdf").replace("_", " ").replace("-", " ")
        row["Product Name"] = clean_name
        file_note = f"Source: {pdf.name}"
        row["Notes"] = f"{file_note} | {notes}" if notes else file_note
        row["Source Type"] = SOURCE_PDF
        row["Status"] = "Pending Extraction"
        rows.append(row)
    return rows


def create_photo_rows(
    photo_files: list[dict],
    project: str,
    room: str,
) -> list[dict]:
    """Create one blank, photo-only product row per uploaded image."""
    rows = []
    for photo in photo_files:
        row = make_base_row(project, room, "", "")
        row.update({
            "Product Name": "",
            "Brand": "",
            "Dimensions": "",
            "Finish / Color": "",
            "Model/SKU": "",
            "Product Category": "",
            "Quantity": None,
            "Price": "",
            "Supplier": "",
            "Product URL": "",
            "Notes": "",
            "Source Type": SOURCE_PHOTO,
            "Import Type": "Photo Upload",
            "photo_only": True,
            "Status": "Needs Review",
            "Image URL": "",
            "Local Image Path": str(photo.get("local_image_path", "") or ""),
            "Image Filename": str(photo.get("image_filename", "") or ""),
            "Image Upload Status": str(photo.get("image_upload_status", "") or "Ready"),
        })
        rows.append(row)
    return rows


def create_manual_row(
    project: str,
    room: str,
    supplier: str,
    notes: str,
    product_name: str = "",
    brand: str = "",
    dimensions: str = "",
    finish_color: str = "",
    model_sku: str = "",
    category: str = "",
    quantity: int = 1,
    price: str = "",
    product_url: str = "",
) -> dict:
    """
    Build a manually-entered product row.

    Status is derived here so the review table shows the correct badge before
    confidence checks run:
    - "Needs Enrichment" if a Serial/Model Number is provided but important
      fields are blank (the serial number can be used to fill those gaps later).
    - "Needs Review" otherwise.
    """
    from src.intake_schema import IMPORTANT_FIELDS

    row = make_base_row(project, room, supplier, notes)
    row.update({
        "Product Name":     product_name.strip(),
        "Brand":            brand.strip(),
        "Dimensions":       dimensions.strip(),
        "Finish / Color":   finish_color.strip(),
        "Model/SKU":        model_sku.strip(),
        "Product Category": category.strip(),
        "Quantity":         max(1, int(quantity)) if quantity else 1,
        "Price":            price.strip(),
        "Product URL":      product_url.strip(),
        "Source Type":      SOURCE_MANUAL,
    })

    # Determine initial Status
    has_serial = bool(row["Model/SKU"])
    missing_important = [
        f for f in IMPORTANT_FIELDS
        if not str(row.get(f, "") or "").strip()
           and not (f == "Quantity" and int(row.get("Quantity", 0)) > 0)
    ]
    if has_serial and missing_important:
        row["Status"] = "Needs Enrichment"
    else:
        row["Status"] = "Needs Review"

    return row


# ── DataFrame builder ──────────────────────────────────────────────────────────


def build_intake_dataframe(
    url_rows: list[dict],
    pdf_rows: list[dict],
    manual_rows: list[dict] | None = None,
) -> pd.DataFrame:
    all_rows = (manual_rows or []) + pdf_rows + url_rows
    if not all_rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(all_rows)
    # Guarantee every column exists, in the canonical order
    _col_defaults = {"Include": True, "Quantity": 1, "AI Category Confidence": 0, "Category Source": "Unknown"}
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = _col_defaults.get(col, "")
    return df[COLUMNS]
