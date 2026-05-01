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
6. Finish / Color: extract finish or colour if stated (e.g. "Matte Black", "Stainless Steel"). Leave empty string if not stated.
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


# ── Public entry point ─────────────────────────────────────────────────────────


def extract_products_from_pdf_with_ai(
    pdf_file,
    project_name: str,
    default_room: str,
    supplier: str,
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

    # ── 1b. Run heuristic parser for structured context ────────────────────────
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

    return df[_OUTPUT_COLUMNS], None
