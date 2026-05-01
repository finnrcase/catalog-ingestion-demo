"""
AI-assisted batch category suggestion for SCH DesignOps Intake.

Public API
----------
suggest_categories_batch(rows, row_indices) -> tuple[dict[int, dict], str | None]
    Returns ({row_index: {category, confidence, reason}}, error_or_None).
    On any failure returns ({}, error_string) — caller leaves table unchanged.

_normalise_category(raw) -> str
    Maps any category string to a canonical CATEGORIES value.
    Imported by document_parser.py.
"""

import json
import os
import re

from dotenv import load_dotenv

from src.intake_schema import CATEGORIES

load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

_CATEGORY_ALIASES: dict[str, str] = {
    # Seating
    "chair":        "seating",
    "sofa":         "seating",
    "couch":        "seating",
    "bench":        "seating",
    "stool":        "seating",
    # Paint/Wallpaper
    "paint":        "paint/wallpaper",
    "paint color":  "paint/wallpaper",
    "wallcovering": "paint/wallpaper",
    "wallpaper":    "paint/wallpaper",
    # Fabrics/Pillows
    "fabric":       "fabrics/pillows",
    "pillow":       "fabrics/pillows",
    "cushion":      "fabrics/pillows",
    # Dressers/Drawers/Storage
    "dresser":      "dressers/drawers/storage",
    "drawer":       "dressers/drawers/storage",
    "cabinet":      "dressers/drawers/storage",
    "storage":      "dressers/drawers/storage",
    # Beds/Mattresses
    "bed":          "beds/mattresses",
    "mattress":     "beds/mattresses",
    # Appliances (old singular)
    "appliance":    "appliances",
    # Rugs / Mirrors (old singular)
    "rug":          "rugs",
    "mirror":       "mirrors",
    # Tables
    "table":        "tables",
    "desk":         "tables",
    "console":      "tables",
    # Artwork
    "art":          "artwork",
    "print":        "artwork",
    # Bedding/Linens/Bath Linens
    "towel":        "bedding/linens/bath linens",
    "sheets":       "bedding/linens/bath linens",
    "bedding":      "bedding/linens/bath linens",
    "linens":       "bedding/linens/bath linens",
    "bath linens":  "bedding/linens/bath linens",
    # Catch-alls that no longer exist
    "other":        "",  # leave blank — AI will re-suggest
    "plumbing":     "hardware",
}


def _normalise_category(raw: str) -> str:
    lower = raw.strip().lower()
    canonical = _CATEGORY_ALIASES.get(lower, lower)
    match = next((c for c in CATEGORIES if c.lower() == canonical), None)
    return match if match else ""


def _parse_batch_response(response_text: str) -> list[dict]:
    text = re.sub(r"```(?:json)?\s*|```", "", response_text).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON array in response. First 300 chars: {text[:300]}")
    items = json.loads(text[start : end + 1])
    if not isinstance(items, list):
        raise ValueError("Response root is not a JSON array.")
    return items


def _build_category_prompt(rows: list[dict], row_indices: list[int]) -> str:
    categories_str = ", ".join(CATEGORIES)
    items = []
    for row, row_idx in zip(rows, row_indices):
        url = str(row.get("Product URL") or "")[:200]
        items.append({
            "id": row_idx,
            "product_name": str(row.get("Product Name") or "").strip(),
            "brand": str(row.get("Brand") or "").strip(),
            "finish_color": str(row.get("Finish / Color") or "").strip(),
            "model_sku": str(row.get("Model/SKU") or "").strip(),
            "notes": str(row.get("Notes") or "").strip(),
            "product_url": url,
        })

    return f"""You are a procurement assistant for Saffron Case Homes, an interior design firm.
For each product below, suggest the single best category from the allowed list.

ALLOWED CATEGORIES: {categories_str}

RULES
1. Return ONLY a valid JSON array — no explanation, no markdown, no code fences.
2. Each element must have exactly: "id" (integer), "category" (string), "confidence" (0-100), "reason" (short string).
3. "id" must match the input "id" exactly.
4. You MUST return one of the allowed categories above — do NOT invent new ones or return empty string.
5. If uncertain, choose the closest allowed category and use a lower confidence score (≤ 60).
6. Category guidance:
   - Chairs, sofas, couches, benches, stools, sectionals → "Seating"
   - Paint, stain, wallpaper, wallcovering → "Paint/Wallpaper"
   - Fabric, pillows, cushions → "Fabrics/Pillows"
   - Dressers, drawers, cabinets, armoires, storage units → "Dressers/Drawers/Storage"
   - Beds, headboards, bed frames, mattresses → "Beds/Mattresses"
   - Appliances (Wolf, Sub-Zero, Miele, Fisher & Paykel, Bosch, etc.) → "Appliances"
   - Area rugs, runner rugs → "Rugs"
   - Mirrors (all types) → "Mirrors"
   - Tables, desks, consoles, coffee tables, side tables → "Tables"
   - Art, prints, photographs, sculptures → "Artwork"
   - Towels, sheets, duvets, bedding, bath linens, throw blankets → "Bedding/Linens/Bath Linens"
   - Tile, stone, marble, travertine → "Stone/Tile"
   - Flooring (hardwood, LVP, carpet) → "Flooring"
   - Gym equipment (weights, benches, treadmills) → "Gym Equipment"
   - Pendant lights, sconces, floor lamps, chandeliers → "Lighting"
   - Door hardware, knobs, pulls, hinges → "Hardware"
   - Candles, baskets, vases, small decor, kitchen accessories, step stools → "Accessories"

PRODUCTS
{json.dumps(items, indent=2)}"""


def suggest_categories_batch(
    rows: list[dict],
    row_indices: list[int],
) -> tuple[dict[int, dict], str | None]:
    """
    Suggest categories for a batch of rows with blank Product Category.

    Parameters
    ----------
    rows         : Row dicts (only those with blank Product Category).
    row_indices  : Their integer positions in the full DataFrame.

    Returns
    -------
    (suggestions, error_or_None)
    suggestions  : {row_index: {"category": str, "confidence": int, "reason": str}}
    On any failure: ({}, error_string) — caller must leave the table unchanged.
    """
    if not rows:
        return {}, None

    if not ANTHROPIC_API_KEY:
        return {}, (
            "AI category suggestion requires ANTHROPIC_API_KEY. "
            "Add it to your .env file and restart the app."
        )

    try:
        import anthropic
    except ImportError:
        return {}, "The 'anthropic' package is not installed. Run: pip install anthropic"

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": _build_category_prompt(rows, row_indices)}],
        )
        response_text = message.content[0].text
    except Exception as exc:
        return {}, f"AI category API call failed: {exc}"

    try:
        items = _parse_batch_response(response_text)
    except (ValueError, json.JSONDecodeError) as exc:
        return {}, f"Could not parse AI category response: {exc}"

    suggestions: dict[int, dict] = {}
    for item in items:
        try:
            row_idx = int(item["id"])
            raw_cat = str(item.get("category", "")).strip()
            category = _normalise_category(raw_cat)
            confidence = max(0, min(100, int(item.get("confidence", 0))))
            reason = str(item.get("reason", "")).strip()
            # If normaliser couldn't map to a valid category, zero confidence so it stays flagged
            if not category:
                confidence = 0
            suggestions[row_idx] = {
                "category": category,
                "confidence": confidence,
                "reason": reason,
            }
        except (KeyError, TypeError, ValueError):
            continue

    return suggestions, None
