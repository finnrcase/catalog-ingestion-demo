"""
AI-assisted PDF product extraction for SCH DesignOps Intake.

Sends the text content of an uploaded PDF quote sheet to Claude and returns
structured product rows ready for the review table.

Public API
----------
extract_products_from_pdf_with_ai(
    pdf_file, project_name, default_room, supplier
) -> tuple[pd.DataFrame, str | None]
    Returns (dataframe_of_rows, error_message_or_None).

ALLOWED_CATEGORIES : list[str]
    Valid product category values — imported by app.py for the column widget.

Environment
-----------
ANTHROPIC_API_KEY   Required. Add to .env and restart the app.
"""

import json
import os
import re
import time

import pandas as pd
from dotenv import load_dotenv

from src.intake_schema import CATEGORIES
from src.category_ai import _normalise_category
from src.document_parser import parse_pdf_rows
from src.location_normalizer import normalize_location

load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# Re-export for backward compatibility with app.py imports
ALLOWED_CATEGORIES: list[str] = CATEGORIES

# Maps AI JSON keys → DataFrame column names
_FIELD_MAP: dict[str, str] = {
    "project":          "Project",
    "room":             "Room",
    "product_name":     "Product Name",
    "brand":            "Brand",
    "dimensions":       "Dimensions",
    "finish_color":     "Finish / Color",
    "color":            "Color",
    "material":         "Material",
    "model_sku":        "Model/SKU",
    "quantity":         "Quantity",
    "price":            "Price",
    "supplier":         "Supplier",
    "product_url":      "Product URL",
    "notes":            "Notes",
    "product_category": "Product Category",
    "confidence_score": "Confidence Score",
    "review_required":  "Review Required",
    "missing_fields":   "Missing Fields",
    "suggested_action": "Suggested Action",
}

# Expected column order in the output DataFrame — aligns with DISPLAY_ORDER in confidence.py
_OUTPUT_COLUMNS: list[str] = [
    "Include",
    "Confidence Score",
    "Review Required",
    "Suggested Action",
    "Project",
    "Room",
    "Product Name",
    "Brand",
    "Dimensions",
    "Finish / Color",
    "Color",
    "Material",
    "Model/SKU",
    "Product Category",
    "Quantity",
    "Price",
    "Supplier",
    "Product URL",
    "Notes",
    "Source Type",
    "Status",
    "Missing Fields",
    "AI Category Confidence",
    "Category Source",
]

_PARSE_DEBUG_FIELDS: list[str] = [
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
    "final_dimensions",
    "ai_used",
    "ai_skipped_reason",
    "parse_confidence",
    "missing_critical_fields_before_ai",
    "missing_critical_fields_after_ai",
]

_CRITICAL_PARSE_FIELDS: tuple[str, ...] = (
    "Product Name",
    "Brand",
    "Model/SKU",
    "Dimensions",
    "Supplier",
    "Product Category",
)
_MERGE_FIELDS: tuple[str, ...] = (
    "Project",
    "Room",
    "Product Name",
    "Brand",
    "Dimensions",
    "Finish / Color",
    "Color",
    "Material",
    "Model/SKU",
    "Product Category",
    "Quantity",
    "Price",
    "Supplier",
    "Notes",
)


# ── PDF text extraction ────────────────────────────────────────────────────────


def _read_pdf_text(pdf_file) -> str:
    """
    Extract plain text from a Streamlit UploadedFile using PyMuPDF.
    Resets the file pointer to 0 after reading so the caller can re-use the object.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError(
            "PyMuPDF is required for AI extraction. Run: pip install pymupdf"
        )

    raw = pdf_file.read()
    pdf_file.seek(0)

    doc = fitz.open(stream=raw, filetype="pdf")
    pages = [page.get_text("text") for page in doc]
    doc.close()
    return "\n".join(pages)


# ── Prompt construction ────────────────────────────────────────────────────────


def _build_prompt(
    pdf_text: str,
    project_name: str,
    default_room: str,
    supplier: str,
    structured_rows: list[dict] | None = None,
) -> str:
    import json as _json
    categories_str = ", ".join(ALLOWED_CATEGORIES)

    pre_parse_section = ""
    if structured_rows:
        compact = [
            {
                "product_name": r.get("Product Name", ""),
                "brand": r.get("Brand", ""),
                "model_sku": r.get("Model/SKU", ""),
                "dimensions": r.get("Dimensions", ""),
                "finish_color": r.get("Finish / Color", ""),
                "material": r.get("Material", ""),
                "supplier": r.get("Supplier", ""),
                "product_category": r.get("Product Category", ""),
                "quantity": r.get("Quantity", 1),
                "price": r.get("Price", ""),
                "notes": r.get("Notes", ""),
            }
            for r in structured_rows
        ]
        pre_parse_section = f"""
STRUCTURED PRE-PARSE
A rule-based parser produced the {len(compact)} rows below before this call.
Treat each row as one confirmed product line from the document.
Rules:
- Output EXACTLY one JSON object per pre-parsed row (do not split or merge rows).
- Correct obvious parser errors (e.g. garbled product names) using document text.
- Fill in fields the parser left blank only when the document explicitly states the value.
- Add rows for any product lines the parser missed entirely.
- Do NOT invent dimensions, finish, or brand if not present in the document.

{_json.dumps(compact, indent=2)}

"""

    return f"""You are a procurement assistant for Saffron Case Homes, an interior design firm.
A team member has uploaded a product quote sheet or tear sheet (text extracted below).
Your job is to extract every product row and return them as structured JSON for import into Programa, a design project management platform.
{pre_parse_section}
PROJECT CONTEXT
- Project name: {project_name}
- Default room (use if no per-line room annotation is visible): {default_room}
- Supplier (use if not extractable from the document): {supplier}

EXTRACTION RULES
1. Return ONLY product items — skip: subtotals, tax, delivery, freight, service plan-only rows, balance due, deposit lines, and blank rows.
2. Product Name = Manufacturer + " " + Description (e.g. "Wolf Microwave", "Sub-Zero 36\" Refrigerator"). Do NOT use a model number as the Product Name unless there is absolutely no usable description.
3. Brand = manufacturer name only (e.g. "Wolf", "Sub-Zero", "Miele").
4. Model/SKU = the model or part number from the quote line. Accept any of: serial number, model number, SKU, item number, product code, part number, manufacturer number.
5. Dimensions: extract ONLY if the exact product dimensions are explicitly stated in a specification, table, or labelled field in the document (e.g. '30"W × 18"D × 16"H'). Do NOT infer dimensions from product names — a name like "36-inch refrigerator" or "30\\" range" does not give you H×W×D. Leave empty string "" if dimensions are not explicitly stated — the enrichment step will fill this from the manufacturer spec sheet. If dimensions are partially or ambiguously stated, leave empty string and include "Verify dimensions from spec sheet" in suggested_action.
6. Finish / Color and Material: extract finish, colour, and material if stated (e.g. "Matte Black", "Stainless Steel", "Brass"). Leave empty string if not stated.
7. Quantity: if the line description says "qty 2" or similar, use 2. If no quantity is shown, default to 1.
8. Price: use the line price exactly as shown on the quote. If the price appears to be a total for multiple units, keep it as shown and add a note: "Price appears to reflect quoted line total."
9. Room / Location: Location may appear ANYWHERE near the product row — in a separate column, as a handwritten-style annotation, in red text beside the description, or as an informal phrase. Examples: "Bar - if we can fit it", "laundry room floor 2", "exterior", "primary", "mudroom", "nanny vestibule", "gym", "Nanny Vestibule", "Exterior".
   - Scan the full row and nearby context for any room or location hint.
   - Normalise to Title Case (e.g. "laundry room floor 2" → "Laundry Room Floor 2", "exterior" → "Exterior").
   - If a note contains uncertainty (e.g. "Bar - if we can fit it", "kitchen if it fits"), extract the clean room name ("Bar"), set review_required = true, and include the ORIGINAL note verbatim in the notes field.
   - If no location is visible for a line, use the default room value above.
10. Product Category must be exactly one of: {categories_str}. For appliance quotes (Wolf, Sub-Zero, Miele, etc.) use "Appliances". For seating (chairs, sofas, benches) use "Seating". If uncertain, pick the closest category — do not leave blank unless truly no category applies.
11. Confidence scoring (start at 85, apply deductions):
    - Deduct 20 if Product Name cannot be reliably determined.
    - Deduct 20 if Model/SKU is missing.
    - Deduct 15 if room is unclear or missing.
    - Deduct 15 if quantity is ambiguous.
    - Deduct 10 if price is missing or unclear.
12. Set review_required = true when confidence_score < 75, room is unclear, or quantity is ambiguous.
13. missing_fields: comma-separated list of field names that are empty or uncertain (e.g. "Room, Quantity").
14. suggested_action: one short instruction for the reviewer (e.g. "Confirm room assignment", "Verify quantity — description may imply qty 2").
15. Deduplication: each physical product line in the document must appear EXACTLY ONCE in the output. Do not create two rows for the same line item.
16. Ambiguity: when a field has two or more plausible values, pick the most likely one, set review_required=true, and explain the ambiguity in suggested_action.

RESPONSE FORMAT
Return ONLY a raw JSON array. No prose before it. No prose after it. No markdown. No code fences. No explanation. The very first character of your response must be "[" and the very last must be "]". Each element must include every key below:
[
  {{
    "project": "{project_name}",
    "room": "<room or default>",
    "product_name": "<Manufacturer Description>",
    "brand": "<manufacturer>",
    "dimensions": "<dimensions string or empty string>",
    "finish_color": "<finish or colour or empty string>",
    "color": "<colour only if separately stated or empty string>",
    "material": "<material only if separately stated or empty string>",
    "model_sku": "<model number or empty string>",
    "quantity": <integer>,
    "price": "<price string as shown on quote>",
    "supplier": "<supplier name>",
    "product_url": "",
    "notes": "<any notes or empty string>",
    "product_category": "<one of the allowed categories or empty string>",
    "confidence_score": <integer 0-100>,
    "review_required": <true or false>,
    "missing_fields": "<comma-separated list or empty string>",
    "suggested_action": "<short instruction or empty string>"
  }}
]

DOCUMENT TEXT
---
{pdf_text}
---"""


# ── Response parsing ───────────────────────────────────────────────────────────

# Smart/curly quote characters that Claude sometimes emits
_QUOTE_NORMALIZE = str.maketrans({
    "“": '"', "”": '"',   # left/right double curly quotes
    "‘": "'", "’": "'",   # left/right single curly quotes
    "［": "[", "］": "]",   # fullwidth brackets
})


def extract_json_array_from_text(text: str) -> list:
    """
    Robustly extract a JSON array from text that may contain prose or markdown.

    Steps:
      1. Strip markdown fences (```json ... ``` or ``` ... ```).
      2. Normalise smart/curly quotes and fullwidth brackets.
      3. Locate the first '[' and the last ']'.
      4. Parse the substring with json.loads.

    Raises ValueError with up to 500 chars of context on failure.
    """
    # 1. Strip markdown fences
    text = re.sub(r"```(?:json)?\s*|```", "", text)
    # 2. Normalise typographic characters
    text = text.translate(_QUOTE_NORMALIZE).strip()

    # 3. Find array boundaries
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(
            f"No JSON array found in AI response. "
            f"First 500 chars: {text[:500]!r}"
        )

    candidate = text[start : end + 1]

    # 4. Parse
    try:
        items = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON parse failed ({exc}). "
            f"Extracted candidate (first 500 chars): {candidate[:500]!r}"
        ) from exc

    if not isinstance(items, list):
        raise ValueError("AI response root is not a JSON array.")
    return items


def _parse_ai_response(response_text: str) -> list[dict]:
    """Thin wrapper around extract_json_array_from_text for backward compatibility."""
    return extract_json_array_from_text(response_text)


# ── Row mapping ────────────────────────────────────────────────────────────────


def _item_to_row(
    item: dict, project_name: str, default_room: str, supplier: str
) -> dict:
    """Translate one AI JSON object into a DataFrame row dict."""
    row: dict = {
        "Include": True,
        "Source Type": "PDF_AI",
        "AI Category Confidence": 0,
        "Category Source": "Unknown",
    }

    for ai_key, col_name in _FIELD_MAP.items():
        val = item.get(ai_key, "")

        if col_name == "Quantity":
            try:
                val = max(1, int(val))
            except (TypeError, ValueError):
                val = 1

        elif col_name == "Confidence Score":
            try:
                val = max(0, min(100, int(val)))
            except (TypeError, ValueError):
                val = 50

        elif col_name == "Review Required":
            # Accept bool or string "true"/"false"
            if isinstance(val, bool):
                pass
            else:
                val = str(val).strip().lower() == "true"

        elif col_name == "Product Category":
            raw = str(val or "").strip()
            val = _normalise_category(raw) if raw else ""

        else:
            val = str(val or "").strip()

        row[col_name] = val

    # Enforce fallbacks for required context fields
    if not str(row.get("Project", "")).strip():
        row["Project"] = project_name
    if not str(row.get("Room", "")).strip():
        row["Room"] = default_room
    if not str(row.get("Supplier", "")).strip():
        row["Supplier"] = supplier

    # ── Location normalisation ─────────────────────────────────────────────────
    # normalize_location strips uncertainty qualifiers, title-cases, and scores.
    # If confidence < 75 we lower the row's Confidence Score by 15 so that
    # apply_confidence_checks naturally sets Review Required = True (it uses the
    # stored score for PDF_AI rows rather than re-deriving it).
    _raw_room = str(row.get("Room", "") or "").strip()
    _cleaned_room, _loc_conf, _loc_reason = normalize_location(_raw_room, default_room)
    if _raw_room and _loc_conf < 75 and _cleaned_room != _raw_room:
        _existing_notes = str(row.get("Notes", "") or "").strip()
        _loc_note = f"[Location note: {_raw_room}]"
        if _loc_note not in _existing_notes:
            row["Notes"] = (
                f"{_existing_notes} {_loc_note}".strip()
                if _existing_notes else _loc_note
            )
    row["Room"] = _cleaned_room or default_room
    if _loc_conf < 75:
        row["Confidence Score"] = max(0, int(row.get("Confidence Score", 50)) - 15)

    # Derive Status from confidence so the review table badge is consistent
    score = row.get("Confidence Score", 50)
    row["Status"] = "Ready for Review" if int(score) >= 75 else "Needs Review"

    return row


def _str_val(value) -> str:
    return "" if value is None else str(value).strip()


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _str_val(value).lower())


def _missing_critical_fields(row: dict) -> list[str]:
    missing: list[str] = []
    for field in _CRITICAL_PARSE_FIELDS:
        value = _str_val(row.get(field))
        if not value:
            missing.append(field)
    qty = row.get("Quantity")
    try:
        if int(qty or 0) < 1:
            missing.append("Quantity")
    except Exception:
        missing.append("Quantity")
    return missing


def _parse_confidence(row: dict) -> int:
    penalties = {
        "Product Name": 20,
        "Brand": 15,
        "Model/SKU": 20,
        "Dimensions": 10,
        "Supplier": 10,
        "Product Category": 10,
        "Quantity": 10,
    }
    score = 100
    for field in _missing_critical_fields(row):
        score -= penalties.get(field, 5)
    return max(0, min(100, score))


def _debug_string(fields: list[str]) -> str:
    return ", ".join(fields)


def _stamp_parse_debug(
    row: dict,
    deterministic_row: dict | None = None,
    ai_row: dict | None = None,
    *,
    ai_used: bool = False,
    ai_skipped_reason: str = "",
) -> dict:
    deterministic_row = deterministic_row or {}
    ai_row = ai_row or {}
    stamped = dict(row)
    before = _missing_critical_fields(deterministic_row or row)
    after = _missing_critical_fields(stamped)
    stamped.update({
        "deterministic_product_name": _str_val(deterministic_row.get("Product Name")),
        "ai_product_name": _str_val(ai_row.get("Product Name")),
        "final_product_name": _str_val(stamped.get("Product Name")),
        "deterministic_supplier": _str_val(deterministic_row.get("Supplier")),
        "ai_supplier": _str_val(ai_row.get("Supplier")),
        "final_supplier": _str_val(stamped.get("Supplier")),
        "deterministic_model_sku": _str_val(deterministic_row.get("Model/SKU")),
        "ai_model_sku": _str_val(ai_row.get("Model/SKU")),
        "final_model_sku": _str_val(stamped.get("Model/SKU")),
        "deterministic_dimensions": _str_val(deterministic_row.get("Dimensions")),
        "ai_dimensions": _str_val(ai_row.get("Dimensions")),
        "final_dimensions": _str_val(stamped.get("Dimensions")),
        "ai_used": bool(ai_used),
        "ai_skipped_reason": ai_skipped_reason,
        "parse_confidence": _parse_confidence(stamped),
        "missing_critical_fields_before_ai": _debug_string(before),
        "missing_critical_fields_after_ai": _debug_string(after),
    })
    return stamped


def stamp_deterministic_parse_debug(rows: list[dict], reason: str = "AI not requested") -> list[dict]:
    return [
        _stamp_parse_debug(row, row, None, ai_used=False, ai_skipped_reason=reason)
        for row in rows
    ]


def _choose_final_value(field: str, deterministic_value, ai_value):
    det = _str_val(deterministic_value)
    ai = _str_val(ai_value)
    if field == "Quantity":
        try:
            det_qty = int(deterministic_value or 0)
        except Exception:
            det_qty = 0
        try:
            ai_qty = int(ai_value or 0)
        except Exception:
            ai_qty = 0
        if det_qty > 1 and ai_qty <= 1:
            return det_qty
        return ai_qty if ai_qty > 0 else (det_qty if det_qty > 0 else 1)
    if field == "Notes":
        if det and ai and ai not in det:
            return f"{det} {ai}".strip()
        return ai or det
    if ai:
        return ai
    return det


def _find_matching_ai_row(det_row: dict, ai_rows: list[dict], used: set[int], index: int) -> tuple[int | None, dict | None]:
    det_model = _norm(det_row.get("Model/SKU"))
    if det_model:
        for i, ai_row in enumerate(ai_rows):
            if i in used:
                continue
            if _norm(ai_row.get("Model/SKU")) == det_model:
                return i, ai_row
    if index < len(ai_rows) and index not in used:
        return index, ai_rows[index]
    det_name = _norm(det_row.get("Product Name"))
    if det_name:
        for i, ai_row in enumerate(ai_rows):
            if i in used:
                continue
            ai_name = _norm(ai_row.get("Product Name"))
            if ai_name and (det_name in ai_name or ai_name in det_name):
                return i, ai_row
    return None, None


def merge_ai_rows_with_deterministic(deterministic_rows: list[dict], ai_rows: list[dict]) -> list[dict]:
    merged_rows: list[dict] = []
    used_ai: set[int] = set()
    for index, det_row in enumerate(deterministic_rows):
        ai_index, ai_row = _find_matching_ai_row(det_row, ai_rows, used_ai, index)
        if ai_index is not None:
            used_ai.add(ai_index)
        if not ai_row:
            merged_rows.append(_stamp_parse_debug(det_row, det_row, None, ai_used=False, ai_skipped_reason="AI returned no matching row"))
            continue

        merged = dict(det_row)
        for field in _MERGE_FIELDS:
            if field in ai_row or field in det_row:
                merged[field] = _choose_final_value(field, det_row.get(field), ai_row.get(field))

        for field in ("Confidence Score", "Review Required", "Missing Fields", "Suggested Action", "AI Category Confidence", "Category Source"):
            if field in ai_row and _str_val(ai_row.get(field)) != "":
                merged[field] = ai_row.get(field)
        merged["Source Type"] = "PDF_AI"
        merged["parse_confidence"] = _parse_confidence(merged)
        if not _str_val(merged.get("Status")) or _parse_confidence(merged) >= 75:
            merged["Status"] = "Ready for Review" if _parse_confidence(merged) >= 75 else "Needs Review"
        merged_rows.append(_stamp_parse_debug(merged, det_row, ai_row, ai_used=True))

    for i, ai_row in enumerate(ai_rows):
        if i in used_ai:
            continue
        merged_rows.append(_stamp_parse_debug(ai_row, None, ai_row, ai_used=True))

    return merged_rows


def should_run_ai_parse(deterministic_rows: list[dict]) -> tuple[bool, str]:
    if not deterministic_rows:
        return True, "deterministic parser returned no rows"
    low_conf = [row for row in deterministic_rows if _parse_confidence(row) < 90]
    if low_conf:
        return True, "deterministic parse missing critical fields"
    return False, "deterministic parse complete"


# ── Public entry point ─────────────────────────────────────────────────────────


def extract_products_from_pdf_with_ai(
    pdf_file,
    project_name: str,
    default_room: str,
    supplier: str,
    structured_rows: list[dict] | None = None,
    stage_timings: dict | None = None,
) -> tuple[pd.DataFrame, str | None]:
    """
    Send a PDF quote sheet to Claude and return extracted product rows.

    Parameters
    ----------
    pdf_file      : Streamlit UploadedFile (must support .read() and .seek()).
    project_name  : Project name from the metadata form.
    default_room  : Room dropdown value — fallback when the quote has no per-line room.
    supplier      : Supplier from the metadata form.

    Returns
    -------
    (df, error_message)
    df            : DataFrame of extracted rows aligned to _OUTPUT_COLUMNS.
                    Empty DataFrame on failure.
    error_message : None on success; human-readable string on any failure so the
                    caller can display it and fall back to a placeholder row.
    """
    if not ANTHROPIC_API_KEY:
        return pd.DataFrame(), (
            "AI extraction requires ANTHROPIC_API_KEY. "
            "Add it to your .env file and restart the app."
        )

    # ── 1. Extract text from PDF ───────────────────────────────────────────────
    try:
        pdf_text = _read_pdf_text(pdf_file)
    except ImportError as exc:
        return pd.DataFrame(), str(exc)
    except Exception as exc:
        return pd.DataFrame(), f"Could not read '{pdf_file.name}': {exc}"

    if not pdf_text.strip():
        return pd.DataFrame(), (
            f"No extractable text in '{pdf_file.name}'. "
            "The file may be image-based (scanned). AI extraction requires a text-layer PDF."
        )

    # ── 1b. Use heuristic parser output for structured context ─────────────────
    if structured_rows is None:
        try:
            structured_rows = parse_pdf_rows(pdf_file, project_name, default_room, supplier)
        except Exception:
            structured_rows = []

    # ── 2. Call Claude ─────────────────────────────────────────────────────────
    try:
        import anthropic
    except ImportError:
        return pd.DataFrame(), (
            "The 'anthropic' package is not installed. Run: pip install anthropic"
        )

    try:
        ai_start = time.perf_counter()
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            temperature=0,
            messages=[
                {"role": "user", "content": _build_prompt(
                    pdf_text, project_name, default_room, supplier, structured_rows
                )}
            ],
        )
        response_text = message.content[0].text
        if stage_timings is not None:
            stage_timings["ai_extraction_ms"] = stage_timings.get("ai_extraction_ms", 0.0) + (
                time.perf_counter() - ai_start
            ) * 1000
    except Exception as exc:
        return pd.DataFrame(), f"AI API call failed for '{pdf_file.name}': {exc}"

    # ── 3. Parse JSON response ─────────────────────────────────────────────────
    try:
        items = _parse_ai_response(response_text)
    except (ValueError, json.JSONDecodeError) as exc:
        return pd.DataFrame(), (
            f"Could not parse AI response for '{pdf_file.name}': {exc}"
        )

    if not items:
        return pd.DataFrame(), (
            f"AI returned no product rows for '{pdf_file.name}'. "
            "The document may not contain recognisable product lines."
        )

    # ── 4. Build DataFrame ─────────────────────────────────────────────────────
    rows = [_item_to_row(item, project_name, default_room, supplier) for item in items]
    df = pd.DataFrame(rows)

    # Guarantee all expected columns exist with safe defaults
    _defaults: dict = {
        "Include": True,
        "Review Required": False,
        "Quantity": 1,
        "Confidence Score": 0,
        "AI Category Confidence": 0,
        "Category Source": "Unknown",
    }
    for col in _OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = _defaults.get(col, "")

    for col in _PARSE_DEBUG_FIELDS:
        if col not in df.columns:
            df[col] = ""

    return df[[*_OUTPUT_COLUMNS, *_PARSE_DEBUG_FIELDS]], None


def augment_pdf_rows_with_ai(
    pdf_file,
    deterministic_rows: list[dict],
    project_name: str,
    default_room: str,
    supplier: str = "",
    stage_timings: dict | None = None,
) -> tuple[list[dict], str | None]:
    """Run AI source-document extraction only when deterministic parsing is incomplete."""
    should_run, reason = should_run_ai_parse(deterministic_rows)
    if not should_run:
        return stamp_deterministic_parse_debug(deterministic_rows, reason), None

    df, error = extract_products_from_pdf_with_ai(
        pdf_file,
        project_name,
        default_room,
        supplier,
        structured_rows=deterministic_rows,
        stage_timings=stage_timings,
    )
    if error:
        return stamp_deterministic_parse_debug(deterministic_rows, error), error

    ai_rows = df.to_dict("records")
    if not deterministic_rows:
        return [
            _stamp_parse_debug(row, None, row, ai_used=True)
            for row in ai_rows
        ], None
    return merge_ai_rows_with_deterministic(deterministic_rows, ai_rows), None
