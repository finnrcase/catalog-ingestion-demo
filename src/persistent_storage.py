from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path

import requests


@dataclass
class StorageResult:
    ok: bool
    provider: str = "local"
    object_path: str = ""
    public_url: str = ""
    error: str = ""


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _storage_provider() -> str:
    return os.getenv("UPLOAD_STORAGE_PROVIDER", "local").strip().lower() or "local"


def persistent_upload_storage_enabled() -> bool:
    return _storage_provider() == "supabase"


def require_persistent_upload_storage() -> bool:
    return _env_flag("REQUIRE_PERSISTENT_UPLOAD_STORAGE", False)


def _supabase_config() -> tuple[str, str, str]:
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "").strip()
    return url, key, bucket


def _normalise_object_path(object_path: str) -> str:
    cleaned = object_path.replace("\\", "/").strip("/")
    parts = [part for part in cleaned.split("/") if part not in {"", ".", ".."}]
    return "/".join(parts)


def upload_file_to_persistent_storage(
    local_path: str | Path,
    object_path: str,
    *,
    content_type: str = "",
) -> StorageResult:
    """Persist an uploaded artifact outside the local filesystem when configured.

    The backend still uses a local temp file for parsing, but production deploys
    can set UPLOAD_STORAGE_PROVIDER=supabase to keep an authoritative copy in
    Supabase Storage for retry/debug/recovery after process restarts.
    """
    provider = _storage_provider()
    if provider in {"", "local", "filesystem"}:
        return StorageResult(ok=True, provider="local", object_path="")
    if provider != "supabase":
        return StorageResult(ok=False, provider=provider, error=f"Unsupported upload storage provider: {provider}")

    supabase_url, service_role_key, bucket = _supabase_config()
    if not supabase_url or not service_role_key or not bucket:
        return StorageResult(
            ok=False,
            provider="supabase",
            error="Supabase upload storage requires SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, and SUPABASE_STORAGE_BUCKET.",
        )

    path = Path(local_path)
    if not path.exists() or not path.is_file():
        return StorageResult(ok=False, provider="supabase", error="Local upload file was not found.")

    clean_path = _normalise_object_path(object_path)
    if not clean_path:
        return StorageResult(ok=False, provider="supabase", error="Storage object path was empty.")

    guessed_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    upload_type = content_type or guessed_type
    endpoint = f"{supabase_url}/storage/v1/object/{bucket}/{clean_path}"
    try:
        with path.open("rb") as handle:
            response = requests.put(
                endpoint,
                data=handle,
                headers={
                    "Authorization": f"Bearer {service_role_key}",
                    "apikey": service_role_key,
                    "Content-Type": upload_type,
                    "x-upsert": "true",
                },
                timeout=30,
            )
        if response.status_code not in {200, 201}:
            return StorageResult(
                ok=False,
                provider="supabase",
                object_path=clean_path,
                error=f"Supabase Storage upload failed with status {response.status_code}.",
            )
    except Exception as exc:
        return StorageResult(ok=False, provider="supabase", object_path=clean_path, error=str(exc))

    public_base = os.getenv("SUPABASE_STORAGE_PUBLIC_BASE_URL", "").strip().rstrip("/")
    public_url = f"{public_base}/{clean_path}" if public_base else ""
    return StorageResult(ok=True, provider="supabase", object_path=clean_path, public_url=public_url)
