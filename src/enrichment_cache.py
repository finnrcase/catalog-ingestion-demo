# src/enrichment_cache.py
"""
Enrichment cache, session deduplication, and search budget for SCH DesignOps Intake.

Public API
----------
normalize_key(brand, model) -> str
normalize_mode(mode) -> str
budget_for_mode(mode) -> SearchBudget
confidence_ok(entry, field_name) -> bool
SearchBudget       — per-product Brave call counter
SessionCache       — in-memory per-run query/URL dedup store
ManufacturerDomainCache  — best-effort runtime manufacturer_domain_cache.json
ProductEnrichmentCache   — best-effort runtime product_enrichment_cache.json
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

from src.runtime_storage import record_storage_warning, runtime_data_path
from src.url_utils import validate_http_url

_log = logging.getLogger(__name__)

_DATA_DIR = str(runtime_data_path())
_MFR_CACHE_PATH = os.path.normpath(os.path.join(_DATA_DIR, "manufacturer_domain_cache.json"))
_PRODUCT_CACHE_PATH = os.path.normpath(os.path.join(_DATA_DIR, "product_enrichment_cache.json"))

_CACHE_ENABLED: bool = os.getenv("ENRICHMENT_CACHE_ENABLED", "true").lower() != "false"

_MODE_LIMITS: dict[str, dict] = {
    # Fetches are used to exploit already-located product pages/spec sheets.
    # They are intentionally less restrictive than Brave searches because
    # Programa readiness depends on fully parsing the verified source.
    "fast":     {"max_searches": 1, "max_urls": 6,  "retailer": False, "general_fallback": False},
    "standard": {"max_searches": 4, "max_urls": 8,  "retailer": False, "general_fallback": True},
    "deep":     {"max_searches": 8,  "max_urls": 14, "retailer": True,  "general_fallback": True},
    "max_accuracy": {"max_searches": 20, "max_urls": 32, "retailer": True, "general_fallback": True},
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
    """Return mode unchanged if valid, else 'standard'."""
    normalized = str(mode or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"max", "accuracy", "maxaccuracy"}:
        normalized = "max_accuracy"
    return normalized if normalized in VALID_MODES else "standard"


def budget_for_mode(mode: str) -> "SearchBudget":
    """Create a SearchBudget for the given enrichment mode, respecting env overrides."""
    limits = _MODE_LIMITS[normalize_mode(mode)]
    env_searches = os.getenv("BRAVE_MAX_SEARCHES_PER_PRODUCT")
    env_urls = os.getenv("ENRICHMENT_MAX_URLS_PER_PRODUCT")
    return SearchBudget(
        max_searches=int(env_searches) if env_searches else limits["max_searches"],
        max_urls=int(env_urls) if env_urls else limits["max_urls"],
        allows_retailer=limits["retailer"],
        allows_general_fallback=limits["general_fallback"],
    )


def confidence_ok(entry: dict, field_name: str) -> bool:
    """True if the relevant confidence for this field is 'high' or 'medium'."""
    conf_key = "dimension_confidence" if field_name in _DIMENSION_FIELDS else "general_confidence"
    return entry.get(conf_key, "") in ("high", "medium")


_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)
_URL_FIELDS: frozenset[str] = frozenset(
    {
        "product_url",
        "verified_product_url",
        "product_page_url",
        "manufacturer_url",
        "spec_sheet_url",
        "dimension_source_url",
        "image_source_url",
        "image_url",
        "cloudinary_url",
        "original_image_url",
    }
)


def _valid_domain(value: object) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return False
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        host = (parsed.hostname or "").lower()
    except Exception:
        return False
    if host.startswith("www."):
        host = host[4:]
    return bool(_DOMAIN_RE.match(host))


def _valid_fetch_url(value: object) -> bool:
    return not validate_http_url(value)


# ── SearchBudget ───────────────────────────────────────────────────────────────

@dataclass
class SearchBudget:
    max_searches: int
    max_urls: int
    allows_retailer: bool = False
    allows_general_fallback: bool = True
    searches_used: int = 0
    urls_used: int = 0

    def can_search(self) -> bool:
        return self.searches_used < self.max_searches

    def can_fetch(self) -> bool:
        return self.urls_used < self.max_urls

    def consume_search(self) -> None:
        self.searches_used += 1

    def consume_fetch(self) -> None:
        self.urls_used += 1


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
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self._path)
        except Exception as exc:
            record_storage_warning(self._path, exc)
            _log.warning(
                "ManufacturerDomainCache: could not save %s",
                self._path,
                exc_info=True,
            )
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def get(self, brand_key: str) -> dict | None:
        self._load()
        entry = self._data.get(brand_key)
        if not entry:
            return None
        if not _valid_domain(entry.get("domain")):
            _log.warning("ManufacturerDomainCache: removing invalid domain for %s: %r", brand_key, entry.get("domain"))
            self._data.pop(brand_key, None)
            self._save()
            return None
        return entry

    def set(self, brand_key: str, domain: str, source: str = "discovered") -> None:
        self._load()
        if not _valid_domain(domain):
            _log.warning("ManufacturerDomainCache: rejected invalid domain for %s: %r", brand_key, domain)
            return
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
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self._path)
        except Exception as exc:
            record_storage_warning(self._path, exc)
            _log.warning(
                "ProductEnrichmentCache: could not save %s",
                self._path,
                exc_info=True,
            )
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def get(self, key: str) -> dict | None:
        self._load()
        entry = self._data.get(key)
        if not isinstance(entry, dict):
            return entry
        cleaned = dict(entry)
        changed = False
        null_fields = dict(cleaned.get("null_fields") or {})
        for field_name in _URL_FIELDS:
            value = cleaned.get(field_name)
            if value and not _valid_fetch_url(value):
                _log.warning(
                    "ProductEnrichmentCache: removing invalid URL field %s for %s: %r",
                    field_name,
                    key,
                    value,
                )
                cleaned[field_name] = None
                null_fields[field_name] = {
                    "last_attempted": datetime.now().isoformat(timespec="seconds"),
                    "failure_reason": "invalid URL removed from cache",
                }
                changed = True
        if changed:
            cleaned["null_fields"] = null_fields
            self._data[key] = cleaned
            self._save()
        return cleaned

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
            if field_name in _URL_FIELDS and value not in (None, "") and not _valid_fetch_url(value):
                null_fields = entry.setdefault("null_fields", {})
                null_fields[field_name] = {
                    "last_attempted": now,
                    "failure_reason": fields.get(f"{field_name}__reason", "invalid URL rejected from cache"),
                }
                entry[field_name] = None
                continue
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
