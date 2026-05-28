"""Persistent source success memory for product dimensions/images.

This registry is intentionally domain-level memory only: it remembers which
domains have worked for a brand/category so future uploads can try those sources
before broad search. Exact product URLs/images/spec values belong in exact
product memory, not here.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from src.durable_cache import durable_cache_enabled, load_map, upsert_payload
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


def _use_durable(path: str | Path | None = None) -> bool:
    return path is None and not os.getenv("SOURCE_SUCCESS_REGISTRY_PATH", "").strip() and durable_cache_enabled()


def load_source_registry(path: str | Path | None = None) -> dict:
    path_obj = registry_path(path)
    data: dict = {}
    if path_obj.exists():
        try:
            loaded = json.loads(path_obj.read_text(encoding="utf-8"))
            data = loaded if isinstance(loaded, dict) else {}
        except Exception:
            data = {}
    if _use_durable(path):
        durable = load_map("source_success_registry", "cache_key")
        if durable:
            data.update(durable)
    return data


def save_source_registry(registry: dict, path: str | Path | None = None) -> None:
    path_obj = registry_path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    tmp = path_obj.with_suffix(path_obj.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path_obj)
    if _use_durable(path):
        for key, entry in registry.items():
            if not isinstance(entry, dict):
                continue
            upsert_payload(
                "source_success_registry",
                "cache_key",
                str(key),
                entry,
                extra={
                    "normalized_brand": entry.get("normalized_brand") or "",
                    "normalized_sku": "",
                    "normalized_category": entry.get("normalized_category") or "",
                    "domain": entry.get("successful_domain") or entry.get("domain") or "",
                    "success_count": int(entry.get("success_count") or 0),
                    "failure_count": int(entry.get("failure_count") or 0),
                    "average_confidence": float(entry.get("average_confidence") or 0.0),
                    "image_success_rate": float(entry.get("image_success_rate") or 0.0),
                    "dimension_success_rate": float(entry.get("dimension_success_rate") or 0.0),
                },
            )


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
        success_count = int(entry.get("success_count") or 0)
        failure_count = int(entry.get("failure_count") or 0)
        score = (
            success_count * 10
            - failure_count * 6
            + category_bonus
            + int(_average_confidence(entry) * 6)
            + int((_field_success_rate(entry, "image") + _field_success_rate(entry, "dimensions")) * 4)
        )
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
    """Deprecated compatibility shim.

    Domain success memory must not return product URLs. Exact URLs now come from
    product_lookup_cache / exact product memory.
    """
    return []


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
        field_bonus = int((_field_success_rate(entry, "dimensions") + _field_success_rate(entry, "image")) * 2)
        confidence_bonus = int(_average_confidence(entry) * 3)
        best = max(best, min(12, successes * 4 + category_bonus + field_bonus + confidence_bonus - failures * 3))
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
    fields = _normalise_fields_found(fields_found or {})
    previous_success_count = int(existing.get("success_count") or 0)
    success_count = previous_success_count + 1
    field_counts = {
        "dimensions": int(existing.get("dimension_success_count") or 0) + int(fields["dimensions"]),
        "image": int(existing.get("image_success_count") or 0) + int(fields["image"]),
        "spec_sheet": int(existing.get("spec_sheet_success_count") or 0) + int(fields["spec_sheet"]),
        "product_url": int(existing.get("product_url_success_count") or 0) + int(fields["product_url"]),
    }
    previous_average = _average_confidence(existing)
    average_confidence = (
        (previous_average * previous_success_count) + _confidence_value(confidence)
    ) / success_count
    last_success = _now()
    entry = {
        **existing,
        "brand": str(row.get("Brand") or existing.get("brand") or "").strip(),
        "normalized_brand": _brand_key(row) or existing.get("normalized_brand", ""),
        "category": str(row.get("Product Category") or existing.get("category") or "").strip(),
        "normalized_category": _category_key(row) or existing.get("normalized_category", ""),
        "successful_domain": clean,
        "domain": clean,
        "fields_found": _fields_usually_found(field_counts, success_count),
        "fields_usually_found": _fields_usually_found(field_counts, success_count),
        "dimension_success_count": field_counts["dimensions"],
        "image_success_count": field_counts["image"],
        "spec_sheet_success_count": field_counts["spec_sheet"],
        "product_url_success_count": field_counts["product_url"],
        "dimension_success_rate": _success_rate(field_counts["dimensions"], success_count),
        "image_success_rate": _success_rate(field_counts["image"], success_count),
        "spec_sheet_success_rate": _success_rate(field_counts["spec_sheet"], success_count),
        "product_url_success_rate": _success_rate(field_counts["product_url"], success_count),
        "average_confidence": round(average_confidence, 4),
        "confidence": confidence if confidence in {"high", "medium", "low", "none"} else "medium",
        "timestamp": last_success,
        "last_success_date": last_success,
        "failure_count": int(existing.get("failure_count") or 0),
        "success_count": success_count,
    }
    for exact_field in ("model", "normalized_sku", "successful_url", "verified_product_url", "verified_image_url", "cloudinary_url"):
        entry.pop(exact_field, None)
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
        "category": str(row.get("Product Category") or existing.get("category") or "").strip(),
        "normalized_category": _category_key(row) or existing.get("normalized_category", ""),
        "successful_domain": clean,
        "domain": clean,
        "fields_found": _normalise_fields_found(existing.get("fields_found") or {}),
        "fields_usually_found": _normalise_fields_found(existing.get("fields_usually_found") or existing.get("fields_found") or {}),
        "average_confidence": _average_confidence(existing),
        "dimension_success_count": int(existing.get("dimension_success_count") or 0),
        "image_success_count": int(existing.get("image_success_count") or 0),
        "spec_sheet_success_count": int(existing.get("spec_sheet_success_count") or 0),
        "product_url_success_count": int(existing.get("product_url_success_count") or 0),
        "dimension_success_rate": float(existing.get("dimension_success_rate") or 0.0),
        "image_success_rate": float(existing.get("image_success_rate") or 0.0),
        "spec_sheet_success_rate": float(existing.get("spec_sheet_success_rate") or 0.0),
        "product_url_success_rate": float(existing.get("product_url_success_rate") or 0.0),
        "confidence": str(existing.get("confidence") or "none"),
        "timestamp": _now(),
        "failure_count": int(existing.get("failure_count") or 0) + 1,
        "success_count": int(existing.get("success_count") or 0),
        "last_success_date": str(existing.get("last_success_date") or ""),
        "last_failure_reason": str(reason or "unknown"),
    }
    for exact_field in ("model", "normalized_sku", "successful_url", "verified_product_url", "verified_image_url", "cloudinary_url"):
        entry.pop(exact_field, None)
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


def _confidence_value(confidence: str) -> float:
    return {
        "high": 1.0,
        "medium": 0.7,
        "low": 0.25,
        "none": 0.0,
    }.get(str(confidence or "").lower(), 0.7)


def _average_confidence(entry: dict) -> float:
    try:
        return float(entry.get("average_confidence"))
    except (TypeError, ValueError):
        return _confidence_value(str(entry.get("confidence") or "none"))


def _success_rate(count: int, success_count: int) -> float:
    if success_count <= 0:
        return 0.0
    return round(max(0.0, min(1.0, count / success_count)), 4)


def _field_success_rate(entry: dict, field: str) -> float:
    direct_key = "dimension_success_rate" if field == "dimensions" else f"{field}_success_rate"
    try:
        return float(entry.get(direct_key))
    except (TypeError, ValueError):
        fields = _normalise_fields_found(entry.get("fields_usually_found") or entry.get("fields_found") or {})
        return 1.0 if fields.get(field) else 0.0


def _fields_usually_found(field_counts: dict[str, int], success_count: int) -> dict:
    if success_count <= 0:
        return _normalise_fields_found({})
    return {
        "dimensions": field_counts.get("dimensions", 0) / success_count >= 0.5,
        "image": field_counts.get("image", 0) / success_count >= 0.5,
        "spec_sheet": field_counts.get("spec_sheet", 0) / success_count >= 0.5,
        "product_url": field_counts.get("product_url", 0) / success_count >= 0.5,
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
