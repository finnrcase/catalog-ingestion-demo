"""Durable enrichment memory backed by Supabase PostgREST.

The existing enrichment system uses small local JSON files.  This module adds a
production storage layer underneath those helpers without making Supabase a hard
runtime dependency.  When Supabase is not configured, callers should keep using
their local JSON fallback.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import requests


logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 8


def _text(value: object) -> str:
    return str(value or "").strip()


def _real_env_value(value: str) -> bool:
    raw = _text(value)
    if not raw:
        return False
    lowered = raw.lower()
    return not (
        lowered.startswith("your_")
        or lowered.startswith("https://your-")
        or "your-project" in lowered
        or lowered in {"placeholder", "changeme", "none", "null"}
    )


def durable_cache_enabled() -> bool:
    """True when durable cache should be attempted.

    Defaults to auto-enabled if Supabase URL + service role key are present.
    Explicit ``ENRICHMENT_DURABLE_CACHE_ENABLED=false`` disables it.
    """

    if os.getenv("ENRICHMENT_DURABLE_CACHE_ENABLED", "true").lower() == "false":
        return False
    provider = os.getenv("ENRICHMENT_DURABLE_CACHE_PROVIDER", "supabase").strip().lower()
    if provider not in {"supabase", "auto"}:
        return False
    return bool(_supabase_config())


def _supabase_config() -> tuple[str, str] | None:
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (_real_env_value(url) and _real_env_value(key)):
        return None
    return url, key


class SupabaseDurableCache:
    """Tiny Supabase REST client for JSON payload cache tables."""

    def __init__(self) -> None:
        config = _supabase_config()
        self.enabled = bool(config)
        self.base_url = config[0] if config else ""
        self.key = config[1] if config else ""

    @property
    def headers(self) -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

    def table_url(self, table: str) -> str:
        return f"{self.base_url}/rest/v1/{table}"

    def get_payload(self, table: str, key_column: str, key: str) -> dict[str, Any] | None:
        if not self.enabled or not key:
            return None
        try:
            response = requests.get(
                self.table_url(table),
                headers=self.headers,
                params={key_column: f"eq.{key}", "select": "payload", "limit": "1"},
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                logger.warning("Durable cache get failed table=%s status=%s body=%s", table, response.status_code, response.text[:200])
                return None
            rows = response.json() if response.content else []
            if not rows:
                return None
            payload = rows[0].get("payload") if isinstance(rows[0], dict) else None
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            logger.warning("Durable cache get failed table=%s key=%s err=%s", table, key, exc)
            return None

    def load_payload_map(self, table: str, key_column: str) -> dict[str, dict[str, Any]]:
        if not self.enabled:
            return {}
        try:
            response = requests.get(
                self.table_url(table),
                headers=self.headers,
                params={"select": f"{key_column},payload", "limit": "10000"},
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                logger.warning("Durable cache load-map failed table=%s status=%s body=%s", table, response.status_code, response.text[:200])
                return {}
            output: dict[str, dict[str, Any]] = {}
            for row in response.json() if response.content else []:
                if not isinstance(row, dict):
                    continue
                key = _text(row.get(key_column))
                payload = row.get("payload")
                if key and isinstance(payload, dict):
                    output[key] = payload
            return output
        except Exception as exc:
            logger.warning("Durable cache load-map failed table=%s err=%s", table, exc)
            return {}

    def load_payload_list(self, table: str) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            response = requests.get(
                self.table_url(table),
                headers=self.headers,
                params={"select": "payload", "limit": "10000"},
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                logger.warning("Durable cache load-list failed table=%s status=%s body=%s", table, response.status_code, response.text[:200])
                return []
            output: list[dict[str, Any]] = []
            for row in response.json() if response.content else []:
                payload = row.get("payload") if isinstance(row, dict) else None
                if isinstance(payload, dict):
                    output.append(payload)
            return output
        except Exception as exc:
            logger.warning("Durable cache load-list failed table=%s err=%s", table, exc)
            return []

    def upsert_payload(
        self,
        table: str,
        key_column: str,
        key: str,
        payload: dict[str, Any],
        *,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        if not self.enabled or not key:
            return False
        record = {key_column: key, "payload": payload}
        if extra:
            record.update(extra)
        try:
            response = requests.post(
                self.table_url(table),
                headers={**self.headers, "Prefer": "resolution=merge-duplicates"},
                json=record,
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
            if response.status_code not in {200, 201, 204}:
                logger.warning("Durable cache upsert failed table=%s status=%s body=%s", table, response.status_code, response.text[:200])
                return False
            return True
        except Exception as exc:
            logger.warning("Durable cache upsert failed table=%s key=%s err=%s", table, key, exc)
            return False

    def delete_payload(self, table: str, key_column: str, key: str) -> bool:
        if not self.enabled or not key:
            return False
        try:
            response = requests.delete(
                self.table_url(table),
                headers=self.headers,
                params={key_column: f"eq.{key}"},
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
            if response.status_code not in {200, 202, 204}:
                logger.warning("Durable cache delete failed table=%s status=%s body=%s", table, response.status_code, response.text[:200])
                return False
            return True
        except Exception as exc:
            logger.warning("Durable cache delete failed table=%s key=%s err=%s", table, key, exc)
            return False


_CLIENT: SupabaseDurableCache | None = None


def durable_client() -> SupabaseDurableCache:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = SupabaseDurableCache()
    return _CLIENT


def load_map(table: str, key_column: str) -> dict[str, dict[str, Any]]:
    return durable_client().load_payload_map(table, key_column)


def load_list(table: str) -> list[dict[str, Any]]:
    return durable_client().load_payload_list(table)


def get_payload(table: str, key_column: str, key: str) -> dict[str, Any] | None:
    return durable_client().get_payload(table, key_column, key)


def upsert_payload(
    table: str,
    key_column: str,
    key: str,
    payload: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
) -> bool:
    return durable_client().upsert_payload(table, key_column, key, payload, extra=extra)


def delete_payload(table: str, key_column: str, key: str) -> bool:
    return durable_client().delete_payload(table, key_column, key)


def stable_or_random_id(payload: dict[str, Any], *fields: str) -> str:
    for field in fields:
        value = _text(payload.get(field))
        if value:
            return value
    return uuid.uuid4().hex
