"""User-managed preferred brand and vendor websites for enrichment.

Entries are stored in a small persistent JSON database so users can teach the
resolver which sites to try first for brands, vendors, or keywords without
changing export schemas or enrichment trust rules.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.durable_cache import delete_payload, durable_cache_enabled, load_list, upsert_payload


DATA_DIR = Path("data")
DEFAULT_PREFERRED_WEBSITES_PATH = DATA_DIR / "preferred_product_websites.json"


def preferred_websites_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    env_path = os.getenv("PREFERRED_WEBSITES_PATH", "").strip()
    return Path(env_path) if env_path else DEFAULT_PREFERRED_WEBSITES_PATH


def _use_durable(path: str | Path | None = None) -> bool:
    return path is None and not os.getenv("PREFERRED_WEBSITES_PATH", "").strip() and durable_cache_enabled()


def load_preferred_websites(path: str | Path | None = None) -> list[dict[str, Any]]:
    path_obj = preferred_websites_path(path)
    entries: list[dict[str, Any]] = []
    if path_obj.exists():
        try:
            data = json.loads(path_obj.read_text(encoding="utf-8"))
        except Exception:
            data = []
        if isinstance(data, dict):
            loaded = data.get("entries", [])
        else:
            loaded = data
        entries = [entry for entry in loaded if isinstance(entry, dict)]
    if _use_durable(path):
        durable_entries = load_list("preferred_product_websites")
        by_id = {_text(entry.get("id")) or _duplicate_key(entry.get("keyword"), entry.get("url")): entry for entry in entries}
        for entry in durable_entries:
            key = _text(entry.get("id")) or _duplicate_key(entry.get("keyword"), entry.get("url"))
            if key:
                by_id[key] = entry
        entries = list(by_id.values())
    return entries


def save_preferred_websites(entries: list[dict[str, Any]], path: str | Path | None = None) -> None:
    path_obj = preferred_websites_path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    tmp = path_obj.with_suffix(path_obj.suffix + ".tmp")
    tmp.write_text(json.dumps({"entries": entries}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path_obj)
    if _use_durable(path):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = _text(entry.get("id"))
            if not entry_id:
                continue
            upsert_payload(
                "preferred_product_websites",
                "id",
                entry_id,
                entry,
                extra={
                    "keyword": _text(entry.get("keyword")),
                    "domain": _clean_domain(entry.get("domain") or domain_from_url(entry.get("url"))),
                    "url": _text(entry.get("url")),
                    "success_count": int(entry.get("success_count") or 0),
                    "failure_count": int(entry.get("failure_count") or 0),
                },
            )


def list_preferred_websites(path: str | Path | None = None) -> list[dict[str, Any]]:
    return sorted(load_preferred_websites(path), key=lambda item: (_text(item.get("keyword")).lower(), _text(item.get("url")).lower()))


def add_preferred_website(
    *,
    keyword: str,
    url: str,
    notes: str = "",
    path: str | Path | None = None,
) -> dict[str, Any]:
    keyword_clean = _clean_keyword(keyword)
    url_clean = normalise_preferred_url(url)
    if not keyword_clean:
        raise ValueError("Brand / keyword is required.")
    if not url_clean:
        raise ValueError("Preferred website URL must be a valid http or https URL.")
    entries = load_preferred_websites(path)
    duplicate_key = _duplicate_key(keyword_clean, url_clean)
    if any(_duplicate_key(entry.get("keyword"), entry.get("url")) == duplicate_key for entry in entries):
        raise ValueError("Preferred website already exists for this keyword and URL.")
    now = _now()
    entry = {
        "id": uuid.uuid4().hex,
        "keyword": keyword_clean,
        "url": url_clean,
        "domain": domain_from_url(url_clean),
        "notes": _text(notes),
        "created_at": now,
        "updated_at": now,
        "success_count": 0,
        "failure_count": 0,
        "last_checked": "",
        "last_status": "",
        "last_fields_found": {},
        "field_success_counts": {
            "dimensions": 0,
            "image": 0,
            "price": 0,
            "specs": 0,
            "product_url": 0,
        },
    }
    entries.append(entry)
    save_preferred_websites(entries, path)
    return entry


def update_preferred_website(
    entry_id: str,
    *,
    keyword: str,
    url: str,
    notes: str = "",
    path: str | Path | None = None,
) -> dict[str, Any]:
    entries = load_preferred_websites(path)
    entry_id = _text(entry_id)
    index = next((i for i, entry in enumerate(entries) if _text(entry.get("id")) == entry_id), -1)
    if index < 0:
        raise KeyError("Preferred website was not found.")
    keyword_clean = _clean_keyword(keyword)
    url_clean = normalise_preferred_url(url)
    if not keyword_clean:
        raise ValueError("Brand / keyword is required.")
    if not url_clean:
        raise ValueError("Preferred website URL must be a valid http or https URL.")
    duplicate_key = _duplicate_key(keyword_clean, url_clean)
    for entry in entries:
        if _text(entry.get("id")) != entry_id and _duplicate_key(entry.get("keyword"), entry.get("url")) == duplicate_key:
            raise ValueError("Preferred website already exists for this keyword and URL.")
    updated = {
        **entries[index],
        "keyword": keyword_clean,
        "url": url_clean,
        "domain": domain_from_url(url_clean),
        "notes": _text(notes),
        "updated_at": _now(),
    }
    entries[index] = updated
    save_preferred_websites(entries, path)
    return updated


def delete_preferred_website(entry_id: str, path: str | Path | None = None) -> bool:
    entries = load_preferred_websites(path)
    kept = [entry for entry in entries if _text(entry.get("id")) != _text(entry_id)]
    if len(kept) == len(entries):
        return False
    save_preferred_websites(kept, path)
    if _use_durable(path):
        delete_payload("preferred_product_websites", "id", _text(entry_id))
    return True


def matching_preferred_websites(row: dict, path: str | Path | None = None) -> list[dict[str, Any]]:
    haystack = _normalise_match_text(" ".join(
        _text(row.get(field))
        for field in (
            "Product Name",
            "Description",
            "Brand",
            "Manufacturer",
            "Vendor",
            "Supplier",
            "Model/SKU",
            "SKU",
            "_extracted_model_sku",
            "Product Category",
            "Notes",
            "Product URL",
            "Source URL",
        )
    ))
    if not haystack:
        return []
    matches: list[dict[str, Any]] = []
    for entry in load_preferred_websites(path):
        terms = _preferred_match_terms(entry)
        if any(_term_matches_haystack(term, haystack) for term in terms):
            matches.append(entry)
    return sorted(matches, key=lambda item: (-int(item.get("success_count") or 0), int(item.get("failure_count") or 0), _text(item.get("domain"))))


def preferred_domains_for_row(row: dict, path: str | Path | None = None) -> list[str]:
    domains: list[str] = []
    for entry in matching_preferred_websites(row, path):
        domain = _clean_domain(entry.get("domain") or domain_from_url(entry.get("url")))
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def preferred_direct_urls_for_row(row: dict, path: str | Path | None = None) -> list[dict[str, str]]:
    urls: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in matching_preferred_websites(row, path):
        url = _text(entry.get("url"))
        if not url or not _looks_like_direct_product_url(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append({
            "url": url,
            "entry_id": _text(entry.get("id")),
            "keyword": _text(entry.get("keyword")),
            "domain": _clean_domain(entry.get("domain") or domain_from_url(url)),
        })
    return urls


def record_preferred_website_result(
    *,
    entry_id: str = "",
    domain: str = "",
    url: str = "",
    success: bool,
    fields_found: dict[str, Any] | None = None,
    status: str = "",
    path: str | Path | None = None,
) -> dict[str, Any] | None:
    entries = load_preferred_websites(path)
    clean_domain = _clean_domain(domain or domain_from_url(url))
    fields = _normalise_fields(fields_found or {})
    for idx, entry in enumerate(entries):
        entry_matches = _text(entry.get("id")) == _text(entry_id) if entry_id else False
        domain_matches = clean_domain and _clean_domain(entry.get("domain")) == clean_domain
        if not entry_matches and not domain_matches:
            continue
        updated = dict(entry)
        if success:
            updated["success_count"] = int(updated.get("success_count") or 0) + 1
            counts = dict(updated.get("field_success_counts") or {})
            for field, found in fields.items():
                if found:
                    counts[field] = int(counts.get(field) or 0) + 1
            updated["field_success_counts"] = counts
        else:
            updated["failure_count"] = int(updated.get("failure_count") or 0) + 1
        updated["last_checked"] = _now()
        updated["last_status"] = status or ("success" if success else "failed")
        updated["last_fields_found"] = fields
        updated["updated_at"] = _now()
        entries[idx] = updated
        save_preferred_websites(entries, path)
        return updated
    return None


def normalise_preferred_url(url: str) -> str:
    raw = _text(url)
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or re.search(r"\s", parsed.netloc):
        return ""
    host = parsed.netloc.lower().strip()
    path = parsed.path or "/"
    cleaned = parsed._replace(netloc=host, path=path, fragment="")
    return cleaned.geturl().rstrip("/") if path != "/" else cleaned.geturl().rstrip("/") + "/"


def domain_from_url(url: object) -> str:
    return _clean_domain(urlparse(_text(url)).hostname or "")


def _normalise_fields(fields: dict[str, Any]) -> dict[str, bool]:
    return {
        "dimensions": bool(fields.get("dimensions")),
        "image": bool(fields.get("image")),
        "price": bool(fields.get("price")),
        "specs": bool(fields.get("specs") or fields.get("spec_sheet")),
        "product_url": bool(fields.get("product_url")),
    }


def _looks_like_direct_product_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").strip("/")
    if not path:
        return False
    return not re.search(r"^(?:search|collections?|categories?|catalog|sitemap|contact|support|privacy|terms)/?$", path, re.I)


def _duplicate_key(keyword: object, url: object) -> str:
    return f"{_normalise_match_text(keyword)}|{normalise_preferred_url(_text(url)).lower()}"


def _preferred_match_terms(entry: dict[str, Any]) -> list[str]:
    raw_terms: list[str] = [
        _text(entry.get("keyword")),
        _text(entry.get("domain")),
        domain_from_url(entry.get("url")),
    ]
    aliases = entry.get("aliases") or entry.get("alias") or entry.get("keywords")
    if isinstance(aliases, list):
        raw_terms.extend(_text(alias) for alias in aliases)
    elif aliases:
        raw_terms.extend(re.split(r"[,;|/]+", _text(aliases)))

    # Notes are not a required alias field, but users often paste shorthand
    # cues there. Only consume explicit "aliases:" / "aka:" fragments so normal
    # notes do not accidentally over-match broad terms.
    notes = _text(entry.get("notes"))
    note_match = re.search(r"\b(?:aliases?|aka|also known as)\s*:\s*([^.;\n]+)", notes, re.I)
    if note_match:
        raw_terms.extend(re.split(r"[,;|/]+", note_match.group(1)))

    terms: list[str] = []
    for term in raw_terms:
        clean = _normalise_match_text(term)
        compact = clean.replace(" ", "")
        for candidate in (clean, compact):
            if len(candidate) > 1 and candidate not in terms:
                terms.append(candidate)
    return terms


def _term_matches_haystack(term: str, haystack: str) -> bool:
    if not term or not haystack:
        return False
    if term in haystack:
        return True
    compact_haystack = haystack.replace(" ", "")
    if len(term) >= 4 and term in compact_haystack:
        return True
    tokens = [token for token in term.split() if len(token) > 1]
    return bool(tokens and all(token in haystack for token in tokens))


def _clean_keyword(value: object) -> str:
    return re.sub(r"\s+", " ", _text(value)).strip()


def _normalise_match_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _text(value).lower()).strip()


def _clean_domain(value: object) -> str:
    raw = _text(value).lower().strip()
    if "://" in raw:
        raw = urlparse(raw).hostname or ""
    raw = raw.strip("/")
    if raw.startswith("www."):
        raw = raw[4:]
    return raw


def _text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
