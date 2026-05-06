"""
Programa send eligibility rules shared by the legacy app and API backend.
"""

from __future__ import annotations

from src.dimensions import has_complete_3d_dimensions

BLOCKED_STATUSES = {"Ignored", "Excluded", "Error"}


def _text(value: object) -> str:
    return str(value or "").strip()


def _is_photo_only(row: dict) -> bool:
    value = _text(row.get("photo_only")).lower()
    return (
        value in {"true", "1", "yes"}
        or _text(row.get("Import Type")).lower() == "photo upload"
        or _text(row.get("Source Type")) == "Photo"
    )


def _is_public_https_url(value: object) -> bool:
    return _text(value).lower().startswith("https://")


def evaluate_programa_eligibility(row: dict, allow_blank_fields: bool = False) -> tuple[bool, list[str]]:
    """
    Return whether a row may be sent to Programa and plain-language blockers.
    """
    blockers: list[str] = []

    if row.get("Include", True) is not True:
        blockers.append("Not included")
    if _text(row.get("Status")) in BLOCKED_STATUSES:
        blockers.append("Ignored or excluded")
    if _is_photo_only(row):
        if not _text(row.get("Product Name")):
            blockers.append("Missing product name")
        if not _text(row.get("Product Category")):
            blockers.append("Missing category")
        if not _is_public_https_url(row.get("Image URL")):
            blockers.append("Missing hosted image URL")
        return len(blockers) == 0, blockers
    if allow_blank_fields:
        return len(blockers) == 0, blockers

    if row.get("Review Required", False) is True:
        blockers.append("Needs review")
    if not _text(row.get("Product Name")):
        blockers.append("Missing product name")
    if not _text(row.get("Brand")):
        blockers.append("Missing brand")
    if not has_complete_3d_dimensions(row.get("Dimensions")):
        blockers.append("Missing full W x H x D dimensions")
    try:
        if int(row.get("Quantity", 0) or 0) < 1:
            blockers.append("Missing quantity")
    except (TypeError, ValueError):
        blockers.append("Missing quantity")
    if not _text(row.get("Supplier")):
        blockers.append("Missing supplier")
    if not _text(row.get("Room")):
        blockers.append("Missing location")
    if not _text(row.get("Product Category")):
        blockers.append("Missing category")

    return len(blockers) == 0, blockers


def split_eligible_rows(rows: list[dict], allow_blank_fields: bool = False) -> tuple[list[dict], list[dict]]:
    """Return (eligible_rows, blocked_rows_with_reasons)."""
    eligible: list[dict] = []
    blocked: list[dict] = []

    for row in rows:
        ok, reasons = evaluate_programa_eligibility(row, allow_blank_fields=allow_blank_fields)
        if ok:
            eligible.append(row)
        else:
            blocked.append({**row, "Eligibility Issues": reasons})

    return eligible, blocked
