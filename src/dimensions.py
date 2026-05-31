"""
Shared dimension parsing helpers for SCH DesignOps Intake.

The send-to-Programa gate is intentionally strict: a dimension string is only
complete when width, height, and depth are explicitly labeled.
"""

from __future__ import annotations

import re

_NUM = r"\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?"

_AFTER_LABEL_RE = re.compile(
    rf"(?P<num>{_NUM})\s*(?:[\"“”″']|\bin(?:ches)?\b|\s)*\s*"
    r"(?P<label>[WwHhDdLl])(?![a-zA-Z])"
)

_BEFORE_LABEL_RE = re.compile(
    rf"\b(?P<label>width|wide|w|height|high|h|depth|deep|d|length|long|l)\b"
    rf"\s*(?:[:=]|is|of)?\s*(?P<num>{_NUM})",
    re.IGNORECASE,
)

_LABEL_MAP = {
    "w": "width",
    "width": "width",
    "wide": "width",
    "h": "height",
    "height": "height",
    "high": "height",
    "d": "depth",
    "depth": "depth",
    "deep": "depth",
    "l": "length",
    "length": "length",
    "long": "length",
}


def _fraction_to_float(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    mixed = re.match(r"^(\d+)\s+(\d+)/(\d+)$", text)
    if mixed:
        whole, num, den = int(mixed.group(1)), int(mixed.group(2)), int(mixed.group(3))
        return None if den == 0 else whole + (num / den)
    fraction = re.match(r"^(\d+)/(\d+)$", text)
    if fraction:
        num, den = int(fraction.group(1)), int(fraction.group(2))
        return None if den == 0 else num / den
    try:
        return float(text)
    except ValueError:
        return None


def extract_labeled_dimensions(dimensions: object) -> dict[str, str]:
    """
    Return labeled dimension components from a free-form string.

    Supports both number-first labels such as 36"W and word-first labels such as
    Width 36. Values are returned as originally written; conversion to decimals
    happens in Programa-specific code.
    """
    result = {"width": "", "height": "", "depth": "", "length": ""}
    try:
        text = str(dimensions or "").strip()
    except Exception:
        return result
    if not text:
        return result

    for match in _AFTER_LABEL_RE.finditer(text):
        key = _LABEL_MAP.get(match.group("label").lower())
        if key and not result[key]:
            result[key] = match.group("num").strip()

    for match in _BEFORE_LABEL_RE.finditer(text):
        key = _LABEL_MAP.get(match.group("label").lower())
        if key and not result[key]:
            result[key] = match.group("num").strip()

    return result


def has_complete_3d_dimensions(dimensions: object) -> bool:
    """True only when width, height, and depth are explicitly present."""
    try:
        if dimensions is None:
            return False
        text = str(dimensions or "").strip()
        if not text or text.lower() in {"nan", "none", "null"}:
            return False
        parts = extract_labeled_dimensions(text)
        return bool(parts["width"] and parts["height"] and parts["depth"])
    except Exception:
        return False


def dimension_sanity_reason(dimensions: object, category: object = "") -> str:
    """
    Return a rejection reason when dimensions are clearly not product dimensions.

    The bounds are intentionally broad. They only catch obvious extraction failures
    such as page numbers/manual fragments becoming dimensions.
    """
    try:
        text = str(dimensions or "").strip()
    except Exception:
        return "unreadable dimensions"
    if not text or text.lower() in {"nan", "none", "null"}:
        return ""

    lowered = text.lower()
    if re.search(r"\b(shipping|package|packaged|carton|box)\b", lowered):
        return "shipping/package dimensions"
    if re.search(r"\b(cutout|rough[- ]?in|opening|clearance)\b", lowered):
        return "cutout/opening dimensions"

    parts = extract_labeled_dimensions(text)
    values: dict[str, float] = {}
    for key, raw_value in parts.items():
        if not raw_value:
            continue
        parsed = _fraction_to_float(raw_value)
        if parsed is not None:
            values[key] = parsed

    if not values:
        return ""
    if any(value <= 0 for value in values.values()):
        return "non-positive dimension value"
    if any(value > 300 for value in values.values()):
        return "dimension exceeds plausible product bounds"

    category_key = str(category or "").lower()
    if "appliance" in category_key:
        appliance_bounds = {
            "width": (6, 96),
            "height": (3, 120),
            "depth": (6, 60),
            "length": (3, 120),
        }
        for key, value in values.items():
            low, high = appliance_bounds.get(key, (1, 300))
            if value < low:
                return f"{key} below plausible appliance bounds"
            if value > high:
                return f"{key} exceeds plausible appliance bounds"
        if len(values) >= 3 and max(values.values()) < 6:
            return "all appliance dimensions are implausibly small"
    return ""
