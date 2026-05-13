"""Brand/supplier lookup registry for targeted official product searches."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BrandLookupEntry:
    brand: str
    domains: tuple[str, ...]
    search_templates: tuple[str, ...] = field(default_factory=tuple)
    preferred_fields: tuple[str, ...] = ("dimensions", "finish", "material", "image")


_DEFAULT_TEMPLATES = (
    "site:{domain} {sku}",
    "site:{domain} {brand} {sku}",
    "site:{domain} {product_name} {sku}",
    "site:{domain} {brand} {product_name}",
)


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _entry(brand: str, *domains: str) -> BrandLookupEntry:
    return BrandLookupEntry(
        brand=brand,
        domains=tuple(domains),
        search_templates=_DEFAULT_TEMPLATES,
    )


_ENTRIES: tuple[BrandLookupEntry, ...] = (
    _entry("Visual Comfort", "visualcomfort.com"),
    _entry("Circa Lighting", "circalighting.com", "visualcomfort.com"),
    _entry("Palecek", "palecek.com"),
    _entry("Four Hands", "fourhands.com"),
    _entry("Arteriors", "arteriorshome.com"),
    _entry("McGee & Co", "mcgeeandco.com"),
    _entry("RH", "rh.com", "restorationhardware.com"),
    _entry("Restoration Hardware", "rh.com", "restorationhardware.com"),
    _entry("West Elm", "westelm.com"),
    _entry("Pottery Barn", "potterybarn.com"),
    _entry("Rejuvenation", "rejuvenation.com"),
    _entry("Serena & Lily", "serenaandlily.com"),
    _entry("CB2", "cb2.com"),
    _entry("Crate & Barrel", "crateandbarrel.com"),
    _entry("Lulu and Georgia", "luluandgeorgia.com"),
    _entry("Burke Decor", "burkedecor.com"),
    _entry("Wayfair", "wayfair.com"),
    _entry("Perigold", "perigold.com"),
    _entry("1stDibs", "1stdibs.com"),
    _entry("Scotsman", "scotsman-ice.com"),
    _entry("Wolf", "subzero-wolf.com", "wolfappliance.com"),
    _entry("Sub-Zero", "subzero-wolf.com"),
    _entry("Miele", "mieleusa.com", "miele.com"),
    _entry("Kohler", "kohler.com"),
    _entry("Kallista", "kallista.com"),
)

_REGISTRY = {_norm(entry.brand): entry for entry in _ENTRIES}


def get_brand_lookup_entry(brand: object) -> BrandLookupEntry | None:
    """Return a registry entry for a brand, accepting common punctuation drift."""
    key = _norm(brand)
    if not key:
        return None
    if key in _REGISTRY:
        return _REGISTRY[key]
    compact = key.replace(" ", "")
    for entry_key, entry in _REGISTRY.items():
        if compact == entry_key.replace(" ", ""):
            return entry
    return None


def build_brand_search_queries(row: dict) -> tuple[list[str], BrandLookupEntry | None]:
    """Build official-domain search queries from the registry for a product row."""
    brand = str(row.get("Brand") or "").strip()
    sku = str(row.get("Model/SKU") or row.get("SKU") or "").strip()
    product_name = str(row.get("Product Name") or "").strip()
    entry = get_brand_lookup_entry(brand)
    if not entry:
        return [], None

    queries: list[str] = []
    for domain in entry.domains:
        for template in entry.search_templates:
            if "{sku}" in template and not sku:
                continue
            if "{product_name}" in template and not product_name:
                continue
            query = template.format(
                domain=domain,
                brand=brand or entry.brand,
                sku=sku,
                product_name=product_name,
            )
            query = " ".join(query.split()).strip()
            if query and query not in queries:
                queries.append(query)
    return queries, entry


def registry_domains_for_brand(brand: object) -> list[str]:
    entry = get_brand_lookup_entry(brand)
    return list(entry.domains) if entry else []
