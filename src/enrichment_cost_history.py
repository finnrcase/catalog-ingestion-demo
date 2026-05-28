"""Persistent cost history for enrichment runs.

The history is intentionally append-only and lightweight. It records run-level
cost telemetry for internal visibility without touching Programa export data.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.durable_cache import durable_cache_enabled, load_list, stable_or_random_id, upsert_payload


DATA_DIR = Path("data")
DEFAULT_COST_HISTORY_PATH = DATA_DIR / "enrichment_cost_history.json"
MAX_HISTORY_ENTRIES = 500


def cost_history_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    env_path = os.getenv("ENRICHMENT_COST_HISTORY_PATH", "").strip()
    return Path(env_path) if env_path else DEFAULT_COST_HISTORY_PATH


def _use_durable(path: str | Path | None = None) -> bool:
    return path is None and not os.getenv("ENRICHMENT_COST_HISTORY_PATH", "").strip() and durable_cache_enabled()


def load_cost_history(path: str | Path | None = None) -> list[dict[str, Any]]:
    path_obj = cost_history_path(path)
    entries: list[dict[str, Any]] = []
    if path_obj.exists():
        try:
            data = json.loads(path_obj.read_text(encoding="utf-8"))
            entries = data if isinstance(data, list) else []
        except Exception:
            entries = []
    if _use_durable(path):
        durable_entries = load_list("enrichment_cost_history")
        if durable_entries:
            seen = {str(entry.get("id") or entry.get("upload_id") or "") for entry in entries}
            for entry in durable_entries:
                key = str(entry.get("id") or entry.get("upload_id") or "")
                if key and key in seen:
                    continue
                entries.append(entry)
    return entries[-MAX_HISTORY_ENTRIES:]


def save_cost_history(entries: list[dict[str, Any]], path: str | Path | None = None) -> None:
    path_obj = cost_history_path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    trimmed = entries[-MAX_HISTORY_ENTRIES:]
    tmp = path_obj.with_suffix(path_obj.suffix + ".tmp")
    tmp.write_text(json.dumps(trimmed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path_obj)
    if _use_durable(path):
        for entry in trimmed:
            entry_id = stable_or_random_id(entry, "id", "upload_id")
            entry.setdefault("id", entry_id)
            upsert_payload(
                "enrichment_cost_history",
                "id",
                entry_id,
                entry,
                extra={
                    "upload_id": str(entry.get("upload_id") or ""),
                    "project_name": str(entry.get("project_name") or ""),
                    "file_name": str(entry.get("file_name") or ""),
                    "bravi_cost_usd": float(entry.get("bravi_cost_usd") or 0.0),
                    "total_enrichment_cost_usd": float(entry.get("total_enrichment_cost_usd") or 0.0),
                },
            )


def append_cost_history(
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    path: str | Path | None = None,
) -> dict[str, Any]:
    entry = build_cost_history_entry(summary, rows)
    entries = load_cost_history(path)
    entries.append(entry)
    save_cost_history(entries, path)
    return entry


def build_cost_history_entry(summary: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    upload_id = _first_non_empty(rows, ("upload_id", "Upload ID", "session_id", "_source_pdf_id"))
    source_filename = _first_non_empty(rows, ("_source_filename", "Source Filename", "source_filename", "fileName"))
    if not upload_id:
        upload_id = f"run_{uuid.uuid4().hex[:12]}"
    return {
        "id": f"cost_{uuid.uuid4().hex[:12]}",
        "upload_id": upload_id,
        "project_name": _first_non_empty(rows, ("Project", "Project Name", "project", "project_name")),
        "file_name": source_filename,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": str(summary.get("mode") or ""),
        "bravi_calls": int(summary.get("bravi_searches") or summary.get("brave_searches") or 0),
        "bravi_cost_usd": round(float(summary.get("bravi_cost_usd") or summary.get("brave_cost_usd") or 0.0), 6),
        "total_enrichment_cost_usd": round(float(summary.get("estimated_cost_usd") or 0.0), 6),
        "items_enriched": int(summary.get("external_enrichment_rows") or 0),
        "items_total": int(summary.get("total_rows") or len(rows)),
        "cache_hits": int(summary.get("cache_hits") or 0),
        "paid_calls": int(summary.get("paid_calls") or summary.get("bravi_searches") or 0),
        "cache_hit_rate": float(summary.get("cache_hit_rate") or 0.0),
        "hard_budget_usd": round(float(summary.get("hard_budget_usd") or 0.0), 6),
        "target_budget_usd": round(float(summary.get("target_budget_usd") or 0.0), 6),
        "budget_skipped_calls": int(summary.get("skipped_calls_due_budget") or 0),
    }


def _first_non_empty(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> str:
    for row in rows:
        for field in fields:
            value = str(row.get(field) or "").strip()
            if value and value.lower() not in {"nan", "none", "null"}:
                return value
    return ""
