from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path

from dotenv import load_dotenv

from src.ai_extraction import ALLOWED_CATEGORIES, extract_json_array_from_text
from src.image_uploader import (
    is_public_https_image_url,
    public_https_url_is_accessible,
    upload_image,
)
from src.intake_schema import SOURCE_PHOTO, make_base_row

load_dotenv()

PHOTO_ONLY_NOTE = "Photo-only item; details generated from uploaded image."
PHOTO_ONLY_BULK_NOTE = "Photo-only inventory item."
MISSING_IMAGE_STATUS = "Missing Image"

_SUPPORTED_AI_MEDIA_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}


def filename_to_product_name(filename: str) -> str:
    stem = Path(filename or "Photo item").stem
    cleaned = stem.replace("_", " ").replace("-", " ").strip()
    return " ".join(cleaned.split()).title() or "Photo Item"


def filename_stem_as_product_name(filename: str) -> str:
    """Return the original filename stem for fast photo-only imports."""
    stem = Path(filename or "").stem.strip()
    return stem or "photo_item"


def generated_photo_item_name(index: int) -> str:
    return f"Photo Item {max(1, int(index)):03d}"


def build_photo_only_bulk_product_names(
    photos: list[dict],
    naming_mode: str,
    default_product_name: str = "",
    append_sequence: bool = True,
) -> list[str]:
    """Derive fast photo-only product names, preserving input order."""
    default_name = str(default_product_name or "").strip()
    if default_name:
        return [
            f"{default_name} {idx:03d}" if append_sequence else default_name
            for idx, _photo in enumerate(photos, start=1)
        ]

    names: list[str] = []
    for idx, photo in enumerate(photos, start=1):
        filename = str(photo.get("image_filename", "") or "")
        if naming_mode == "Generated names":
            names.append(generated_photo_item_name(idx))
        else:
            names.append(filename_stem_as_product_name(filename))
    return names


def _photo_prompt(filename: str) -> str:
    categories = ", ".join(ALLOWED_CATEGORIES)
    return f"""Analyze this uploaded product photo for a Programa import row.

The item is handmade, vintage, custom, or otherwise photo-only. Do not invent brand, SKU, model number, product URL, or exact dimensions.

Filename fallback: {filename_to_product_name(filename)}
Allowed sections/categories: {categories}

Return ONLY a raw JSON object with these keys:
{{
  "product_name": "short human product name, or filename fallback if unclear",
  "product_category": "one allowed category",
  "description": "one concise sentence describing visible form/use",
  "color": "visible dominant color(s), or empty string",
  "material": "only if visually obvious, otherwise empty string",
  "notes": "short human-readable review note"
}}"""


def analyze_photo_with_ai(image_path: str, filename: str) -> tuple[dict, str | None]:
    """Return AI-suggested photo-only product fields, or a fallback dict plus error."""
    fallback = {
        "product_name": filename_to_product_name(filename),
        "product_category": "",
        "description": "",
        "color": "",
        "material": "",
        "notes": PHOTO_ONLY_NOTE,
    }
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return fallback, "AI photo analysis requires ANTHROPIC_API_KEY."

    media_type = mimetypes.guess_type(filename or image_path)[0] or "image/jpeg"
    if media_type not in _SUPPORTED_AI_MEDIA_TYPES:
        return fallback, f"Unsupported image type for AI analysis: {media_type}."

    try:
        import anthropic
    except ImportError:
        return fallback, "The 'anthropic' package is not installed. Run: pip install anthropic"

    try:
        raw = Path(image_path).read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": encoded,
                            },
                        },
                        {"type": "text", "text": _photo_prompt(filename)},
                    ],
                }
            ],
        )
        text = message.content[0].text
        obj = json.loads(text.strip())
        if not isinstance(obj, dict):
            raise ValueError("AI response was not a JSON object.")
        return {**fallback, **obj}, None
    except json.JSONDecodeError:
        try:
            items = extract_json_array_from_text(text)
            if items and isinstance(items[0], dict):
                return {**fallback, **items[0]}, None
        except Exception:
            pass
        return fallback, "Could not parse AI photo analysis response."
    except Exception as exc:
        return fallback, f"AI photo analysis failed for '{filename}': {exc}"


def upload_image_to_cloudinary(image_path: str) -> tuple[str, str | None]:
    """Upload an image to Cloudinary and return its public secure_url."""
    if not (
        os.getenv("CLOUDINARY_CLOUD_NAME")
        and os.getenv("CLOUDINARY_API_KEY")
        and os.getenv("CLOUDINARY_API_SECRET")
    ):
        return "", "Cloudinary upload requires CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET."
    try:
        with open(image_path, "rb") as fh:
            secure_url = upload_image(fh)
        if not secure_url:
            return "", f"Cloudinary upload failed or did not return secure_url for '{Path(image_path).name}'."
        if not is_public_https_image_url(secure_url):
            return "", f"Cloudinary returned a non-HTTPS image URL for '{Path(image_path).name}'."
        if not public_https_url_is_accessible(secure_url):
            return "", f"Cloudinary image URL is not publicly accessible for '{Path(image_path).name}'."
        return secure_url, None
    except Exception as exc:
        return "", f"Cloudinary upload failed for '{Path(image_path).name}': {exc}"


def create_photo_inventory_row(
    photo: dict,
    project: str,
    room: str,
    ai_fields: dict | None = None,
    image_url: str = "",
    status_note: str = "",
) -> dict:
    """Create one photo-first row with AI draft data and no manufacturer lookup fields."""
    ai_fields = ai_fields or {}
    filename = str(photo.get("image_filename", "") or "")
    product_name = str(ai_fields.get("product_name", "") or "").strip() or filename_to_product_name(filename)
    category = str(ai_fields.get("product_category", "") or "").strip()
    color = str(ai_fields.get("color", "") or "").strip()
    material = str(ai_fields.get("material", "") or "").strip()
    description = str(ai_fields.get("description", "") or "").strip()
    ai_notes = str(ai_fields.get("notes", "") or "").strip()

    note_parts = [PHOTO_ONLY_NOTE]
    if description:
        note_parts.append(description)
    if ai_notes and ai_notes != PHOTO_ONLY_NOTE:
        note_parts.append(ai_notes)
    if status_note:
        note_parts.append(status_note)

    row = make_base_row(project, room, "", "")
    row.update(
        {
            "Product Name": product_name,
            "Brand": "",
            "Dimensions": "",
            "Finish / Color": color,
            "Color": color,
            "Material": material,
            "Model/SKU": "",
            "Product Category": category,
            "Quantity": 1,
            "Price": "",
            "Supplier": "",
            "Product URL": "",
            "Notes": " ".join(part for part in note_parts if part).strip(),
            "Source Type": SOURCE_PHOTO,
            "Import Type": "Photo Inventory Upload",
            "photo_only": True,
            "Status": "Needs Review",
            "Image URL": image_url,
            "Local Image Path": "",
            "Image Filename": filename,
            "Image Upload Status": "Uploaded" if image_url else str(photo.get("image_upload_status", "") or MISSING_IMAGE_STATUS),
        }
    )
    return row


def create_photo_only_bulk_row(
    photo: dict,
    project: str,
    room: str,
    section: str,
    product_name: str,
    image_url: str,
    image_upload_status: str = "",
) -> dict:
    """Create one fast photo-only Programa import row without AI analysis."""
    row = make_base_row(project, room, "", "")
    row.update(
        {
            "Product Name": product_name.strip() or filename_stem_as_product_name(str(photo.get("image_filename", ""))),
            "Brand": "",
            "Dimensions": "",
            "Finish / Color": "",
            "Color": "",
            "Material": "",
            "Model/SKU": "",
            "Product Category": section.strip() or "General",
            "Quantity": 1,
            "Price": "",
            "Supplier": "",
            "Product URL": "",
            "Notes": PHOTO_ONLY_BULK_NOTE,
            "Source Type": SOURCE_PHOTO,
            "Import Type": "Photo-only Bulk Import",
            "photo_only": True,
            "Status": "Needs Review",
            "Image URL": image_url.strip(),
            "Local Image Path": "",
            "Image Filename": str(photo.get("image_filename", "") or ""),
            "Image Upload Status": image_upload_status or ("Uploaded" if image_url else MISSING_IMAGE_STATUS),
        }
    )
    return row


def create_photo_only_bulk_rows(
    photos: list[dict],
    project: str,
    room: str,
    section: str,
    naming_mode: str,
    image_urls: list[str],
    upload_statuses: list[str] | None = None,
    default_product_name: str = "",
    append_sequence: bool = True,
) -> list[dict]:
    """Create one fast photo-only row per photo, preserving input order."""
    rows: list[dict] = []
    statuses = upload_statuses or []
    product_names = build_photo_only_bulk_product_names(
        photos,
        naming_mode=naming_mode,
        default_product_name=default_product_name,
        append_sequence=append_sequence,
    )
    for idx, photo in enumerate(photos, start=1):
        product_name = product_names[idx - 1] if idx - 1 < len(product_names) else ""
        image_url = image_urls[idx - 1] if idx - 1 < len(image_urls) else ""
        status = statuses[idx - 1] if idx - 1 < len(statuses) else ""
        rows.append(
            create_photo_only_bulk_row(
                photo,
                project=project,
                room=room,
                section=section,
                product_name=product_name,
                image_url=image_url,
                image_upload_status=status,
            )
        )
    return rows
