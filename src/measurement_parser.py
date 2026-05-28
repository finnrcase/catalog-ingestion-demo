"""Dimension parsing and normalization helpers."""

from __future__ import annotations

import re

_UNICODE_FRACTIONS = {
    "¼": "1/4",
    "½": "1/2",
    "¾": "3/4",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}

_NUM = r"\d+(?:\.\d+)?(?:\s+\d+/\d+)?|\d+/\d+"
_UNIT = r"(?:\"|in\.?|inches|inch|ft\.?|feet|foot|cm|mm)?"


def _clean(text: object) -> str:
    value = str(text or "")
    for frac, replacement in _UNICODE_FRACTIONS.items():
        value = value.replace(frac, f" {replacement}")
    return value.replace("×", "x").replace("Ｘ", "x")


def _to_float(raw: str, unit: str = "") -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    total = 0.0
    matched = False
    for part in raw.split():
        if "/" in part:
            try:
                num, den = part.split("/", 1)
                total += float(num) / float(den)
                matched = True
            except Exception:
                return None
        else:
            try:
                total += float(part)
                matched = True
            except Exception:
                return None
    if not matched:
        return None
    unit_l = unit.lower().strip(".")
    if unit_l in {"ft", "feet", "foot", "'"}:
        total *= 12
    elif unit_l == "cm":
        total /= 2.54
    elif unit_l == "mm":
        total /= 25.4
    return round(total, 4)


def _fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def parse_dimensions(text: object) -> dict[str, str]:
    """Parse labeled dimensions into normalized inch strings."""
    value = _clean(text)
    result = {"width": "", "height": "", "depth": "", "length": "", "diameter": ""}
    if not value.strip():
        return result

    labels = {
        "w": "width", "width": "width", "wide": "width",
        "h": "height", "height": "height", "high": "height",
        "d": "depth", "depth": "depth", "deep": "depth",
        "l": "length", "length": "length", "long": "length",
        "dia": "diameter", "diameter": "diameter",
    }

    after = re.compile(
        rf"(?P<num>{_NUM})\s*(?P<unit>{_UNIT})\s*(?P<label>W|H|D|L|Dia\.?|Diameter)\b",
        re.I,
    )
    before = re.compile(
        rf"\b(?P<label>Width|W|Height|H|Depth|D|Length|L|Diameter|Dia\.?)\b\s*[:=]?\s*(?P<num>{_NUM})\s*(?P<unit>{_UNIT})",
        re.I,
    )
    for regex in (after, before):
        for match in regex.finditer(value):
            key = labels.get(match.group("label").lower().rstrip("."))
            if key and not result[key]:
                result[key] = _fmt(_to_float(match.group("num"), match.group("unit")))

    # Pattern: W 36 x D 18 x H 72
    axis_first = re.compile(
        rf"\b(?P<label>W|H|D|L)\s*(?P<num>{_NUM})\s*(?P<unit>{_UNIT})",
        re.I,
    )
    for match in axis_first.finditer(value):
        key = labels.get(match.group("label").lower())
        if key and not result[key]:
            result[key] = _fmt(_to_float(match.group("num"), match.group("unit")))

    # Pattern: Overall 36 x 18 x 72. Use conventional W x D x H order.
    if not (result["width"] and result["height"] and result["depth"]):
        m = re.search(
            rf"(?:overall|dimensions?)?\s*[:\-]?\s*(?P<a>{_NUM})\s*(?P<unit_a>{_UNIT})\s*x\s*"
            rf"(?P<b>{_NUM})\s*(?P<unit_b>{_UNIT})\s*x\s*(?P<c>{_NUM})\s*(?P<unit_c>{_UNIT})",
            value,
            re.I,
        )
        if m:
            if not result["width"]:
                result["width"] = _fmt(_to_float(m.group("a"), m.group("unit_a")))
            if not result["depth"]:
                result["depth"] = _fmt(_to_float(m.group("b"), m.group("unit_b")))
            if not result["height"]:
                result["height"] = _fmt(_to_float(m.group("c"), m.group("unit_c")))

    return result


def combined_dimensions(parts: dict[str, str]) -> str:
    order = [("width", "W"), ("depth", "D"), ("height", "H"), ("length", "L"), ("diameter", "Dia.")]
    tokens = [f'{parts[k]}"{label}' for k, label in order if str(parts.get(k) or "").strip()]
    return " x ".join(tokens)


def normalize_dimension_fields(row: dict) -> tuple[dict, dict]:
    """Fill axis fields from Dimensions and Dimensions from axis fields."""
    updated = row.copy()
    debug = {"dimensions_conflict": False, "dimensions_conflict_details": ""}
    existing = _clean(updated.get("Dimensions"))
    parsed = parse_dimensions(existing)
    axis_map = {
        "width": "Width (in)",
        "height": "Height (in)",
        "depth": "Depth (in)",
        "length": "Length (in)",
    }
    for key, col in axis_map.items():
        if not str(updated.get(col, "") or "").strip() and parsed.get(key):
            updated[col] = parsed[key]
    axis_parts = {
        "width": str(updated.get("Width (in)", "") or "").strip(),
        "height": str(updated.get("Height (in)", "") or "").strip(),
        "depth": str(updated.get("Depth (in)", "") or "").strip(),
        "length": str(updated.get("Length (in)", "") or "").strip(),
        "diameter": str(updated.get("Diameter (in)", "") or "").strip(),
    }
    if not str(updated.get("Dimensions", "") or "").strip():
        combined = combined_dimensions(axis_parts)
        if combined:
            updated["Dimensions"] = combined
    return updated, debug
