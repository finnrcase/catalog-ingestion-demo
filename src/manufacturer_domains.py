from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DATA_DIR = Path("data")
CACHE_PATH = DATA_DIR / "manufacturer_domain_cache.json"

HARDCODED_DOMAINS: dict[str, str] = {
    "scotsman": "scotsman-ice.com",
    "sub zero": "subzero-wolf.com",
    "wolf": "subzero-wolf.com",
}

_RETAILER_DOMAINS = (
    "1stdibs.com",
    "amazon.com",
    "build.com",
    "chairish.com",
    "ebay.com",
    "etsy.com",
    "ferguson.com",
    "homedepot.com",
    "houzz.com",
    "lowes.com",
    "lumens.com",
    "perigold.com",
    "wayfair.com",
    "walmart.com",
)

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


def is_retailer_domain(domain: str) -> bool:
    host = clean_domain(domain)
    return any(host == root or host.endswith("." + root) for root in _RETAILER_DOMAINS)


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
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_domain_cache(cache: dict, path: Path | None = None) -> None:
    path = _cache_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _entry(
    brand_key: str,
    domain: str,
    *,
    source: str,
    confidence: str,
    evidence_url: str = "",
) -> dict:
    return {
        "brand": brand_key,
        "official_domain": domain,
        "domain": domain,
        "source": source,
        "confidence": confidence,
        "last_verified": _timestamp(),
        "evidence_url": str(evidence_url or ""),
    }


def save_manufacturer_override(brand: str, website: str, path: Path | None = None) -> dict:
    key = normalize_brand(brand)
    if not key:
        raise ValueError("Brand is required.")
    domain = clean_domain(website)
    if is_retailer_domain(domain):
        raise ValueError("Retailer domains cannot be saved as manufacturer domains.")
    cache = load_domain_cache(path)
    entry = _entry(key, domain, source="user", confidence="high")
    cache[key] = entry
    save_domain_cache(cache, path)
    return {"brand": key, **entry}


def record_discovered_domain(
    brand: str,
    domain: str,
    path: Path | None = None,
    *,
    confidence: str = "medium",
    evidence_url: str = "",
) -> dict | None:
    key = normalize_brand(brand)
    if not key:
        return None
    clean = clean_domain(domain)
    if is_retailer_domain(clean):
        return None
    cache = load_domain_cache(path)
    existing = cache.get(key)
    if isinstance(existing, dict) and existing.get("source") == "user":
        return existing
    entry = _entry(key, clean, source="discovered", confidence=confidence, evidence_url=evidence_url)
    cache[key] = entry
    save_domain_cache(cache, path)
    return entry


def record_verified_domain(
    brand: str,
    domain: str,
    path: Path | None = None,
    *,
    confidence: str = "high",
    evidence_url: str = "",
) -> dict | None:
    key = normalize_brand(brand)
    if not key:
        return None
    clean = clean_domain(domain)
    if is_retailer_domain(clean):
        return None
    cache = load_domain_cache(path)
    existing = cache.get(key)
    if isinstance(existing, dict) and existing.get("source") == "user":
        return existing
    entry = _entry(key, clean, source="verified", confidence=confidence, evidence_url=evidence_url)
    cache[key] = entry
    save_domain_cache(cache, path)
    return entry


def get_domain_for_brand(brand: str, path: Path | None = None) -> tuple[str, str] | None:
    key = normalize_brand(brand)
    if not key:
        return None

    cache = load_domain_cache(path)
    cached = cache.get(key)
    cached_domain = str(cached.get("official_domain") or cached.get("domain") or "") if isinstance(cached, dict) else ""
    if isinstance(cached, dict) and cached.get("source") == "user" and cached_domain:
        if not is_retailer_domain(cached_domain):
            return clean_domain(cached_domain), "user"

    hardcoded = HARDCODED_DOMAINS.get(key)
    if hardcoded:
        return hardcoded, "hardcoded"

    if isinstance(cached, dict) and cached_domain:
        if is_retailer_domain(cached_domain):
            return None
        return clean_domain(cached_domain), str(cached.get("source") or "cached")

    return None
