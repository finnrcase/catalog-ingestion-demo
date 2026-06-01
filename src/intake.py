import re

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

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _str_val(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _norm_token(value) -> str:
    return _NON_ALNUM_RE.sub("", _str_val(value).lower())


def _norm_text(value) -> str:
    return _NON_ALNUM_RE.sub(" ", _str_val(value).lower()).strip()


def _dedupe_identity(row: dict) -> tuple[str, str] | None:
    brand = _norm_token(row.get("Brand"))
    model = _norm_token(row.get("Model/SKU"))
    if brand and model:
        return brand, model
    return None


def _room_key(row: dict) -> str:
    return _norm_text(row.get("Room"))


def _description_key(row: dict) -> str:
    text = _norm_text(row.get("Product Name"))
    # Keep the key broad enough to collapse late fallback rows whose descriptions
    # differ only by room/finish noise, while still preserving genuinely distinct
    # products with the same model in different rooms.
    return " ".join(text.split()[:8])


def _row_richness_score(row: dict) -> int:
    score = 0
    for field, weight in (
        ("Brand", 20),
        ("Model/SKU", 20),
        ("Product Name", 18),
        ("Room", 14),
        ("Supplier", 10),
        ("Dimensions", 12),
        ("Image URL", 10),
        ("Product URL", 9),
        ("Finish / Color", 6),
        ("Material", 6),
        ("Product Category", 6),
        ("Price", 3),
    ):
        if _str_val(row.get(field)):
            score += weight
    if _str_val(row.get("Import Type")) == "unresolved_charge":
        score -= 100
    return score


def _should_merge_duplicate_rows(a: dict, b: dict) -> bool:
    if _dedupe_identity(a) != _dedupe_identity(b):
        return False
    room_a, room_b = _room_key(a), _room_key(b)
    if room_a and room_b and room_a != room_b:
        return False
    desc_a, desc_b = _description_key(a), _description_key(b)
    if desc_a and desc_b and (desc_a in desc_b or desc_b in desc_a):
        return True
    # If one row is a late/raw fallback with weak context, collapse it into the
    # richer parsed row for the same brand/model.
    return not room_a or not room_b or not desc_a or not desc_b


def _merge_duplicate_rows(primary: dict, secondary: dict) -> dict:
    rows = sorted([primary, secondary], key=_row_richness_score, reverse=True)
    merged = dict(rows[0])
    weaker = rows[1]
    for key, value in weaker.items():
        if key not in merged or not _str_val(merged.get(key)):
            merged[key] = value
    if primary.get("Include") is False or secondary.get("Include") is False:
        merged["Include"] = primary.get("Include", True) is not False and secondary.get("Include", True) is not False
    return merged


def dedupe_intake_rows(rows: list[dict]) -> list[dict]:
    """Collapse duplicate parsed rows before confidence/enrichment/export."""
    deduped: list[dict] = []
    for row in rows:
        if row.get("Include") is False or _str_val(row.get("Import Type")) == "unresolved_charge":
            deduped.append(row)
            continue
        match_index: int | None = None
        for idx, existing in enumerate(deduped):
            if existing.get("Include") is False:
                continue
            if _should_merge_duplicate_rows(existing, row):
                match_index = idx
                break
        if match_index is None:
            deduped.append(row)
        else:
            deduped[match_index] = _merge_duplicate_rows(deduped[match_index], row)
    return deduped


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
    all_rows = dedupe_intake_rows((manual_rows or []) + pdf_rows + url_rows)
    if not all_rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(all_rows)
    # Guarantee every column exists, in the canonical order
    _col_defaults = {"Include": True, "Quantity": 1, "AI Category Confidence": 0, "Category Source": "Unknown"}
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = _col_defaults.get(col, "")
    return df[COLUMNS]
