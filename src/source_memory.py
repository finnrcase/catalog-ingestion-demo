from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - httpx is present in supported envs
    httpx = None

from src.dimensions import extract_labeled_dimensions, has_complete_3d_dimensions
from src.runtime_storage import record_storage_warning, runtime_data_path, write_json_best_effort

_log = logging.getLogger(__name__)

PRODUCT_SOURCE_TABLE = "stored_product_sources"
PREFERRED_DOMAIN_TABLE = "preferred_source_domains"

_PRODUCT_SOURCE_PATH = runtime_data_path("stored_product_sources.json")
_PREFERRED_DOMAIN_PATH = runtime_data_path("preferred_source_domains.json")
_SOURCE_MEMORY_ENABLED = os.getenv("SOURCE_MEMORY_ENABLED", "true").lower() != "false"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_lookup_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def normalize_domain(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"^https?://", "", text)
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return text[4:] if text.startswith("www.") else text


def domain_from_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        from urllib.parse import urlparse

        return normalize_domain(urlparse(text).netloc)
    except Exception:
        return ""


def _source_type_from_url(url: str, fallback: object = "") -> str:
    text = str(fallback or "").strip().lower().replace("_", " ")
    if "manufacturer" in text:
        return "manufacturer"
    if "retailer" in text:
        return "retailer"
    if "manual" in text:
        return "manual"
    if "spec" in text or str(url or "").lower().split("?", 1)[0].endswith(".pdf"):
        return "spec_sheet"
    return "other"


def _confidence_score(*values: object) -> int:
    text = " ".join(str(v or "").lower() for v in values)
    if "high" in text:
        return 90
    if "medium" in text:
        return 70
    if "low" in text:
        return 35
    return 60 if any(v for v in values) else 0


def _confidence_from_score(value: object) -> str:
    try:
        score = int(float(value or 0))
    except (TypeError, ValueError):
        return "none"
    if score >= 80:
        return "high"
    if score >= 60:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def _parse_json_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        record_storage_warning(path, exc)
        return []
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [dict(item) for item in payload.values() if isinstance(item, dict)]
    return []


def _write_json_file(path: Path, rows: list[dict]) -> None:
    write_json_best_effort(path, rows, description=path.name)


def _supabase_configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and _supabase_key() and httpx is not None)


def _supabase_key() -> str:
    return (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_SERVICE_KEY")
        or ""
    )


def storage_backend_name() -> str:
    return "supabase" if _supabase_configured() else "local_json"


def _supabase_headers(*, returning: bool = False) -> dict[str, str]:
    key = _supabase_key()
    prefer = "return=representation" if returning else "return=minimal"
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def _supabase_url(table: str) -> str:
    base = str(os.getenv("SUPABASE_URL") or "").rstrip("/")
    return f"{base}/rest/v1/{table}"


def _supabase_request(method: str, table: str, *, params: dict | None = None, json_body: Any = None, returning: bool = False):
    if not _supabase_configured():
        return None
    try:
        with httpx.Client(timeout=12) as client:
            response = client.request(
                method,
                _supabase_url(table),
                params=params or {},
                json=json_body,
                headers=_supabase_headers(returning=returning),
            )
            response.raise_for_status()
            if not response.content:
                return [] if returning else {"ok": True}
            return response.json()
    except Exception as exc:
        record_storage_warning(f"supabase:{table}", exc)
        _log.warning("Supabase source memory operation failed for %s", table, exc_info=True)
        return None


def _local_list(path: Path) -> list[dict]:
    return _parse_json_file(path)


def _local_upsert(path: Path, record: dict, conflict_fields: tuple[str, ...], *, increment_counts: bool = True) -> dict:
    rows = _local_list(path)
    now = _now_iso()
    merged = dict(record)
    for existing in rows:
        if all(str(existing.get(field) or "") == str(record.get(field) or "") for field in conflict_fields):
            merged = {**existing, **record, "updated_at": now}
            if increment_counts and record.get("success_count") is not None:
                merged["success_count"] = int(existing.get("success_count") or 0) + int(record.get("success_count") or 0)
            if increment_counts and record.get("failure_count") is not None:
                merged["failure_count"] = int(existing.get("failure_count") or 0) + int(record.get("failure_count") or 0)
            existing.clear()
            existing.update(merged)
            _write_json_file(path, rows)
            return existing
    merged.setdefault("id", str(uuid.uuid4()))
    merged.setdefault("created_at", now)
    merged.setdefault("updated_at", now)
    rows.append(merged)
    _write_json_file(path, rows)
    return merged


def _local_delete(path: Path, item_id: str) -> bool:
    rows = _local_list(path)
    next_rows = [row for row in rows if str(row.get("id") or "") != item_id]
    _write_json_file(path, next_rows)
    return len(next_rows) < len(rows)


def _local_update(path: Path, item_id: str, fields: dict) -> dict | None:
    rows = _local_list(path)
    for row in rows:
        if str(row.get("id") or "") == item_id:
            row.update(fields)
            row["updated_at"] = _now_iso()
            _write_json_file(path, rows)
            return row
    return None


def _supabase_first_by_params(table: str, params: dict) -> dict | None:
    rows = _supabase_request("GET", table, params={**params, "limit": "1"}, returning=True)
    return rows[0] if isinstance(rows, list) and rows else None


def _usable_source(entry: dict) -> bool:
    if not entry:
        return False
    score = int(entry.get("confidence_score") or 0)
    if score < 60:
        return False
    return any(
        entry.get(field)
        for field in (
            "dimensions",
            "dimensions_text",
            "dimension_source_url",
            "image_url",
            "image_source_url",
            "manufacturer_url",
            "product_page_url",
            "spec_sheet_url",
        )
    )


def _source_rank(record: dict) -> tuple[int, int]:
    source_type = str(record.get("source_type") or "").lower()
    source_rank = {
        "manufacturer": 4,
        "spec_sheet": 3,
        "manual": 2,
        "retailer": 1,
        "trusted_retailer": 1,
        "other": 0,
    }.get(source_type, 0)
    try:
        score = int(float(record.get("confidence_score") or 0))
    except (TypeError, ValueError):
        score = 0
    return source_rank, score


def _normalize_product_source_payload(record: dict) -> dict:
    now = _now_iso()
    display_brand = str(record.get("display_brand") or record.get("brand") or "").strip()
    display_model = str(record.get("display_model_sku") or record.get("model_sku") or record.get("model") or "").strip()
    normalized_brand = normalize_lookup_token(record.get("normalized_brand") or display_brand)
    normalized_model_sku = normalize_lookup_token(
        record.get("normalized_model_sku")
        or record.get("normalized_model")
        or display_model
    )
    dimensions_text = str(record.get("dimensions_text") or record.get("dimensions") or "").strip()
    payload = {
        **record,
        "normalized_brand": normalized_brand,
        "normalized_model_sku": normalized_model_sku,
        "normalized_model": normalized_model_sku,
        "display_brand": display_brand,
        "display_model_sku": display_model,
        "brand": record.get("brand") or display_brand,
        "model_sku": record.get("model_sku") or display_model,
        "dimensions_text": dimensions_text,
        "dimensions": record.get("dimensions") or dimensions_text,
        "first_seen_at": record.get("first_seen_at") or record.get("created_at") or now,
        "last_verified_at": record.get("last_verified_at") or now,
        "updated_at": now,
    }
    payload.setdefault("success_count", 0)
    payload.setdefault("failure_count", 0)
    payload.setdefault(
        "confidence_score",
        _confidence_score(payload.get("confidence"), payload.get("dimension_confidence"), payload.get("image_confidence")),
    )
    if not payload.get("dimension_confidence"):
        payload["dimension_confidence"] = payload.get("Dimension Confidence") or payload.get("confidence") or _confidence_from_score(payload.get("confidence_score"))
    if not payload.get("image_confidence"):
        payload["image_confidence"] = payload.get("confidence") or _confidence_from_score(payload.get("confidence_score"))
    if not payload.get("source_domain"):
        payload["source_domain"] = domain_from_url(
            payload.get("product_page_url")
            or payload.get("manufacturer_url")
            or payload.get("dimension_source_url")
            or payload.get("image_source_url")
            or payload.get("spec_sheet_url")
        )
    if not payload.get("manufacturer_url") and payload.get("source_type") == "manufacturer":
        payload["manufacturer_url"] = payload.get("product_page_url") or payload.get("dimension_source_url") or ""
    if not payload.get("source_type"):
        payload["source_type"] = _source_type_from_url(
            payload.get("product_page_url") or payload.get("dimension_source_url") or payload.get("spec_sheet_url"),
            payload.get("source_type"),
        )
    return payload


def _merge_product_source_records(existing: dict | None, incoming: dict) -> dict:
    if not existing:
        return incoming
    now = _now_iso()
    existing_rank = _source_rank(existing)
    incoming_rank = _source_rank(incoming)
    incoming_is_better = incoming_rank >= existing_rank
    preserve_existing = existing_rank[0] > incoming_rank[0] and existing_rank[1] >= incoming_rank[1]
    merged = {**existing, **incoming} if incoming_is_better else {**incoming, **existing}
    merged["success_count"] = int(existing.get("success_count") or 0) + int(incoming.get("success_count") or 0)
    merged["failure_count"] = int(existing.get("failure_count") or 0) + int(incoming.get("failure_count") or 0)
    merged["first_seen_at"] = existing.get("first_seen_at") or existing.get("created_at") or incoming.get("first_seen_at") or now
    merged["created_at"] = existing.get("created_at") or incoming.get("created_at") or now
    merged["updated_at"] = now
    if preserve_existing:
        protected_fields = (
            "product_page_url",
            "manufacturer_url",
            "spec_sheet_url",
            "dimension_source_url",
            "image_source_url",
            "image_url",
            "dimensions_text",
            "dimensions",
            "width_in",
            "height_in",
            "depth_in",
            "dimension_confidence",
            "image_confidence",
            "source_domain",
            "source_type",
            "confidence_score",
            "confidence",
        )
        for field in protected_fields:
            if existing.get(field):
                merged[field] = existing[field]
            elif incoming.get(field):
                merged[field] = incoming[field]
    return merged


def list_product_sources(query: str = "", source_type: str = "", limit: int = 100) -> list[dict]:
    if not _SOURCE_MEMORY_ENABLED:
        return []
    limit = max(1, min(int(limit or 100), 500))
    if _supabase_configured():
        params = {
            "select": "*",
            "order": "success_count.desc,last_verified_at.desc",
            "limit": str(limit),
        }
        if source_type:
            params["source_type"] = f"eq.{source_type}"
        rows = _supabase_request("GET", PRODUCT_SOURCE_TABLE, params=params, returning=True)
        if isinstance(rows, list):
            return _filter_sources(rows, query)[:limit]
    return _filter_sources(_local_list(_PRODUCT_SOURCE_PATH), query, source_type)[:limit]


def _filter_sources(rows: list[dict], query: str = "", source_type: str = "") -> list[dict]:
    query_norm = str(query or "").strip().lower()
    source_type_norm = str(source_type or "").strip().lower()
    filtered = []
    for row in rows:
        if source_type_norm and str(row.get("source_type") or "").lower() != source_type_norm:
            continue
        haystack = " ".join(str(row.get(field) or "") for field in (
            "display_brand",
            "display_model_sku",
            "brand",
            "model_sku",
            "product_name",
            "source_domain",
            "manufacturer_url",
            "product_page_url",
            "spec_sheet_url",
            "notes",
        )).lower()
        if query_norm and query_norm not in haystack:
            continue
        filtered.append(row)
    return sorted(
        filtered,
        key=lambda row: (
            int(row.get("success_count") or 0),
            int(row.get("confidence_score") or 0),
            str(row.get("last_verified_at") or ""),
        ),
        reverse=True,
    )


def lookup_product_source(brand: str, model: str) -> dict | None:
    if not _SOURCE_MEMORY_ENABLED:
        return None
    normalized_brand = normalize_lookup_token(brand)
    normalized_model_sku = normalize_lookup_token(model)
    if not normalized_brand or not normalized_model_sku:
        return None
    if _supabase_configured():
        params = {
            "select": "*",
            "normalized_brand": f"eq.{normalized_brand}",
            "normalized_model_sku": f"eq.{normalized_model_sku}",
            "order": "confidence_score.desc,success_count.desc,last_verified_at.desc",
            "limit": "1",
        }
        rows = _supabase_request("GET", PRODUCT_SOURCE_TABLE, params=params, returning=True)
        if isinstance(rows, list) and rows and _usable_source(rows[0]):
            return rows[0]
        legacy_params = {
            "select": "*",
            "normalized_brand": f"eq.{normalized_brand}",
            "normalized_model": f"eq.{normalized_model_sku}",
            "order": "confidence_score.desc,success_count.desc,last_verified_at.desc",
            "limit": "1",
        }
        rows = _supabase_request("GET", PRODUCT_SOURCE_TABLE, params=legacy_params, returning=True)
        if isinstance(rows, list) and rows and _usable_source(rows[0]):
            return rows[0]
    rows = [
        row for row in _local_list(_PRODUCT_SOURCE_PATH)
        if row.get("normalized_brand") == normalized_brand
        and (row.get("normalized_model_sku") == normalized_model_sku or row.get("normalized_model") == normalized_model_sku)
    ]
    rows = _filter_sources(rows)
    return rows[0] if rows and _usable_source(rows[0]) else None


def upsert_product_source(record: dict) -> dict:
    payload = _normalize_product_source_payload(record)
    normalized_brand = str(payload.get("normalized_brand") or "")
    normalized_model_sku = str(payload.get("normalized_model_sku") or "")
    if not normalized_brand or not normalized_model_sku:
        return payload
    if _supabase_configured():
        existing = _supabase_first_by_params(
            PRODUCT_SOURCE_TABLE,
            {
                "select": "*",
                "normalized_brand": f"eq.{normalized_brand}",
                "normalized_model_sku": f"eq.{normalized_model_sku}",
            },
        )
        if not existing:
            existing = _supabase_first_by_params(
                PRODUCT_SOURCE_TABLE,
                {
                    "select": "*",
                    "normalized_brand": f"eq.{normalized_brand}",
                    "normalized_model": f"eq.{normalized_model_sku}",
                },
            )
        payload = _merge_product_source_records(existing, payload)
        params = {"on_conflict": "normalized_brand,normalized_model_sku"}
        headers = _supabase_headers(returning=True)
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        try:
            with httpx.Client(timeout=12) as client:
                response = client.post(
                    _supabase_url(PRODUCT_SOURCE_TABLE),
                    params=params,
                    json=[payload],
                    headers=headers,
                )
                response.raise_for_status()
                rows = response.json()
                return rows[0] if isinstance(rows, list) and rows else payload
        except Exception as exc:
            record_storage_warning(f"supabase:{PRODUCT_SOURCE_TABLE}", exc)
            _log.warning("Supabase upsert failed for product source", exc_info=True)
    existing_local = None
    for row in _local_list(_PRODUCT_SOURCE_PATH):
        if row.get("normalized_brand") == normalized_brand and (
            row.get("normalized_model_sku") == normalized_model_sku or row.get("normalized_model") == normalized_model_sku
        ):
            existing_local = row
            break
    payload = _merge_product_source_records(existing_local, payload)
    if existing_local and existing_local.get("id"):
        payload["id"] = existing_local["id"]
        updated = _local_update(_PRODUCT_SOURCE_PATH, str(existing_local["id"]), payload)
        return updated or payload
    return _local_upsert(_PRODUCT_SOURCE_PATH, payload, ("normalized_brand", "normalized_model_sku"), increment_counts=False)


def update_product_source(source_id: str, fields: dict) -> dict | None:
    fields = dict(fields or {})
    if any(key in fields for key in ("brand", "model_sku", "display_brand", "display_model_sku", "dimensions", "dimensions_text")):
        normalized = _normalize_product_source_payload(fields)
        fields.update({
            key: value
            for key, value in normalized.items()
            if key in {
                "normalized_brand",
                "normalized_model_sku",
                "normalized_model",
                "display_brand",
                "display_model_sku",
                "brand",
                "model_sku",
                "dimensions",
                "dimensions_text",
            }
        })
    fields["updated_at"] = _now_iso()
    if _supabase_configured():
        params = {"id": f"eq.{source_id}"}
        rows = _supabase_request("PATCH", PRODUCT_SOURCE_TABLE, params=params, json_body=fields, returning=True)
        return rows[0] if isinstance(rows, list) and rows else None
    return _local_update(_PRODUCT_SOURCE_PATH, source_id, fields)


def delete_product_source(source_id: str) -> bool:
    if _supabase_configured():
        result = _supabase_request("DELETE", PRODUCT_SOURCE_TABLE, params={"id": f"eq.{source_id}"})
        return bool(result)
    return _local_delete(_PRODUCT_SOURCE_PATH, source_id)


def reverify_product_source(source_id: str) -> dict | None:
    return update_product_source(source_id, {"last_verified_at": _now_iso(), "notes": "Marked for manual re-verify."})


def source_record_from_row(row: dict, *, brand: str = "", model: str = "", product_name: str = "", notes: str = "") -> dict:
    brand = str(brand or row.get("Brand") or "").strip()
    model = str(model or row.get("Model/SKU") or "").strip()
    product_name = str(product_name or row.get("Product Name") or "").strip()
    dimensions = str(row.get("Dimensions") or "").strip()
    parts = extract_labeled_dimensions(dimensions)
    dimension_source_url = str(row.get("Dimension Source URL") or row.get("dimension_source_url") or "").strip()
    image_source_url = str(row.get("image_source_url") or row.get("Image Source URL") or "").strip()
    product_page_url = str(row.get("Product URL") or row.get("selected_product_url") or row.get("product_url") or "").strip()
    manufacturer_url = str(row.get("Manufacturer URL") or row.get("manufacturer_url") or "").strip()
    spec_sheet_url = str(row.get("spec_sheet_url") or row.get("Spec Sheet URL") or "").strip()
    if not spec_sheet_url:
        for value in (dimension_source_url, product_page_url):
            if str(value).lower().split("?", 1)[0].endswith(".pdf"):
                spec_sheet_url = value
                break
    source_url = dimension_source_url or product_page_url or manufacturer_url or spec_sheet_url or image_source_url
    score = _confidence_score(
        row.get("Dimension Confidence"),
        row.get("dimension_confidence"),
        row.get("selected_product_url_confidence"),
        row.get("image_confidence"),
    )
    dimension_confidence = str(row.get("Dimension Confidence") or row.get("dimension_confidence") or "").strip()
    image_confidence = str(row.get("image_confidence") or row.get("Image Confidence") or "").strip()
    normalized_model_sku = normalize_lookup_token(model)
    return {
        "display_brand": brand,
        "display_model_sku": model,
        "brand": brand,
        "model_sku": model,
        "normalized_brand": normalize_lookup_token(brand),
        "normalized_model_sku": normalized_model_sku,
        "normalized_model": normalized_model_sku,
        "product_name": product_name,
        "manufacturer_url": manufacturer_url,
        "dimensions_text": dimensions if dimensions else "",
        "dimensions": dimensions if dimensions else "",
        "dimension_source_url": dimension_source_url,
        "image_source_url": image_source_url,
        "product_page_url": product_page_url,
        "spec_sheet_url": spec_sheet_url,
        "width_in": str(row.get("Width (in)") or parts.get("width") or "").strip(),
        "height_in": str(row.get("Height (in)") or parts.get("height") or "").strip(),
        "depth_in": str(row.get("Depth (in)") or parts.get("depth") or "").strip(),
        "image_url": str(row.get("Image URL") or "").strip(),
        "source_domain": domain_from_url(source_url),
        "confidence_score": score,
        "confidence": _confidence_from_score(score),
        "dimension_confidence": dimension_confidence or _confidence_from_score(score),
        "image_confidence": image_confidence or _confidence_from_score(score),
        "source_type": _source_type_from_url(source_url, row.get("Dimension Source Type")),
        "last_verified_at": _now_iso(),
        "first_seen_at": row.get("first_seen_at") or _now_iso(),
        "success_count": 1,
        "failure_count": 0,
        "notes": notes,
        "raw": {
            "dimension_confidence": row.get("Dimension Confidence") or row.get("dimension_confidence"),
            "image_confidence": row.get("image_confidence"),
            "selected_product_url_confidence": row.get("selected_product_url_confidence"),
        },
    }


def save_successful_source_from_row(row: dict, *, notes: str = "") -> dict | None:
    record = source_record_from_row(row, notes=notes)
    if not record.get("normalized_brand") or not record.get("normalized_model_sku"):
        return None
    if not any(record.get(field) for field in ("dimensions_text", "dimensions", "image_url", "product_page_url", "manufacturer_url", "dimension_source_url", "spec_sheet_url")):
        return None
    saved = upsert_product_source(record)
    if saved and saved.get("source_domain"):
        upsert_preferred_domain({
            "domain": saved["source_domain"],
            "source_type": saved.get("source_type") or "other",
            "success_count": 1,
            "notes": "Learned from successful enrichment.",
            "last_success_at": _now_iso(),
        })
    return saved


def apply_product_source_to_row(row: dict, source: dict) -> tuple[dict, list[str]]:
    updated = dict(row)
    filled: list[str] = []

    def fill(column: str, value: object) -> None:
        text = str(value or "").strip()
        if text and not str(updated.get(column) or "").strip():
            updated[column] = text
            filled.append(column)

    dimensions = source.get("dimensions_text") or source.get("dimensions")
    if dimensions and not has_complete_3d_dimensions(updated.get("Dimensions")):
        updated["Dimensions"] = dimensions
        filled.append("Dimensions")
    fill("Width (in)", source.get("width_in"))
    fill("Height (in)", source.get("height_in"))
    fill("Depth (in)", source.get("depth_in"))
    fill("Image URL", source.get("image_url"))
    fill("Product URL", source.get("product_page_url"))
    fill("Manufacturer URL", source.get("manufacturer_url"))
    fill("Dimension Source URL", source.get("dimension_source_url") or source.get("spec_sheet_url"))
    fill("image_source_url", source.get("image_source_url"))
    fill("selected_product_url", source.get("product_page_url"))
    fill("manufacturer_domain_used", source.get("source_domain"))
    fill("Dimension Confidence", source.get("dimension_confidence") or source.get("confidence"))
    fill("image_confidence", source.get("image_confidence") or source.get("confidence"))
    fill("Dimension Source Type", source.get("source_type"))
    updated["stored_source_hit"] = True
    updated["stored_source_used"] = source.get("product_page_url") or source.get("manufacturer_url") or source.get("dimension_source_url") or source.get("spec_sheet_url") or source.get("image_source_url") or ""
    updated["knowledge_base_hit"] = True
    updated["knowledge_base_source_used"] = updated["stored_source_used"]
    updated["cache_hit"] = "stored_source"
    return updated, filled


def increment_source_failure(brand: str, model: str, source_url: str = "", reason: str = "") -> None:
    source = lookup_product_source(brand, model)
    if not source:
        return
    source_id = str(source.get("id") or "")
    if not source_id:
        return
    failure_count = int(source.get("failure_count") or 0) + 1
    update_product_source(source_id, {
        "failure_count": failure_count,
        "notes": reason or source.get("notes") or "",
        "updated_at": _now_iso(),
    })
    domain = domain_from_url(source_url or source.get("product_page_url") or source.get("dimension_source_url"))
    if domain:
        upsert_preferred_domain({
            "domain": domain,
            "source_type": source.get("source_type") or "other",
            "failure_count": 1,
            "notes": reason,
            "last_failure_at": _now_iso(),
        })


def preferred_domain_hint(brand: str, category: str = "") -> str:
    """Return the best learned/preferred domain hint for a brand/category.

    This is intentionally conservative: exact product memory is handled
    separately, and this helper only returns domains with a visible brand/domain
    relationship so unrelated preferred sites do not hijack SKU lookup.
    """
    brand_norm = normalize_lookup_token(brand)
    category_norm = normalize_lookup_token(category)
    if not brand_norm:
        return ""

    source_candidates: list[dict] = []
    for row in list_product_sources(query=str(brand or ""), limit=100):
        if row.get("source_domain") and normalize_lookup_token(row.get("brand")) == brand_norm:
            source_candidates.append(row)
    if source_candidates:
        source_candidates.sort(
            key=lambda row: (
                int(row.get("success_count") or 0) - int(row.get("failure_count") or 0),
                int(row.get("confidence_score") or 0),
                str(row.get("last_verified_at") or ""),
            ),
            reverse=True,
        )
        return normalize_domain(source_candidates[0].get("source_domain"))

    domain_candidates: list[dict] = []
    for row in list_preferred_domains(limit=100):
        domain_norm = normalize_lookup_token(row.get("domain"))
        notes_norm = normalize_lookup_token(row.get("notes"))
        type_norm = normalize_lookup_token(row.get("source_type"))
        if row.get("downranked"):
            continue
        if brand_norm in domain_norm or brand_norm in notes_norm or (category_norm and category_norm in notes_norm):
            domain_candidates.append(row)
        elif type_norm == "manufacturer" and brand_norm and domain_norm.startswith(brand_norm):
            domain_candidates.append(row)
    if not domain_candidates:
        return ""
    domain_candidates.sort(
        key=lambda row: (
            int(row.get("success_count") or 0) - int(row.get("failure_count") or 0),
            str(row.get("last_success_at") or ""),
        ),
        reverse=True,
    )
    return normalize_domain(domain_candidates[0].get("domain"))


def list_preferred_domains(query: str = "", source_type: str = "", limit: int = 100) -> list[dict]:
    limit = max(1, min(int(limit or 100), 500))
    if _supabase_configured():
        params = {"select": "*", "order": "success_count.desc,failure_count.asc,domain.asc", "limit": str(limit)}
        if source_type:
            params["source_type"] = f"eq.{source_type}"
        rows = _supabase_request("GET", PREFERRED_DOMAIN_TABLE, params=params, returning=True)
        if isinstance(rows, list):
            return _filter_domains(rows, query)[:limit]
    return _filter_domains(_local_list(_PREFERRED_DOMAIN_PATH), query, source_type)[:limit]


def _filter_domains(rows: list[dict], query: str = "", source_type: str = "") -> list[dict]:
    query_norm = str(query or "").strip().lower()
    source_type_norm = str(source_type or "").strip().lower()
    filtered = []
    for row in rows:
        if source_type_norm and str(row.get("source_type") or "").lower() != source_type_norm:
            continue
        haystack = " ".join(str(row.get(field) or "") for field in ("domain", "source_type", "notes")).lower()
        if query_norm and query_norm not in haystack:
            continue
        filtered.append(row)
    return sorted(
        filtered,
        key=lambda row: (
            bool(row.get("downranked")) is False,
            int(row.get("success_count") or 0),
            -int(row.get("failure_count") or 0),
            str(row.get("domain") or ""),
        ),
        reverse=True,
    )


def upsert_preferred_domain(record: dict) -> dict:
    domain = normalize_domain(record.get("domain"))
    payload = {
        **record,
        "domain": domain,
        "source_type": record.get("source_type") or "manufacturer",
        "updated_at": _now_iso(),
    }
    payload.setdefault("success_count", 0)
    payload.setdefault("failure_count", 0)
    payload.setdefault("downranked", False)
    if not domain:
        return payload
    if _supabase_configured():
        existing = _supabase_first_by_params(
            PREFERRED_DOMAIN_TABLE,
            {"select": "*", "domain": f"eq.{domain}"},
        )
        if existing:
            payload["success_count"] = int(existing.get("success_count") or 0) + int(payload.get("success_count") or 0)
            payload["failure_count"] = int(existing.get("failure_count") or 0) + int(payload.get("failure_count") or 0)
            payload.setdefault("created_at", existing.get("created_at"))
        params = {"on_conflict": "domain"}
        headers = _supabase_headers(returning=True)
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        try:
            with httpx.Client(timeout=12) as client:
                response = client.post(
                    _supabase_url(PREFERRED_DOMAIN_TABLE),
                    params=params,
                    json=[payload],
                    headers=headers,
                )
                response.raise_for_status()
                rows = response.json()
                return rows[0] if isinstance(rows, list) and rows else payload
        except Exception as exc:
            record_storage_warning(f"supabase:{PREFERRED_DOMAIN_TABLE}", exc)
            _log.warning("Supabase upsert failed for preferred domain", exc_info=True)
    return _local_upsert(_PREFERRED_DOMAIN_PATH, payload, ("domain",))


def update_preferred_domain(domain_id: str, fields: dict) -> dict | None:
    fields = {**fields, "updated_at": _now_iso()}
    if fields.get("domain"):
        fields["domain"] = normalize_domain(fields["domain"])
    if _supabase_configured():
        rows = _supabase_request("PATCH", PREFERRED_DOMAIN_TABLE, params={"id": f"eq.{domain_id}"}, json_body=fields, returning=True)
        return rows[0] if isinstance(rows, list) and rows else None
    return _local_update(_PREFERRED_DOMAIN_PATH, domain_id, fields)


def delete_preferred_domain(domain_id: str) -> bool:
    if _supabase_configured():
        result = _supabase_request("DELETE", PREFERRED_DOMAIN_TABLE, params={"id": f"eq.{domain_id}"})
        return bool(result)
    return _local_delete(_PREFERRED_DOMAIN_PATH, domain_id)
