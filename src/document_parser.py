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

import re
import time

from src.intake_schema import IMPORTANT_FIELDS, SOURCE_PDF, make_base_row
from src.dimensions import extract_labeled_dimensions
from src.location_normalizer import normalize_location

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
    r"freight\s*&\s*handling|f&h|service\s+plan|protection\s+plan|warrant(?:y|ies))\b",
    re.IGNORECASE,
)

_QTY_RE = re.compile(
    r"(?:qty|quantity)\s*[:\-]?\s*(\d+)"
    r"|(?<!\w)x\s*(\d+)(?!\w)"
    r"|\((\d+)\)",
    re.IGNORECASE,
)

_PRICE_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?|\b\d{1,6}\.\d{2}\b")
_PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_ROW_NUMBER_PREFIX_RE = re.compile(r"^\s*(?:row\s*)?#?\d{1,3}\s*(?:[|.)\-:]\s*)?", re.IGNORECASE)
_ONLY_PUNCT_OR_NUMBER_RE = re.compile(r"^[\d\s|.,;:()#-]+$")
_HEADER_CONTEXT_RE = re.compile(
    r"\b("
    r"pc\s+richard|quotation|quote|selection|salesperson|sales\s*person|"
    r"phone|tel(?:ephone)?|fax|email|e-mail|address|date|page\s+\d+|"
    r"manufacturer|model|description|color|colour|price|amount|extension"
    r")\b",
    re.IGNORECASE,
)

# Labels that are mundane enough not to need recording in Notes
_COMMON_SKU_LABELS = frozenset({"model #", "model number", "model no", "sku", "serial number", "serial #"})

_KNOWN_BRANDS = (
    "Sub-Zero",
    "Scotsman",
    "Wolf",
    "Miele",
    "Bosch",
    "GE",
    "Monogram",
    "Lynx",
    "Fisher Paykel",
    "Fisher & Paykel",
    "Thermador",
    "JennAir",
    "KitchenAid",
    "Viking",
    "Dacor",
    "Samsung",
    "LG",
    "Whirlpool",
    "Frigidaire",
    "Electrolux",
    "Sharp",
    "Zephyr",
    "Kohler",
    "Kallista",
    "Waterworks",
    "Rohl",
    "Brizo",
    "Moen",
    "Toto",
)
_MODEL_TOKEN_RE = re.compile(r"\b(?=[A-Z0-9./-]{4,}\b)(?=[A-Z0-9./-]*\d)[A-Z0-9][A-Z0-9./-]{3,}\b")
_FINISH_RE = re.compile(r"\b(?:finish|color|colour)\s*[:\-]\s*([^|;,]+)", re.IGNORECASE)
_MATERIAL_RE = re.compile(r"\bmaterial\s*[:\-]\s*([^|;,]+)", re.IGNORECASE)
_PROJECT_RE = re.compile(r"\bproject\s*[:\-]\s*(.+)", re.IGNORECASE)
_SUPPLIER_RE = re.compile(r"\b(?:supplier|vendor|sold\s+by|dealer)\s*[:\-]\s*(.+)", re.IGNORECASE)

_ROOM_TERMS: tuple[str, ...] = (
    "Outdoor Kitchen",
    "Primary Bathroom",
    "Primary Bath",
    "Powder Room",
    "Laundry Room",
    "Living Room",
    "Dining Room",
    "Family Room",
    "Great Room",
    "Media Room",
    "Mud Room",
    "Pool House",
    "Kitchen",
    "Bar",
    "Exterior",
    "Mudroom",
    "Pantry",
    "Laundry",
    "Bathroom",
    "Bath",
    "Bedroom",
    "Office",
    "Garage",
    "Basement",
    "Foyer",
    "Entry",
    "Gym",
)
_ROOM_RE = re.compile(
    r"\b(" + "|".join(re.escape(term) for term in _ROOM_TERMS) + r")\b",
    re.IGNORECASE,
)

_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "Product Name": ("description", "product description", "product", "item description", "item name", "name"),
    "Brand": ("manufacturer", "mfr", "brand", "vendor brand"),
    "Model/SKU": ("model", "model #", "model no", "model number", "sku", "serial", "serial number", "item #", "item no", "part #", "part number", "mfr number"),
    "Dimensions": ("dimensions", "dimension", "size", "measurements", "w x d x h", "width", "height", "depth"),
    "Finish / Color": ("finish", "color", "colour", "finish/color", "finish colour"),
    "Material": ("material", "materials"),
    "Quantity": ("qty", "quantity", "qnty"),
    "Price": ("price", "unit price", "amount", "extended", "line total", "total price"),
    "Supplier": ("supplier", "vendor", "dealer", "sold by"),
    "Room": ("room", "location", "area"),
    "Product Category": ("category", "type"),
    "Notes": ("notes", "comments", "remarks"),
}


# ── Internal helpers ───────────────────────────────────────────────────────────


def _is_skip_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(_SKIP_RE.search(stripped))


def _is_contact_or_header_line(line: str) -> bool:
    """True for quote headers/contact rows that should never become products."""
    stripped = str(line or "").strip()
    if not stripped:
        return False
    if _EMAIL_RE.search(stripped):
        return True
    if _PHONE_RE.search(stripped) and _HEADER_CONTEXT_RE.search(stripped):
        return True
    if _HEADER_CONTEXT_RE.search(stripped) and not _extract_known_brand(stripped):
        # Column headers and quote metadata often contain model/price words but
        # no actual product identity.
        model_like = bool(_MODEL_TOKEN_RE.search(stripped))
        return not model_like or bool(_PHONE_RE.search(stripped))
    return False


def _is_bad_model_token(token: str, context: str = "") -> bool:
    clean = str(token or "").strip().strip(".,;:()[]")
    upper = clean.upper()
    if not clean:
        return True
    if clean.startswith("$"):
        return True
    if _PHONE_RE.fullmatch(clean) or _PHONE_RE.search(clean):
        return True
    if _EMAIL_RE.search(clean):
        return True
    if upper in {"PHONE", "EMAIL", "TOTAL", "QUOTE", "MODEL", "COLOR", "PRICE", "FAX", "DATE"}:
        return True
    if _HEADER_CONTEXT_RE.search(context or "") and not _extract_known_brand(context or ""):
        return True
    return False


def _is_price_only_line(line: str) -> bool:
    """True when a line/table row is only an item marker plus a price."""
    text = str(line or "").strip()
    if not text or not _PRICE_RE.search(text):
        return False
    without_price = _PRICE_RE.sub(" ", text)
    without_price = _ROW_NUMBER_PREFIX_RE.sub(" ", without_price)
    without_price = re.sub(r"\s+", " ", without_price).strip()
    return not without_price or bool(_ONLY_PUNCT_OR_NUMBER_RE.fullmatch(without_price))


def _make_unresolved_charge_row(
    line: str,
    project: str,
    room: str,
    supplier: str,
    notes: str,
) -> dict:
    row = make_base_row(project=project, room=room, supplier=supplier, notes=notes)
    row["Include"] = False
    row["Price"] = _extract_price(line)
    row["Source Type"] = SOURCE_PDF
    row["Import Type"] = "unresolved_charge"
    row["Status"] = "Manual Review"
    row["Notes"] = f"{notes} [Unresolved charge: {str(line).strip()}]".strip() if notes else f"[Unresolved charge: {str(line).strip()}]"
    return row


def _extract_model_sku(line: str) -> tuple[str, str]:
    """Return (value, original_label) or ('', '') if none found."""
    m = _SKU_LABEL_RE.search(line)
    if m:
        label = m.group("label").strip().rstrip(":")
        value = m.group("value").strip().rstrip(".,;")
        if _is_bad_model_token(value, line):
            return "", ""
        return value, label

    for token in _MODEL_TOKEN_RE.findall(line or ""):
        clean = token.strip().rstrip(".,;")
        if _is_bad_model_token(clean, line):
            continue
        return clean, "model token"
    return "", ""


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


def _clean_product_name(line: str, model_sku: str = "") -> str:
    """Strip labels, prices, and quantities from a line to get a product description."""
    text = _SKU_LABEL_RE.sub("", line)
    if model_sku:
        text = re.sub(rf"\b{re.escape(model_sku)}\b", " ", text, flags=re.IGNORECASE)
    text = _PRICE_RE.sub("", text)
    text = _QTY_RE.sub("", text)
    text = _FINISH_RE.sub("", text)
    text = _MATERIAL_RE.sub("", text)
    text = _ROW_NUMBER_PREFIX_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" .,;:-")
    if _ONLY_PUNCT_OR_NUMBER_RE.fullmatch(text):
        return ""
    return text


def _clean_inline_text(text: object) -> str:
    return re.sub(r"\s{2,}", " ", str(text or "")).strip(" .,;:-")


def _is_reddish_color(color: object) -> bool:
    """True when a PyMuPDF text span color looks like a red room annotation."""
    try:
        value = int(color)
    except Exception:
        return False
    r = (value >> 16) & 255
    g = (value >> 8) & 255
    b = value & 255
    return r >= 120 and r > (g * 1.25 + 20) and r > (b * 1.25 + 20)


def _extract_room_annotation(text: str, preferred_rooms: list[str] | None = None) -> str:
    """Return a normalized room/location token found in text, preferring red-span hints."""
    haystack = f" {text or ''} "
    for raw_room in preferred_rooms or []:
        room = _clean_inline_text(raw_room)
        if room and re.search(rf"\b{re.escape(room)}\b", haystack, re.IGNORECASE):
            cleaned, _confidence, _reason = normalize_location(room)
            return cleaned
    match = _ROOM_RE.search(haystack)
    if not match:
        return ""
    cleaned, _confidence, _reason = normalize_location(match.group(1))
    return cleaned


def _strip_room_annotation(text: object, room: str = "", preferred_rooms: list[str] | None = None) -> str:
    """Remove room annotations from product/finish text without touching model tokens."""
    cleaned = str(text or "")
    candidates = []
    if room:
        candidates.append(room)
    candidates.extend(preferred_rooms or [])
    if not candidates:
        candidates.extend(_ROOM_TERMS)
    for candidate in sorted({c for c in candidates if c}, key=len, reverse=True):
        cleaned = re.sub(
            rf"(?i)(?<![A-Za-z0-9]){re.escape(candidate)}(?![A-Za-z0-9])",
            " ",
            cleaned,
        )
    return _clean_inline_text(cleaned)


def _extract_red_room_annotations(page) -> list[str]:
    """Extract red/colored room labels from a PDF page using PyMuPDF span metadata."""
    annotations: list[str] = []
    seen: set[str] = set()
    try:
        data = page.get_text("dict")
    except Exception:
        return annotations
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = _clean_inline_text(span.get("text", ""))
                if not text or not _is_reddish_color(span.get("color")):
                    continue
                room = _extract_room_annotation(text)
                if room and room.lower() not in seen:
                    seen.add(room.lower())
                    annotations.append(room)
    return annotations


def _apply_room_annotation(row: dict, preferred_rooms: list[str] | None = None, default_room: str = "") -> dict:
    """Move visual room annotations out of description/finish fields into Room."""
    updated = row.copy()
    existing_room = _clean_inline_text(updated.get("Room"))
    combined = " ".join(
        _clean_inline_text(updated.get(field))
        for field in ("Product Name", "Finish / Color", "Color", "Notes")
    )
    detected_room = existing_room or _extract_room_annotation(combined, preferred_rooms)
    if not detected_room and default_room:
        detected_room = default_room
    if detected_room:
        normalized_room, _confidence, _reason = normalize_location(detected_room, default_room)
        updated["Room"] = normalized_room
        for field in ("Product Name", "Finish / Color", "Color"):
            if updated.get(field):
                updated[field] = _strip_room_annotation(updated.get(field), normalized_room, preferred_rooms)
    return updated


def _extract_known_brand(text: str) -> str:
    haystack = str(text or "")
    for brand in _KNOWN_BRANDS:
        if re.search(rf"\b{re.escape(brand)}\b", haystack, re.IGNORECASE):
            return brand.replace("&", "and") if brand == "Fisher & Paykel" else brand
    return ""


def _format_dimensions_from_parts(text: str) -> str:
    parts = extract_labeled_dimensions(text)
    ordered = []
    if parts.get("width"):
        ordered.append(f'{parts["width"]}"W')
    if parts.get("height"):
        ordered.append(f'{parts["height"]}"H')
    if parts.get("depth"):
        ordered.append(f'{parts["depth"]}"D')
    if parts.get("length"):
        ordered.append(f'{parts["length"]}"L')
    return " x ".join(ordered)


def _extract_dimensions_from_text(text: str) -> str:
    formatted = _format_dimensions_from_parts(text)
    if formatted:
        return formatted
    m = re.search(
        r"\b(?:dimensions?|size|measurements?)\s*[:\-]\s*"
        r"(\d+(?:\.\d+)?(?:\s+\d+/\d+)?\s*(?:\"|in)?\s*[x×]\s*"
        r"\d+(?:\.\d+)?(?:\s+\d+/\d+)?\s*(?:\"|in)?"
        r"(?:\s*[x×]\s*\d+(?:\.\d+)?(?:\s+\d+/\d+)?\s*(?:\"|in)?)?)",
        text,
        re.IGNORECASE,
    )
    return m.group(1).strip() if m else ""


def _extract_label_value(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text or "")
    return re.sub(r"\s{2,}", " ", m.group(1)).strip(" .,:;-") if m else ""


def _normalise_header(text: str) -> str:
    return re.sub(r"[^a-z0-9#]+", " ", str(text or "").lower()).strip()


def _canonical_header(text: str) -> str:
    normal = _normalise_header(text)
    if not normal:
        return ""
    for canonical, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            alias_norm = _normalise_header(alias)
            if normal == alias_norm or alias_norm in normal:
                return canonical
    return ""


def _is_header_row(cells: list[str]) -> bool:
    hits = {_canonical_header(c) for c in cells if c}
    hits.discard("")
    return len(hits) >= 2 and bool(hits & {"Product Name", "Model/SKU", "Brand"})


def _extract_global_context(text: str, project: str, supplier: str) -> dict:
    context = {"project": project, "supplier": supplier, "category": ""}
    if not context["project"]:
        m = _PROJECT_RE.search(text or "")
        if m:
            context["project"] = m.group(1).splitlines()[0].strip(" .,:;-")
    if not context["supplier"]:
        m = _SUPPLIER_RE.search(text or "")
        if m:
            context["supplier"] = m.group(1).splitlines()[0].strip(" .,:;-")
        elif re.search(r"\bPC\s+Richard\b|\bP\.?C\.?\s+Richard\b", text or "", re.IGNORECASE):
            context["supplier"] = "PC Richard"
    if re.search(r"\bappliance(?:s)?\b", text or "", re.IGNORECASE):
        context["category"] = "Appliances"
    return context


def _compute_status(row: dict) -> str:
    has_serial = bool(str(row.get("Model/SKU", "") or "").strip())
    missing = [
        f for f in IMPORTANT_FIELDS
        if not str(row.get(f, "") or "").strip()
        and not (f == "Quantity" and int(row.get("Quantity", 0) or 0) > 0)
    ]
    return "Needs Enrichment" if (has_serial and missing) else "Needs Review"


def _row_from_line(
    line: str,
    project: str,
    room: str,
    supplier: str,
    notes: str,
    room_annotations: list[str] | None = None,
) -> dict | None:
    """Parse a single text line into a row dict, or return None if it should be skipped."""
    if _is_contact_or_header_line(line):
        return None
    if _is_price_only_line(line):
        return _make_unresolved_charge_row(line, project, room, supplier, notes)
    if _is_skip_line(line):
        return None

    model_sku, sku_label = _extract_model_sku(line)
    name = _clean_product_name(line, model_sku)

    # Require at least a name or a model number to treat as a product row
    if not name and not model_sku:
        return None

    row = make_base_row(project=project, room=room, supplier=supplier, notes=notes)
    row["Product Name"] = name
    row["Brand"] = _extract_known_brand(line)
    row["Model/SKU"] = model_sku
    row["Dimensions"] = _extract_dimensions_from_text(line)
    row["Finish / Color"] = _extract_label_value(_FINISH_RE, line)
    row["Material"] = _extract_label_value(_MATERIAL_RE, line)
    if row["Brand"] in {"Wolf", "Sub-Zero", "Scotsman", "Miele", "Bosch", "GE", "Monogram", "Lynx"}:
        row["Product Category"] = "Appliances"
    row["Quantity"] = _extract_quantity(line)
    row["Price"] = _extract_price(line)
    row["Source Type"] = SOURCE_PDF

    # Append the original label to Notes only when it adds useful context
    if sku_label and sku_label.lower() not in _COMMON_SKU_LABELS:
        note_tag = f"[{sku_label}: {model_sku}]"
        row["Notes"] = f"{notes} {note_tag}".strip() if notes else note_tag

    row = _apply_room_annotation(row, room_annotations, room)
    row["Status"] = _compute_status(row)
    return row


def _parse_table_rows(
    page,
    project: str,
    room: str,
    supplier: str,
    notes: str,
    category: str = "",
    room_annotations: list[str] | None = None,
) -> list[dict]:
    """Extract product rows from PyMuPDF table structures on a single page."""
    rows = []
    try:
        tables = page.find_tables()
    except Exception:
        return rows

    for table in tables:
        extracted = table.extract()
        if not extracted:
            continue

        header: list[str] | None = None
        for i, trow in enumerate(extracted):
            cells = [str(c or "").strip() for c in trow]
            joined = " | ".join(c for c in cells if c)

            if _is_header_row(cells):
                header = [_canonical_header(c) for c in cells]
                continue

            if _is_contact_or_header_line(joined):
                continue
            if _is_skip_line(joined):
                continue
            if _is_price_only_line(joined):
                rows.append(_make_unresolved_charge_row(joined, project, room, supplier, notes))
                continue

            row = make_base_row(project=project, room=room, supplier=supplier, notes=notes)
            row["Source Type"] = SOURCE_PDF

            if header:
                col_map = {name: idx for idx, name in enumerate(header) if name}

                for column in (
                    "Product Name",
                    "Brand",
                    "Model/SKU",
                    "Dimensions",
                    "Finish / Color",
                    "Material",
                    "Supplier",
                    "Room",
                    "Product Category",
                    "Notes",
                ):
                    idx = col_map.get(column)
                    if idx is not None and idx < len(cells):
                        row[column] = cells[idx]

                qty_idx = col_map.get("Quantity")
                if qty_idx is not None and qty_idx < len(cells):
                    try:
                        row["Quantity"] = max(1, int(re.sub(r"\D+", "", cells[qty_idx]) or "1"))
                    except (ValueError, IndexError):
                        row["Quantity"] = _extract_quantity(joined)

                price_idx = col_map.get("Price")
                if price_idx is not None and price_idx < len(cells):
                    row["Price"] = cells[price_idx]

                if not row["Brand"]:
                    row["Brand"] = _extract_known_brand(joined)
                if not row["Model/SKU"]:
                    row["Model/SKU"], _ = _extract_model_sku(joined)
                if not row["Dimensions"]:
                    row["Dimensions"] = _extract_dimensions_from_text(joined)
                if not row["Finish / Color"]:
                    row["Finish / Color"] = _extract_label_value(_FINISH_RE, joined)
                if not row["Material"]:
                    row["Material"] = _extract_label_value(_MATERIAL_RE, joined)
                if not row["Product Category"] and category:
                    row["Product Category"] = category
            else:
                # No header — fall back to line parser on the joined string
                parsed = _row_from_line(joined, project, room, supplier, notes, room_annotations)
                if parsed is None:
                    continue
                row = parsed

            if _is_contact_or_header_line(joined):
                continue
            if not str(row.get("Product Name", "")).strip() and not str(row.get("Model/SKU", "")).strip():
                continue

            row = _apply_room_annotation(row, room_annotations, room)
            row["Status"] = _compute_status(row)
            rows.append(row)

    return rows


# ── Public API ─────────────────────────────────────────────────────────────────


def parse_pdf_rows(
    pdf_file,
    project: str = "",
    room: str = "",
    supplier: str = "",
    notes: str = "",
    stage_timings: dict | None = None,
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
    """
    try:
        import fitz
    except ImportError:
        raise ImportError("PyMuPDF is required. Run: pip install pymupdf")

    total_start = time.perf_counter()
    read_start = time.perf_counter()
    raw = pdf_file.read()
    pdf_file.seek(0)
    if stage_timings is not None:
        stage_timings["pdf_file_read_ms"] = stage_timings.get("pdf_file_read_ms", 0.0) + (
            time.perf_counter() - read_start
        ) * 1000

    open_start = time.perf_counter()
    doc = fitz.open(stream=raw, filetype="pdf")
    if stage_timings is not None:
        stage_timings["pdf_open_ms"] = stage_timings.get("pdf_open_ms", 0.0) + (
            time.perf_counter() - open_start
        ) * 1000
        stage_timings["page_count"] = stage_timings.get("page_count", 0) + len(doc)

    page_texts: list[str] = []
    for page in doc:
        try:
            page_texts.append(page.get_text("text"))
        except Exception:
            page_texts.append("")
    context = _extract_global_context("\n".join(page_texts), project, supplier)
    project = context.get("project") or project
    supplier = context.get("supplier") or supplier
    category = context.get("category") or ""

    all_rows: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()

    for page_index, page in enumerate(doc):
        room_annotations = _extract_red_room_annotations(page)

        # 1. Try table extraction first
        table_start = time.perf_counter()
        table_rows = _parse_table_rows(page, project, room, supplier, notes, category, room_annotations)
        if stage_timings is not None:
            stage_timings["table_row_parsing_ms"] = stage_timings.get("table_row_parsing_ms", 0.0) + (
                time.perf_counter() - table_start
            ) * 1000
        if table_rows:
            for r in table_rows:
                norm_start = time.perf_counter()
                key = (
                    str(r.get("Product Name", "")).lower().strip(),
                    str(r.get("Model/SKU", "")).lower().strip(),
                )
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_rows.append(r)
                if stage_timings is not None:
                    stage_timings["normalization_ms"] = stage_timings.get("normalization_ms", 0.0) + (
                        time.perf_counter() - norm_start
                    ) * 1000
            continue

        # 2. Fall back to line-by-line text parsing
        text_start = time.perf_counter()
        text = page_texts[page_index] if page_index < len(page_texts) else page.get_text("text")
        if stage_timings is not None:
            stage_timings["pdf_text_extraction_ms"] = stage_timings.get("pdf_text_extraction_ms", 0.0) + (
                time.perf_counter() - text_start
            ) * 1000

        line_start = time.perf_counter()
        for line in text.splitlines():
            line = line.strip()
            if len(line) < 3:
                continue
            row = _row_from_line(line, project, room, supplier, notes, room_annotations)
            if row is None:
                continue
            key = (
                str(row.get("Product Name", "")).lower().strip(),
                str(row.get("Model/SKU", "")).lower().strip(),
            )
            if key not in seen_keys:
                seen_keys.add(key)
                all_rows.append(row)
        if stage_timings is not None:
            stage_timings["table_row_parsing_ms"] = stage_timings.get("table_row_parsing_ms", 0.0) + (
                time.perf_counter() - line_start
            ) * 1000

    doc.close()
    if stage_timings is not None:
        stage_timings["rows_returned"] = len(all_rows)
        stage_timings["pdf_parse_total_ms"] = stage_timings.get("pdf_parse_total_ms", 0.0) + (
            time.perf_counter() - total_start
        ) * 1000
    return all_rows
