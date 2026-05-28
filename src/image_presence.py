"""Shared image-presence helpers for UI, validation, recovery, and export."""

from __future__ import annotations

from pathlib import Path

IMAGE_PRESENCE_FIELDS = [
    "Image URL",
    "Image Filename",
    "local_image_path",
    "local_image_filename",
    "recovered_image_path",
    "image_path",
    "Local Image Path",
]


def _text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def row_has_image(row: dict) -> bool:
    """True when a row has any usable public or recovered local image field."""
    if _text(row.get("Image URL")).lower().startswith("https://"):
        return True
    return any(
        _text(row.get(field))
        for field in IMAGE_PRESENCE_FIELDS
        if field != "Image URL"
    )


def row_image_status(row: dict) -> str:
    if _text(row.get("Image URL")):
        return "Image URL"
    if _text(row.get("local_image_path")) or _text(row.get("Local Image Path")):
        return "Local Image"
    if _text(row.get("recovered_image_path")) or _text(row.get("image_path")):
        return "Recovered Image"
    if _text(row.get("Image Filename")) or _text(row.get("local_image_filename")):
        return "Image Filename"
    return "Missing"


def local_image_path(row: dict) -> str:
    for field in ("local_image_path", "Local Image Path", "recovered_image_path", "image_path"):
        value = _text(row.get(field))
        if value:
            return value
    return ""


def image_filename(row: dict) -> str:
    for field in ("local_image_filename", "Image Filename"):
        value = _text(row.get(field))
        if value:
            return Path(value).name
    path = local_image_path(row)
    return Path(path).name if path else ""
