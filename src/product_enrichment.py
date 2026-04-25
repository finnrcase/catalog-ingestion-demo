"""
Product enrichment orchestrator for SCH DesignOps Intake.

Automatically fills blank fields (Product Name, Dimensions, Finish / Color,
Product Category, Product URL, Notes/materials) using Brave Search + httpx +
Claude Haiku. Never overwrites existing data.

Public API
----------
enrich_row(row: dict) -> tuple[dict, str | None]
enrich_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]
"""

import json
import os
import re
import time
import urllib.parse

import httpx
import pandas as pd
from dotenv import load_dotenv

from src.brave_search import BRAVE_API_KEY, search_product_candidates
from src.category_ai import _normalise_category

try:
    import html2text as _html2text
except ImportError:
    _html2text = None

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None

load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

_ENRICHABLE_FIELDS: list = [
    "Product Name",
    "Dimensions",
    "Finish / Color",
    "Product Category",
    "Product URL",
]

MIN_USE_SCORE = 40   # below this: skip entirely, note in Notes
MIN_CONF_SCORE = 60  # 40–59: fill fields but force Review Required = True


def _str_val(v) -> str:
    """Safely convert a row cell value to a stripped string, handling None."""
    if v is None:
        return ""
    return str(v).strip()


def _qualifies(row: dict) -> bool:
    """True if this row should be sent through enrichment."""
    source = _str_val(row.get("Source Type", ""))
    if source == "URL":
        return False
    if source.endswith("_Enriched"):
        return False
    if not _str_val(row.get("Brand")):
        return False
    if not _str_val(row.get("Model/SKU")):
        return False
    blank = [
        f for f in _ENRICHABLE_FIELDS
        if not _str_val(row.get(f))
    ]
    return bool(blank)


def _build_search_query(row: dict) -> str:
    """Build a Brave Search query from the row's Brand, Model/SKU, and Product Name."""
    parts = [
        _str_val(row.get("Brand")),
        _str_val(row.get("Model/SKU")),
        _str_val(row.get("Product Name")),
        "specifications official",
    ]
    return " ".join(p for p in parts if p)


def _apply_enrichment(
    row: dict,
    extracted: dict,
    source_url: str,
    domain_score: int,
) -> dict:
    """
    Apply extracted fields to a row copy, filling blank fields only.
    Sets Source Type suffix and confidence flags. Never overwrites existing data.
    """
    updated = row.copy()

    for field in _ENRICHABLE_FIELDS:
        if field == "Product URL":
            if not _str_val(updated.get("Product URL")):
                updated["Product URL"] = source_url
            continue

        # Never overwrite non-empty fields
        if _str_val(updated.get(field)):
            continue

        value = _str_val(extracted.get(field))
        if not value:
            continue

        if field == "Product Category":
            value = _normalise_category(value)

        if value:
            updated[field] = value

    # Materials → Notes (only if not already expressed in Finish / Color)
    materials = _str_val(extracted.get("materials"))
    finish = _str_val(updated.get("Finish / Color")).lower()
    if materials and materials.lower() not in finish:
        existing_notes = _str_val(updated.get("Notes"))
        mat_tag = f"[Materials: {materials}]"
        updated["Notes"] = f"{existing_notes} {mat_tag}".strip() if existing_notes else mat_tag

    # Source Type suffix
    original = _str_val(updated.get("Source Type"))
    if original and not original.endswith("_Enriched"):
        updated["Source Type"] = f"{original}_Enriched"
    elif not original:
        updated["Source Type"] = "Enriched"

    # Confidence flagging
    if domain_score < MIN_CONF_SCORE:
        updated["Review Required"] = True
        updated["Suggested Action"] = "Enriched from low-confidence source — verify fields"

    return updated


def _fetch_page_text(url: str) -> str:
    """Fetch URL with httpx and return plain text (max 6 000 chars). Empty string on error."""
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SCH-Intake/1.0)"},
            timeout=10,
            follow_redirects=True,
        )
        resp.raise_for_status()

        if _html2text is not None:
            h = _html2text.HTML2Text()
            h.ignore_links = True
            h.ignore_images = True
            text = h.handle(resp.text)
        else:
            text = re.sub(r"<[^>]+>", " ", resp.text)
            text = re.sub(r"\s{2,}", " ", text)

        return text[:6000].strip()
    except Exception:
        return ""


def _build_extraction_prompt(page_text: str, row: dict) -> str:
    """Build the Claude Haiku prompt listing which fields are blank and need filling."""
    blank = [
        f for f in ["Product Name", "Dimensions", "Finish / Color", "Product Category"]
        if not _str_val(row.get(f))
    ]
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))

    return (
        f"You are extracting product specification data for {brand} model {model}.\n\n"
        f"The following fields are currently blank and need to be filled:\n"
        f"{', '.join(blank)}\n\n"
        "Also extract: materials (short description of primary construction materials, "
        "e.g. 'Solid Oak', 'Stainless Steel')\n\n"
        "Return ONLY a JSON object. No prose. No markdown fences. Example:\n"
        '{"Product Name": "Wolf 30\\" Drawer Microwave Oven", '
        '"Dimensions": "29 7/8\\" W x 23 1/2\\" D x 11 7/8\\" H", '
        '"Finish / Color": "Stainless Steel", '
        '"Product Category": "Appliance", '
        '"materials": "Stainless steel exterior"}\n\n'
        "Rules:\n"
        "- Only include the fields listed above as blank, plus 'materials'.\n"
        "- If a field is not clearly stated in the page, return \"\" for that field.\n"
        "- Never invent values not present in the page.\n"
        "- Product Category must be one of: Chair, Sofa, Paint, Fabric, Table, "
        "Lighting, Plumbing, Hardware, Rug, Artwork, Mirror, Appliance, Accessories, Other.\n\n"
        f"PAGE TEXT:\n---\n{page_text}\n---"
    )


def _extract_with_claude(page_text: str, row: dict) -> dict:
    """Call Claude Haiku to extract missing fields from page text. Returns {} on any failure."""
    if not ANTHROPIC_API_KEY or _anthropic is None:
        return {}
    try:
        client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": _build_extraction_prompt(page_text, row)}],
        )
        text = msg.content[0].text.strip()
        text = re.sub(r"```(?:json)?\s*|```", "", text).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return {}
        return json.loads(text[start : end + 1])
    except Exception:
        return {}


def enrich_row(row: dict) -> tuple[dict, str | None]:
    """
    Enrich a single row using Brave Search + httpx + Claude.

    Returns (updated_row, None) on success or graceful no-result.
    Returns (row_unchanged, error_string) only on unexpected exceptions.
    """
    try:
        query = _build_search_query(row)
        brand = _str_val(row.get("Brand"))

        results = search_product_candidates(query, brand)

        if not results or results[0].domain_score < MIN_USE_SCORE:
            updated = row.copy()
            existing = _str_val(updated.get("Notes"))
            note = "[Enrichment: no confident source found]"
            updated["Notes"] = f"{existing} {note}".strip() if existing else note
            return updated, None

        best = results[0]
        page_text = _fetch_page_text(best.url)

        if not page_text:
            updated = row.copy()
            existing = _str_val(updated.get("Notes"))
            domain = urllib.parse.urlparse(best.url).netloc or best.url[:50]
            note = f"[Enrichment: could not fetch {domain}]"
            updated["Notes"] = f"{existing} {note}".strip() if existing else note
            return updated, None

        extracted = _extract_with_claude(page_text, row)
        updated = _apply_enrichment(row, extracted, best.url, best.domain_score)
        return updated, None
    except Exception as exc:
        return row, str(exc)


def enrich_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Enrich all qualifying rows in df. Returns (updated_df, error_list).
    Exceptions in individual rows are caught and logged; the row is left unchanged.
    """
    df = df.copy()
    errors: list[str] = []

    for idx, row in df.iterrows():
        r = row.to_dict()
        if not _qualifies(r):
            continue

        try:
            updated, error = enrich_row(r)
            if error:
                errors.append(error)
            else:
                # Only write back columns that already exist in the DataFrame.
                # The intake schema guarantees all expected columns are present;
                # this guard prevents accidental column creation mid-iteration.
                for col, val in updated.items():
                    if col in df.columns:
                        df.at[idx, col] = val
        except Exception as exc:
            label = _str_val(r.get("Product Name")) or _str_val(r.get("Brand")) or _str_val(r.get("Model/SKU")) or str(idx)
            errors.append(f"Row '{label}': {exc}")

        time.sleep(0.5)

    return df, errors
