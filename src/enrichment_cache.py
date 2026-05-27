# src/enrichment_cache.py
"""
Enrichment cache, session deduplication, and search budget for SCH DesignOps Intake.

Public API
----------
normalize_key(brand, model) -> str
normalize_mode(mode) -> str
budget_for_mode(mode) -> ProductLookupBudget
confidence_ok(entry, field_name) -> bool
ProductLookupBudget — per-product Brave/page/AI call counter
EnrichmentRunBudget — per-upload cost/call limiter
SearchBudget       — backwards-compatible alias
SessionCache       — in-memory per-run query/URL dedup store
ManufacturerDomainCache  — persistent data/manufacturer_domain_cache.json
ProductEnrichmentCache   — persistent data/product_enrichment_cache.json
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

from src.product_lookup_budget import EnrichmentRunBudget, ProductLookupBudget, run_budget_for_mode

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_MFR_CACHE_PATH = os.path.normpath(os.path.join(_DATA_DIR, "manufacturer_domain_cache.json"))
_PRODUCT_CACHE_PATH = os.path.normpath(os.path.join(_DATA_DIR, "product_enrichment_cache.json"))

_CACHE_ENABLED: bool = os.getenv("ENRICHMENT_CACHE_ENABLED", "true").lower() != "false"

_MODE_LIMITS: dict[str, dict] = {
    "fast": {"max_searches": 1, "max_urls": 1, "max_ai_calls": 0, "retailer": False, "general_fallback": False},
    "standard": {"max_searches": 3, "max_urls": 6, "max_ai_calls": 1, "retailer": False, "general_fallback": True},
    "balanced": {"max_searches": 3, "max_urls": 6, "max_ai_calls": 1, "retailer": False, "general_fallback": True},
    "deep": {"max_searches": 6, "max_urls": 12, "max_ai_calls": 2, "retailer": True, "general_fallback": True},
    "manual_retry": {"max_searches": 10, "max_urls": 20, "max_ai_calls": 3, "retailer": True, "general_fallback": True},
}

VALID_MODES: frozenset[str] = frozenset(_MODE_LIMITS)

# Fields whose confidence is governed by dimension_confidence vs general_confidence
_DIMENSION_FIELDS: frozenset[str] = frozenset(
    {"dimensions", "width_in", "height_in", "depth_in", "length_in"}
)


def normalize_key(brand: str, model: str) -> str:
    """Stable cache key: lowercase, alphanumeric only, joined by underscore.
    Raises ValueError if both brand and model are empty after normalization."""
    def _clean(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", s.lower().strip())
    brand_clean = _clean(brand)
    model_clean = _clean(model)
    if not brand_clean and not model_clean:
        raise ValueError(f"normalize_key: both brand and model are empty after normalization (brand={brand!r}, model={model!r})")
    return f"{brand_clean}_{model_clean}"


def normalize_mode(mode: str) -> str:
    """Return mode unchanged if valid, else 'fast'."""
    return mode if mode in VALID_MODES else "fast"


def budget_for_mode(
    mode: str,
    *,
    run_budget: EnrichmentRunBudget | None = None,
    item_key: str = "",
) -> ProductLookupBudget:
    """Create a ProductLookupBudget for the given enrichment mode, respecting env overrides."""
    mode_name = normalize_mode(mode)
    limits = _MODE_LIMITS[mode_name]
    env_searches = os.getenv("BRAVE_MAX_SEARCHES_PER_PRODUCT")
    env_urls = os.getenv("ENRICHMENT_MAX_URLS_PER_PRODUCT")
    env_ai_calls = os.getenv("ENRICHMENT_MAX_AI_CALLS_PER_PRODUCT")
    max_searches = int(env_searches) if env_searches else limits["max_searches"]
    max_urls = int(env_urls) if env_urls else limits["max_urls"]
    max_ai_calls = int(env_ai_calls) if env_ai_calls else limits["max_ai_calls"]
    if mode_name == "fast":
        max_searches = min(max_searches, limits["max_searches"])
        max_urls = min(max_urls, limits["max_urls"])
        max_ai_calls = min(max_ai_calls, limits["max_ai_calls"])
    return ProductLookupBudget(
        max_searches=max_searches,
        max_urls=max_urls,
        max_ai_calls=max_ai_calls,
        mode=mode_name,
        allows_retailer=limits["retailer"],
        allows_general_fallback=limits["general_fallback"],
        run_budget=run_budget,
        item_key=item_key,
    )


def confidence_ok(entry: dict, field_name: str) -> bool:
    """True if the relevant confidence for this field is 'high' or 'medium'."""
    conf_key = "dimension_confidence" if field_name in _DIMENSION_FIELDS else "general_confidence"
    return entry.get(conf_key, "") in ("high", "medium")


# ── SearchBudget ───────────────────────────────────────────────────────────────

SearchBudget = ProductLookupBudget


# ── SessionCache ───────────────────────────────────────────────────────────────

@dataclass
class SessionCache:
    queries: dict = field(default_factory=dict)  # query str → list[SearchResult]
    urls: dict = field(default_factory=dict)      # url str → page text/html
    force_refresh: bool = False


# ── ManufacturerDomainCache ────────────────────────────────────────────────────

class ManufacturerDomainCache:
    """Persistent cache for brand → official domain mappings."""

    def __init__(self) -> None:
        self._data: dict | None = None
        self._path: str = _MFR_CACHE_PATH

    def _load(self) -> None:
        if self._data is not None:
            return
        if not _CACHE_ENABLED or not os.path.exists(self._path):
            self._data = {}
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "ManufacturerDomainCache: could not load %s (%s) — treating as empty",
                self._path, exc,
            )
            self._data = {}

    def _save(self) -> None:
        if not _CACHE_ENABLED or self._data is None:
            return
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def get(self, brand_key: str) -> dict | None:
        self._load()
        return self._data.get(brand_key)

    def set(self, brand_key: str, domain: str, source: str = "discovered") -> None:
        self._load()
        existing = self._data.get(brand_key)
        if existing and existing.get("source") == "hardcoded":
            return  # never overwrite hardcoded entries
        self._data[brand_key] = {
            "domain": domain,
            "source": source,
            "last_verified": datetime.now().strftime("%Y-%m-%d"),
        }
        self._save()


# ── ProductEnrichmentCache ─────────────────────────────────────────────────────

class ProductEnrichmentCache:
    """Persistent unified cache for product enrichment fields (general + dimension)."""

    def __init__(self) -> None:
        self._data: dict | None = None
        self._path: str = _PRODUCT_CACHE_PATH

    def _load(self) -> None:
        if self._data is not None:
            return
        if not _CACHE_ENABLED or not os.path.exists(self._path):
            self._data = {}
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "ProductEnrichmentCache: could not load %s (%s) — treating as empty",
                self._path, exc,
            )
            self._data = {}

    def _save(self) -> None:
        if not _CACHE_ENABLED or self._data is None:
            return
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def get(self, key: str) -> dict | None:
        self._load()
        return self._data.get(key)

    def update(self, key: str, fields: dict) -> None:
        """Merge fields into existing entry. Only stores non-empty values or explicit None.

        To record a specific failure reason for a null field, pass a sidecar key:
            update(key, {"image_url": None, "image_url__reason": "HTTP 404"})
        Keys ending in '__reason' are stored in null_fields metadata and not as top-level fields.
        """
        self._load()
        entry = dict(self._data.get(key) or {})
        now = datetime.now().isoformat(timespec="seconds")
        for field_name, value in fields.items():
            if field_name.endswith("__reason"):
                continue  # skip internal reason hints
            if value is None:
                null_fields = entry.setdefault("null_fields", {})
                null_fields[field_name] = {
                    "last_attempted": now,
                    "failure_reason": fields.get(f"{field_name}__reason", "not found"),
                }
                entry[field_name] = None
            elif value != "":
                entry[field_name] = value
        if "timestamp" not in entry:
            entry["timestamp"] = now
        self._data[key] = entry
        self._save()
