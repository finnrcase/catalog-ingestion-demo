"""
Heuristic PDF parser for SCH DesignOps Intake.

Extracts structured product rows from receipts, quotes, invoices, and tear sheets
using PyMuPDF text/table extraction — no AI required.

Public API
----------
parse_pdf_rows(pdf_file, project, room, supplier, notes) -> list[dict]
    Returns partial row dicts using make_base_row() field names.
    Unknown fields are left at their default — never invented.
"""

import hashlib
import re

from src.intake_schema import IMPORTANT_FIELDS, SOURCE_PDF, make_base_row
from src.pdf_item_normalizer import build_quote_item_rows

# ── Regex patterns ─────────────────────────────────────────────────────────────

# Recognised serial/model/SKU label patterns
_SKU_LABEL_RE = re.compile(
    r"(?P<label>"
    r"serial\s*(?:number|#|no\.?)?|"
    r"s/?n|"
    r"model\s*(?:number|#|no\.?)?|"
    r"sku|"
    r"item\s*(?:number|#|no\.?)?|"
    r"product\s*(?:code|#)?|"
    r"part\s*(?:number|#|no\.?)?|"
    r"mfr\.?\s*(?:number|#|no\.?)?"
    r")\s*[:\-]?\s*(?P<value>\S+)",
    re.IGNORECASE,
)

# Lines that represent non-product rows (subtotals, taxes, fees, etc.)
_SKIP_RE = re.compile(
    r"\b(subtotal|sub[- ]?total|tax|gst|hst|vat|pst|delivery|shipping|freight|"
    r"total|balance(\s+due)?|discount|credit|surcharge|handling|deposit|"
    r"freight\s*&\s*handling|f&h|service\s+plan)\b",
    re.IGNORECASE,
)

_QTY_RE = re.compile(
    r"(?:qty|quantity)\s*[:\-]?\s*(\d+)"
    r"|(?<!\w)x\s*(\d+)(?!\w)"
    r"|\((\d+)\)",
    re.IGNORECASE,
)

_PRICE_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?|\b\d{1,6}\.\d{2}\b")

# Labels that are mundane enough not to need recording in Notes
_COMMON_SKU_LABELS = frozenset({"model #", "model number", "model no", "sku", "serial number", "serial #"})


# ── Internal helpers ───────────────────────────────────────────────────────────


def _is_skip_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(_SKIP_RE.search(stripped))


def _extract_model_sku(line: str) -> tuple[str, str]:
    """Return (value, original_label) or ('', '') if none found."""
    m = _SKU_LABEL_RE.search(line)
    if not m:
        return "", ""
    label = m.group("label").strip().rstrip(":")
    value = m.group("value").strip().rstrip(".,;")
    return value, label


def _extract_quantity(line: str) -> int:
    m = _QTY_RE.search(line)
    if not m:
        return 1
    val = next(g for g in m.groups() if g is not None)
    try:
        return max(1, int(val))
    except ValueError:
        return 1


def _extract_price(line: str) -> str:
    m = _PRICE_RE.search(line)
    return m.group(0) if m else ""


def _clean_product_name(line: str) -> str:
    """Strip labels, prices, and quantities from a line to get a product description."""
    text = _SKU_LABEL_RE.sub("", line)
    text = _PRICE_RE.sub("", text)
    text = _QTY_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" .,;:-")
    return text


def _compute_status(row: dict) -> str:
    has_serial = bool(str(row.get("Model/SKU", "") or "").strip())
    missing = [
        f for f in IMPORTANT_FIELDS
        if not str(row.get(f, "") or "").strip()
        and not (f == "Quantity" and int(row.get("Quantity", 0) or 0) > 0)
    ]
    return "Needs Enrichment" if (has_serial and missing) else "Needs Review"


def _stamp_extraction_audit_fields(row: dict) -> dict:
    """Attach the raw extracted lookup key and a simple parser confidence."""
    model = str(row.get("Model/SKU", "") or "").strip()
    row["_extracted_model_sku"] = model
    score = 55
    if str(row.get("Product Name", "") or "").strip():
        score += 15
    if str(row.get("Brand", "") or "").strip():
        score += 15
    if model:
        score += 15
    if str(row.get("Dimensions", "") or "").strip():
        score += 5
    row["_extraction_confidence"] = min(100, score)
    return row


def _row_from_line(
    line: str, project: str, room: str, supplier: str, notes: str
) -> dict | None:
    """Parse a single text line into a row dict, or return None if it should be skipped."""
    if _is_skip_line(line):
        return None

    model_sku, sku_label = _extract_model_sku(line)
    name = _clean_product_name(line)

    # Require at least a name or a model number to treat as a product row
    if not name and not model_sku:
        return None

    row = make_base_row(project=project, room=room, supplier=supplier, notes=notes)
    row["Product Name"] = name
    row["Model/SKU"] = model_sku
    row["Quantity"] = _extract_quantity(line)
    row["Price"] = _extract_price(line)
    row["Source Type"] = SOURCE_PDF

    # Append the original label to Notes only when it adds useful context
    if sku_label and sku_label.lower() not in _COMMON_SKU_LABELS:
        note_tag = f"[{sku_label}: {model_sku}]"
        row["Notes"] = f"{notes} {note_tag}".strip() if notes else note_tag

    row["Status"] = _compute_status(row)
    return _stamp_extraction_audit_fields(row)


def _parse_table_rows(
    page, project: str, room: str, supplier: str, notes: str
) -> list[dict]:
    """Extract product rows from PyMuPDF table structures on a single page."""
    rows = []
    try:
        tables = page.find_tables()
    except Exception:
        return rows
    tables = list(tables)

    page_lines = (page.get_text("text") or "").splitlines()
    table_lines: list[str] = []
    for table in tables:
        extracted = table.extract()
        if not extracted:
            continue
        for trow in extracted:
            cells = [str(c or "").strip() for c in trow]
            if any(cells):
                table_lines.append(" | ".join(cells))

    grouped_rows = build_quote_item_rows(
        table_lines,
        project=project,
        room=room,
        supplier=supplier,
        notes=notes,
        context_lines=page_lines,
    )
    if grouped_rows:
        return grouped_rows

    for table in tables:
        extracted = table.extract()
        if not extracted:
            continue

        header = None
        for i, trow in enumerate(extracted):
            cells = [str(c or "").strip() for c in trow]
            joined = " | ".join(c for c in cells if c)

            if i == 0:
                header = [c.lower() for c in cells]
                continue

            if _is_skip_line(joined):
                continue

            row = make_base_row(project=project, room=room, supplier=supplier, notes=notes)
            row["Source Type"] = SOURCE_PDF

            if header:
                col_map = {name: idx for idx, name in enumerate(header)}

                for key in ("description", "product", "item", "name"):
                    if key in col_map and col_map[key] < len(cells):
                        row["Product Name"] = cells[col_map[key]]
                        break

                for key in ("model", "sku", "model #", "model no", "item #", "part #", "serial"):
                    if key in col_map and col_map[key] < len(cells):
                        row["Model/SKU"] = cells[col_map[key]]
                        break

                for key in ("qty", "quantity"):
                    if key in col_map and col_map[key] < len(cells):
                        try:
                            row["Quantity"] = max(1, int(cells[col_map[key]]))
                        except (ValueError, IndexError):
                            pass
                        break

                for key in ("price", "unit price", "total", "amount"):
                    if key in col_map and col_map[key] < len(cells):
                        row["Price"] = cells[col_map[key]]
                        break

                for key in ("brand", "manufacturer", "mfr"):
                    if key in col_map and col_map[key] < len(cells):
                        row["Brand"] = cells[col_map[key]]
                        break
            else:
                # No header — fall back to line parser on the joined string
                parsed = _row_from_line(joined, project, room, supplier, notes)
                if parsed is None:
                    continue
                row = parsed

            if not str(row.get("Product Name", "")).strip() and not str(row.get("Model/SKU", "")).strip():
                continue

            row["Status"] = _compute_status(row)
            _stamp_extraction_audit_fields(row)
            rows.append(row)

    return rows


# ── Public API ─────────────────────────────────────────────────────────────────


def parse_pdf_rows(
    pdf_file,
    project: str = "",
    room: str = "",
    supplier: str = "",
    notes: str = "",
) -> list[dict]:
    """
    Extract structured product rows from a PDF using heuristic text/table parsing.
    No AI call is made. Unknown fields are left blank — never invented.

    Parameters
    ----------
    pdf_file : Streamlit UploadedFile or any object with .read() and .seek().
    project, room, supplier, notes : metadata applied to every row.

    Returns
    -------
    list[dict] of partial row dicts aligned to make_base_row() field names.
    Empty list if the PDF has no parseable text.

    Phase 1 image recovery (2026-05-08) annotates each row with internal
    `_source_pdf_id` (SHA1[:12] of the PDF bytes), `_source_page_number`
    (1-indexed page the row came from), and `_source_filename` (when the
    upload object exposes a `name` attribute).
    """
    try:
        import fitz
    except ImportError:
        raise ImportError("PyMuPDF is required. Run: pip install pymupdf")

    raw = pdf_file.read()
    pdf_file.seek(0)

    pdf_id = hashlib.sha1(raw).hexdigest()[:12]
    filename = getattr(pdf_file, "name", "") or ""

    doc = fitz.open(stream=raw, filetype="pdf")
    all_rows: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()

    for page_index, page in enumerate(doc):
        page_number = page_index + 1

        # 1. Try table extraction / quote item grouping first
        table_rows = _parse_table_rows(page, project, room, supplier, notes)
        if table_rows:
            for r in table_rows:
                key = (
                    str(r.get("Product Name", "")).lower().strip(),
                    str(r.get("Model/SKU", "")).lower().strip(),
                )
                if key not in seen_keys:
                    seen_keys.add(key)
                    r["_source_pdf_id"] = pdf_id
                    r["_source_page_number"] = page_number
                    r["_source_filename"] = filename
                    all_rows.append(r)
            continue

        # 2. Group raw text into quote items before falling back to line parsing.
        text = page.get_text("text")
        grouped_rows = build_quote_item_rows(
            text.splitlines(),
            project=project,
            room=room,
            supplier=supplier,
            notes=notes,
        )
        if grouped_rows:
            for row in grouped_rows:
                key = (
                    str(row.get("Product Name", "")).lower().strip(),
                    str(row.get("Model/SKU", "")).lower().strip(),
                )
                if key not in seen_keys:
                    seen_keys.add(key)
                    row["_source_pdf_id"] = pdf_id
                    row["_source_page_number"] = page_number
                    row["_source_filename"] = filename
                    all_rows.append(row)
            continue

        # 3. Last resort: line-by-line text parsing
        for line in text.splitlines():
            line = line.strip()
            if len(line) < 3:
                continue
            row = _row_from_line(line, project, room, supplier, notes)
            if row is None:
                continue
            key = (
                str(row.get("Product Name", "")).lower().strip(),
                str(row.get("Model/SKU", "")).lower().strip(),
            )
            if key not in seen_keys:
                seen_keys.add(key)
                row["_source_pdf_id"] = pdf_id
                row["_source_page_number"] = page_number
                row["_source_filename"] = filename
                all_rows.append(row)

    doc.close()
    return all_rows
