"""Persistent image cache for product enrichment.

This is intentionally separate from the Programa export image columns.  It only
remembers verified/accepted image URLs so future enrichment can reuse them
before any search or page fetch.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.durable_cache import durable_cache_enabled, load_map, upsert_payload
from src.product_evidence import normalize_brand, normalize_sku


DATA_DIR = Path("data")
DEFAULT_IMAGE_CACHE_PATH = DATA_DIR / "image_cache.json"


def image_cache_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    env_path = os.getenv("IMAGE_CACHE_PATH", "").strip()
    return Path(env_path) if env_path else DEFAULT_IMAGE_CACHE_PATH


def _use_durable(path: str | Path | None = None) -> bool:
    return path is None and not os.getenv("IMAGE_CACHE_PATH", "").strip() and durable_cache_enabled()


def make_image_cache_key(row: dict) -> str:
    brand = normalize_brand(str(row.get("Brand") or ""))
    sku = normalize_sku(str(row.get("Model/SKU") or row.get("SKU") or row.get("_extracted_model_sku") or ""))
    if brand and sku:
        return f"{_clean(brand)}_{_clean(sku)}"
    return ""


class ImageCache:
    def __init__(self, path: str | Path | None = None) -> None:
        self._explicit_path = path
        self.path = image_cache_path(path)
        self._loaded_path: Path | None = None
        self._data: dict | None = None

    def _load(self) -> None:
        self.path = image_cache_path(self._explicit_path)
        if self._data is not None and self._loaded_path == self.path:
            return
        data: dict = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                data = loaded if isinstance(loaded, dict) else {}
            except Exception:
                data = {}
        if _use_durable(self._explicit_path):
            durable = load_map("image_cache", "cache_key")
            if durable:
                data.update(durable)
        self._data = data
        self._loaded_path = self.path

    def get(self, key: str) -> dict | None:
        self._load()
        entry = (self._data or {}).get(key)
        return entry if isinstance(entry, dict) else None

    def get_for_row(self, row: dict) -> dict | None:
        key = make_image_cache_key(row)
        if not key:
            return None
        entry = self.get(key)
        if not entry:
            return None
        confidence = str(entry.get("confidence") or entry.get("image_confidence") or "").upper()
        image_url = str(entry.get("image_url") or entry.get("Image URL") or "").strip()
        if confidence not in {"HIGH", "MEDIUM"} or not image_url:
            return None
        return entry

    def set(self, key: str, value: dict) -> dict:
        self._load()
        entry = _normalise_entry(value)
        data = self._data if self._data is not None else {}
        data[key] = entry
        self._save(data)
        self._write_durable_entry(key, entry)
        return entry

    def save_for_row(self, row: dict, **fields: Any) -> dict | None:
        key = make_image_cache_key(row)
        if not key:
            return None
        entry = {
            "brand": row.get("Brand"),
            "sku": row.get("Model/SKU") or row.get("SKU"),
            "product_name": row.get("Product Name"),
            **fields,
        }
        return self.set(key, entry)

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        self._data = data
        self._loaded_path = self.path

    def _write_durable_entry(self, key: str, entry: dict) -> None:
        if not _use_durable(self._explicit_path):
            return
        upsert_payload(
            "image_cache",
            "cache_key",
            key,
            entry,
            extra={
                "normalized_brand": entry.get("normalized_brand") or "",
                "normalized_sku": entry.get("normalized_sku") or "",
                "image_url": entry.get("image_url") or "",
                "source_url": entry.get("source_url") or "",
                "confidence": entry.get("confidence") or "",
            },
        )


def _normalise_entry(value: dict) -> dict:
    entry = dict(value or {})
    brand = str(entry.get("brand") or entry.get("Brand") or "").strip()
    sku = str(entry.get("sku") or entry.get("Model/SKU") or entry.get("SKU") or "").strip()
    confidence = str(entry.get("confidence") or entry.get("image_confidence") or entry.get("Image Recovery Confidence") or "").upper()
    if confidence not in {"HIGH", "MEDIUM", "LOW", "NONE"}:
        confidence = "NONE"
    entry.update({
        "brand": brand,
        "normalized_brand": normalize_brand(brand),
        "sku": sku,
        "normalized_sku": normalize_sku(sku),
        "product_name": str(entry.get("product_name") or entry.get("Product Name") or "").strip(),
        "image_url": str(entry.get("image_url") or entry.get("Image URL") or "").strip(),
        "source_url": str(entry.get("source_url") or entry.get("Product URL") or entry.get("Source URL") or "").strip(),
        "source_domain": str(entry.get("source_domain") or "").strip(),
        "source_type": str(entry.get("source_type") or entry.get("image_source") or "").strip(),
        "confidence": confidence,
        "evidence": str(entry.get("evidence") or entry.get("image_evidence") or "").strip(),
        "timestamp": str(entry.get("timestamp") or datetime.now(timezone.utc).isoformat(timespec="seconds")),
    })
    return entry


def _clean(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
