"""Persistent cache for official product page/image/spec lookup results."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "product_lookup_cache.json"


def _clean(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def make_lookup_cache_key(row: dict) -> str:
    parts = [
        _clean(row.get("Brand")),
        _clean(row.get("Model/SKU") or row.get("SKU")),
        _clean(row.get("Product Name")),
        _clean(row.get("Supplier")),
    ]
    key = "_".join(part for part in parts if part)
    return key or "unknown"


class ProductLookupCache:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else _CACHE_PATH
        self._data: dict | None = None

    def _load(self) -> None:
        if self._data is not None:
            return
        if not self.path.exists():
            self._data = {}
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self._data = {}

    def get(self, key: str) -> dict | None:
        self._load()
        return (self._data or {}).get(key)

    def set(self, key: str, value: dict) -> None:
        self._load()
        data = self._data if self._data is not None else {}
        data[key] = {
            **value,
            "timestamp": value.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        self._data = data
