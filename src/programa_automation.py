"""
Programa automation via Playwright (sync, headless=False, slow_mo=300).

Public API
----------
open_programa_login_window() -> str
    Open a persistent Chrome window for manual login/setup.
    Blocks until the user closes the window or 10 minutes elapse.
    Session is saved to PROFILE_DIR; future runs reuse it automatically.

normalize_project_name(text) -> str
    Lowercase and collapse whitespace for fuzzy project-name matching.

navigate_to_project(page, project_name) -> tuple[bool, str]
    Attempt to find and click the named project in Programa.
    Tries (A) search box, (B) exact card/link, (C) partial/normalised match.
    Returns (success, method_used).  method_used ∈ {"search","card","partial","not_found"}.

send_urls_to_programa(rows, project_name, auto_click_done, skip_navigation) -> list[dict]
    Launch persistent Chrome, check login, navigate to project, run URL automation.
    Returns log entries (does not write to disk).
    If navigation fails and skip_navigation is False, returns early with a "nav_failed"
    entry so the Streamlit UI can offer the manual-continue flow.

run_programa_automation(rows, project_name, auto_done, skip_navigation) -> (list[dict], str)
    Full orchestrator — runs automation and persists a JSON log.

Environment
-----------
PROGRAMA_URL             Base URL (default: https://app.programa.design/)
PROGRAMA_BROWSER_PROFILE Profile path (default: data/browser_profiles/programa_assistant)
"""

import os
import time
from pathlib import Path

from dotenv import load_dotenv

from src.automation_logs import (
    ensure_dirs,
    make_log_entry,
    save_log,
    take_screenshot,
)

load_dotenv()

PROGRAMA_URL = os.environ.get("PROGRAMA_URL", "https://app.programa.design/").rstrip("/") + "/"

PROFILE_DIR = Path(
    os.getenv("PROGRAMA_BROWSER_PROFILE", "data/browser_profiles/programa_assistant")
).resolve()  # always absolute so Playwright and the OS agree on the path

# ── Selector text variants (tried in order) ────────────────────────────────────

ADD_FROM_URL_TEXTS = [
    "Add from URL",
    "Add From URL",
    "Add Product from URL",
    "Add URL",
    "Add from Url",
    "+ Add from URL",
]

URL_INPUT_PLACEHOLDERS = [
    "https://example.com",
    "Product url",
    "Product URL",
    "URL",
]

DONE_TEXTS = [
    "Done",
    "Save",
    "Add",
    "Add Item",
    "Submit",
    "Confirm",
    "Apply",
]

SCHEDULE_TEXTS = ["Schedule", "Schedules", "Project Schedule", "schedule"]

NEW_ITEM_TEXTS = ["New", "+ New", "New item", "Add item", "Add row", "+ Add", "Add"]

SCHEDULE_FIELD_LABELS: dict[str, list[str]] = {
    "Product Name": ["Product name", "Name", "Product title", "Title", "Item name", "Item"],
    "Description":  ["Product details", "Description", "Details", "Product description", "Notes"],
    "Brand":        ["Brand", "Manufacturer", "Maker"],
    "Dimensions":   ["Dimensions", "Dimension", "Size", "W/L/H/D", "W x H x D"],
    "Quantity":     ["Quantity", "Qty", "Qty.", "Amount"],
    "Supplier":     ["Supplier", "Vendor", "Bought from", "Who we bought it from", "Source"],
    "Color":        ["Color", "Colour", "Finish color", "Finish / Color"],
    "Finish":       ["Finish", "Finish type", "Surface finish"],
    "Material":     ["Material", "Materials", "Construction"],
    "Notes":        ["Notes", "Note", "Comments", "Additional notes"],
}

# Text patterns that must never be clicked — safety guard against project creation
_NEVER_CLICK_PATTERNS = [
    "create project",
    "new project",
    "create new project",
    "+ new project",
    "+ create project",
]

# CSS selectors tried (in order) when looking for a project search input
SEARCH_INPUT_SELECTORS = [
    "input[placeholder*='search' i]",
    "input[placeholder*='project' i]",
    "input[placeholder*='find' i]",
    "input[type='search']",
    "[role='searchbox']",
    "[aria-label*='search' i]",
    "[aria-label*='project' i]",
]

# CSS selectors tried when scanning for project result items after typing in search
RESULT_ITEM_SELECTORS = [
    "[role='option']",
    "[role='listitem']",
    "[class*='result' i]",
    "[class*='suggestion' i]",
    "[class*='project' i]",
    "li",
]

# Broad element types to scan for partial-match project navigation (Strategy C)
CANDIDATE_SELECTOR = (
    "h1, h2, h3, h4, li, a, "
    "[role='listitem'], [role='option'], "
    "[class*='project' i], [class*='card' i], [class*='item' i]"
)

# ── Text normalisation ─────────────────────────────────────────────────────────


def normalize_project_name(text: str) -> str:
    """Lowercase and collapse whitespace for fuzzy project-name comparison."""
    return " ".join(text.lower().split())


# ── Browser interaction primitives ─────────────────────────────────────────────


def _is_create_project_text(text: str) -> bool:
    """Return True if the text matches a 'Create Project' pattern that must never be clicked."""
    lower = text.strip().lower()
    return any(pattern in lower for pattern in _NEVER_CLICK_PATTERNS)


def _click_by_text(page, texts: list[str], timeout_ms: int = 4000) -> bool:
    """Try to click a button or link matching any of the given text labels.

    Never clicks anything matching _NEVER_CLICK_PATTERNS (e.g. 'Create Project').
    """
    for text in texts:
        if _is_create_project_text(text):
            continue  # safety: skip any accidental create-project text in the caller list
        for role in ("button", "link"):
            try:
                loc = page.get_by_role(role, name=text, exact=False)
                if loc.count() > 0 and loc.first.is_visible(timeout=1500):
                    el_text = ""
                    try:
                        el_text = loc.first.inner_text(timeout=400) or ""
                    except Exception:
                        pass
                    if _is_create_project_text(el_text):
                        continue  # guard: element text resolves to a create-project action
                    loc.first.click(timeout=timeout_ms)
                    return True
            except Exception:
                pass
    return False


def _fill_field_by_label(page, labels: list[str], value: str) -> bool:
    """Try to fill a visible input field by any of the given aria-label texts."""
    if not str(value).strip():
        return False
    for label in labels:
        try:
            loc = page.get_by_label(label, exact=False)
            if loc.count() > 0 and loc.first.is_visible(timeout=1500):
                loc.first.clear()
                loc.first.fill(str(value))
                return True
        except Exception:
            pass
    return False


def _find_url_input(page):
    """
    Return the first visible input that looks like a URL entry field, or None.
    Tries placeholder-based CSS selectors first, then get_by_placeholder.
    """
    css_candidates = [
        "input[placeholder*='URL' i]",
        "input[placeholder*='url' i]",
        "input[placeholder*='https' i]",
        "input[placeholder*='link' i]",
        "input[placeholder*='paste' i]",
        "input[placeholder*='product' i]",
        "input[type='url']",
    ]
    for sel in css_candidates:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1500):
                return loc
        except Exception:
            pass

    for ph in URL_INPUT_PLACEHOLDERS:
        try:
            loc = page.get_by_placeholder(ph, exact=False)
            if loc.count() > 0 and loc.first.is_visible(timeout=1500):
                return loc.first
        except Exception:
            pass

    return None


def _inject_banner(page, text: str) -> None:
    js = """
    (msg) => {
        const existing = document.getElementById('sch-automation-banner');
        if (existing) existing.remove();
        const div = document.createElement('div');
        div.id = 'sch-automation-banner';
        div.style.cssText = [
            'position:fixed','top:0','left:0','right:0','z-index:2147483647',
            'background:#7A5438','color:#FAF8F4','font-family:sans-serif',
            'font-size:13px','letter-spacing:0.04em','padding:10px 18px',
            'box-shadow:0 2px 10px rgba(0,0,0,0.25)','text-align:center',
            'pointer-events:none'
        ].join(';');
        div.textContent = msg;
        document.body.prepend(div);
    }
    """
    try:
        page.evaluate(js, text)
    except Exception:
        pass


def _remove_banner(page) -> None:
    try:
        page.evaluate(
            "() => { const el = document.getElementById('sch-automation-banner'); if (el) el.remove(); }"
        )
    except Exception:
        pass


def _js_confirm(page, message: str) -> None:
    """Show a browser confirm() and wait for user to click OK."""
    try:
        page.evaluate("(msg) => window.confirm(msg)", message)
    except Exception:
        pass


def _js_alert(page, message: str) -> None:
    try:
        page.evaluate("(msg) => window.alert(msg)", message)
    except Exception:
        pass


# ── Login detection ────────────────────────────────────────────────────────────


def _is_logged_in(page) -> bool:
    """Return True if the page looks like an authenticated session."""
    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass
    try:
        return page.locator("input[type='password']:visible").count() == 0
    except Exception:
        return False


def _wait_for_login(page, timeout_seconds: int = 300) -> bool:
    """Poll until no password input is visible. Returns True on success, False on timeout."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            if page.locator("input[type='password']:visible").count() == 0:
                time.sleep(1.5)
                if page.locator("input[type='password']:visible").count() == 0:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


# ── Project navigation ─────────────────────────────────────────────────────────


def _find_search_input(page):
    """Return the first visible search/project-search input, or None."""
    for sel in SEARCH_INPUT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1000):
                return loc
        except Exception:
            pass
    return None


def _find_project_result(page, norm: str):
    """
    After typing a search query, scan result-item selectors for a text match
    against the normalised project name.  Returns a Locator or None.
    """
    for sel in RESULT_ITEM_SELECTORS:
        try:
            items = page.locator(sel)
            count = items.count()
            for i in range(min(count, 25)):
                try:
                    el = items.nth(i)
                    if not el.is_visible(timeout=400):
                        continue
                    text = (el.inner_text(timeout=400) or "").strip()
                    norm_text = normalize_project_name(text)
                    if norm and (norm in norm_text or norm_text in norm):
                        return el
                except Exception:
                    continue
        except Exception:
            pass
    return None


def navigate_to_project(page, project_name: str) -> tuple[bool, str]:
    """
    Attempt to navigate to the named project inside Programa.

    Strategies (tried in order):
      A  Search box — type the project name, click the matching result.
      B  Exact card / link — click a visible element whose text exactly matches.
      C  Partial / normalised match — click the first visible element whose
         normalised text contains (or is contained in) the normalised project name.

    Returns (success, method) where method ∈ {"search", "card", "partial", "not_found"}.
    The caller is responsible for the manual-fallback flow when success is False.
    """
    norm = normalize_project_name(project_name)
    if not norm:
        return False, "not_found"

    # ── Strategy A: search box ─────────────────────────────────────────────────
    search_input = _find_search_input(page)
    if search_input:
        try:
            search_input.click(timeout=2000)
            search_input.fill(project_name)
            page.wait_for_timeout(1800)  # let results populate

            result = _find_project_result(page, norm)
            if result:
                result.click(timeout=4000)
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass
                return True, "search"
        except Exception:
            pass

    # ── Strategy B: exact text match on role=link or role=button ──────────────
    for role in ("link", "button"):
        try:
            loc = page.get_by_role(role, name=project_name, exact=True)
            if loc.count() > 0 and loc.first.is_visible(timeout=1500):
                loc.first.click(timeout=4000)
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass
                return True, "card"
        except Exception:
            pass

    # Fallback exact text (catches non-button/link elements like divs)
    try:
        loc = page.get_by_text(project_name, exact=True)
        if loc.count() > 0 and loc.first.is_visible(timeout=1500):
            loc.first.click(timeout=4000)
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            return True, "card"
    except Exception:
        pass

    # ── Strategy C: partial / normalised match ─────────────────────────────────
    try:
        candidates = page.locator(CANDIDATE_SELECTOR)
        count = candidates.count()
        for i in range(min(count, 80)):
            try:
                el = candidates.nth(i)
                if not el.is_visible(timeout=400):
                    continue
                text = (el.inner_text(timeout=400) or "").strip()
                if not text:
                    continue
                if _is_create_project_text(text):
                    continue  # never click create-project elements during navigation scan
                norm_text = normalize_project_name(text)
                if norm in norm_text or norm_text in norm:
                    el.click(timeout=4000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        pass
                    return True, "partial"
            except Exception:
                continue
    except Exception:
        pass

    return False, "not_found"


# ── Per-row automation ─────────────────────────────────────────────────────────


def _process_url_row(page, row: dict, auto_done: bool, index: int, total: int) -> dict:
    url = (row.get("Product URL") or "").strip()
    room = (row.get("Room") or "").strip()
    quantity = str(row.get("Quantity") or "1").strip()
    supplier = (row.get("Supplier") or "").strip()
    notes = (row.get("Notes") or "").strip()
    short_url = (url[:55] + "…") if len(url) > 55 else url

    # ── Step 1: click "Add from URL" ──────────────────────────────────────────
    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Looking for Add from URL…")

    if not _click_by_text(page, ADD_FROM_URL_TEXTS):
        shot = take_screenshot(page, f"no_add_btn_{index}")
        return make_log_entry(
            url, "error",
            f"'Add from URL' button not found. Tried: {ADD_FROM_URL_TEXTS}",
            shot,
        )

    page.wait_for_timeout(1200)

    # ── Step 2: paste the product URL ─────────────────────────────────────────
    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Pasting URL…")

    url_field = _find_url_input(page)
    if url_field is None:
        shot = take_screenshot(page, f"no_url_field_{index}")
        return make_log_entry(
            url, "error",
            "URL input field not found after clicking 'Add from URL'.",
            shot,
        )

    try:
        url_field.clear()
        url_field.fill(url)
        url_field.press("Enter")
    except Exception as exc:
        shot = take_screenshot(page, f"url_fill_error_{index}")
        return make_log_entry(url, "error", f"Error filling URL field: {exc}", shot)

    # ── Step 3: wait for Programa to process the URL ──────────────────────────
    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Waiting for Programa to process…")
    page.wait_for_timeout(4000)

    # ── Step 4: fill supporting metadata ─────────────────────────────────────
    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Filling metadata…")

    _fill_field_by_label(page, ["Room", "Location", "Space"], room)
    _fill_field_by_label(page, ["Quantity", "Qty", "Qty."], quantity)
    _fill_field_by_label(page, ["Supplier", "Vendor", "Source"], supplier)
    _fill_field_by_label(page, ["Notes", "Note", "Comments"], notes)

    page.wait_for_timeout(500)

    # ── Step 5: save or pause for manual confirmation ─────────────────────────
    if auto_done:
        _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Clicking Done…")
        if not _click_by_text(page, DONE_TEXTS):
            shot = take_screenshot(page, f"no_done_btn_{index}")
            return make_log_entry(
                url, "filled_no_save",
                f"Form filled but Done/Save button not found. Tried: {DONE_TEXTS}",
                shot,
            )
        page.wait_for_timeout(1500)
        _remove_banner(page)
        return make_log_entry(url, "success", f"Auto-saved: {short_url}")

    else:
        _remove_banner(page)
        _js_confirm(
            page,
            f"SCH DesignOps — Item {index} of {total}\n\n"
            f"URL: {short_url}\n\n"
            "The form has been filled. Please review it, then click Done in Programa.\n"
            "Click OK here when you are ready to move to the next item.",
        )
        return make_log_entry(
            url, "filled_awaiting_confirm",
            "Filled — auto-save is off. User clicked Done manually.",
        )


def _is_url_row(row: dict) -> bool:
    """True when this row should use the 'Add from URL' path."""
    return (
        str(row.get("Source Type", "")) == "URL"
        and bool(str(row.get("Product URL", "") or "").strip())
    )


def _process_schedule_row(page, row: dict, auto_done: bool, index: int, total: int) -> dict:
    """
    Enter a non-URL row via the Programa Schedule tab.

    Runs inside an already-open, already-logged-in, already-project-navigated browser.
    """
    product_name = str(row.get("Product Name", "") or "").strip()
    short_name = (product_name[:40] + "…") if len(product_name) > 40 else product_name

    # Pull Material from Notes [Materials: …] tag if present
    import re as _re
    notes_raw = str(row.get("Notes", "") or "")
    _mat_match = _re.search(r'\[Materials:\s*([^\]]+)\]', notes_raw)
    material_val = _mat_match.group(1).strip() if _mat_match else ""

    field_values: dict[str, str] = {
        "Product Name": product_name,
        "Description":  notes_raw,
        "Brand":        str(row.get("Brand", "") or ""),
        "Dimensions":   str(row.get("Dimensions", "") or ""),
        "Quantity":     str(row.get("Quantity", "") or ""),
        "Supplier":     str(row.get("Supplier", "") or ""),
        "Color":        str(row.get("Finish / Color", "") or ""),
        "Finish":       str(row.get("Finish / Color", "") or ""),
        "Material":     material_val,
        "Notes":        notes_raw,
    }

    # ── Step 1: navigate to Schedule tab ──────────────────────────────────────
    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Opening Schedule…")

    if not _click_by_text(page, SCHEDULE_TEXTS):
        shot = take_screenshot(page, f"schedule_nav_failed_{index}")
        _js_confirm(
            page,
            f"SCH DesignOps — Item {index} of {total}\n\n"
            "Please open the Schedule file for this project in Programa, then click OK.",
        )
        log_entries_local = []
        log_entries_local.append(make_log_entry(
            "", "schedule_nav_failed",
            f"Schedule tab not found — user prompted to navigate manually. Item: {product_name}",
            shot,
            product_name=product_name,
        ))
    else:
        log_entries_local = []

    page.wait_for_timeout(800)

    # ── Step 2: click "New" ────────────────────────────────────────────────────
    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Adding new row…")

    if not _click_by_text(page, NEW_ITEM_TEXTS):
        shot = take_screenshot(page, f"new_item_failed_{index}")
        _js_confirm(
            page,
            f"SCH DesignOps — Item {index} of {total}\n\n"
            "Please click New to add a product row in Programa, then click OK.",
        )
        log_entries_local.append(make_log_entry(
            "", "new_item_failed",
            f"New-item button not found — user prompted to click manually. Item: {product_name}",
            shot,
            product_name=product_name,
        ))

    # ── Step 3: wait for blank row/form to appear ─────────────────────────────
    page.wait_for_timeout(1200)

    # ── Step 4: fill fields ───────────────────────────────────────────────────
    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Filling fields…")

    skipped_fields: list[str] = []
    for field_key, labels in SCHEDULE_FIELD_LABELS.items():
        value = field_values.get(field_key, "").strip()
        if not value:
            continue
        if not _fill_field_by_label(page, labels, value):
            skipped_fields.append(field_key)

    page.wait_for_timeout(500)

    # ── Step 5: save or pause for manual confirmation ─────────────────────────
    skip_note = f" (skipped fields: {', '.join(skipped_fields)})" if skipped_fields else ""
    if not auto_done:
        _remove_banner(page)
        _js_confirm(
            page,
            f"SCH DesignOps — Item {index} of {total}: {short_name}\n\n"
            "Form filled — please review it in Programa, then click Done.\n"
            "Click OK here when ready for the next item.",
        )
        return make_log_entry(
            "", "filled_awaiting_confirm",
            f"Filled — awaiting manual confirmation.{skip_note}",
            product_name=product_name,
        )
    else:
        _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Clicking Done…")
        if not _click_by_text(page, DONE_TEXTS):
            shot = take_screenshot(page, f"no_done_btn_schedule_{index}")
            return make_log_entry(
                "", "filled_no_save",
                f"Form filled but Done button not found.{skip_note}",
                shot,
                product_name=product_name,
            )
        page.wait_for_timeout(1500)
        _remove_banner(page)
        return make_log_entry(
            "", "success",
            f"Auto-saved: {short_name}.{skip_note}",
            product_name=product_name,
        )


# ── Orchestrators ──────────────────────────────────────────────────────────────


def open_programa_login_window() -> str:
    """
    Open a persistent Chrome window so the user can log into Programa manually.

    - Does NOT attempt any automation.
    - Blocks until the user closes the browser window or 10 minutes elapse.
    - The session (cookies, local-storage) is written to PROFILE_DIR so that
      subsequent send_urls_to_programa() calls reuse it without re-logging-in.

    Returns a plain-text status message suitable for display in Streamlit.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "Playwright is not installed. Run: pip install playwright && playwright install"

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        try:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                channel="chrome",
                headless=False,
                slow_mo=300,
            )
        except Exception as exc:
            return (
                f"Could not launch Chrome: {exc}\n"
                "Make sure Google Chrome is installed, then try again."
            )

        page = context.new_page()

        try:
            page.goto(PROGRAMA_URL, wait_until="domcontentloaded", timeout=20_000)
        except Exception:
            pass  # navigation timeout is fine — browser is still open

        _inject_banner(
            page,
            "SCH DesignOps  ·  Log in as Assistant@saffroncasehomes.com  "
            "·  Close this window when done.",
        )

        deadline = time.time() + 600  # 10 minutes
        while time.time() < deadline:
            try:
                page.title()
                time.sleep(3)
            except Exception:
                break  # user closed the window

        try:
            context.close()  # flush session to disk
        except Exception:
            pass  # already closed by the user

    return f"Login window closed. Session saved to:\n{PROFILE_DIR}"


def send_urls_to_programa(
    rows: list[dict],
    project_name: str = "",
    auto_click_done: bool = False,
    skip_navigation: bool = False,
) -> list[dict]:
    """
    Open a persistent Chrome profile and process each URL row.

    Parameters
    ----------
    rows            : List of row dicts from the intake table.
    project_name    : Project name to navigate to before adding URLs.
    auto_click_done : When True, automatically click Done/Save after each item.
    skip_navigation : When True, skip automatic project navigation and instead
                      show a browser dialog asking the user to confirm they are
                      already inside the correct project.

    Returns the list of log entries (does not write the log to disk).

    Navigation failure
    ------------------
    If skip_navigation is False and auto-navigation fails, the function logs a
    "nav_failed" entry and returns early (without processing any URLs).  The
    Streamlit UI detects this status and shows the "Continue After Manual Project
    Open" button, which re-calls this function with skip_navigation=True.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [make_log_entry(
            "", "error",
            "Playwright is not installed. Run: pip install playwright && playwright install",
        )]

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    ensure_dirs()
    log_entries: list[dict] = []

    with sync_playwright() as pw:
        # ── Launch persistent context ─────────────────────────────────────────
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            slow_mo=300,
            viewport={"width": 1440, "height": 900},
        )
        log_entries.append(make_log_entry(
            "", "info",
            f"Browser launched — profile: {PROFILE_DIR}",
        ))

        page = context.new_page()
        page.goto(PROGRAMA_URL, wait_until="domcontentloaded")

        # ── Check login state ─────────────────────────────────────────────────
        if _is_logged_in(page):
            log_entries.append(make_log_entry(
                "", "session_reused",
                "Saved session active — login skipped.",
            ))
        else:
            log_entries.append(make_log_entry(
                "", "login_required",
                "Login form detected — waiting for manual sign-in (timeout: 5 min).",
            ))
            _inject_banner(
                page,
                "SCH DesignOps Intake  ·  Please log in as Assistant@saffroncasehomes.com. "
                "This banner disappears automatically once you are signed in.",
            )
            if not _wait_for_login(page, timeout_seconds=300):
                log_entries.append(make_log_entry(
                    "", "error",
                    "Login timed out after 5 minutes.",
                    take_screenshot(page, "login_timeout"),
                ))
                context.close()
                return log_entries
            _remove_banner(page)
            log_entries.append(make_log_entry("", "info", "Login successful — session will be saved."))

        # ── Navigate to the target project ────────────────────────────────────
        if skip_navigation:
            # User already opened the project manually; just confirm before we start.
            log_entries.append(make_log_entry(
                "", "nav_skipped",
                f"Auto-navigation skipped for project '{project_name}' — manual confirmation requested.",
            ))
            _inject_banner(
                page,
                f"SCH DesignOps  ·  Navigate to project '{project_name}' in Programa, then click OK below.",
            )
            _js_confirm(
                page,
                f"SCH DesignOps — Manual Navigation\n\n"
                f"Please open project \"{project_name}\" in Programa.\n\n"
                "When you are inside the correct project, click OK to begin adding items.",
            )
            _remove_banner(page)
            log_entries.append(make_log_entry(
                "", "nav_manual",
                f"User confirmed manual navigation to '{project_name}'.",
            ))

        else:
            # Attempt automatic project navigation.
            _inject_banner(page, f"SCH DesignOps  ·  Navigating to project: {project_name}…")
            nav_ok, nav_method = navigate_to_project(page, project_name)
            _remove_banner(page)

            if nav_ok:
                log_entries.append(make_log_entry(
                    "", "nav_success",
                    f"Project '{project_name}' found and opened — method: {nav_method}.",
                ))
                page.wait_for_timeout(800)
            else:
                # Navigation failed — screenshot, log, and return early.
                # The Streamlit caller detects "nav_failed" and shows the
                # "Continue After Manual Project Open" button.
                shot = take_screenshot(page, "nav_failed")
                log_entries.append(make_log_entry(
                    "", "nav_failed",
                    f"Could not locate project '{project_name}' automatically "
                    f"(tried search, card, and partial-match strategies). "
                    "Use 'Continue After Manual Project Open' in the app to proceed.",
                    shot,
                ))
                context.close()
                return log_entries

        # ── Process each row (URL or Schedule path) ───────────────────────────
        log_entries.append(make_log_entry(
            "", "info",
            f"Starting entry — {len(rows)} item(s) queued for project '{project_name}'.",
        ))

        total = len(rows)
        for i, row in enumerate(rows, start=1):
            if _is_url_row(row):
                entry = _process_url_row(page, row, auto_click_done, index=i, total=total)
            else:
                entry = _process_schedule_row(page, row, auto_click_done, index=i, total=total)
            log_entries.append(entry)
            page.wait_for_timeout(1200)

        # ── Summary and close ─────────────────────────────────────────────────
        success_n = sum(1 for e in log_entries if e["status"] == "success")
        filled_n  = sum(1 for e in log_entries if e["status"] == "filled_awaiting_confirm")
        error_n   = sum(1 for e in log_entries if e["status"] == "error")

        take_screenshot(page, "automation_complete")
        _js_alert(
            page,
            f"SCH DesignOps — Done\n\n"
            f"Saved: {success_n}   Filled (manual): {filled_n}   Errors: {error_n}\n\n"
            "Session saved. You may close this window.",
        )
        context.close()  # saves session to PROFILE_DIR; do NOT call browser.close()

    return log_entries


def run_programa_automation(
    rows: list[dict],
    project_name: str,
    auto_done: bool = False,
    skip_navigation: bool = False,
) -> tuple[list[dict], str]:
    """
    Full orchestrator — runs the automation and persists a JSON log.
    Returns (log_entries, log_file_path).
    """
    entries = send_urls_to_programa(
        rows,
        project_name=project_name,
        auto_click_done=auto_done,
        skip_navigation=skip_navigation,
    )
    log_path = save_log(entries, project_name)
    return entries, log_path
