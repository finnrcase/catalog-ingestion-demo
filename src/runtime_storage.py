from __future__ import annotations

import json
import logging
import os
import traceback
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)
_WARNINGS: list[dict[str, str]] = []


def _record_warning(path: Path, error: BaseException) -> None:
    _WARNINGS.append(
        {
            "path": str(path),
            "error": str(error),
            "traceback": traceback.format_exc(),
        }
    )
    del _WARNINGS[:-20]


def record_storage_warning(path: Path | str, error: BaseException) -> None:
    _record_warning(Path(path), error)


def consume_storage_warnings() -> list[dict[str, str]]:
    warnings = list(_WARNINGS)
    _WARNINGS.clear()
    return warnings


def runtime_data_path(*parts: str) -> Path:
    """
    Return a writable data path for runtime-only cache/debug artifacts.

    Runtime cache/debug artifacts default to /tmp/sch-data so serverless and
    read-only deployments never try to create a repo-local data directory.
    Set SCH_DATA_DIR (or DATA_DIR) explicitly if a persistent volume is mounted.
    """
    configured = os.getenv("SCH_DATA_DIR") or os.getenv("DATA_DIR")
    if configured:
        base = Path(configured)
    else:
        base = Path("/tmp/sch-data")
    return base.joinpath(*parts)


def ensure_directory(path: Path, *, description: str = "runtime directory") -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as exc:
        _record_warning(path, exc)
        _log.warning("Could not create %s at %s", description, path, exc_info=True)
        return False


def write_json_best_effort(
    path: Path,
    payload: Any,
    *,
    description: str = "runtime JSON",
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = True,
) -> bool:
    """
    Best-effort JSON persistence. Returns False on storage failures instead of
    raising, so parse/enrichment can continue in read-only deployments.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
        return True
    except Exception as exc:
        _record_warning(path, exc)
        _log.warning("Could not write %s to %s", description, path, exc_info=True)
        try:
            path.with_name(path.name + ".tmp").unlink(missing_ok=True)
        except Exception:
            pass
        return False
