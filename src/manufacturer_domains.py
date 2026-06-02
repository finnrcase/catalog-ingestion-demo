from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from src.runtime_storage import runtime_data_path, write_json_best_effort

_log = logging.getLogger(__name__)

DATA_DIR = runtime_data_path()
CACHE_PATH = DATA_DIR / "manufacturer_domain_cache.json"

HARDCODED_DOMAINS: dict[str, str] = {
    "scotsman": "scotsman-ice.com",
}

_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def normalize_brand(brand: str) -> str:
    text = str(brand or "").strip().lower()
    text = _PUNCT_RE.sub(" ", text)
    return " ".join(text.split())


def clean_domain(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Manufacturer Website is required.")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    if not _HOST_RE.match(host):
        raise ValueError("Enter a valid manufacturer hostname, such as scotsman-ice.com.")
    return host


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_path(path: Path | None = None) -> Path:
    return path or CACHE_PATH


def load_domain_cache(path: Path | None = None) -> dict:
    path = _cache_path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        repaired: dict = {}
        changed = False
        for brand, entry in data.items():
            if not isinstance(entry, dict):
                changed = True
                continue
            try:
                domain = clean_domain(entry.get("domain", ""))
            except Exception:
                _log.warning("Removing invalid manufacturer domain cache entry for %s: %r", brand, entry.get("domain"))
                changed = True
                continue
            if domain != entry.get("domain"):
                changed = True
            repaired[brand] = {**entry, "domain": domain}
        if changed:
            save_domain_cache(repaired, path)
        return repaired
    except Exception:
        _log.warning("Could not read manufacturer domain cache at %s", path, exc_info=True)
        return {}


def save_domain_cache(cache: dict, path: Path | None = None) -> None:
    path = _cache_path(path)
    write_json_best_effort(
        path,
        cache,
        description="manufacturer domain cache",
        sort_keys=True,
    )


def save_manufacturer_override(brand: str, website: str, path: Path | None = None) -> dict:
    key = normalize_brand(brand)
    if not key:
        raise ValueError("Brand is required.")
    domain = clean_domain(website)
    cache = load_domain_cache(path)
    entry = {
        "domain": domain,
        "source": "user",
        "last_verified": _timestamp(),
    }
    cache[key] = entry
    save_domain_cache(cache, path)
    return {"brand": key, **entry}


def record_discovered_domain(brand: str, domain: str, path: Path | None = None) -> dict | None:
    key = normalize_brand(brand)
    if not key:
        return None
    clean = clean_domain(domain)
    cache = load_domain_cache(path)
    existing = cache.get(key)
    if isinstance(existing, dict) and existing.get("source") == "user":
        return existing
    entry = {
        "domain": clean,
        "source": "discovered",
        "last_verified": _timestamp(),
    }
    cache[key] = entry
    save_domain_cache(cache, path)
    return entry


def get_domain_for_brand(brand: str, path: Path | None = None) -> tuple[str, str] | None:
    key = normalize_brand(brand)
    if not key:
        return None

    cache = load_domain_cache(path)
    cached = cache.get(key)
    if isinstance(cached, dict) and cached.get("source") == "user" and cached.get("domain"):
        try:
            return clean_domain(str(cached["domain"])), "user"
        except ValueError:
            cache.pop(key, None)
            save_domain_cache(cache, path)

    hardcoded = HARDCODED_DOMAINS.get(key)
    if hardcoded:
        return hardcoded, "hardcoded"

    if isinstance(cached, dict) and cached.get("domain"):
        try:
            return clean_domain(str(cached["domain"])), str(cached.get("source") or "cached")
        except ValueError:
            cache.pop(key, None)
            save_domain_cache(cache, path)

    return None
