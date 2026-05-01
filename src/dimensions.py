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
