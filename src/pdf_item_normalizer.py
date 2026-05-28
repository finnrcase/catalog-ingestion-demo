"""Quote item grouping for PDF product extraction.

This module separates variable extraction from item construction. PDF text
often arrives as isolated cells such as brand, model, description, and color;
those cells should populate one quote item, not become independent rows.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable

from src.intake_schema import SOURCE_PDF, make_base_row


_SPACE_RE = re.compile(r"\s+")
_ITEM_NUMBER_RE = re.compile(r"^\s*(?:item\s*)?(?P<num>\d{1,3})\s*$", re.IGNORECASE)
_ROW_START_RE = re.compile(r"^\s*(?P<num>\d{1,3})\s+(?P<rest>.+)$")
_MODEL_LABEL_RE = re.compile(
    r"\b(?:model|model\s*#|model\s*no\.?|sku|serial|item\s*#|part\s*#|mfr\.?\s*#?)\s*[:#-]?\s*",
    re.IGNORECASE,
)
_MODEL_TOKEN_RE = re.compile(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9./_-]{3,}")
_PRICE_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?|\b\d{1,6}\.\d{2}\b")
_PRICE_SKIP_RE = re.compile(r"\b(?:warranty|protection|service\s*plan|extended\s*service|years?)\b", re.IGNORECASE)
_PRICE_LABEL_RE = re.compile(r"\b(?:price|unit\s*price|ext(?:ended)?\s*price|amount|subtotal|total)\b", re.IGNORECASE)
_DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}")

_HEADER_OR_JUNK_RE = re.compile(
    r"\b("
    r"quotation|quote\s*sheet|appliance\s*selection|salesperson|sales\s*person|"
    r"prepared\s*by|customer|client|phone|fax|email|address|terms|"
    r"manufacturer|model|description|color|finish|qty|quantity|unit\s*price|price|"
    r"extended|extension|warranty|years?|protection|subtotal|tax|total|delivery|"
    r"shipping|freight|deposit|balance"
    r")\b",
    re.IGNORECASE,
)

_COLUMN_HEADER_TOKENS = {
    "item",
    "item no",
    "item number",
    "manufacturer",
    "brand",
    "model",
    "model number",
    "description",
    "color",
    "finish",
    "qty",
    "quantity",
    "price",
    "unit price",
    "ext price",
    "extended price",
}

_BRAND_ALIASES = {
    "ASKO": "Asko",
    "BEST": "Best",
    "BOSCH": "Bosch",
    "BROAN": "Broan",
    "COVE": "Cove",
    "FISHER PAYKEL": "Fisher & Paykel",
    "FISHER & PAYKEL": "Fisher & Paykel",
    "GE": "GE",
    "JENNAIR": "JennAir",
    "JENN AIR": "JennAir",
    "KITCHENAID": "KitchenAid",
    "KITCHEN AID": "KitchenAid",
    "LYNX": "Lynx",
    "MIELE": "Miele",
    "MONOGRAM": "Monogram",
    "SCOTSMAN": "Scotsman",
    "SCOTTSMAN": "Scotsman",
    "SHARP": "Sharp",
    "SUB ZERO": "Sub-Zero",
    "SUB-ZERO": "Sub-Zero",
    "SUBZERO": "Sub-Zero",
    "THERMADOR": "Thermador",
    "VIKING": "Viking",
    "WOLF": "Wolf",
}

_FINISH_ALIASES = {
    "BLK": "Black",
    "BLACK": "Black",
    "PANEL": "Panel Ready",
    "PANEL READY": "Panel Ready",
    "PNL": "Panel Ready",
    "SS": "Stainless Steel",
    "STAINLESS": "Stainless Steel",
    "STAINLESS STEEL": "Stainless Steel",
    "WH": "White",
    "WHITE": "White",
}

_ROOM_HINTS = {
    "bar": "Bar",
    "basement": "Basement",
    "butler": "Butler's Pantry",
    "kitchen": "Kitchen",
    "laundry": "Laundry",
    "mudroom": "Mudroom",
    "outdoor": "Outdoor",
    "pantry": "Pantry",
}


@dataclass
class QuoteContext:
    project: str = ""
    supplier: str = ""
    category: str = ""
    quote_date: str = ""


@dataclass
class ItemGroup:
    item_number: str = ""
    lines: list[str] = field(default_factory=list)


def build_quote_item_rows(
    lines: Iterable[str],
    *,
    project: str = "",
    room: str = "",
    supplier: str = "",
    notes: str = "",
    context_lines: Iterable[str] | None = None,
) -> list[dict]:
    """Build one row per quote item from raw PDF table/text fragments."""
    grouping_lines = _clean_lines(lines)
    if not grouping_lines:
        return []

    all_context_lines = _clean_lines(context_lines) if context_lines is not None else grouping_lines
    context = extract_quote_context(
        [*all_context_lines, *grouping_lines],
        fallback_project=project,
        fallback_supplier=supplier,
    )
    groups = _group_by_item_number(grouping_lines)
    if not groups:
        groups = _group_by_brand_model(grouping_lines)

    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for group in groups:
        row = _row_from_group(group, context, fallback_room=room, notes=notes)
        if row is None:
            continue
        key = (
            _norm_key(row.get("Brand")),
            _norm_key(row.get("Model/SKU")),
            _norm_key(row.get("Product Name")),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def extract_quote_context(
    lines: Iterable[str],
    *,
    fallback_project: str = "",
    fallback_supplier: str = "",
) -> QuoteContext:
    cleaned = _clean_lines(lines)
    project = str(fallback_project or "").strip()
    supplier = str(fallback_supplier or "").strip()
    quote_date = ""
    category = ""

    for index, line in enumerate(cleaned):
        lower = line.lower()
        if not project:
            match = re.search(r"\bproject\s*[:#-]?\s*(.+)$", line, re.IGNORECASE)
            if match and _usable_context_value(match.group(1)):
                project = _tidy_text(match.group(1))
            elif lower.rstrip(":") == "project":
                next_value = _next_context_value(cleaned, index)
                if next_value:
                    project = next_value

        if not supplier and re.search(r"\bpc\s*richard\b", line, re.IGNORECASE):
            supplier = "PC Richard"

        if not quote_date and ("date" in lower or _DATE_RE.search(line)):
            match = _DATE_RE.search(line)
            if match:
                quote_date = match.group(0)

        if not category and ("appliance" in lower or _find_brand(line)):
            category = "Appliances"

    return QuoteContext(project=project, supplier=supplier, category=category, quote_date=quote_date)


def _group_by_item_number(lines: list[str]) -> list[ItemGroup]:
    groups: list[ItemGroup] = []
    current: ItemGroup | None = None
    saw_item_start = False

    for index, line in enumerate(lines):
        if _is_item_start(lines, index):
            saw_item_start = True
            if current and _group_has_product_signal(current.lines):
                groups.append(current)
            current = ItemGroup(item_number=_extract_item_number(line), lines=[line])
            continue

        if current is None:
            continue
        current.lines.append(line)

    if current and _group_has_product_signal(current.lines):
        groups.append(current)

    return groups if saw_item_start else []


def _group_by_brand_model(lines: list[str]) -> list[ItemGroup]:
    groups: list[ItemGroup] = []
    current: ItemGroup | None = None

    for line in lines:
        if _is_header_or_junk(line):
            continue
        starts_product = bool(_find_brand(line) or _find_model(line))
        if starts_product and current and _group_has_product_signal(current.lines):
            groups.append(current)
            current = ItemGroup(lines=[line])
        elif starts_product:
            current = ItemGroup(lines=[line])
        elif current is not None:
            current.lines.append(line)

    if current and _group_has_product_signal(current.lines):
        groups.append(current)
    return groups


def _row_from_group(group: ItemGroup, context: QuoteContext, *, fallback_room: str, notes: str) -> dict | None:
    raw_lines = [_tidy_text(line) for line in group.lines if _tidy_text(line)]
    useful_lines = [line for line in raw_lines if not _is_header_or_junk(line)]
    group_text = "\n".join(useful_lines)
    if not group_text:
        return None

    brand = _find_brand(group_text)
    model = _find_model(group_text)
    description = _extract_description(useful_lines, brand, model)
    finish = _extract_finish(useful_lines)
    quantity = _extract_quantity_from_group(useful_lines, group.item_number)
    price = _extract_price(group_text)
    inferred_room = _infer_room(group_text) or fallback_room

    if not model and not _strong_description(description):
        return None

    product_name = _clean_product_name(_product_name(brand, description, model), brand=brand, model=model, room=inferred_room)
    row = make_base_row(
        project=context.project,
        room=inferred_room,
        supplier=context.supplier,
        notes=notes,
    )
    row.update(
        {
            "Product Name": product_name,
            "Brand": brand,
            "Model/SKU": model,
            "Model": model,
            "Finish / Color": finish,
            "Color": finish,
            "Product Category": context.category or ("Appliances" if brand else ""),
            "Quantity": quantity,
            "Price": price,
            "Source Type": SOURCE_PDF,
            "Status": "Needs Enrichment" if model else "Needs Review",
            "_item_number": group.item_number,
            "_item_description": description,
            "_quote_date": context.quote_date,
            "_raw_grouped_text": "\n".join(raw_lines),
            "_parsed_fields": json.dumps(
                {
                    "item_number": group.item_number,
                    "brand": brand,
                    "model": model,
                    "description": description,
                    "finish": finish,
                    "quantity": quantity,
                    "price": price,
                    "project": context.project,
                    "supplier": context.supplier,
                    "category": context.category,
                    "quote_date": context.quote_date,
                },
                sort_keys=True,
            ),
            "_enrichment_query_used": " ".join(part for part in (brand, model, description) if part),
        }
    )
    row["_extracted_model_sku"] = model
    row["_extraction_confidence"] = _extraction_confidence(brand, model, description, finish)
    row["_confidence_reason"] = _confidence_reason(brand, model, description, finish)
    row["_missing_fields_initial"] = ", ".join(_initial_missing_fields(row))
    return row


def _clean_lines(lines: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in lines:
        text = _tidy_text(raw)
        if not text:
            continue
        if "|" in text:
            parts = [_tidy_text(part) for part in text.split("|")]
            cleaned.extend(part for part in parts if part)
        else:
            cleaned.append(text)
    return cleaned


def _tidy_text(value: object) -> str:
    text = str(value or "").replace("\xa0", " ").strip()
    return _SPACE_RE.sub(" ", text).strip(" \t\r\n")


def _norm_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _brand_lookup_key(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _find_brand(text: str) -> str:
    key_text = f" {_brand_lookup_key(text)} "
    words = re.findall(r"[A-Za-z&-]+", text.upper())
    word_keys = {_brand_lookup_key(word) for word in words}
    for alias, canonical in sorted(_BRAND_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        alias_key = _brand_lookup_key(alias)
        if alias_key == "GE":
            if alias_key in word_keys:
                return canonical
            continue
        if alias_key in key_text:
            return canonical
    return ""


def _find_model(text: str) -> str:
    labelled = _MODEL_LABEL_RE.sub(" ", text)
    for candidate in _MODEL_TOKEN_RE.findall(labelled):
        model = candidate.strip(".,;:()[]")
        if _is_model_candidate(model):
            return model.upper()
    return ""


def _is_model_candidate(value: str) -> bool:
    token = value.strip(".,;:()[]")
    if len(token) < 4 or len(token) > 32:
        return False
    if token.isdigit() or token.upper() in _FINISH_ALIASES:
        return False
    if _PRICE_RE.search(token) or _DATE_RE.search(token) or _EMAIL_RE.search(token) or _PHONE_RE.search(token):
        return False
    if token.lower() in {"model", "color", "price", "year", "years", "warranty"}:
        return False
    return bool(re.search(r"[A-Za-z]", token) and re.search(r"\d", token))


def _find_finish_token(text: str) -> str:
    cleaned = _tidy_text(_PRICE_RE.sub(" ", text)).upper().strip(" .,:;()[]")
    return _FINISH_ALIASES.get(cleaned, "")


def _extract_finish(lines: list[str]) -> str:
    for line in lines:
        finish = _find_finish_token(line)
        if finish:
            return finish
        match = re.search(r"\b(?:color|finish)\s*[:#-]?\s*(.+)$", line, re.IGNORECASE)
        if match:
            labelled = _tidy_text(match.group(1))
            return _FINISH_ALIASES.get(labelled.upper(), _title(labelled))
    return ""


def _extract_price(text: str) -> str:
    candidates: list[tuple[bool, float, str]] = []
    for line in str(text or "").splitlines():
        cleaned = _tidy_text(line)
        if not cleaned or _PRICE_SKIP_RE.search(cleaned):
            continue
        labelled = bool(_PRICE_LABEL_RE.search(cleaned))
        for match in _PRICE_RE.finditer(cleaned):
            raw = match.group(0)
            candidates.append((labelled, _price_amount(raw), raw))
    if not candidates:
        return ""
    labelled_candidates = [candidate for candidate in candidates if candidate[0]]
    pool = labelled_candidates or candidates
    return max(pool, key=lambda candidate: candidate[1])[2]


def _price_amount(raw: str) -> float:
    try:
        return float(re.sub(r"[^0-9.]", "", raw))
    except ValueError:
        return 0.0


def _extract_quantity_from_group(lines: list[str], item_number: str) -> int:
    labelled = re.search(r"\b(?:qty|quantity)\s*[:#-]?\s*(\d{1,3})\b", "\n".join(lines), re.IGNORECASE)
    if labelled:
        return max(1, int(labelled.group(1)))

    numeric = [line for line in lines if _ITEM_NUMBER_RE.match(line)]
    if len(numeric) >= 2:
        for candidate in reversed(numeric):
            value = _extract_item_number(candidate)
            if value != item_number:
                return max(1, int(value))
    return 1


def _extract_description(lines: list[str], brand: str, model: str) -> str:
    pieces: list[str] = []
    for line in lines:
        text = _tidy_text(line)
        if not text or _ITEM_NUMBER_RE.match(text):
            continue
        if _is_header_or_junk(text) or _PRICE_RE.fullmatch(text):
            continue
        if _find_finish_token(text):
            continue
        text = _remove_brand_and_model(text, brand, model)
        text = _PRICE_RE.sub(" ", text)
        text = re.sub(r"\b(?:qty|quantity)\s*[:#-]?\s*\d{1,3}\b", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"^\d{1,3}\b", " ", text).strip()
        text = re.sub(r"\b\d{1,3}$", " ", text).strip()
        text = _remove_finish_words(text)
        text = _tidy_text(text.strip(" -,:;"))
        if not text or _find_brand(text) == brand or _find_model(text) == model:
            continue
        if _strong_description(text):
            pieces.append(text)
    if not pieces:
        return ""
    description = " ".join(pieces)
    description = _remove_brand_and_model(description, brand, model)
    description = _remove_finish_words(description)
    return _title(_tidy_text(description.strip(" -,:;")))


def _remove_brand_and_model(text: str, brand: str, model: str) -> str:
    out = text
    if brand:
        for alias, canonical in _BRAND_ALIASES.items():
            if canonical == brand:
                out = re.sub(rf"\b{re.escape(alias)}\b", " ", out, flags=re.IGNORECASE)
        out = re.sub(rf"\b{re.escape(brand)}\b", " ", out, flags=re.IGNORECASE)
    if model:
        out = re.sub(rf"\b{re.escape(model)}\b", " ", out, flags=re.IGNORECASE)
    out = _MODEL_LABEL_RE.sub(" ", out)
    return _tidy_text(out)


def _remove_finish_words(text: str) -> str:
    out = text
    for alias in sorted(_FINISH_ALIASES, key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(alias)}\b", " ", out, flags=re.IGNORECASE)
    return _tidy_text(out)


def _strong_description(text: str) -> bool:
    words = re.findall(r"[A-Za-z]{3,}", text)
    return len(words) >= 2 or len(text.strip()) >= 12


def _product_name(brand: str, description: str, model: str) -> str:
    if brand and description:
        return f"{brand} {description}"
    if description:
        return description
    if brand and model:
        return f"{brand} {model}"
    return model


def _clean_product_name(name: str, *, brand: str, model: str, room: str) -> str:
    cleaned = _tidy_text(str(name or "").replace('"', " "))
    if brand and not re.search(rf"\b{re.escape(brand)}\b", cleaned, re.IGNORECASE):
        cleaned = f"{brand} {cleaned}".strip()
    cleaned = re.sub(r"\bfridge\b", "Refrigerator", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bdraws\b", "Drawers", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bdraw\b", "Drawer", cleaned, flags=re.IGNORECASE)
    if room:
        room_token = re.escape(room.split("'")[0])
        cleaned = re.sub(rf"\s+\b{room_token}\b$", "", cleaned, flags=re.IGNORECASE)
    cleaned = _tidy_text(cleaned.strip(" -,:;"))
    return _title(cleaned)


def _title(text: str) -> str:
    keep_upper = {"GE", "LED", "SS"}
    keep_case = {
        "sub-zero": "Sub-Zero",
        "jennair": "JennAir",
        "kitchenaid": "KitchenAid",
    }
    words = []
    for word in _tidy_text(text).split():
        stripped = word.strip()
        if stripped.lower() in keep_case:
            words.append(keep_case[stripped.lower()])
        elif stripped.upper() in keep_upper or (re.fullmatch(r"[A-Z0-9./_-]{4,}", stripped) and re.search(r"\d", stripped)):
            words.append(stripped.upper())
        else:
            words.append(stripped[:1].upper() + stripped[1:].lower())
    return " ".join(words)


def _is_header_or_junk(line: str) -> bool:
    text = _tidy_text(line)
    if not text:
        return True
    lower = text.lower().strip(" :")
    if lower in _COLUMN_HEADER_TOKENS:
        return True
    if _EMAIL_RE.search(text) or _PHONE_RE.search(text):
        return True
    if re.search(r"\b\d+\s+years?\b", text, re.IGNORECASE):
        return True
    if _HEADER_OR_JUNK_RE.search(text):
        if _find_brand(text) and _find_model(text):
            return False
        return True
    if re.search(r"\b(?:street|st\.|avenue|ave\.|road|rd\.|lane|ln\.|drive|dr\.)\b", text, re.IGNORECASE):
        return True
    return False


def _is_item_start(lines: list[str], index: int) -> bool:
    line = lines[index]
    item_match = _ITEM_NUMBER_RE.match(line)
    row_match = _ROW_START_RE.match(line)
    if not item_match and not row_match:
        return False
    if row_match and _group_has_product_signal([row_match.group("rest")]):
        return True
    return _following_window_has_product_signal(lines, index + 1)


def _following_window_has_product_signal(lines: list[str], start: int) -> bool:
    meaningful: list[str] = []
    for line in lines[start : min(len(lines), start + 14)]:
        if _is_header_or_junk(line):
            continue
        if _ITEM_NUMBER_RE.match(line) and meaningful:
            break
        meaningful.append(line)
        if _group_has_product_signal(meaningful):
            return True
    return False


def _group_has_product_signal(lines: list[str]) -> bool:
    text = "\n".join(line for line in lines if not _is_header_or_junk(line))
    if not text:
        return False
    brand = _find_brand(text)
    model = _find_model(text)
    if model:
        return True
    return bool(brand and _strong_description(_remove_brand_and_model(text, brand, "")))


def _extract_item_number(line: str) -> str:
    match = _ITEM_NUMBER_RE.match(line)
    if match:
        return match.group("num")
    match = _ROW_START_RE.match(line)
    return match.group("num") if match else ""


def _infer_room(text: str) -> str:
    lower = text.lower()
    for needle, room in _ROOM_HINTS.items():
        if re.search(rf"\b{re.escape(needle)}\b", lower):
            return room
    return ""


def _usable_context_value(value: str) -> bool:
    text = _tidy_text(value)
    lower = text.lower().strip(" :")
    if not text:
        return False
    if lower in _COLUMN_HEADER_TOKENS:
        return False
    if _EMAIL_RE.search(text) or _PHONE_RE.search(text):
        return False
    if re.search(r"\b(salesperson|sales\s*person|quotation|quote\s*sheet|date)\b", text, re.IGNORECASE):
        return False
    return len(text) > 2


def _next_context_value(lines: list[str], index: int) -> str:
    for line in lines[index + 1 : index + 5]:
        if _usable_context_value(line):
            return _tidy_text(line)
    return ""


def _extraction_confidence(brand: str, model: str, description: str, finish: str) -> int:
    score = 50
    if brand:
        score += 15
    if model:
        score += 20
    if description:
        score += 15
    if finish:
        score += 5
    return min(95, score)


def _confidence_reason(brand: str, model: str, description: str, finish: str) -> str:
    parts = []
    if brand:
        parts.append("brand")
    if model:
        parts.append("model")
    if description:
        parts.append("description")
    if finish:
        parts.append("finish")
    return "Grouped quote item with " + ", ".join(parts) if parts else "Grouped quote item"


def _initial_missing_fields(row: dict) -> list[str]:
    missing: list[str] = []
    for field in ("Project", "Room", "Product Name", "Brand", "Model/SKU", "Supplier", "Product Category", "Dimensions", "Image URL"):
        if not str(row.get(field) or "").strip():
            missing.append(field)
    return missing
