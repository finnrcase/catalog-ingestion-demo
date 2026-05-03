from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from src.ai_extraction import ALLOWED_CATEGORIES, extract_json_array_from_text
from src.intake_schema import SOURCE_PHOTO, make_base_row

load_dotenv()

PHOTO_ONLY_NOTE = "Photo-only item; details generated from uploaded image."
PHOTO_ONLY_BULK_NOTE = "Photo-only inventory item."

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


def _cloudinary_config() -> tuple[str, str, str] | None:
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "").strip()
    api_key = os.getenv("CLOUDINARY_API_KEY", "").strip()
    api_secret = os.getenv("CLOUDINARY_API_SECRET", "").strip()
    cloudinary_url = os.getenv("CLOUDINARY_URL", "").strip()

    if cloudinary_url and not (cloud_name and api_key and api_secret):
        from urllib.parse import urlparse

        parsed = urlparse(cloudinary_url)
        if parsed.scheme == "cloudinary" and parsed.hostname and parsed.username and parsed.password:
            cloud_name = parsed.hostname
            api_key = parsed.username
            api_secret = parsed.password

    if cloud_name and api_key and api_secret:
        return cloud_name, api_key, api_secret
    return None


def upload_image_to_cloudinary(image_path: str) -> tuple[str, str | None]:
    """Upload an image to Cloudinary with a signed upload and return its public URL."""
    config = _cloudinary_config()
    if not config:
        return "", "Cloudinary upload requires CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET."

    cloud_name, api_key, api_secret = config
    timestamp = str(int(time.time()))
    public_id = f"programa-photo-inventory/{Path(image_path).stem}"
    signature_base = f"public_id={public_id}&timestamp={timestamp}{api_secret}"
    signature = hashlib.sha1(signature_base.encode("utf-8")).hexdigest()

    try:
        with open(image_path, "rb") as fh:
            response = requests.post(
                f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload",
                data={
                    "api_key": api_key,
                    "timestamp": timestamp,
                    "public_id": public_id,
                    "signature": signature,
                },
                files={"file": fh},
                timeout=45,
            )
        response.raise_for_status()
        payload = response.json()
        return str(payload.get("secure_url") or payload.get("url") or ""), None
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
            "Local Image Path": str(photo.get("local_image_path", "") or ""),
            "Image Filename": filename,
            "Image Upload Status": "Uploaded" if image_url else str(photo.get("image_upload_status", "") or "Needs Upload"),
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
            "Local Image Path": str(photo.get("local_image_path", "") or ""),
            "Image Filename": str(photo.get("image_filename", "") or ""),
            "Image Upload Status": image_upload_status or ("Uploaded" if image_url else "Needs Cloudinary"),
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
) -> list[dict]:
    """Create one fast photo-only row per photo, preserving input order."""
    rows: list[dict] = []
    statuses = upload_statuses or []
    for idx, photo in enumerate(photos, start=1):
        filename = str(photo.get("image_filename", "") or "")
        if naming_mode == "Generated names":
            product_name = generated_photo_item_name(idx)
        else:
            product_name = filename_stem_as_product_name(filename)
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
