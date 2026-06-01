"""
Log entry schema and persistence for SCH Programa automation runs.

Schema:
    timestamp       ISO-8601 string
    product_name    str   (used for non-URL rows; empty for URL rows)
    product_url     str   (used for URL rows; empty for non-URL rows)
    status          "success" | "filled_awaiting_confirm" | "filled_no_save"
                    | "error" | "skipped" | "schedule_nav_failed" | "new_item_failed"
    message         Human-readable description of what happened
    screenshot_path Absolute or relative path to a screenshot, or ""
"""

import logging
from datetime import datetime

from src.runtime_storage import ensure_directory, runtime_data_path, write_json_best_effort

_log = logging.getLogger(__name__)

LOG_DIR = runtime_data_path("automation_logs")
SCREENSHOT_DIR = LOG_DIR / "screenshots"


def ensure_dirs() -> None:
    ensure_directory(LOG_DIR, description="automation log directory")
    ensure_directory(SCREENSHOT_DIR, description="automation screenshot directory")


def make_log_entry(
    product_url: str,
    status: str,
    message: str = "",
    screenshot_path: str = "",
    product_name: str = "",
) -> dict:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "product_name": product_name,
        "product_url": product_url,
        "status": status,
        "message": message,
        "screenshot_path": screenshot_path,
    }


def take_screenshot(page, label: str) -> str:
    """Save a screenshot and return its path. Silently ignores errors."""
    ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = label[:60].replace("/", "_").replace(":", "_").replace(" ", "_")
    path = str(SCREENSHOT_DIR / f"{safe}_{ts}.png")
    try:
        page.screenshot(path=path, full_page=False)
    except Exception:
        pass
    return path


def save_log(entries: list[dict], project_name: str) -> str:
    """Write entries to a timestamped JSON file. Returns the file path."""
    ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = project_name.strip().replace(" ", "_") or "intake"
    path = LOG_DIR / f"{safe}_{ts}.json"
    if write_json_best_effort(path, entries, description="Programa automation log"):
        return str(path)
    _log.warning("Programa automation log was not persisted: %s", path)
    return ""
