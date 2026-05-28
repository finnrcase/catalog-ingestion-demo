"""Exact product memory for verified product page/image/spec lookup results.

The cache is keyed by normalized brand + SKU/model with an optional product-name
hash stored for audit/disambiguation. Entries may be reusable HIGH/MEDIUM
verified lookups or LOW/NONE searched-no-result records that prevent repeat
expensive searches without autofilling row fields.
"""

from __future__ import annotations

import json
import re
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.durable_cache import durable_cache_enabled, load_map, upsert_payload
from src.product_evidence import normalize_brand, normalize_sku

_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "product_lookup_cache.json"
DEFAULT_MAX_AGE_DAYS = 30


def _uses_default_path(path: Path) -> bool:
    return path.resolve() == _CACHE_PATH.resolve()


def _clean(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def make_lookup_cache_key(row: dict) -> str:
    """Return the persistent product lookup key: normalized brand + SKU."""
    brand = normalize_brand(str(row.get("Brand") or ""))
    sku = normalize_sku(str(row.get("Model/SKU") or row.get("SKU") or row.get("_extracted_model_sku") or ""))
    parts = [_clean(brand), _clean(sku)]
    key = "_".join(part for part in parts if part)
    return key or "unknown"


def product_name_hash(value: object) -> str:
    """Short stable hash for optional product-name disambiguation."""
    normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


class ProductLookupCache:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else _CACHE_PATH
        self._data: dict | None = None

    def _load(self) -> None:
        if self._data is not None:
            return
        data: dict = {}
        if not self.path.exists():
            data = {}
        else:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                data = loaded if isinstance(loaded, dict) else {}
            except Exception:
                data = {}
        if _uses_default_path(self.path) and durable_cache_enabled():
            durable = load_map("exact_product_lookup_cache", "cache_key")
            if durable:
                data.update({key: _normalise_entry(value) for key, value in durable.items()})
        self._data = data

    def get(self, key: str) -> dict | None:
        self._load()
        return (self._data or {}).get(key)

    def set(self, key: str, value: dict) -> None:
        self._load()
        data = self._data if self._data is not None else {}
        entry = _normalise_entry(value)
        data[key] = entry
        self._save(data)
        self._write_durable_entry(key, entry)

    def get_for_row(self, row: dict, *, force_refresh: bool = False, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> dict | None:
        if force_refresh:
            return None
        entry = self.get(make_lookup_cache_key(row))
        if not entry or is_stale(entry, max_age_days=max_age_days):
            return None
        return entry

    def save_verified_lookup(self, row: dict, **fields: Any) -> dict:
        """Persist a HIGH/MEDIUM verified lookup and return the saved entry."""
        key = make_lookup_cache_key(row)
        entry = _normalise_entry({
            "brand": fields.get("brand") or row.get("Brand"),
            "sku": fields.get("sku") or row.get("Model/SKU") or row.get("SKU"),
            "product_name": fields.get("product_name") or row.get("Product Name"),
            "status": "verified",
            **fields,
        })
        self.set(key, entry)
        return entry

    def record_no_result(
        self,
        row: dict,
        *,
        confidence: str = "none",
        evidence_score: int = 0,
        evidence_summary: str = "",
    ) -> dict:
        """Persist a LOW/NONE lookup miss without creating autofill data."""
        key = make_lookup_cache_key(row)
        entry = _normalise_entry({
            "brand": row.get("Brand"),
            "sku": row.get("Model/SKU") or row.get("SKU"),
            "product_name": row.get("Product Name"),
            "status": "searched_no_result",
            "confidence": confidence if confidence in {"low", "none"} else "none",
            "evidence_score": evidence_score,
            "evidence_summary": evidence_summary,
        })
        self.set(key, entry)
        return entry

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        self._data = data

    def _write_durable_entry(self, key: str, entry: dict) -> None:
        if not (_uses_default_path(self.path) and durable_cache_enabled()):
            return
        upsert_payload(
            "exact_product_lookup_cache",
            "cache_key",
            key,
            entry,
            extra={
                "normalized_brand": entry.get("normalized_brand") or "",
                "normalized_sku": entry.get("normalized_sku") or "",
                "confidence": entry.get("confidence") or "",
                "source_type": entry.get("source_type") or "",
                "product_name_hash": entry.get("product_name_hash") or "",
                "selected_product_url": entry.get("selected_product_url") or "",
                "cloudinary_url": entry.get("cloudinary_url") or "",
            },
        )


ExactProductMemory = ProductLookupCache


def can_reuse_lookup(entry: dict | None) -> bool:
    if not entry or entry.get("status") == "searched_no_result":
        return False
    return str(entry.get("confidence", "")).lower() in {"high", "medium"}


def exact_product_urls_for_row(row: dict) -> list[str]:
    """Return verified exact product URLs from exact product memory only."""
    entry = ProductLookupCache().get_for_row(row)
    if not can_reuse_lookup(entry):
        return []
    url = str(
        (entry or {}).get("verified_product_url")
        or (entry or {}).get("selected_product_url")
        or (entry or {}).get("selected_product_page_url")
        or ""
    ).strip()
    return [url] if url else []


def is_no_result(entry: dict | None) -> bool:
    if not entry:
        return False
    return entry.get("status") == "searched_no_result" or str(entry.get("confidence", "")).lower() in {"low", "none"}


def is_stale(entry: dict | None, *, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> bool:
    if not entry:
        return False
    stamp = str(entry.get("last_verified") or entry.get("timestamp") or "")
    if not stamp:
        return True
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - parsed > timedelta(days=max_age_days)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalise_entry(value: dict) -> dict:
    entry = dict(value or {})
    brand = str(entry.get("brand") or "").strip()
    sku = str(entry.get("sku") or "").strip()
    confidence = str(entry.get("confidence") or entry.get("Product Resolution Confidence") or "").lower()
    if confidence not in {"high", "medium", "low", "none"}:
        confidence = "none"

    selected_url = str(
        entry.get("verified_product_url")
        or entry.get("selected_product_url")
        or entry.get("selected_product_page_url")
        or entry.get("Product Resolution URL")
        or ""
    ).strip()
    cloudinary_url = str(entry.get("cloudinary_url") or entry.get("cloudinary_secure_url") or "").strip()
    image_url = str(entry.get("verified_image_url") or entry.get("image_url") or entry.get("selected_image_url") or entry.get("Image URL") or "").strip()
    image_confidence = str(entry.get("image_confidence") or entry.get("Image Recovery Confidence") or "").upper()
    if image_confidence not in {"HIGH", "MEDIUM", "LOW", "NONE"}:
        image_confidence = ""

    last_verified = str(entry.get("last_verified") or entry.get("timestamp") or "").strip() or _now()
    entry.update({
        "brand": brand,
        "normalized_brand": normalize_brand(brand),
        "sku": sku,
        "normalized_sku": normalize_sku(sku),
        "product_name": str(entry.get("product_name") or entry.get("Product Name") or "").strip(),
        "product_name_hash": str(entry.get("product_name_hash") or product_name_hash(entry.get("product_name") or entry.get("Product Name"))),
        "verified_product_url": selected_url,
        "selected_product_url": selected_url,
        "selected_product_page_url": selected_url,
        "source_type": str(entry.get("source_type") or "unknown"),
        "confidence": confidence,
        "evidence_score": int(entry.get("evidence_score") or entry.get("selected_product_page_score") or 0),
        "dimensions": str(entry.get("dimensions") or entry.get("Dimensions") or "").strip(),
        "width_in": str(entry.get("width_in") or entry.get("Width (in)") or "").strip(),
        "height_in": str(entry.get("height_in") or entry.get("Height (in)") or "").strip(),
        "depth_in": str(entry.get("depth_in") or entry.get("Depth (in)") or "").strip(),
        "finish": str(entry.get("finish") or entry.get("Finish / Color") or "").strip(),
        "material": str(entry.get("material") or entry.get("Material") or "").strip(),
        "verified_image_url": image_url,
        "cloudinary_url": cloudinary_url,
        "cloudinary_secure_url": cloudinary_url,
        "image_url": image_url,
        "selected_image_url": image_url,
        "image_confidence": image_confidence,
        "last_verified": last_verified,
        "timestamp": str(entry.get("timestamp") or last_verified),
        "evidence_summary": str(entry.get("evidence_summary") or entry.get("evidence") or entry.get("Product Resolution Evidence") or "").strip(),
        "status": str(entry.get("status") or ("verified" if confidence in {"high", "medium"} else "searched_no_result")),
    })
    return entry
