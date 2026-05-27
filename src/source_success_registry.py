"""Persistent source success memory for product dimensions/images.

This registry is intentionally lightweight: it remembers which domains have
worked for a brand/category so future uploads can try those sources before broad
search. It stores only upstream enrichment evidence and never writes export
columns directly.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from src.product_evidence import normalize_brand, normalize_sku

DATA_DIR = Path("data")
DEFAULT_REGISTRY_PATH = DATA_DIR / "source_success_registry.json"

APPLIANCE_SOURCE_DOMAINS = [
    "subzero-wolf.com",
    "ca.subzero-wolf.com",
    "scotsman-ice.com",
    "mieleusa.com",
    "miele.com",
    "bosch-home.com",
    "geappliances.com",
    "fisherpaykel.com",
    "lynxgrills.com",
    "thermador.com",
    "jennair.com",
    "kitchenaid.com",
    "monogram.com",
    "vikingrange.com",
    "dacor.com",
    "samsung.com",
    "lg.com",
    "whirlpool.com",
    "frigidaire.com",
    "electrolux.com",
    "sharpusa.com",
    "zephyronline.com",
    "broan-nutone.com",
    "bestrangehoods.com",
    "faberspa.com",
    "home.hestan.com",
]
LIGHTING_SOURCE_DOMAINS = [
    "visualcomfort.com",
    "circalighting.com",
    "arteriorshome.com",
    "lumens.com",
    "rejuvenation.com",
    "shadesoflight.com",
    "schoolhouse.com",
    "hudsonvalleylighting.com",
    "techlighting.com",
]
PLUMBING_BATH_SOURCE_DOMAINS = [
    "kohler.com",
    "waterworks.com",
    "rohlhome.com",
    "houseofrohl.com",
    "brizo.com",
    "deltafaucet.com",
    "moen.com",
    "hansgrohe-usa.com",
    "grohe.us",
    "toto.com",
    "duravit.us",
    "build.com",
    "ferguson.com",
]
FURNITURE_DECOR_SOURCE_DOMAINS = [
    "rh.com",
    "potterybarn.com",
    "westelm.com",
    "crateandbarrel.com",
    "cb2.com",
    "perigold.com",
    "wayfair.com",
    "serenaandlily.com",
    "mcgeeandco.com",
    "rejuvenation.com",
    "anthropologie.com",
    "arhaus.com",
    "fourhands.com",
    "bernhardt.com",
    "visualcomfort.com",
]
TILE_SURFACES_HARDWARE_SOURCE_DOMAINS = [
    "annesacks.com",
    "fireclaytile.com",
    "bedrosians.com",
    "daltile.com",
    "cletile.com",
    "waterworks.com",
    "emtek.com",
    "baldwinhardware.com",
    "topknobs.com",
    "rejuvenation.com",
]

CATEGORY_SOURCE_HINTS: dict[str, list[str]] = {
    "appliances": APPLIANCE_SOURCE_DOMAINS,
    "appliance": APPLIANCE_SOURCE_DOMAINS,
    "lighting": LIGHTING_SOURCE_DOMAINS,
    "plumbing": PLUMBING_BATH_SOURCE_DOMAINS,
    "bath": PLUMBING_BATH_SOURCE_DOMAINS,
    "bathroom": PLUMBING_BATH_SOURCE_DOMAINS,
    "furniture": FURNITURE_DECOR_SOURCE_DOMAINS,
    "decor": FURNITURE_DECOR_SOURCE_DOMAINS,
    "accessories": FURNITURE_DECOR_SOURCE_DOMAINS,
    "tile": TILE_SURFACES_HARDWARE_SOURCE_DOMAINS,
    "stone tile": TILE_SURFACES_HARDWARE_SOURCE_DOMAINS,
    "surfaces": TILE_SURFACES_HARDWARE_SOURCE_DOMAINS,
    "hardware": TILE_SURFACES_HARDWARE_SOURCE_DOMAINS,
}

BRAND_SOURCE_HINTS: dict[str, list[str]] = {
    "sub zero": ["subzero-wolf.com", "ca.subzero-wolf.com"],
    "subzero": ["subzero-wolf.com", "ca.subzero-wolf.com"],
    "wolf": ["subzero-wolf.com", "ca.subzero-wolf.com"],
    "scotsman": ["scotsman-ice.com"],
    "miele": ["mieleusa.com", "miele.com"],
    "bosch": ["bosch-home.com", "bosch-home.com/us"],
    "ge": ["geappliances.com"],
    "ge appliances": ["geappliances.com"],
    "fisher paykel": ["fisherpaykel.com"],
    "fisher & paykel": ["fisherpaykel.com"],
    "lynx": ["lynxgrills.com"],
    "thermador": ["thermador.com"],
    "jennair": ["jennair.com"],
    "kitchenaid": ["kitchenaid.com"],
    "monogram": ["monogram.com"],
    "viking": ["vikingrange.com"],
    "viking range": ["vikingrange.com"],
    "dacor": ["dacor.com"],
    "samsung": ["samsung.com"],
    "lg": ["lg.com"],
    "whirlpool": ["whirlpool.com"],
    "frigidaire": ["frigidaire.com"],
    "electrolux": ["electrolux.com"],
    "sharp": ["sharpusa.com"],
    "zephyr": ["zephyronline.com"],
    "broan": ["broan-nutone.com"],
    "broan nutone": ["broan-nutone.com"],
    "best": ["bestrangehoods.com"],
    "faber": ["faberspa.com"],
    "hestan": ["home.hestan.com"],
    "visual comfort": ["visualcomfort.com"],
    "circa lighting": ["circalighting.com", "visualcomfort.com"],
    "arteriors": ["arteriorshome.com"],
    "hudson valley": ["hudsonvalleylighting.com"],
    "tech lighting": ["techlighting.com"],
    "kohler": ["kohler.com"],
    "waterworks": ["waterworks.com"],
    "rohl": ["rohlhome.com", "houseofrohl.com"],
    "brizo": ["brizo.com"],
    "delta": ["deltafaucet.com"],
    "moen": ["moen.com"],
    "hansgrohe": ["hansgrohe-usa.com"],
    "grohe": ["grohe.us"],
    "toto": ["toto.com"],
    "duravit": ["duravit.us"],
    "rh": ["rh.com"],
    "restoration hardware": ["rh.com"],
    "pottery barn": ["potterybarn.com"],
    "west elm": ["westelm.com"],
    "crate and barrel": ["crateandbarrel.com"],
    "crate barrel": ["crateandbarrel.com"],
    "cb2": ["cb2.com"],
    "perigold": ["perigold.com"],
    "wayfair": ["wayfair.com"],
    "serena and lily": ["serenaandlily.com"],
    "mcgee": ["mcgeeandco.com"],
    "mcgee co": ["mcgeeandco.com"],
    "anthropologie": ["anthropologie.com"],
    "arhaus": ["arhaus.com"],
    "four hands": ["fourhands.com"],
    "bernhardt": ["bernhardt.com"],
    "annsacks": ["annesacks.com"],
    "ann sacks": ["annesacks.com"],
    "fireclay": ["fireclaytile.com"],
    "bedrosians": ["bedrosians.com"],
    "daltile": ["daltile.com"],
    "cle tile": ["cletile.com"],
    "cletile": ["cletile.com"],
    "emtek": ["emtek.com"],
    "baldwin": ["baldwinhardware.com"],
    "top knobs": ["topknobs.com"],
}

_MARKETPLACE_OR_SOCIAL = (
    "amazon.",
    "ebay.",
    "etsy.",
    "facebook.",
    "houzz.",
    "instagram.",
    "pinterest.",
    "reddit.",
    "temu.",
    "walmart.",
)


def registry_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    env_path = os.getenv("SOURCE_SUCCESS_REGISTRY_PATH", "").strip()
    return Path(env_path) if env_path else DEFAULT_REGISTRY_PATH


def load_source_registry(path: str | Path | None = None) -> dict:
    path_obj = registry_path(path)
    if not path_obj.exists():
        return {}
    try:
        data = json.loads(path_obj.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_source_registry(registry: dict, path: str | Path | None = None) -> None:
    path_obj = registry_path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    tmp = path_obj.with_suffix(path_obj.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path_obj)


def preferred_source_domains_for_row(row: dict, path: str | Path | None = None) -> list[str]:
    """Return learned/hinted source domains ordered by prior success."""
    brand_key = _brand_key(row)
    category_key = _category_key(row)
    registry = load_source_registry(path)
    scored: dict[str, int] = {}

    for entry in registry.values():
        if not isinstance(entry, dict) or entry.get("normalized_brand") != brand_key:
            continue
        domain = _clean_domain(entry.get("successful_domain") or entry.get("domain"))
        if not domain or _blocked_domain(domain):
            continue
        entry_category = str(entry.get("normalized_category") or "")
        category_bonus = 5 if category_key and entry_category == category_key else 0
        score = int(entry.get("success_count") or 0) * 10 - int(entry.get("failure_count") or 0) * 6 + category_bonus
        if score > scored.get(domain, -10_000):
            scored[domain] = score

    for domain in brand_source_hints(row.get("Brand")):
        scored.setdefault(domain, 3)
    for domain in category_source_hints(row.get("Product Category") or row.get("Section")):
        scored.setdefault(domain, 1)

    return [
        domain
        for domain, _score in sorted(scored.items(), key=lambda item: (-item[1], item[0]))
        if _score > -12
    ]


def brand_source_hints(brand: object) -> list[str]:
    brand_key = normalize_brand(str(brand or ""))
    if not brand_key:
        return []
    hints = BRAND_SOURCE_HINTS.get(brand_key)
    if not hints:
        compact = brand_key.replace(" ", "")
        for key, value in BRAND_SOURCE_HINTS.items():
            if key.replace(" ", "") == compact:
                hints = value
                break
    return _dedupe_domains(hints or [])


def category_source_hints(category: object) -> list[str]:
    category_key = _normalise_category_text(category)
    if not category_key:
        return []
    hints = CATEGORY_SOURCE_HINTS.get(category_key)
    if not hints:
        for key, value in CATEGORY_SOURCE_HINTS.items():
            if key in category_key or category_key in key:
                hints = value
                break
    return _dedupe_domains(hints or [])


def successful_urls_for_row(row: dict, path: str | Path | None = None) -> list[str]:
    """Return exact prior successful URLs for this brand/model, if any."""
    brand_key = _brand_key(row)
    sku_key = _sku_key(row)
    if not brand_key or not sku_key:
        return []
    urls: list[str] = []
    for entry in load_source_registry(path).values():
        if not isinstance(entry, dict):
            continue
        if entry.get("normalized_brand") != brand_key or entry.get("normalized_sku") != sku_key:
            continue
        if int(entry.get("success_count") or 0) <= 0:
            continue
        url = str(entry.get("successful_url") or "").strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def source_success_score(row: dict, domain: str, path: str | Path | None = None) -> int:
    """Small scoring adjustment from learned source history."""
    clean = _clean_domain(domain)
    if not clean:
        return 0
    brand_key = _brand_key(row)
    category_key = _category_key(row)
    best = 0
    for entry in load_source_registry(path).values():
        if not isinstance(entry, dict) or entry.get("normalized_brand") != brand_key:
            continue
        entry_domain = _clean_domain(entry.get("successful_domain") or entry.get("domain"))
        if not _domain_matches(clean, entry_domain):
            continue
        successes = int(entry.get("success_count") or 0)
        failures = int(entry.get("failure_count") or 0)
        category_bonus = 3 if category_key and entry.get("normalized_category") == category_key else 0
        best = max(best, min(12, successes * 4 + category_bonus - failures * 3))
    return best


def record_source_success(
    row: dict,
    *,
    domain: str = "",
    url: str = "",
    fields_found: dict | None = None,
    confidence: str = "medium",
    path: str | Path | None = None,
) -> dict | None:
    clean = _clean_domain(domain or _domain_from_url(url))
    if not clean or _blocked_domain(clean):
        return None
    registry = load_source_registry(path)
    key = _entry_key(row, clean)
    existing = dict(registry.get(key) or {})
    fields = _normalise_fields_found(fields_found or existing.get("fields_found") or {})
    entry = {
        **existing,
        "brand": str(row.get("Brand") or existing.get("brand") or "").strip(),
        "normalized_brand": _brand_key(row) or existing.get("normalized_brand", ""),
        "model": str(row.get("Model/SKU") or row.get("SKU") or existing.get("model") or "").strip(),
        "normalized_sku": _sku_key(row) or existing.get("normalized_sku", ""),
        "category": str(row.get("Product Category") or existing.get("category") or "").strip(),
        "normalized_category": _category_key(row) or existing.get("normalized_category", ""),
        "successful_domain": clean,
        "domain": clean,
        "successful_url": str(url or existing.get("successful_url") or "").strip(),
        "fields_found": fields,
        "confidence": confidence if confidence in {"high", "medium", "low", "none"} else "medium",
        "timestamp": _now(),
        "failure_count": int(existing.get("failure_count") or 0),
        "success_count": int(existing.get("success_count") or 0) + 1,
    }
    registry[key] = entry
    save_source_registry(registry, path)
    return entry


def record_source_failure(
    row: dict,
    *,
    domain: str = "",
    url: str = "",
    reason: str = "",
    path: str | Path | None = None,
) -> dict | None:
    clean = _clean_domain(domain or _domain_from_url(url))
    if not clean or _blocked_domain(clean):
        return None
    registry = load_source_registry(path)
    key = _entry_key(row, clean)
    existing = dict(registry.get(key) or {})
    entry = {
        **existing,
        "brand": str(row.get("Brand") or existing.get("brand") or "").strip(),
        "normalized_brand": _brand_key(row) or existing.get("normalized_brand", ""),
        "model": str(row.get("Model/SKU") or row.get("SKU") or existing.get("model") or "").strip(),
        "normalized_sku": _sku_key(row) or existing.get("normalized_sku", ""),
        "category": str(row.get("Product Category") or existing.get("category") or "").strip(),
        "normalized_category": _category_key(row) or existing.get("normalized_category", ""),
        "successful_domain": clean,
        "domain": clean,
        "successful_url": str(existing.get("successful_url") or "").strip(),
        "fields_found": _normalise_fields_found(existing.get("fields_found") or {}),
        "confidence": str(existing.get("confidence") or "none"),
        "timestamp": _now(),
        "failure_count": int(existing.get("failure_count") or 0) + 1,
        "success_count": int(existing.get("success_count") or 0),
        "last_failure_reason": str(reason or "unknown"),
    }
    registry[key] = entry
    save_source_registry(registry, path)
    return entry


def _normalise_fields_found(fields: dict) -> dict:
    return {
        "dimensions": bool(fields.get("dimensions")),
        "image": bool(fields.get("image")),
        "spec_sheet": bool(fields.get("spec_sheet")),
        "product_url": bool(fields.get("product_url")),
    }


def _entry_key(row: dict, domain: str) -> str:
    return "|".join([
        _brand_key(row) or "unknown",
        _category_key(row) or "uncategorized",
        _clean_domain(domain) or "unknown",
    ])


def _brand_key(row: dict) -> str:
    return normalize_brand(str(row.get("Brand") or ""))


def _sku_key(row: dict) -> str:
    return normalize_sku(str(row.get("Model/SKU") or row.get("SKU") or row.get("_extracted_model_sku") or ""))


def _category_key(row: dict) -> str:
    return _normalise_category_text(row.get("Product Category") or row.get("Section"))


def _normalise_category_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _domain_from_url(url: str) -> str:
    return _clean_domain(urlparse(str(url or "")).hostname or "")


def _clean_domain(value: object) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        raw = urlparse(raw).hostname or ""
    raw = raw.strip().strip("/")
    if raw.startswith("www."):
        raw = raw[4:]
    return raw


def _blocked_domain(domain: str) -> bool:
    return any(marker in domain for marker in _MARKETPLACE_OR_SOCIAL)


def _domain_matches(domain: str, root: str) -> bool:
    return bool(root and (domain == root or domain.endswith("." + root)))


def _dedupe_domains(domains: list[str]) -> list[str]:
    output: list[str] = []
    for domain in domains:
        clean = _clean_domain(domain)
        if clean and clean not in output:
            output.append(clean)
    return output


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
