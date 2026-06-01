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
PROGRAMA_BROWSER_PROFILE Profile path (default: runtime storage browser profile)
"""

import os
import re
import hashlib
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from dotenv import load_dotenv

from src.automation_logs import (
    ensure_dirs,
    make_log_entry,
    save_log,
    take_screenshot,
)
from src.dimensions import extract_labeled_dimensions
from src.notes import remove_notes_row_prefix
from src.runtime_storage import runtime_data_path

load_dotenv()

PROGRAMA_URL = os.environ.get("PROGRAMA_URL", "https://app.programa.design/").rstrip("/") + "/"

PROFILE_DIR = Path(
    os.getenv("PROGRAMA_BROWSER_PROFILE", str(runtime_data_path("browser_profiles", "programa_assistant")))
).resolve()  # always absolute so Playwright and the OS agree on the path

# ── Automation safety flags ───────────────────────────────────────────────────


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _real_integrations_enabled() -> bool:
    """Public demo safety: Programa browser/API side effects are opt-in only."""
    return _env_flag("ENABLE_REAL_INTEGRATIONS", False) and not _env_flag("DEMO_MODE", True)

# When True: keep the browser open on any failure so you can inspect the UI.
# Show a blocking alert then close only after the user clicks OK.
# Set to False to close immediately on failure (original behaviour).
KEEP_BROWSER_OPEN_ON_FAILURE = True

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

# Dropdown option that appears after clicking "New"
CUSTOM_PRODUCT_TEXTS = [
    "Custom Product",
    "Custom product",
    "custom product",
    "Add custom product",
    "New custom product",
    "Custom Item",
    "Custom item",
    "Manual entry",
    "Manual Item",
]

# Dropdown option that appears after clicking "New" when creating a section
NEW_SECTION_TEXTS = [
    "Section",
    "Add Section",
    "New Section",
    "section",
    "Add section",
    "New section",
]

FILES_TAB_TEXTS = ["Files", "Project Files", "Documents"]

SCHEDULE_FILE_IDENTIFIERS = ["schedule", "untitled schedule", "project schedule"]

SCHEDULE_FIELD_LABELS: dict[str, list[str]] = {
    "Product Name": ["Product name", "Name", "Product title", "Title", "Item name", "Item"],
    "Description":  ["Product details", "Description", "Details", "Product description"],
    "Brand":        ["Brand", "Manufacturer", "Maker"],
    "W":            ["W (in)", "W", "Width (in)", "Width"],
    "H":            ["H (in)", "H", "Height (in)", "Height"],
    "D":            ["D (in)", "D", "Depth (in)", "Depth"],
    "L":            ["L (in)", "L", "Length (in)", "Length"],
    "Quantity":     ["Quantity", "Qty", "Qty.", "Amount"],
    "Supplier":     ["Supplier", "Vendor", "Bought from", "Who we bought it from", "Source"],
    "Room":         ["Room", "Location", "Space", "Area"],
    "Color":        ["Color", "Colour", "Finish color", "Finish / Color"],
    "Finish":       ["Finish", "Finish type", "Surface finish"],
    "Material":     ["Material", "Materials", "Construction"],
    "Category":     ["Category", "Product category", "Type"],
    "Model/SKU":    ["Model/SKU", "SKU", "Model", "Item number", "Serial", "Part number"],
    "Product URL":  ["Product URL", "URL", "Link", "Product link"],
    "Price":        ["Price", "Cost", "Price/cost", "Unit cost"],
    "Notes":        ["Notes", "Note", "Comments", "Additional notes"],
}

# Fill order for Schedule → Custom Product rows (matches Programa's left-to-right field layout)
_SCHEDULE_ENTRY_FIELDS: list[tuple[str, str]] = [
    ("Product Name", "Product Name"),
    ("Brand",        "Brand"),
    ("W",            "W (width)"),
    ("L",            "L (length)"),
    ("H",            "H (height)"),
    ("D",            "D (depth)"),
    ("Quantity",     "Qty"),
    ("Finish",       "Finish"),
    ("Material",     "Material"),
    ("Supplier",     "Supplier"),
]

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


def _visible_page_text(page, limit: int = 4000) -> str:
    """Return visible body text for failure diagnostics."""
    try:
        text = page.locator("body").inner_text(timeout=2000)
        return text[:limit]
    except Exception as exc:
        return f"<visible text unavailable: {exc}>"


def _active_element_debug(page) -> dict:
    """Return active element details and nearby DOM after a click."""
    try:
        return page.evaluate("""() => {
            const el = document.activeElement;
            if (!el) return {found: false};
            const parent = el.parentElement;
            return {
                found: true,
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                placeholder: el.getAttribute('placeholder') || '',
                text: (el.textContent || '').trim().slice(0, 200),
                value: (el.value || '').slice(0, 200),
                outerHTML: el.outerHTML.slice(0, 1000),
                parentHTML: parent ? parent.outerHTML.slice(0, 1500) : '',
            };
        }""")
    except Exception as exc:
        return {"found": False, "error": str(exc)}


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


def _robust_click_locator(page, locator, description: str, timeout_ms: int = 4000) -> tuple[bool, str]:
    """Click a locator with scroll, normal click, force click, then keyboard fallback."""
    try:
        locator.scroll_into_view_if_needed(timeout=timeout_ms)
    except Exception:
        pass
    try:
        locator.click(timeout=timeout_ms)
        return True, f"{description}: clicked"
    except Exception as first_exc:
        try:
            locator.click(timeout=timeout_ms, force=True)
            return True, f"{description}: force clicked after {first_exc}"
        except Exception as force_exc:
            try:
                locator.focus(timeout=timeout_ms)
                page.keyboard.press("Enter")
                return True, f"{description}: keyboard Enter after click failures"
            except Exception as key_exc:
                return False, f"{description}: click failed={first_exc}; force failed={force_exc}; keyboard failed={key_exc}"


def _click_new_button(page) -> tuple[bool, str]:
    """Click the top-level New button using role selectors first."""
    for exact in (True, False):
        for name in ("New", "+ New"):
            try:
                loc = page.get_by_role("button", name=name, exact=exact)
                for i in range(min(loc.count(), 6)):
                    candidate = loc.nth(i)
                    if candidate.is_visible(timeout=500):
                        return _robust_click_locator(page, candidate, f"button name={name!r} exact={exact}")
            except Exception:
                pass
    clicked = _click_by_text(page, NEW_ITEM_TEXTS, timeout_ms=5000)
    return clicked, "text fallback NEW_ITEM_TEXTS" if clicked else f"New button not found; tried {NEW_ITEM_TEXTS}"


def _fill_field_by_label(page, labels: list[str], value: str) -> bool:
    """Try to fill a visible input by aria-label, placeholder, or CSS attribute selector."""
    if not str(value).strip():
        return False
    for label in labels:
        # aria-label (Playwright built-in)
        try:
            loc = page.get_by_label(label, exact=False)
            if loc.count() > 0 and loc.first.is_visible(timeout=1500):
                loc.first.clear()
                loc.first.fill(str(value))
                return True
        except Exception:
            pass
        # placeholder text
        try:
            loc = page.get_by_placeholder(label, exact=False)
            if loc.count() > 0 and loc.first.is_visible(timeout=1000):
                loc.first.clear()
                loc.first.fill(str(value))
                return True
        except Exception:
            pass
        # CSS attribute selectors (handles Programa's inline table cells)
        safe = label.replace('"', '\\"')
        for attr in ("aria-label", "data-label", "name", "placeholder"):
            try:
                sel = f'input[{attr}*="{safe}" i], textarea[{attr}*="{safe}" i]'
                loc = page.locator(sel).first
                if loc.is_visible(timeout=800):
                    loc.clear()
                    loc.fill(str(value))
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


# ── Tab / page management ─────────────────────────────────────────────────────

# URLs that indicate a non-workspace page the automation must NOT operate on
_JUNK_URL_PATTERNS = (
    "/changelog", "/whats-new", "/what-s-new",
    "chrome://", "about:blank", "about:newtab",
    "google.com/chrome",
)
_JUNK_TITLE_PATTERNS = (
    "what's new", "changelog", "welcome", "new tab",
)

# URL fragments that identify a valid Programa workspace page
_WORKSPACE_URL_PATTERNS = (
    "/projects", "/files", "/schedule", "/items",
    "/boards", "/settings",
    "app.programa.design",
)

PROGRAMA_PROJECTS_URL = "https://app.programa.design/projects"


def _is_workspace_url(url: str) -> bool:
    url_l = url.lower()
    if any(j in url_l for j in _JUNK_URL_PATTERNS):
        return False
    return any(w in url_l for w in _WORKSPACE_URL_PATTERNS)


def _log_all_pages(context, current_page=None) -> None:
    """Print all open tabs with [PAGE] prefix."""
    try:
        pages = context.pages
        print(f"[PAGE] Open tabs: {len(pages)}")
        for i, p in enumerate(pages):
            try:
                title = p.title()
                url   = p.url
            except Exception:
                title, url = "?", "?"
            marker = " ← ACTIVE" if p is current_page else ""
            print(f"[PAGE]   [{i}] {title!r:45s}  {url[:100]}{marker}")
    except Exception as exc:
        print(f"[PAGE] log_all_pages error: {exc}")


def _select_workspace_page(context):
    """
    From all pages in the restored browser context, choose the best workspace
    page and close junk tabs (changelog, blank, chrome://).

    Priority:
      1. Any page whose URL matches _WORKSPACE_URL_PATTERNS
      2. Any page on app.programa.design (even if URL not yet fully loaded)
      3. A freshly created new page (navigate to projects URL after returning)

    Returns the selected page object.
    """
    pages = list(context.pages)
    print(f"[PAGE] Selecting workspace page from {len(pages)} open tab(s)…")
    _log_all_pages(context)

    workspace_page = None
    junk_pages     = []

    for p in pages:
        try:
            url   = p.url.lower()
            title = p.title().lower()
        except Exception:
            url, title = "", ""

        is_junk = (
            any(j in url   for j in _JUNK_URL_PATTERNS) or
            any(j in title for j in _JUNK_TITLE_PATTERNS) or
            url in ("about:blank", "chrome://newtab/", "")
        )
        if is_junk:
            junk_pages.append(p)
        elif workspace_page is None and _is_workspace_url(url):
            workspace_page = p

    # Fall back: any non-junk Programa page
    if workspace_page is None:
        for p in pages:
            if p in junk_pages:
                continue
            try:
                url = p.url.lower()
            except Exception:
                url = ""
            if "programa.design" in url:
                workspace_page = p
                break

    # Close junk tabs
    for p in junk_pages:
        try:
            title = p.title()
            url   = p.url
        except Exception:
            title, url = "?", "?"
        if p is workspace_page:
            continue  # never close the page we selected
        print(f"[PAGE] Closing junk tab: {title!r}  {url}")
        try:
            p.close()
        except Exception:
            pass

    if workspace_page is not None:
        print(f"[PAGE] Selected workspace page: {workspace_page.title()!r}  {workspace_page.url}")
        workspace_page.bring_to_front()
        return workspace_page

    # No workspace page found — create one
    print("[PAGE] No workspace page found — creating new page")
    new_page = context.new_page()
    return new_page


def _ensure_workspace_url(page, phase: str = "") -> None:
    """
    Check the current URL. If we are on a changelog/junk page, navigate to
    the projects workspace. Logs with [PAGE] prefix.

    Call before every major phase.
    """
    try:
        url   = page.url
        title = page.title()
    except Exception:
        url, title = "", ""

    print(f"[PAGE] Active tab title: {title!r}")
    print(f"[PAGE] Active URL: {url}")

    if not url or _is_workspace_url(url):
        return  # already fine (or not yet navigated)

    # We're on a junk/wrong page — navigate to workspace
    reason = "changelog" if "/changelog" in url.lower() else f"non-workspace URL ({url[:60]})"
    print(f"[PAGE] Switched from {reason} → navigating to projects  (phase={phase or 'unknown'})")
    try:
        page.goto(PROGRAMA_PROJECTS_URL, wait_until="domcontentloaded", timeout=20_000)
        page.wait_for_timeout(1500)
        print(f"[PAGE] Active URL after redirect: {page.url}")
    except Exception as exc:
        print(f"[PAGE] Navigation to projects failed: {exc}")


def _activate_automation_tab(context, page) -> None:
    """
    Legacy helper kept for call-sites that don't need page selection.
    Brings page to front and closes obvious junk tabs.
    """
    _log_all_pages(context, page)
    # Close junk tabs (never close the automation page)
    for p in list(context.pages):
        if p is page:
            continue
        try:
            url   = p.url.lower()
            title = p.title().lower()
        except Exception:
            url, title = "", ""
        if (any(j in url for j in _JUNK_URL_PATTERNS) or
                any(j in title for j in _JUNK_TITLE_PATTERNS)):
            print(f"[PAGE] Closing junk tab: {p.title()!r}  {p.url}")
            try:
                p.close()
            except Exception:
                pass
    page.bring_to_front()
    try:
        print(f"[PAGE] Active tab title: {page.title()!r}")
        print(f"[PAGE] Active URL: {page.url}")
    except Exception:
        pass


def _looks_like_schedule_url(url: str) -> bool:
    value = str(url or "").strip().lower()
    return value.startswith("https://app.programa.design/") and "/schedules" in value


def _visible_text_found(page, texts: list[str], timeout_ms: int = 700) -> bool:
    for text in texts:
        try:
            loc = page.get_by_text(text, exact=False)
            if loc.count() > 0 and loc.first.is_visible(timeout=timeout_ms):
                return True
        except Exception:
            pass
        try:
            loc = page.get_by_role("button", name=text, exact=False)
            if loc.count() > 0 and loc.first.is_visible(timeout=timeout_ms):
                return True
        except Exception:
            pass
    return False


def _wait_for_schedule_controls(page) -> tuple[bool, dict[str, bool]]:
    """
    Confirm that the direct schedule URL landed on a usable schedule page.

    "Custom Product" and "Section" usually live inside the New dropdown, so this
    briefly opens that menu for detection and then closes it again.
    """
    controls = {
        "New": False,
        "Custom Product": False,
        "Add from URL": False,
        "Section": False,
    }
    deadline = time.time() + 12
    while time.time() < deadline:
        controls["New"] = _visible_text_found(page, NEW_ITEM_TEXTS)
        controls["Add from URL"] = _visible_text_found(page, ADD_FROM_URL_TEXTS)
        if controls["New"]:
            try:
                if _click_by_text(page, NEW_ITEM_TEXTS, timeout_ms=2500):
                    page.wait_for_timeout(700)
                    controls["Custom Product"] = _visible_text_found(page, CUSTOM_PRODUCT_TEXTS)
                    controls["Section"] = _visible_text_found(page, NEW_SECTION_TEXTS)
                    controls["Add from URL"] = controls["Add from URL"] or _visible_text_found(page, ADD_FROM_URL_TEXTS)
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
                    page.wait_for_timeout(300)
                    if all(controls.values()):
                        break
            except Exception as exc:
                print(f"[SCH Automation] schedule controls dropdown check failed: {exc}")
        page.wait_for_timeout(800)

    ok = all(controls.values())
    print(f"[SCH Automation] schedule controls visible: {controls} ok={ok}")
    return ok, controls


def _navigate_directly_to_schedule_url(page, schedule_url: str) -> tuple[bool, str]:
    """Navigate directly to the pasted Programa schedule URL and verify controls."""
    print(f"[SCH Automation] schedule_url received: {schedule_url}")
    if not _looks_like_schedule_url(schedule_url):
        return False, "schedule_url does not look like a Programa schedule link"

    _inject_banner(page, "SCH DesignOps  ·  Opening pasted Programa schedule…")
    try:
        page.goto(schedule_url, wait_until="domcontentloaded", timeout=30_000)
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
    except Exception as exc:
        _remove_banner(page)
        return False, f"direct schedule navigation failed: {exc}"

    current = ""
    try:
        current = page.url
    except Exception:
        pass
    print(f"[SCH Automation] direct schedule navigation current URL: {current}")
    controls_ok, controls = _wait_for_schedule_controls(page)
    _remove_banner(page)
    if not controls_ok:
        return False, f"schedule controls not visible after direct navigation: {controls}"
    return True, f"direct schedule navigation success: {current} controls={controls}"


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
    notes = remove_notes_row_prefix(row.get("Notes", ""))
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


def _row_is_photo_only(row: dict) -> bool:
    """True when this row is a photo-upload-only entry (no product fields to fill)."""
    value = str(row.get("photo_only", "") or "").strip().lower()
    return (
        value in {"true", "1", "yes"}
        or str(row.get("Import Type", "") or "").strip().lower() == "photo upload"
        or str(row.get("Source Type", "") or "").strip() == "Photo"
    )


def _local_image_path_for_row(row: dict) -> str:
    return str(
        row.get("local_image_path")
        or row.get("Local Image Path")
        or row.get("localImagePath")
        or ""
    ).strip()


# ── Dimension parsing ──────────────────────────────────────────────────────────

def _to_decimal(num_str: str) -> str:
    """Convert '29 7/8', '7/8', or '29.5' to a decimal string. Returns input unchanged if already decimal/integer."""
    s = num_str.strip()
    m = re.match(r'^(\d+)\s+(\d+)/(\d+)$', s)
    if m:
        whole, num, den = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return str(round(whole + num / den, 4)).rstrip('0').rstrip('.')
    m = re.match(r'^(\d+)/(\d+)$', s)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        return str(round(num / den, 4)).rstrip('0').rstrip('.')
    return s


def parse_dimensions_for_programa(dimensions: str) -> dict:
    """
    Parse a combined W×H×D dimension string into separate Programa field values.

    Only labeled dimensions are accepted. Length is returned only when it is
    explicitly labeled in the source string.

    Returns {"width": str, "height": str, "depth": str, "length": str}
    with empty string for any component not found.
    """
    result = {"width": "", "height": "", "depth": "", "length": ""}
    labeled = extract_labeled_dimensions(dimensions)
    for key, value in labeled.items():
        if value:
            result[key] = _to_decimal(value)
    return result


# ── Schedule file navigation ───────────────────────────────────────────────────


def _navigate_to_schedule_file(page) -> tuple[bool, str]:
    """
    Navigate to the Schedule file within an open Programa project.

    Strategy A: Files tab → find a file whose name/type contains "schedule" → click it.
    Strategy B: Click a visible "Schedule" tab/link directly.

    Returns (success, method) where method ∈ {"files_tab", "direct", "not_found"}.
    """
    # Strategy A: Files tab
    if _click_by_text(page, FILES_TAB_TEXTS):
        page.wait_for_timeout(1200)
        for identifier in SCHEDULE_FILE_IDENTIFIERS:
            try:
                loc = page.get_by_text(identifier, exact=False)
                if loc.count() > 0:
                    for i in range(min(loc.count(), 5)):
                        try:
                            el = loc.nth(i)
                            if el.is_visible(timeout=1000):
                                el.click(timeout=3000)
                                page.wait_for_timeout(1000)
                                return True, "files_tab"
                        except Exception:
                            continue
            except Exception:
                pass

    # Strategy B: direct Schedule tab/link
    if _click_by_text(page, SCHEDULE_TEXTS):
        page.wait_for_timeout(800)
        return True, "direct"

    return False, "not_found"


def _open_schedule_file(page, index: int, total: int) -> tuple[bool, str]:
    """
    Navigate to the Schedule file within an open project and wait for full load.

    Calls _navigate_to_schedule_file. If that fails, prompts the user to open
    it manually. Always returns (True, method) so the caller continues.
    """
    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Opening Schedule file…")
    nav_ok, nav_method = _navigate_to_schedule_file(page)

    if not nav_ok:
        take_screenshot(page, f"schedule_nav_failed_{index}")
        _js_confirm(
            page,
            f"SCH DesignOps — Item {index} of {total}\n\n"
            "Could not open the Schedule file automatically.\n"
            "Please open it in Programa, then click OK to continue.",
        )
        nav_method = "manual"

    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(1000)
    return True, nav_method


def _count_product_rows(page) -> int:
    """Count visible rows in the schedule table. Used for before/after comparison."""
    for sel in (
        "tbody tr",
        "[role='grid'] [role='row']",
        "[role='table'] [role='row']",
        "[role='row']",
    ):
        try:
            n = page.locator(sel).count()
            if n > 0:
                return n
        except Exception:
            pass
    return 0


def _click_custom_product_menu_item(page) -> tuple[bool, str]:
    """
    Click the text body of the 'Custom Product' option in an open New dropdown.

    Tries five strategies in order:
      1. get_by_text for each CUSTOM_PRODUCT_TEXTS entry — clicks element
         whose bounding box covers the left/centre portion (avoids chevron).
      2. role=menuitem containing the text.
      3. CSS text selector  text="Custom Product".
      4. Broad sweep: any li/button/[role=option] whose inner text matches.
      5. Keyboard: arrow-down until "Custom Product" is highlighted, then Enter.

    Returns (success, description_of_what_was_clicked).
    """
    # Pass 1 — get_by_text, click left-of-centre to avoid chevron arrows
    for text in CUSTOM_PRODUCT_TEXTS:
        try:
            loc = page.get_by_text(text, exact=False)
            for j in range(min(loc.count(), 8)):
                try:
                    el = loc.nth(j)
                    if not el.is_visible(timeout=500):
                        continue
                    bbox = el.bounding_box()
                    if bbox:
                        # Click at 30 % of width from left — avoids right-side chevron
                        cx = bbox["x"] + bbox["width"] * 0.30
                        cy = bbox["y"] + bbox["height"] / 2
                        page.mouse.click(cx, cy)
                    else:
                        el.click(timeout=3000)
                    return True, f"get_by_text={text!r} idx={j}"
                except Exception:
                    continue
        except Exception:
            pass

    # Pass 2 — role=menuitem
    for role in ("menuitem", "option", "listitem"):
        for text in CUSTOM_PRODUCT_TEXTS:
            try:
                loc = page.get_by_role(role, name=text, exact=False)
                if loc.count() > 0 and loc.first.is_visible(timeout=400):
                    loc.first.click(timeout=3000)
                    return True, f"role={role!r} text={text!r}"
            except Exception:
                pass

    # Pass 3 — CSS text selectors
    for text in CUSTOM_PRODUCT_TEXTS:
        safe = text.replace('"', '\\"')
        try:
            loc = page.locator(f'text="{safe}"').first
            if loc.is_visible(timeout=400):
                loc.click(timeout=3000)
                return True, f"css-text={text!r}"
        except Exception:
            pass

    # Pass 4 — broad element sweep
    for sel in ("[role='menuitem']", "[role='option']", "li", "button", "[class*='item' i]"):
        try:
            els = page.locator(sel)
            for i in range(min(els.count(), 40)):
                try:
                    el = els.nth(i)
                    t = el.inner_text(timeout=300).strip().lower()
                    if any(cp.lower() in t for cp in ("custom product", "custom item")):
                        if el.is_visible(timeout=300):
                            bbox = el.bounding_box()
                            if bbox:
                                page.mouse.click(
                                    bbox["x"] + bbox["width"] * 0.30,
                                    bbox["y"] + bbox["height"] / 2,
                                )
                            else:
                                el.click(timeout=3000)
                            return True, f"broad-sweep sel={sel!r} text={t!r}"
                except Exception:
                    pass
        except Exception:
            pass

    # Pass 5 — keyboard navigation: press ArrowDown until Custom Product is active
    try:
        for _ in range(10):
            active = page.evaluate(
                "() => document.activeElement ? document.activeElement.textContent.trim().toLowerCase() : ''"
            )
            if "custom" in active:
                page.keyboard.press("Enter")
                return True, f"keyboard-nav active={active!r}"
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(150)
    except Exception:
        pass

    return False, "all strategies exhausted"


def _choose_section_after_custom_product(page, section_name: str) -> tuple[bool, str]:
    """
    If Programa shows a section chooser after Custom Product, select section_name.
    If no chooser is visible, return True with a no-op message.
    """
    section_name = str(section_name or "").strip()
    if not section_name:
        return True, "no section requested"

    chooser_visible = False
    for text in ("Choose section", "Select section", "Section", "Add to section"):
        try:
            loc = page.get_by_text(text, exact=False)
            if loc.count() > 0 and loc.first.is_visible(timeout=700):
                chooser_visible = True
                break
        except Exception:
            pass
    for sel in ("[role='dialog']", "[role='menu']", "[role='listbox']", "[class*='popover' i]", "[class*='dropdown' i]"):
        try:
            if page.locator(sel).first.is_visible(timeout=300):
                chooser_visible = True
                break
        except Exception:
            pass
    if not chooser_visible:
        return True, "no section chooser appeared"

    for exact in (True, False):
        try:
            loc = page.get_by_text(section_name, exact=exact)
            for i in range(min(loc.count(), 12)):
                candidate = loc.nth(i)
                if candidate.is_visible(timeout=400):
                    ok, desc = _robust_click_locator(page, candidate, f"section option {section_name!r}")
                    if ok:
                        page.wait_for_timeout(800)
                        return True, f"selected section via text exact={exact}: {desc}"
        except Exception:
            pass
    try:
        active = page.locator("input:focus, [role='combobox']:focus, input:visible").last
        if active.is_visible(timeout=500):
            active.fill(section_name)
            page.wait_for_timeout(600)
            page.keyboard.press("Enter")
            page.wait_for_timeout(800)
            return True, "typed section into picker and pressed Enter"
    except Exception:
        pass
    return False, f"section chooser visible but section option not found: {section_name!r}"


def _create_custom_product_via_global_new(page, section_name: str, index: int, total: int):
    """
    Primary product creation path:
      New → Custom Product → choose target section → wait for newest row/card.
    """
    safe_section = _normalise_section_name(section_name)[:24] or "section"
    shot_before = take_screenshot(page, f"global_cp_before_new_{index}_{safe_section}")
    row_count_before = _count_product_rows(page)
    details_before = 0
    section_heading = _find_section_heading(page, section_name) if section_name else None
    if section_heading is not None:
        details_before = _count_details_buttons_in_section(page, section_heading)

    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Clicking New…")
    new_clicked, new_method = _click_new_button(page)
    if not new_clicked:
        shot = take_screenshot(page, f"global_cp_no_new_{index}_{safe_section}")
        return False, f"New button not found | {new_method}", None, row_count_before, row_count_before, shot
    page.wait_for_timeout(900)
    take_screenshot(page, f"global_cp_menu_open_{index}_{safe_section}")

    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Selecting Custom Product…")
    cp_ok, cp_method = _click_custom_product_menu_item(page)
    if not cp_ok:
        visible = _collect_dropdown_texts(page)
        shot = take_screenshot(page, f"global_cp_no_custom_product_{index}_{safe_section}")
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False, f"Custom Product not found in New menu. Visible items: {visible}", None, row_count_before, row_count_before, shot
    page.wait_for_timeout(900)
    take_screenshot(page, f"global_cp_custom_product_clicked_{index}_{safe_section}")

    section_ok, section_msg = _choose_section_after_custom_product(page, section_name)
    take_screenshot(page, f"global_cp_section_selected_{index}_{safe_section}")
    if not section_ok:
        return False, section_msg, None, row_count_before, _count_product_rows(page), take_screenshot(page, f"global_cp_section_select_failed_{index}_{safe_section}")

    row_locator = None
    row_detail = ""
    after_count = row_count_before
    deadline = time.time() + 10
    while time.time() < deadline:
        if section_heading is not None:
            try:
                after_details = _count_details_buttons_in_section(page, section_heading)
                if after_details > details_before or after_details > 0:
                    row_locator, row_detail = _find_newest_row_in_section(page, section_heading)
                    if row_locator is not None:
                        after_count = _count_product_rows(page)
                        break
            except Exception:
                pass
        after_count = _count_product_rows(page)
        if after_count > row_count_before:
            row_locator = _find_new_row(page, before_count=row_count_before)
            row_detail = f"global row count {row_count_before}->{after_count}"
            if row_locator is not None:
                break
        appeared, signal = _new_row_appeared(page, row_count_before)
        if appeared:
            row_locator = _find_new_row(page, before_count=row_count_before)
            row_detail = signal
            break
        page.wait_for_timeout(500)

    shot_after = take_screenshot(page, f"global_cp_row_created_{index}_{safe_section}")
    if row_locator is None:
        return (
            False,
            f"product row/card not detected after New → Custom Product | {section_msg} | visible text={_visible_page_text(page, 1200)}",
            None,
            row_count_before,
            after_count,
            shot_after,
        )
    return (
        True,
        f"global New → Custom Product ok via {cp_method}; {section_msg}; newest row={row_detail}; screenshot={shot_after}",
        row_locator,
        row_count_before,
        after_count,
        shot_before,
    )


def _new_row_appeared(page, before_count: int) -> tuple[bool, str]:
    """
    Detect whether a new blank product row was created using four signals.
    Returns (appeared, signal_description).

    Signal A — row count increased.
    Signal B — a blank dash-placeholder row exists.
    Signal C — a row containing known field labels (Product name, Brand, W) appeared.
    Signal D — focus moved into an editable element inside a new row.
    """
    # Signal A: row count
    after = _count_product_rows(page)
    if after > before_count:
        return True, f"signal-A: row count {before_count}→{after}"

    # Signal B: blank rows (Programa shows "—" or "-" in empty cells)
    blank_patterns = [
        "tr:has(td:empty)",
        "[role='row']:has([class*='empty' i])",
        "[role='row']:has([class*='blank' i])",
    ]
    for sel in blank_patterns:
        try:
            if page.locator(sel).count() > 0:
                return True, f"signal-B: blank row via {sel!r}"
        except Exception:
            pass

    # Signal C: field labels that only appear inside a product entry row
    field_indicators = [
        "Product name", "Product Name", "Brand", "W (in)", "Qty", "Qty.",
    ]
    for label in field_indicators:
        try:
            loc = page.get_by_text(label, exact=False)
            if loc.count() > 0 and loc.first.is_visible(timeout=300):
                return True, f"signal-C: field label {label!r} visible"
        except Exception:
            pass

    # Signal D: a new input/contenteditable appeared that wasn't there before
    try:
        inputs = page.locator("input:visible, [contenteditable='true']:visible")
        if inputs.count() > 0:
            return True, f"signal-D: {inputs.count()} editable element(s) visible"
    except Exception:
        pass

    return False, f"no signal (row count still {after})"


def _create_custom_product_row(page, index: int, total: int) -> tuple[bool, str]:
    """
    Click 'New' → 'Custom Product' and wait for the blank product row to appear.

    Detection uses four independent signals (row-count increase, blank-row selector,
    field-label visibility, focused input) so it succeeds even when row count stays
    at zero (e.g. Programa renders cards instead of table rows).

    Screenshots are taken:
      • before clicking New
      • after the dropdown opens
      • after clicking Custom Product
      • after the 3-second appearance wait

    Returns (True, detail) on success, (False, reason) on failure.
    Browser is never closed here — caller decides what to do.
    """
    # Snapshot state before touching anything
    take_screenshot(page, f"cp_before_new_{index}")
    before_count = _count_product_rows(page)
    print(f"[SCH Automation] [CP] before_count={before_count}")

    # ── Click "New" ────────────────────────────────────────────────────────────
    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Clicking New…")
    new_clicked = _click_by_text(page, NEW_ITEM_TEXTS, timeout_ms=5000)
    if not new_clicked:
        shot = take_screenshot(page, f"cp_new_missing_{index}")
        print(f"[SCH Automation] [CP] FAIL — New button not found")
        return False, "'New' button not found"

    # Wait for dropdown to animate fully open
    page.wait_for_timeout(1500)
    take_screenshot(page, f"cp_dropdown_open_{index}")

    # ── Click "Custom Product" ─────────────────────────────────────────────────
    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Selecting Custom Product…")
    cp_ok, cp_method = _click_custom_product_menu_item(page)
    take_screenshot(page, f"cp_after_click_{index}")

    if not cp_ok:
        # Log all visible dropdown texts to help diagnose
        visible = _collect_dropdown_texts(page)
        shot = take_screenshot(page, f"cp_not_found_{index}")
        print(f"[SCH Automation] [CP] FAIL — Custom Product not found. Visible: {visible}")
        # Close dropdown before returning
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        except Exception:
            pass
        return False, f"Custom Product not found in dropdown. Visible items: {visible}"

    print(f"[SCH Automation] [CP] clicked via: {cp_method}")

    # ── Wait for dropdown to close ─────────────────────────────────────────────
    # Poll until the menu disappears or 2 s elapse
    deadline_close = time.time() + 2
    while time.time() < deadline_close:
        still_open = False
        for sel in ("[role='menu']", "[role='listbox']", "[class*='dropdown' i]"):
            try:
                if page.locator(sel).first.is_visible(timeout=200):
                    still_open = True
                    break
            except Exception:
                pass
        if not still_open:
            break
        page.wait_for_timeout(200)

    # ── Detect new row via multiple signals (poll up to 5 s) ───────────────────
    deadline_row = time.time() + 5
    appeared = False
    signal_desc = ""
    while time.time() < deadline_row:
        appeared, signal_desc = _new_row_appeared(page, before_count)
        if appeared:
            break
        page.wait_for_timeout(400)

    # Screenshot 3 seconds after the click regardless of outcome
    page.wait_for_timeout(200)
    take_screenshot(page, f"cp_after_wait_{index}")

    if not appeared:
        after_count = _count_product_rows(page)
        msg = (
            f"No new product row detected after Custom Product click "
            f"(before={before_count}, after={after_count}, method={cp_method})"
        )
        print(f"[SCH Automation] [CP] FAIL — {msg}")
        return False, msg

    after_count = _count_product_rows(page)
    print(f"[SCH Automation] [CP] row appeared — {signal_desc} (rows {before_count}→{after_count})")
    return True, f"ok via {cp_method} | {signal_desc} | rows {before_count}→{after_count}"


def open_schedule_file(page) -> tuple[bool, str]:
    """Publicly named helper for the exact Schedule-file step."""
    return _navigate_to_schedule_file(page)


def create_custom_product(page) -> tuple[bool, str]:
    """Publicly named helper for New → Custom Product."""
    return _create_custom_product_row(page, index=1, total=1)


def wait_for_commit(page, label: str, expected: str, row_locator=None) -> bool:
    """Wait until an entered inline value is visible before moving on."""
    deadline = time.time() + 6
    while time.time() < deadline:
        if _value_visible_in_row(page, row_locator, expected):
            print(f"[SCH Automation] committed label={label!r} expected={expected!r}")
            return True
        page.wait_for_timeout(400)
    print(f"[SCH Automation] commit timeout label={label!r} expected={expected!r}")
    return False


def fill_inline_field(page, label: str, value: str, row_locator=None) -> tuple[bool, str]:
    """Publicly named helper for one inline Programa field."""
    ok, msg = fill_inline_programa_field(page, row_locator, label, value)
    if ok and msg.startswith("ok via direct label-fill"):
        return ok, msg
    if ok and str(value).strip() and not wait_for_commit(page, label, str(value).strip(), row_locator):
        return False, f"commit timeout after fill — {msg}"
    return ok, msg


def fill_programa_field(page, row_locator, label_or_column: str, value: str) -> tuple[bool, str]:
    """
    Fill a single Programa row field and verify it committed.

    This helper is intentionally row-scoped first and supports normal inputs,
    contenteditable fields, and custom grid/cell layouts through the existing
    inline fallback strategies.
    """
    value = str(value or "").strip()
    if not value:
        return True, "skipped (blank)"
    print(f"[SCH Automation] Filling field {label_or_column!r} with {value!r}")
    ok, msg = fill_inline_field(page, label_or_column, value, row_locator=row_locator)
    if ok:
        print(f"[SCH Automation] Filled {label_or_column!r}: {msg}")
    else:
        print(f"[SCH Automation] Failed {label_or_column!r}: {msg}; active={_active_element_debug(page)}")
    return ok, msg


def _fill_product_row_inline_fields(page, row_locator, field_values: dict[str, str], index: int) -> dict[str, str]:
    """Fill visible product row fields one by one before using Details fallback."""
    inline_plan: list[tuple[str, str]] = [
        ("Product Name", "Product Name"),
        ("Brand", "Brand"),
        ("Model/SKU", "Model/SKU"),
        ("W", "W"),
        ("L", "L"),
        ("H", "H"),
        ("D", "D"),
        ("Quantity", "Quantity"),
        ("Supplier", "Supplier"),
        ("Material", "Material"),
        ("Color", "Color"),
        ("Finish", "Finish"),
        ("Price", "Price"),
    ]
    results: dict[str, str] = {}
    for field_key, value_key in inline_plan:
        value = str(field_values.get(value_key, "") or "").strip()
        if not value:
            results[field_key] = "skipped (blank)"
            continue
        ok, msg = fill_programa_field(page, row_locator, field_key, value)
        results[field_key] = "ok" if ok else f"failed: {msg}"
        take_screenshot(page, f"field_{index}_{field_key.lower().replace('/', '_')}")
        page.wait_for_timeout(350)
    return results


def _fill_inline_field(
    page, field_key: str, labels: list[str], value: str, index: int
) -> tuple[bool, str]:
    """
    Fill one Programa inline field using a click-activate-fill-commit cycle.

    Programa shows a dash "-" for empty fields. You must click the cell first
    to open the inline editor, then type, then press Tab to commit.

    Three strategies tried in order, with one retry:
      A. Standard label-based fill (aria-label, placeholder, CSS attrs)
         — works when the row form renders proper <input> elements.
      B. Click the label text element → wait for editor → fill.
         — works for table-cell fields where the label appears in-cell.
      C. Column-header position → click first data row in that column.
         — works for pure-table layouts where the label is only in the header.

    Returns (success, failure_reason_or_empty).
    """
    str_value = str(value).strip()
    if not str_value:
        return True, ""

    for attempt in range(2):
        # ── Pass A: input already rendered (e.g. modal / side-panel form) ──────
        if _fill_field_by_label(page, labels, str_value):
            try:
                page.keyboard.press("Tab")
            except Exception:
                pass
            page.wait_for_timeout(400)
            return True, ""

        # ── Pass B: click label text to wake the inline editor ─────────────────
        for label in labels:
            for exact in (True, False):
                try:
                    label_els = page.get_by_text(label, exact=exact)
                    n = min(label_els.count(), 4)
                    for i in range(n):
                        try:
                            el = label_els.nth(i)
                            if not el.is_visible(timeout=600):
                                continue
                            el.click(timeout=2000)
                            page.wait_for_timeout(600)

                            # Try filling whichever editable element appeared
                            for inp_sel in (
                                "input:visible",
                                "textarea:visible",
                                "[contenteditable='true']:visible",
                            ):
                                try:
                                    inp = page.locator(inp_sel).last
                                    if inp.is_visible(timeout=500):
                                        inp.click(click_count=3)
                                        page.wait_for_timeout(100)
                                        inp.type(str_value)
                                        page.keyboard.press("Tab")
                                        page.wait_for_timeout(500)
                                        return True, ""
                                except Exception:
                                    pass
                        except Exception:
                            continue
                except Exception:
                    pass

        # ── Pass C: column header → first data row in that column ──────────────
        for label in labels:
            try:
                safe = label.replace("'", "\\'")
                header = page.locator(
                    f"th:has-text('{safe}'), "
                    f"[role='columnheader']:has-text('{safe}'), "
                    f"[class*='header']:has-text('{safe}')"
                ).first
                if header.is_visible(timeout=600):
                    bbox = header.bounding_box()
                    if bbox:
                        cx = bbox["x"] + bbox["width"] / 2
                        cy = bbox["y"] + bbox["height"] + 20  # just below header = newest row
                        page.mouse.click(cx, cy)
                        page.wait_for_timeout(700)
                        focused = page.locator("input:focus, textarea:focus")
                        if focused.count() > 0:
                            focused.first.click(click_count=3)
                            page.wait_for_timeout(100)
                            focused.first.type(str_value)
                            page.keyboard.press("Tab")
                            page.wait_for_timeout(500)
                            return True, ""
            except Exception:
                pass

        if attempt == 0:
            page.wait_for_timeout(1000)  # pause before retry

    return False, f"field '{field_key}' — labels tried: {labels!r}"


def _click_value_above_label(
    page,
    row_locator,
    label_text: str,
    y_offsets: tuple = (20, 15, 25, 30, 12),
) -> tuple[bool, str]:
    """
    Find `label_text` inside `row_locator`, get its bounding box, then click
    at the same x-centre but N pixels ABOVE the label's top edge.

    Programa card layout:
        [ value / dash ]   ← clickable value cell (what we target)
        [ field label  ]   ← static label text (what we locate)

    Returns (clicked: bool, description: str).
    Used by _fill_product_name_in_row and later by other field helpers.
    """
    scope = row_locator if row_locator is not None else page

    label_el = None
    matched_text = ""
    for exact in (True, False):
        try:
            loc = scope.get_by_text(label_text, exact=exact)
            if loc.count() > 0 and loc.first.is_visible(timeout=600):
                label_el = loc.first
                matched_text = label_text
                break
        except Exception:
            pass

    if label_el is None:
        return False, f"label {label_text!r} not found in row"

    try:
        bbox = label_el.bounding_box()
    except Exception as exc:
        return False, f"bounding_box() failed: {exc}"

    if not bbox:
        return False, "bounding_box() returned None"

    cx = bbox["x"] + bbox["width"] * 0.5

    for y_off in y_offsets:
        cy = bbox["y"] - y_off
        if cy < 0:
            continue
        try:
            page.mouse.click(cx, cy)
            print(
                f"[VABL] clicked above {label_text!r}  "
                f"label_top_y={bbox['y']:.0f}  click_y={cy:.0f}  y_off={y_off}  x={cx:.0f}"
            )
            return True, f"clicked y_off={y_off} above {matched_text!r} at ({cx:.0f},{cy:.0f})"
        except Exception as exc:
            print(f"[VABL] click exc y_off={y_off}: {exc}")

    return False, f"all y_offsets exhausted for {label_text!r}"


def _dump_row_dom(page, row_locator) -> None:
    """
    Print a full DOM diagnosis of the newest row/card to stdout.
    Used when Product Name entry fails so we can identify the editable mechanism.
    """
    print("[PN-DOM] ══════════ BEGIN ROW DOM DIAGNOSIS ══════════")

    # 1. Screenshot
    try:
        shot = take_screenshot(page, "pn_dom_diagnosis")
        print(f"[PN-DOM] screenshot={shot}")
    except Exception as exc:
        print(f"[PN-DOM] screenshot_err={exc}")

    # 2. Raw HTML of the row (truncated to first 6 000 chars)
    try:
        if row_locator is not None:
            html = row_locator.inner_html(timeout=2000)
        else:
            html = page.locator("body").inner_html(timeout=3000)
        print(f"[PN-DOM] row_html_length={len(html)}")
        print(f"[PN-DOM] row_html_preview=\n{html[:6000]}")
    except Exception as exc:
        print(f"[PN-DOM] row_html_err={exc}")

    # 3. All interactive/clickable descendants inside the row
    print("[PN-DOM] --- Clickable descendants ---")
    _sels = [
        ("button",               "button"),
        ("a",                    "anchor"),
        ("input",                "input"),
        ("textarea",             "textarea"),
        ("[contenteditable]",    "contenteditable"),
        ("[role='button']",      "role=button"),
        ("[role='textbox']",     "role=textbox"),
        ("[role='cell']",        "role=cell"),
        ("[role='gridcell']",    "role=gridcell"),
        ("[tabindex]",           "tabindex"),
    ]
    scope = row_locator if row_locator is not None else page
    for sel, label in _sels:
        try:
            els = scope.locator(sel)
            n = els.count()
            for i in range(min(n, 20)):
                el = els.nth(i)
                try:
                    tag          = el.evaluate("e => e.tagName.toLowerCase()")
                    text         = (el.inner_text(timeout=300) or "").strip().replace("\n", " ")[:60]
                    role         = el.get_attribute("role") or ""
                    aria_label   = el.get_attribute("aria-label") or ""
                    placeholder  = el.get_attribute("placeholder") or ""
                    ce           = el.get_attribute("contenteditable") or ""
                    el_type      = el.get_attribute("type") or ""
                    visible      = el.is_visible(timeout=200)
                    print(
                        f"[PN-DOM]   {label}[{i}]  tag={tag}  type={el_type or '-'}  "
                        f"visible={visible}  text={text!r}  role={role!r}  "
                        f"aria-label={aria_label!r}  placeholder={placeholder!r}  "
                        f"contenteditable={ce!r}"
                    )
                except Exception as e2:
                    print(f"[PN-DOM]   {label}[{i}] inspect_err={e2}")
        except Exception as exc:
            print(f"[PN-DOM]   {label} query_err={exc}")

    # 4. Product Name label — parent, prev sibling, next sibling
    print("[PN-DOM] --- Product Name label neighbourhood ---")
    pn_labels = SCHEDULE_FIELD_LABELS.get("Product Name", ["Product name", "Name"])
    for lbl_text in pn_labels:
        try:
            loc = scope.get_by_text(lbl_text, exact=False)
            n = loc.count()
            print(f"[PN-DOM]   label_text={lbl_text!r}  count={n}")
            for i in range(min(n, 3)):
                el = loc.nth(i)
                try:
                    tag     = el.evaluate("e => e.tagName.toLowerCase()")
                    visible = el.is_visible(timeout=200)
                    bbox    = el.bounding_box()
                    parent  = el.evaluate(
                        "e => e.parentElement ? e.parentElement.outerHTML : ''"
                    )[:1000]
                    prev    = el.evaluate(
                        "e => e.previousElementSibling ? e.previousElementSibling.outerHTML : ''"
                    )[:500]
                    nxt     = el.evaluate(
                        "e => e.nextElementSibling ? e.nextElementSibling.outerHTML : ''"
                    )[:500]
                    print(
                        f"[PN-DOM]   [{i}] tag={tag}  visible={visible}  bbox={bbox}\n"
                        f"[PN-DOM]     parent_html={parent!r}\n"
                        f"[PN-DOM]     prev_sibling={prev!r}\n"
                        f"[PN-DOM]     next_sibling={nxt!r}"
                    )
                except Exception as e2:
                    print(f"[PN-DOM]   [{i}] inspect_err={e2}")
        except Exception as exc:
            print(f"[PN-DOM]   label {lbl_text!r} err={exc}")

    # 5. ALL inputs on the page (including hidden) via JS
    print("[PN-DOM] --- All inputs including hidden (page-wide) ---")
    try:
        all_inputs = page.evaluate("""() => {
            const els = document.querySelectorAll('input, textarea, [contenteditable]');
            return Array.from(els).slice(0, 40).map(el => ({
                tag:             el.tagName.toLowerCase(),
                type:            el.type || '',
                id:              el.id || '',
                name:            el.getAttribute('name') || '',
                placeholder:     el.placeholder || '',
                contenteditable: el.contentEditable || '',
                hidden:          el.hidden,
                inert:           el.inert || false,
                offsetParent:    el.offsetParent !== null,
                value:           (el.value || '').substring(0, 40),
                ariaLabel:       el.getAttribute('aria-label') || '',
                className:       el.className.substring(0, 80),
            }));
        }""")
        for i, inp in enumerate(all_inputs):
            print(f"[PN-DOM]   input[{i}] {inp}")
    except Exception as exc:
        print(f"[PN-DOM]   all_inputs_err={exc}")

    print("[PN-DOM] ══════════ END ROW DOM DIAGNOSIS ══════════")


def _fill_product_name_in_row(
    page,
    row_locator,
    value: str,
) -> tuple[bool, str]:
    """
    Enter the Product Name into a newly-created Programa custom product row.

    Primary strategy: locate the "Product name" label, click the value/dash
    area directly ABOVE it (the actual editable cell in Programa's card layout),
    handle any suggestion dropdown, type the value, commit.

    Falls through to strategies B-F only if the primary fails.
    Always dumps row DOM on failure so the mechanism can be diagnosed.
    Never closes the browser on failure.
    """
    debug: list[str] = []

    def _log(key: str, val: str) -> None:
        entry = f"{key}={val}"
        debug.append(entry)
        print(f"[PN] {entry}")

    pn_labels: list[str] = SCHEDULE_FIELD_LABELS.get("Product Name", ["Product name", "Name"])
    scope = row_locator if row_locator is not None else page

    _log("newest_row_found", "yes" if row_locator is not None else "no")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _wait_editor(timeout_s: float = 2.5) -> tuple[bool, str]:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for sel in (
                "input:focus", "textarea:focus",
                "[contenteditable='true']:focus",
                "input:visible", "textarea:visible",
                "[contenteditable='true']:visible",
            ):
                try:
                    el = page.locator(sel).last
                    if el.is_visible(timeout=150):
                        return True, sel
                except Exception:
                    pass
            page.wait_for_timeout(200)
        return False, ""

    def _write_val(editor_sel: str) -> bool:
        try:
            el = page.locator(editor_sel).last
            el.click(click_count=3)
            page.wait_for_timeout(100)
            try:
                el.fill(value)
            except Exception:
                el.type(value)
            page.keyboard.press("Enter")
            page.wait_for_timeout(700)
            visible = _value_visible_in_row(page, row_locator, value)
            _log("typed_value", "yes")
            _log("committed_visible", "yes" if visible else "no")
            return visible
        except Exception as exc:
            _log("write_exc", str(exc))
            return False

    # ── Helpers for editor interaction ────────────────────────────────────────

    def _handle_suggestions_and_commit(editor_el) -> bool:
        """
        After typing into editor_el, look for a suggestion dropdown.
        - If exact match found → click it.
        - If no exact match → Escape to dismiss dropdown, then Enter to commit.
        Returns True if value ends up visible in row.
        """
        # Give dropdown a moment to appear
        page.wait_for_timeout(600)
        # Look for a listbox/menu with suggestions
        suggestion_sels = [
            "[role='listbox']",
            "[role='menu']",
            "[role='option']",
            "ul[class*='suggest' i]",
            "ul[class*='dropdown' i]",
            "[class*='autocomplete' i]",
        ]
        dropdown_visible = False
        for sel in suggestion_sels:
            try:
                el = page.locator(sel)
                if el.count() > 0 and el.first.is_visible(timeout=300):
                    dropdown_visible = True
                    _log("suggestion_dropdown", f"visible ({sel})")
                    break
            except Exception:
                pass

        if dropdown_visible:
            # Check for exact match
            exact_found = False
            for sel in ("[role='option']", "[role='menuitem']", "li"):
                try:
                    opts = page.locator(sel)
                    for i in range(opts.count()):
                        opt = opts.nth(i)
                        try:
                            opt_text = (opt.inner_text(timeout=200) or "").strip()
                            if opt_text.lower() == value.lower():
                                opt.click(timeout=1500)
                                _log("suggestion_exact_match", f"clicked {opt_text!r}")
                                exact_found = True
                                break
                        except Exception:
                            pass
                    if exact_found:
                        break
                except Exception:
                    pass
            if not exact_found:
                # Dismiss dropdown, then commit typed text
                _log("suggestion_no_exact_match", "dismissing dropdown, committing typed text")
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                except Exception:
                    pass
                try:
                    page.keyboard.press("Enter")
                except Exception:
                    pass
        else:
            # No dropdown — just commit
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass

        page.wait_for_timeout(800)
        return _value_visible_in_row(page, row_locator, value)

    def _type_into_editor(editor_sel: str) -> bool:
        """Select-all, type value, handle suggestions, verify."""
        try:
            el = page.locator(editor_sel).last
            el.click(click_count=3)           # select-all
            page.wait_for_timeout(100)
            try:
                el.fill(value)
            except Exception:
                el.type(value)
            _log("typed_value", "yes")
            committed = _handle_suggestions_and_commit(el)
            _log("committed_visible", "yes" if committed else "no")
            return committed
        except Exception as exc:
            _log("type_exc", str(exc))
            return False

    # ════════════════════════════════════════════════════════════════════════
    # Strategy A — primary: click dash/value ABOVE "Product name" label
    # Uses _click_value_above_label helper with several y_offsets.
    # After each successful click, wait for an editor to appear then type.
    # ════════════════════════════════════════════════════════════════════════
    _log("strategy", "A: click_value_above_label")
    for lbl_text in pn_labels:
        for y_off in (20, 15, 25, 30, 12, 10, 35):
            clicked, click_desc = _click_value_above_label(
                page, row_locator, lbl_text, y_offsets=(y_off,)
            )
            if not clicked:
                break  # label not found — no point retrying offsets
            page.wait_for_timeout(700)
            ef, esel = _wait_editor()
            _log("editor_appeared", f"{'yes' if ef else 'no'} A y_off={y_off} sel={esel!r}")
            if ef:
                if _type_into_editor(esel):
                    return True, " | ".join(debug)
                # editor appeared but commit failed — try next offset
            # No editor — try next y_offset
        if clicked:
            break  # label was found, all offsets tried

    # ════════════════════════════════════════════════════════════════════════
    # Strategy B — double-click the dash/value area above label
    # ════════════════════════════════════════════════════════════════════════
    _log("strategy", "B: double-click above label")
    for lbl_text in pn_labels:
        try:
            loc = scope.get_by_text(lbl_text, exact=False)
            if loc.count() > 0 and loc.first.is_visible(timeout=400):
                el = loc.first
                bbox = el.bounding_box()
                if bbox:
                    cx = bbox["x"] + bbox["width"] * 0.5
                    cy = bbox["y"] - 20
                    if cy >= 0:
                        page.mouse.dblclick(cx, cy)
                        page.wait_for_timeout(700)
                        ef, esel = _wait_editor()
                        _log("editor_appeared", f"{'yes' if ef else 'no'} B sel={esel!r}")
                        if ef and _type_into_editor(esel):
                            return True, " | ".join(debug)
                break
        except Exception as exc:
            _log("B_exc", str(exc))

    # ════════════════════════════════════════════════════════════════════════
    # Strategy C — triple-click (click_count=3) above label
    # ════════════════════════════════════════════════════════════════════════
    _log("strategy", "C: triple-click above label")
    for lbl_text in pn_labels:
        try:
            loc = scope.get_by_text(lbl_text, exact=False)
            if loc.count() > 0 and loc.first.is_visible(timeout=400):
                el = loc.first
                bbox = el.bounding_box()
                if bbox:
                    cx = bbox["x"] + bbox["width"] * 0.5
                    cy = bbox["y"] - 20
                    if cy >= 0:
                        page.mouse.click(cx, cy, click_count=3)
                        page.wait_for_timeout(700)
                        ef, esel = _wait_editor()
                        _log("editor_appeared", f"{'yes' if ef else 'no'} C sel={esel!r}")
                        if ef and _type_into_editor(esel):
                            return True, " | ".join(debug)
                break
        except Exception as exc:
            _log("C_exc", str(exc))

    # ════════════════════════════════════════════════════════════════════════
    # Strategy D — click row then Tab into first field
    # ════════════════════════════════════════════════════════════════════════
    _log("strategy", "D: click row + Tab")
    if row_locator is not None:
        try:
            row_locator.click(timeout=2000)
            page.wait_for_timeout(500)
            for tab_n in range(5):
                ef, esel = _wait_editor()
                _log("editor_appeared", f"{'yes' if ef else 'no'} D tab={tab_n} sel={esel!r}")
                if ef and _type_into_editor(esel):
                    return True, " | ".join(debug)
                page.keyboard.press("Tab")
                page.wait_for_timeout(400)
        except Exception as exc:
            _log("D_exc", str(exc))

    # ════════════════════════════════════════════════════════════════════════
    # Strategy E — click row then Enter
    # ════════════════════════════════════════════════════════════════════════
    _log("strategy", "E: click row + Enter")
    if row_locator is not None:
        try:
            row_locator.click(timeout=2000)
            page.wait_for_timeout(400)
            page.keyboard.press("Enter")
            page.wait_for_timeout(700)
            ef, esel = _wait_editor()
            _log("editor_appeared", f"{'yes' if ef else 'no'} E sel={esel!r}")
            if ef and _type_into_editor(esel):
                return True, " | ".join(debug)
        except Exception as exc:
            _log("E_exc", str(exc))

    # ════════════════════════════════════════════════════════════════════════
    # Strategy F — Details button fallback
    # Hover row to reveal hover-only buttons, find Details/Edit, click it,
    # then dump the panel DOM and try to fill via label.
    # ════════════════════════════════════════════════════════════════════════
    _log("strategy", "F: Details button")
    if row_locator is not None:
        try:
            row_locator.hover(timeout=2000)
            page.wait_for_timeout(500)
        except Exception:
            pass

    details_btn = None
    details_btn_label = ""
    for dt in ("Details", "Edit", "View details", "Open", "Expand", "…", "⋯", "···", "•••"):
        for s in (scope, page):
            try:
                el = s.get_by_text(dt, exact=True)
                if el.count() > 0 and el.first.is_visible(timeout=300):
                    details_btn = el.first
                    details_btn_label = f"text={dt!r}"
                    break
            except Exception:
                pass
        if details_btn:
            break
    if not details_btn:
        for sel in (
            "button[aria-label*='detail' i]", "button[aria-label*='edit' i]",
            "button[title*='detail' i]",      "[data-action*='detail' i]",
        ):
            try:
                el = scope.locator(sel)
                if el.count() > 0 and el.first.is_visible(timeout=300):
                    details_btn = el.first
                    details_btn_label = f"css={sel!r}"
                    break
            except Exception:
                pass

    _log("details_button_found", f"yes via {details_btn_label}" if details_btn else "no")

    if details_btn is not None:
        try:
            details_btn.click(timeout=3000)
            page.wait_for_timeout(1500)
            _log("details_clicked", "yes")

            # Dump what opened so the DOM dump is still available
            print("[PN-DOM] --- After Details click: page-wide inputs ---")
            try:
                after_inputs = page.evaluate("""() => {
                    const els = document.querySelectorAll(
                        'input, textarea, [contenteditable="true"]');
                    return Array.from(els).slice(0, 30).map(el => ({
                        tag: el.tagName.toLowerCase(), type: el.type || '',
                        placeholder: el.placeholder || '',
                        ariaLabel: el.getAttribute('aria-label') || '',
                        offsetParent: el.offsetParent !== null,
                        value: (el.value || '').substring(0, 40),
                    }));
                }""")
                for i, inp in enumerate(after_inputs):
                    print(f"[PN-DOM]   after_details_input[{i}] {inp}")
            except Exception as exc:
                print(f"[PN-DOM]   after_details_err={exc}")

            for lbl_text in pn_labels:
                if _fill_field_by_label(page, [lbl_text], value):
                    try:
                        page.keyboard.press("Enter")
                    except Exception:
                        pass
                    page.wait_for_timeout(500)
                    for sv in ("Save", "Apply", "Done", "OK"):
                        try:
                            sb = page.get_by_role("button", name=sv)
                            if sb.count() > 0 and sb.first.is_visible(timeout=400):
                                sb.first.click(timeout=2000)
                                page.wait_for_timeout(800)
                                break
                        except Exception:
                            pass
                    page.wait_for_timeout(400)
                    visible = _value_visible_in_row(page, row_locator, value)
                    _log("details_pn_visible", "yes" if visible else "no")
                    if visible:
                        _log("committed_via", "Details panel")
                        return True, " | ".join(debug)
        except Exception as exc:
            _log("F_exc", str(exc))

    # ── All strategies failed — dump DOM for diagnosis ────────────────────────
    _log("all_strategies_failed", "yes")
    _dump_row_dom(page, row_locator)
    # Browser stays open — KEEP_BROWSER_OPEN_ON_FAILURE handles the pause
    return False, " | ".join(debug)


# ══════════════════════════════════════════════════════════════════════════════
# Details-panel entry — primary field-fill path
# ══════════════════════════════════════════════════════════════════════════════

DETAILS_DRAWER_SELECTORS = (
    "[role='dialog']",
    "[role='complementary']",
    "[class*='drawer' i]",
    "[class*='modal' i]",
    "[class*='panel' i]",
    "[class*='slideout' i]",
    "[class*='flyout' i]",
    "[class*='detail' i]",
)

DETAILS_BUTTON_TEXTS = ("Details", "Edit", "View details", "Open details", "Open", "Expand")
DETAILS_BUTTON_SELECTORS = (
    "button[aria-label*='detail' i]",
    "button[aria-label*='edit' i]",
    "button[aria-label*='open' i]",
    "button[title*='detail' i]",
    "button[title*='edit' i]",
    "[data-action*='detail' i]",
    "[data-testid*='detail' i]",
    "[data-testid*='edit' i]",
)


def _find_details_button_in_scope(scope) -> tuple[object | None, str]:
    """Find a Details/Edit button in a scoped locator, preferring the last visible match."""
    for text in DETAILS_BUTTON_TEXTS:
        try:
            loc = scope.get_by_text(text, exact=True)
            visible: list = []
            for i in range(min(loc.count(), 12)):
                candidate = loc.nth(i)
                if candidate.is_visible(timeout=250):
                    visible.append(candidate)
            if visible:
                return visible[-1], f"text_exact={text!r} visible_index={len(visible)-1}"
        except Exception:
            pass

    for name in ("Details", "Edit", "Open", "Expand"):
        try:
            loc = scope.get_by_role("button", name=name, exact=False)
            visible = []
            for i in range(min(loc.count(), 12)):
                candidate = loc.nth(i)
                if candidate.is_visible(timeout=250):
                    visible.append(candidate)
            if visible:
                return visible[-1], f"role_button_name={name!r} visible_index={len(visible)-1}"
        except Exception:
            pass

    for sel in DETAILS_BUTTON_SELECTORS:
        try:
            loc = scope.locator(sel)
            visible = []
            for i in range(min(loc.count(), 12)):
                candidate = loc.nth(i)
                if candidate.is_visible(timeout=250):
                    visible.append(candidate)
            if visible:
                return visible[-1], f"css={sel!r} visible_index={len(visible)-1}"
        except Exception:
            pass

    return None, ""


def _open_details_panel(page, row_locator) -> tuple[bool, str]:
    """
    Find and click the Details button on row_locator, wait for a panel to open.

    Returns (opened: bool, description: str).

    On failure: takes screenshot, logs row HTML, keeps browser open.
    """
    print("[DETAILS] Searching for Details button…")
    shot_before = take_screenshot(page, "details_before_click")
    print(f"[DETAILS] screenshot before clicking Details: {shot_before}")

    # Pass 1: hover the row to reveal hover-only buttons
    if row_locator is not None:
        try:
            row_locator.hover(timeout=2000)
            page.wait_for_timeout(600)
            print("[DETAILS] hovered row")
        except Exception as exc:
            print(f"[DETAILS] hover exc: {exc}")

    # Pass 2: click the row to select it (some apps show a toolbar only when selected)
    if row_locator is not None:
        try:
            row_locator.click(timeout=2000)
            page.wait_for_timeout(500)
            print("[DETAILS] clicked row to select")
        except Exception as exc:
            print(f"[DETAILS] row-click exc: {exc}")

    details_btn = None
    details_how = ""
    if row_locator is not None:
        details_btn, details_how = _find_details_button_in_scope(row_locator)
        if details_btn:
            details_how = f"newest_row::{details_how}"

    if not details_btn:
        print("[DETAILS] Details button not found in newest row; falling back to last visible page-level Details button")
        details_btn, details_how = _find_details_button_in_scope(page)
        if details_btn:
            details_how = f"page_last_visible::{details_how}"

    # 3d — any button on the row (last resort — log them all first)
    if not details_btn and row_locator is not None:
        try:
            btns = row_locator.locator("button")
            n = btns.count()
            print(f"[DETAILS] row has {n} button(s):")
            for i in range(min(n, 10)):
                b = btns.nth(i)
                try:
                    txt        = (b.inner_text(timeout=200) or "").strip()[:60]
                    aria       = b.get_attribute("aria-label") or ""
                    title      = b.get_attribute("title") or ""
                    visible    = b.is_visible(timeout=200)
                    print(f"[DETAILS]   button[{i}] visible={visible} text={txt!r} aria={aria!r} title={title!r}")
                except Exception:
                    pass
        except Exception as exc:
            print(f"[DETAILS] button enumeration exc: {exc}")

    # ── Diagnostics on failure ─────────────────────────────────────────────────
    if not details_btn:
        shot = take_screenshot(page, "details_btn_not_found")
        print(f"[DETAILS] FAILED to find Details button. screenshot={shot}")
        if row_locator is not None:
            try:
                row_text = row_locator.inner_text(timeout=1500)
                print(f"[DETAILS] Newest row text:\n{row_text[:1000]}")
            except Exception:
                pass
            try:
                row_html = row_locator.inner_html(timeout=1500)
                print(f"[DETAILS] Newest row HTML (first 3000 chars):\n{row_html[:3000]}")
            except Exception:
                pass
        return False, f"details_button_not_found | before_screenshot={shot_before} screenshot={shot}"

    # ── Click it ──────────────────────────────────────────────────────────────
    print(f"[DETAILS] Details button found via {details_how}")
    try:
        details_btn.click(timeout=3000)
    except Exception as exc:
        shot = take_screenshot(page, "details_click_failed")
        return False, f"details_click_failed | method={details_how} exc={exc} screenshot={shot}"

    shot_after = take_screenshot(page, "details_after_click")
    print(f"[DETAILS] Details clicked via {details_how}. screenshot after click: {shot_after}")

    # Wait up to 3 s for a panel/dialog/drawer to appear
    panel_appeared = False
    for _ in range(15):
        page.wait_for_timeout(200)
        for sel in DETAILS_DRAWER_SELECTORS:
            try:
                el = page.locator(sel)
                if el.count() > 0 and el.first.is_visible(timeout=150):
                    panel_appeared = True
                    print(f"[DETAILS] Panel appeared: {sel}")
                    break
            except Exception:
                pass
        if panel_appeared:
            break

    # Accept "any new input visible" as panel-open signal too
    if not panel_appeared:
        try:
            if page.locator("input:visible, textarea:visible, [contenteditable='true']:visible").count() > 0:
                panel_appeared = True
                print("[DETAILS] Panel signal: visible input/textarea/contenteditable")
        except Exception:
            pass

    drawer = _get_details_drawer(page)
    if not panel_appeared or drawer is None:
        shot = take_screenshot(page, "details_panel_not_opened")
        print(f"[DETAILS] Panel did not open after clicking Details button. screenshot={shot}")
        print(f"[DETAILS] Drawer selectors tried: {DETAILS_DRAWER_SELECTORS}")
        return False, (
            f"drawer_not_open | method={details_how} "
            f"selectors={DETAILS_DRAWER_SELECTORS} after_click_screenshot={shot_after} screenshot={shot}"
        )

    _dump_details_drawer_diagnostics(page, "after_details_click")
    return True, f"opened via {details_how}"


def _log_details_panel_inputs(page) -> None:
    """
    Dump all visible inputs, textareas, contenteditable divs, and visible labels
    inside the open Details panel. Prints to stdout for diagnosis.
    """
    print("[DETAILS-PANEL] ── Inputs / fields inside panel ──")
    try:
        inputs = page.evaluate("""() => {
            const els = document.querySelectorAll(
                'input, textarea, [contenteditable="true"], select');
            return Array.from(els).slice(0, 40).map(el => ({
                tag:             el.tagName.toLowerCase(),
                type:            el.type || '',
                id:              el.id || '',
                name:            el.getAttribute('name') || '',
                placeholder:     el.placeholder || '',
                ariaLabel:       el.getAttribute('aria-label') || '',
                contenteditable: el.contentEditable || '',
                visible:         el.offsetParent !== null,
                value:           (el.value || el.textContent || '').substring(0, 50),
                className:       el.className.substring(0, 80),
            }));
        }""")
        print(f"[DETAILS] Found {len(inputs)} inputs in panel")
        for i, inp in enumerate(inputs):
            print(f"[DETAILS-PANEL]   input[{i}] {inp}")
    except Exception as exc:
        print(f"[DETAILS-PANEL]   inputs_err: {exc}")

    print("[DETAILS-PANEL] ── Visible labels inside panel ──")
    try:
        labels = page.evaluate("""() => {
            const all = document.querySelectorAll('label, [class*="label" i], [class*="field-name" i]');
            return Array.from(all).slice(0, 40)
                .filter(el => el.offsetParent !== null)
                .map(el => ({
                    tag:  el.tagName.toLowerCase(),
                    text: el.textContent.trim().substring(0, 80),
                    forAttr: el.getAttribute('for') || '',
                }));
        }""")
        for i, lbl in enumerate(labels):
            print(f"[DETAILS-PANEL]   label[{i}] {lbl}")
    except Exception as exc:
        print(f"[DETAILS-PANEL]   labels_err: {exc}")


def _dump_details_drawer_diagnostics(page, label: str = "details_drawer") -> None:
    """Dump visible drawer text plus placeholders/labels for the active Details drawer."""
    drawer = _get_details_drawer(page)
    print(f"[DETAILS-DIAG] dump={label} drawer_found={drawer is not None}")
    print(f"[DETAILS-DIAG] drawer selectors tried: {DETAILS_DRAWER_SELECTORS}")
    if drawer is None:
        return
    try:
        text = (drawer.inner_text(timeout=1500) or "").strip()
        print(f"[DETAILS-DIAG] visible drawer text ({len(text)} chars):\n{text[:4000]}")
    except Exception as exc:
        print(f"[DETAILS-DIAG] visible text dump failed: {exc}")
    try:
        fields = drawer.evaluate("""(root) => {
            const controls = root.querySelectorAll('input, textarea, select, [contenteditable="true"]');
            return Array.from(controls).slice(0, 80).map((el, index) => ({
                index,
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                name: el.getAttribute('name') || '',
                id: el.id || '',
                placeholder: el.getAttribute('placeholder') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                role: el.getAttribute('role') || '',
                contenteditable: el.getAttribute('contenteditable') || '',
                visible: el.offsetParent !== null,
                value: (el.value || el.textContent || '').substring(0, 120),
            }));
        }""")
        for field in fields:
            print(f"[DETAILS-DIAG] control {field}")
    except Exception as exc:
        print(f"[DETAILS-DIAG] control dump failed: {exc}")
    try:
        labels = drawer.evaluate("""(root) => {
            const labelNodes = root.querySelectorAll('label, [class*="label" i], [class*="field" i]');
            return Array.from(labelNodes).slice(0, 80)
                .filter(el => el.offsetParent !== null)
                .map((el, index) => ({
                    index,
                    tag: el.tagName.toLowerCase(),
                    text: el.textContent.trim().substring(0, 160),
                    forAttr: el.getAttribute('for') || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                }));
        }""")
        for label_info in labels:
            print(f"[DETAILS-DIAG] label {label_info}")
    except Exception as exc:
        print(f"[DETAILS-DIAG] label dump failed: {exc}")


def _get_details_drawer(page):
    """Return the visible Details drawer/modal locator, or None."""
    for sel in DETAILS_DRAWER_SELECTORS:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 8)):
                el = loc.nth(i)
                if not el.is_visible(timeout=200):
                    continue
                try:
                    text = (el.inner_text(timeout=500) or "").lower()
                except Exception:
                    text = ""
                if (
                    sel in ("[role='dialog']", "[role='complementary']")
                    or "product name" in text
                    or "product details" in text
                    or "product specifications" in text
                    or "important information" in text
                ):
                    return el
        except Exception:
            pass
    return None


def _details_drawer_is_open(page) -> bool:
    return _get_details_drawer(page) is not None


def _scroll_details_drawer(page, amount: int) -> bool:
    """Scroll the drawer's own scroll container, falling back to mouse wheel."""
    drawer = _get_details_drawer(page)
    if drawer is None:
        return False
    try:
        drawer.evaluate(
            """(el, amount) => {
                const candidates = [el, ...Array.from(el.querySelectorAll('*'))];
                const scrollable = candidates.find(node => node.scrollHeight > node.clientHeight + 20);
                (scrollable || el).scrollTop += amount;
            }""",
            amount,
        )
        page.wait_for_timeout(300)
        return True
    except Exception:
        pass
    try:
        drawer.hover(timeout=1000)
        page.mouse.wheel(0, amount)
        page.wait_for_timeout(300)
        return True
    except Exception:
        return False


def _scroll_details_drawer_to_text(page, texts: list[str]) -> bool:
    """Scroll inside the drawer until any target text is visible."""
    for _ in range(8):
        drawer = _get_details_drawer(page)
        scope = drawer if drawer is not None else page
        for text in texts:
            try:
                loc = scope.get_by_text(text, exact=False)
                if loc.count() > 0 and loc.first.is_visible(timeout=250):
                    return True
            except Exception:
                pass
        _scroll_details_drawer(page, 650)
    return False


def _scroll_details_drawer_to_bottom(page) -> bool:
    """Scroll the Details drawer's own scroll container to the bottom."""
    drawer = _get_details_drawer(page)
    if drawer is None:
        return False
    try:
        drawer.evaluate("""(el) => {
            const candidates = [el, ...Array.from(el.querySelectorAll('*'))];
            for (const node of candidates) {
                if (node.scrollHeight > node.clientHeight + 20) {
                    node.scrollTop = node.scrollHeight;
                }
            }
        }""")
        page.wait_for_timeout(400)
        return True
    except Exception as exc:
        print(f"[DETAILS] drawer scroll-to-bottom JS failed: {exc}")
    try:
        drawer.hover(timeout=1000)
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(500)
        return True
    except Exception as exc:
        print(f"[DETAILS] drawer scroll-to-bottom wheel failed: {exc}")
        return False


def _field_value_matches(locator, expected: str) -> bool:
    needle = str(expected or "").strip()
    if not needle:
        return True
    try:
        return locator.input_value(timeout=500).strip() == needle
    except Exception:
        pass
    try:
        return needle in (locator.text_content(timeout=500) or "").strip()
    except Exception:
        return False


def _write_details_value(page, locator, value: str) -> tuple[bool, str]:
    """Click, select all, write value, and verify when possible."""
    value = str(value or "").strip()
    if not value:
        return False, "blank value"
    try:
        locator.click(timeout=2500)
        page.wait_for_timeout(100)
        try:
            locator.fill(value, timeout=2500)
        except Exception:
            try:
                page.keyboard.press("Meta+A")
            except Exception:
                try:
                    page.keyboard.press("Control+A")
                except Exception:
                    pass
            page.wait_for_timeout(100)
            locator.type(value, timeout=3000)
        page.keyboard.press("Tab")
        page.wait_for_timeout(300)
        if _field_value_matches(locator, value):
            return True, "verified"
        return True, "filled (verification unavailable)"
    except Exception as exc:
        return False, f"write failed: {exc}"


def _fill_field_in_details_panel(page, field_labels: list[str], value: str) -> tuple[bool, str]:
    """
    Find and fill a field inside the open Details drawer/modal.
    """
    value = str(value or "").strip()
    if not value:
        return False, "blank value"

    drawer = _get_details_drawer(page)
    if drawer is None:
        return False, "details panel is not open"
    scope = drawer

    for lbl in field_labels:
        for getter_name in ("get_by_label", "get_by_placeholder"):
            try:
                loc = getattr(scope, getter_name)(lbl, exact=False)
                for i in range(min(loc.count(), 4)):
                    target = loc.nth(i)
                    if target.is_visible(timeout=250):
                        ok, desc = _write_details_value(page, target, value)
                        if ok:
                            return True, f"{getter_name}={lbl!r} {desc}"
            except Exception:
                pass

        safe = lbl.replace('"', '\\"')
        for attr_sel in (
            f'input[aria-label*="{safe}" i]',
            f'textarea[aria-label*="{safe}" i]',
            f'input[placeholder*="{safe}" i]',
            f'textarea[placeholder*="{safe}" i]',
            f'[contenteditable][aria-label*="{safe}" i]',
        ):
            try:
                inp = scope.locator(attr_sel)
                for i in range(min(inp.count(), 3)):
                    target = inp.nth(i)
                    if target.is_visible(timeout=250):
                        ok, desc = _write_details_value(page, target, value)
                        if ok:
                            return True, f"attr_sel={attr_sel!r} {desc}"
            except Exception:
                pass

        try:
            lbl_el = scope.get_by_text(lbl, exact=False)
            for i in range(min(lbl_el.count(), 3)):
                el = lbl_el.nth(i)
                if not el.is_visible(timeout=200):
                    continue
                for strategy in (
                    "xpath=following-sibling::*[self::input or self::textarea]",
                    "xpath=following-sibling::*/descendant-or-self::input",
                    "xpath=../input",
                    "xpath=../textarea",
                    "xpath=..//input",
                    "xpath=..//textarea",
                    "xpath=../..//input",
                    "xpath=../..//textarea",
                    "xpath=../..//[contenteditable]",
                ):
                    try:
                        inp = el.locator(strategy)
                        if inp.count() > 0 and inp.first.is_visible(timeout=200):
                            ok, desc = _write_details_value(page, inp.first, value)
                            if ok:
                                return True, f"label={lbl!r} strategy={strategy} {desc}"
                    except Exception:
                        pass
        except Exception:
            pass

    return False, f"field not found in panel — labels tried: {field_labels}"


def _select_dropdown_in_details_panel(page, field_labels: list[str], value: str) -> tuple[bool, str]:
    """Best-effort dropdown/select fill. Failure is non-fatal for supplier/category."""
    value = str(value or "").strip()
    if not value:
        return False, "blank value"

    drawer = _get_details_drawer(page)
    scope = drawer if drawer is not None else page

    for lbl in field_labels:
        try:
            loc = scope.get_by_label(lbl, exact=False)
            if loc.count() > 0 and loc.first.is_visible(timeout=300):
                ok, desc = _write_details_value(page, loc.first, value)
                if ok:
                    return True, f"combobox label={lbl!r} {desc}"
        except Exception:
            pass

        try:
            label_el = scope.get_by_text(lbl, exact=False)
            if label_el.count() > 0 and label_el.first.is_visible(timeout=300):
                label_el.first.click(timeout=1500)
                page.wait_for_timeout(500)
                try:
                    page.keyboard.type(value)
                    page.wait_for_timeout(500)
                except Exception:
                    pass
                for option_sel in ("[role='option']", "[role='menuitem']", "li", "button"):
                    try:
                        options = page.locator(option_sel)
                        for i in range(min(options.count(), 30)):
                            option = options.nth(i)
                            text = (option.inner_text(timeout=200) or "").strip()
                            if text and value.lower() in text.lower() and option.is_visible(timeout=200):
                                option.click(timeout=1500)
                                page.wait_for_timeout(400)
                                return True, f"selected option={text!r}"
                    except Exception:
                        pass
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                return False, f"no matching option for {value!r}"
        except Exception:
            pass

    return False, f"dropdown not found — labels tried: {field_labels}"


def _finish_color_values(value: str) -> tuple[str, str]:
    """Return (finish, color) where color is filled only for clearly color-like text."""
    finish = str(value or "").strip()
    color = ""
    color_words = {
        "white", "black", "gray", "grey", "silver", "blue", "green", "red",
        "yellow", "orange", "brown", "beige", "cream", "ivory", "tan",
        "taupe", "gold", "bronze", "copper", "nickel", "brass",
    }
    lower = finish.lower()
    if any(re.search(rf"\b{re.escape(word)}\b", lower) for word in color_words):
        color = finish
    return finish, color


def _looks_like_notes_or_source_text(text: str) -> bool:
    """Detect scraped/contact/source/transcript text that should live in Notes."""
    value = str(text or "").strip()
    if not value:
        return False
    lower = value.lower()
    note_markers = (
        "salesperson",
        "sales person",
        "contact",
        "phone",
        "email",
        "@",
        "transcript",
        "vendor call",
        "source",
        "scraped",
        "extraction",
        "confidence",
        "verify",
        "uncertain",
        "line 1",
        "line 2",
        "http://",
        "https://",
    )
    if any(marker in lower for marker in note_markers):
        return True
    if len(value.splitlines()) > 2:
        return True
    if len(value) > 350:
        return True
    return False


def _clean_description_and_notes(row: dict, notes_raw: str) -> tuple[str, str, str]:
    """
    Split clean product-facing description from operational notes/source text.

    Returns (clean_description, product_details, combined_notes).
    """
    note_keys = (
        "extra_notes",
        "Extra Notes",
        "extraction_notes",
        "Extraction Notes",
        "vendor_call_notes",
        "Vendor Call Notes",
        "source_notes",
        "Source Notes",
        "transcript_notes",
        "Transcript Notes",
    )
    notes_parts = [str(notes_raw or "").strip()]
    notes_parts.extend(str(row.get(key, "") or "").strip() for key in note_keys)

    raw_description = str(
        row.get("description", "")
        or row.get("Product Description", "")
        or row.get("Description", "")
        or ""
    ).strip()
    raw_product_details = str(row.get("product_details", "") or row.get("Product Details", "") or "").strip()

    clean_description = ""
    if raw_description and _looks_like_notes_or_source_text(raw_description):
        print("[DETAILS] Leaving Product description blank because text looked like notes/source data")
        notes_parts.append(raw_description)
    else:
        clean_description = raw_description

    clean_product_details = ""
    if raw_product_details and _looks_like_notes_or_source_text(raw_product_details):
        print("[DETAILS] Moving Product details text to Notes because it looked like notes/source data")
        notes_parts.append(raw_product_details)
    else:
        clean_product_details = raw_product_details

    seen: set[str] = set()
    combined_notes: list[str] = []
    for part in notes_parts:
        part = str(part or "").strip()
        key = part.lower()
        if part and key not in seen:
            seen.add(key)
            combined_notes.append(part)

    if combined_notes:
        print("[DETAILS] Writing notes to Notes field")

    return clean_description, clean_product_details, "\n\n".join(combined_notes)


IMAGE_DOWNLOAD_DIR = runtime_data_path("product_images")
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


class _ProductImageHTMLParser(HTMLParser):
    """Tiny HTML parser for product image discovery without adding dependencies."""

    def __init__(self):
        super().__init__()
        self.meta_images: list[tuple[str, str]] = []
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs):
        attr = {str(k).lower(): str(v or "") for k, v in attrs}
        if tag.lower() == "meta":
            prop = (attr.get("property") or attr.get("name") or "").lower()
            content = attr.get("content", "")
            if prop in ("og:image", "og:image:url", "twitter:image", "twitter:image:src") and content:
                self.meta_images.append((prop, content))
        elif tag.lower() == "img":
            self.images.append(attr)


def _normalise_image_url(url: str, base_url: str = "") -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    return urljoin(base_url, value)


def _image_url_is_usable(url: str) -> bool:
    value = str(url or "").strip()
    if not value:
        return False
    lower = value.lower().split("?", 1)[0]
    if lower.endswith(".svg"):
        return False
    bad_words = ("logo", "icon", "sprite", "placeholder", "tracking", "pixel", "blank", "favicon", "thumbnail", "thumb")
    if any(word in lower for word in bad_words):
        return False
    ext = Path(urlparse(lower).path).suffix.lower()
    return ext in _IMAGE_EXTS or not ext


def _best_src_from_srcset(srcset: str) -> str:
    best_url = ""
    best_score = -1
    for part in str(srcset or "").split(","):
        bits = part.strip().split()
        if not bits:
            continue
        url = bits[0]
        score = 0
        if len(bits) > 1:
            marker = bits[1].lower()
            try:
                if marker.endswith("w"):
                    score = int(float(marker[:-1]))
                elif marker.endswith("x"):
                    score = int(float(marker[:-1]) * 1000)
            except Exception:
                score = 0
        if score > best_score:
            best_url = url
            best_score = score
    return best_url


def _discover_image_url_from_html(html: str, product_url: str) -> tuple[str, str]:
    parser = _ProductImageHTMLParser()
    try:
        parser.feed(str(html or ""))
    except Exception:
        pass

    for source, url in parser.meta_images:
        candidate = _normalise_image_url(url, product_url)
        if _image_url_is_usable(candidate):
            label = "Extracted og:image" if source.startswith("og:") else "Extracted twitter:image"
            return candidate, label

    scored: list[tuple[int, str, str]] = []
    for img in parser.images:
        src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
        if not src and img.get("srcset"):
            src = _best_src_from_srcset(img.get("srcset", ""))
        candidate = _normalise_image_url(src, product_url)
        if not _image_url_is_usable(candidate):
            continue
        text = " ".join([img.get("alt", ""), img.get("class", ""), img.get("id", ""), candidate]).lower()
        try:
            width = int(float(img.get("width") or 0))
        except Exception:
            width = 0
        try:
            height = int(float(img.get("height") or 0))
        except Exception:
            height = 0
        score = width + height
        if "product" in text:
            score += 600
        if "main" in text or "hero" in text:
            score += 400
        if "small" in text or "thumb" in text:
            score -= 500
        if width and height and (width < 180 or height < 180):
            score -= 800
        scored.append((score, candidate, f"Extracted product image candidate score={score}"))

    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1], scored[0][2]
    return "", "no usable image found"


def _find_product_image_url(product: dict) -> tuple[str, str]:
    product_name = str(product.get("Product Name") or product.get("product_name") or "").strip()
    print(f"[IMAGE] Searching product image for: {product_name or '(unnamed product)'}")

    existing = str(
        product.get("image_url")
        or product.get("Image URL")
        or product.get("Product Image URL")
        or ""
    ).strip()
    if existing and _image_url_is_usable(existing):
        print("[IMAGE] Using existing product.image_url")
        return existing, "Using existing product.image_url"

    product_url = str(product.get("product_url") or product.get("Product URL") or "").strip()
    if not product_url:
        return "", "no product_url available"

    try:
        response = requests.get(
            product_url,
            timeout=12,
            headers={"User-Agent": "SCH DesignOps Intake/1.0"},
        )
        response.raise_for_status()
    except Exception as exc:
        return "", f"product page fetch failed: {exc}"

    return _discover_image_url_from_html(response.text, product_url)


def _validate_or_convert_image(path: Path) -> Path:
    """
    Validate basic image size/dimensions. Convert webp to jpg when Pillow exists.
    Returns the path to upload.
    """
    size = path.stat().st_size if path.exists() else 0
    if size < 1024:
        raise ValueError("downloaded image was too small")
    if size > 15 * 1024 * 1024:
        raise ValueError("downloaded image was larger than 15 MB")

    try:
        from PIL import Image
        with Image.open(path) as img:
            width, height = img.size
            if width < 120 or height < 120:
                raise ValueError(f"downloaded image dimensions too small: {width}x{height}")
            if path.suffix.lower() == ".webp":
                jpg_path = path.with_suffix(".jpg")
                img.convert("RGB").save(jpg_path, "JPEG", quality=92)
                print(f"[IMAGE] Converted webp to jpg: {jpg_path}")
                return jpg_path
    except ImportError:
        if path.suffix.lower() == ".webp":
            print("[IMAGE] Pillow not installed; leaving webp as-is")
    return path


def download_product_image(product: dict) -> str | None:
    """
    Find and download a product image to temp/product_images/.
    Returns a local file path, or None on any failure.
    """
    existing_path = str(product.get("local_image_path") or product.get("Local Image Path") or "").strip()
    if existing_path and Path(existing_path).exists():
        print(f"[IMAGE] Using existing local_image_path: {existing_path}")
        return existing_path

    image_url, source = _find_product_image_url(product)
    if not image_url:
        print(f"[IMAGE] Image upload skipped/failed: {source}")
        product["image_upload_status"] = f"skipped: {source}"
        return None
    print(f"[IMAGE] {source}: {image_url}")

    try:
        response = requests.get(
            image_url,
            timeout=15,
            stream=True,
            headers={"User-Agent": "SCH DesignOps Intake/1.0"},
        )
        response.raise_for_status()
        content_type = (response.headers.get("content-type") or "").lower()
        ext = Path(urlparse(image_url).path).suffix.lower()
        if ext not in _IMAGE_EXTS:
            if "jpeg" in content_type or "jpg" in content_type:
                ext = ".jpg"
            elif "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            else:
                raise ValueError(f"unsupported image content-type: {content_type or 'unknown'}")
        if ext == ".svg":
            raise ValueError("svg images are not uploadable")

        IMAGE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:12]
        filename = f"product_{int(time.time())}_{digest}{ext}"
        path = IMAGE_DOWNLOAD_DIR / filename
        total = 0
        with open(path, "wb") as fh:
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > 15 * 1024 * 1024:
                    raise ValueError("download exceeded 15 MB")
                fh.write(chunk)
        upload_path = _validate_or_convert_image(path)
        product["local_image_path"] = str(upload_path)
        product["image_url"] = image_url
        product["image_upload_status"] = "downloaded"
        print(f"[IMAGE] Downloaded image to: {upload_path}")
        return str(upload_path)
    except Exception as exc:
        print(f"[IMAGE] Image upload skipped/failed: {exc}")
        product["image_upload_status"] = f"failed: {exc}"
        return None


def _count_images_in_details_drawer(page) -> int:
    drawer = _get_details_drawer(page)
    scope = drawer if drawer is not None else page
    try:
        return scope.locator("img").count()
    except Exception:
        return 0


def _details_text_visible(scope, text: str, timeout_ms: int = 250) -> bool:
    try:
        loc = scope.get_by_text(text, exact=False)
        return loc.count() > 0 and loc.first.is_visible(timeout=timeout_ms)
    except Exception:
        return False


def _details_product_details_y(scope) -> float | None:
    try:
        loc = scope.get_by_text("Product Details", exact=False)
        visible = []
        for i in range(min(loc.count(), 8)):
            candidate = loc.nth(i)
            if candidate.is_visible(timeout=250):
                bbox = candidate.bounding_box()
                if bbox:
                    visible.append(float(bbox["y"]))
        if visible:
            return min(visible)
    except Exception:
        pass
    return None


def _wait_for_details_image_area(page) -> tuple[bool, str]:
    """Wait until the Details panel is open at the top image area."""
    deadline = time.time() + 8
    last = "details panel not ready"
    while time.time() < deadline:
        drawer = _get_details_drawer(page)
        if drawer is None:
            last = "details drawer not found"
            page.wait_for_timeout(300)
            continue
        product_details_ok = _details_text_visible(drawer, "Product Details", timeout_ms=300)
        add_image_ok = _details_text_visible(drawer, "Add image", timeout_ms=300)
        if product_details_ok:
            print("[IMAGE] Details panel opened")
        if product_details_ok and add_image_ok:
            return True, "Product Details and Add image visible"
        last = f"Product Details visible={product_details_ok}; Add image visible={add_image_ok}"
        page.wait_for_timeout(300)
    return False, last


def _find_add_image_control(scope):
    product_details_y = _details_product_details_y(scope)
    selectors = [
        ("text", lambda root: root.get_by_text("Add image", exact=False)),
        ("role_button", lambda root: root.get_by_role("button", name="Add image", exact=False)),
        ("button_text", lambda root: root.locator("button:has-text('Add image')")),
        ("aria", lambda root: root.locator("[aria-label*='Add image' i], [title*='Add image' i]")),
    ]
    candidates: list[tuple[float, object, str]] = []
    for label, getter in selectors:
        try:
            loc = getter(scope)
            for i in range(min(loc.count(), 8)):
                candidate = loc.nth(i)
                if candidate.is_visible(timeout=250):
                    bbox = candidate.bounding_box()
                    y = float(bbox["y"]) if bbox else 0.0
                    if product_details_y is None or y <= product_details_y + 12:
                        candidates.append((abs((product_details_y or y) - y), candidate, f"{label}[{i}] above Product Details"))
        except Exception:
            pass
    if candidates:
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1], candidates[0][2]

    # Fallback: first clickable tile/control above Product Details.
    if product_details_y is not None:
        for sel in ("button", "[role='button']", "label", "[class*='upload' i]", "[class*='image' i]"):
            try:
                loc = scope.locator(sel)
                tile_candidates = []
                for i in range(min(loc.count(), 40)):
                    candidate = loc.nth(i)
                    if not candidate.is_visible(timeout=200):
                        continue
                    bbox = candidate.bounding_box()
                    if not bbox:
                        continue
                    text = ""
                    try:
                        text = (candidate.inner_text(timeout=200) or "").strip().lower()
                    except Exception:
                        pass
                    y = float(bbox["y"])
                    if y < product_details_y and ("add image" in text or bbox["width"] >= 60):
                        tile_candidates.append((product_details_y - y, candidate, f"tile_above_product_details sel={sel!r} idx={i}"))
                if tile_candidates:
                    tile_candidates.sort(key=lambda item: item[0])
                    return tile_candidates[0][1], tile_candidates[0][2]
            except Exception:
                pass
    return None, "not_found"


def _valid_local_image_path(local_path: str) -> tuple[bool, str]:
    path = Path(str(local_path or ""))
    if not local_path:
        return False, "No image found, skipping upload"
    if not path.exists():
        return False, f"No image found, skipping upload: {local_path}"
    if path.suffix.lower() not in _IMAGE_EXTS:
        return False, f"Image upload skipped/failed: unsupported file type {path.suffix}"
    if path.stat().st_size <= 0:
        return False, "Image upload skipped/failed: image file is empty"
    return True, "ok"


def _image_upload_preview_visible(page, before_count: int) -> bool:
    deadline = time.time() + 5
    while time.time() < deadline:
        after_count = _count_images_in_details_drawer(page)
        if after_count > before_count or after_count > 0:
            return True
        drawer = _get_details_drawer(page)
        if drawer is not None:
            try:
                add_image = drawer.get_by_text("Add image", exact=False)
                if add_image.count() == 0 or not add_image.first.is_visible(timeout=200):
                    return True
            except Exception:
                pass
        page.wait_for_timeout(300)
    return False


def upload_product_image_to_details_panel(page, product: dict) -> tuple[str, str]:
    """Upload a downloaded product image into the open Programa Details panel."""
    local_path = download_product_image(product)
    if not local_path:
        return "skipped", str(product.get("image_upload_status") or "no image available")
    path_ok, path_msg = _valid_local_image_path(local_path)
    if not path_ok:
        print(f"[IMAGE] {path_msg}")
        return "skipped", path_msg
    path = Path(local_path)

    drawer = _get_details_drawer(page)
    scope = drawer if drawer is not None else page
    before_count = _count_images_in_details_drawer(page)
    try:
        ready, ready_msg = _wait_for_details_image_area(page)
        if not ready:
            print(f"[IMAGE] Image upload skipped/failed: {ready_msg}")
            take_screenshot(page, "details_image_area_not_ready")
            return "skipped", ready_msg
        drawer = _get_details_drawer(page)
        scope = drawer if drawer is not None else page
        take_screenshot(page, "details_before_image_upload")

        last_error = ""
        for attempt in range(1, 3):
            control, method = _find_add_image_control(scope)
            if control is None:
                shot = take_screenshot(page, "details_add_image_not_found")
                return "failed", f"Add image control not found screenshot={shot}"

            try:
                control.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            try:
                print(f"[IMAGE] Found Add image tile via {method}")
                print("[IMAGE] Opening file chooser")
                print(f"[IMAGE] Uploading image: {path}")
                with page.expect_file_chooser(timeout=7000) as fc_info:
                    control.click(timeout=4000)
                take_screenshot(page, f"details_after_add_image_click_attempt_{attempt}")
                chooser = fc_info.value
                chooser.set_files(str(path))
                take_screenshot(page, f"details_after_image_set_files_attempt_{attempt}")
                if _image_upload_preview_visible(page, before_count):
                    print("[IMAGE] Image upload successful")
                    return "uploaded", f"uploaded via {method}: {path}"
                last_error = f"preview not visible after attempt {attempt}"
                print(f"[IMAGE] {last_error}")
            except Exception as exc:
                last_error = str(exc)
                shot = take_screenshot(page, f"details_add_image_click_failed_attempt_{attempt}")
                print(f"[IMAGE] Image upload skipped/failed attempt={attempt}: {exc} screenshot={shot}")
            page.wait_for_timeout(700)

        # Final fallback: direct file input if Programa exposes one.
        try:
            file_inputs = scope.locator("input[type='file']")
            if file_inputs.count() > 0:
                print(f"[IMAGE] Uploading image via file input fallback: {path}")
                file_inputs.first.set_input_files(str(path))
                take_screenshot(page, "details_after_image_file_input_fallback")
                if _image_upload_preview_visible(page, before_count):
                    print("[IMAGE] Image upload successful")
                    return "uploaded", f"uploaded via file input fallback: {path}"
        except Exception as exc:
            last_error = f"{last_error}; file input fallback failed: {exc}" if last_error else f"file input fallback failed: {exc}"
        return "failed", f"image preview not confirmed: {last_error}"
    except Exception as exc:
        shot = take_screenshot(page, "details_add_image_click_failed")
        print(f"[IMAGE] Image upload skipped/failed: {exc} screenshot={shot}")
        return "failed", f"{exc} screenshot={shot}"


def _upload_image_in_editor(page, local_image_path: str) -> tuple[str, str]:
    """
    Upload a local image into the currently-open Programa product editor.

    Works with both the side Details drawer and the full-page product editor
    (Summary tab view). Does not require 'Product Details' text to be visible.
    """
    path_ok, path_msg = _valid_local_image_path(local_image_path)
    if not path_ok:
        print(f"[IMAGE] {path_msg}")
        return "skipped", path_msg

    path = Path(local_image_path)
    before_count = _count_images_in_details_drawer(page)

    # Direct file input — fastest path, try immediately without waiting
    try:
        file_inputs = page.locator("input[type='file']")
        if file_inputs.count() > 0:
            print(f"[IMAGE] Uploading via direct file input: {path}")
            file_inputs.first.set_input_files(str(path))
            deadline = time.time() + 3
            while time.time() < deadline:
                if _image_upload_preview_visible(page, before_count):
                    print("[IMAGE] Image preview detected")
                    return "uploaded", f"via file input: {path}"
                page.wait_for_timeout(200)
    except Exception as exc:
        print(f"[IMAGE] Direct file input path failed: {exc}")

    # Condition-based wait for Add image tile (up to 8 s, 250 ms polling)
    print("[IMAGE] Waiting for Add image tile")
    deadline = time.time() + 8
    add_image_loc = None
    while time.time() < deadline:
        for getter in (
            lambda p: p.get_by_text("Add image", exact=True),
            lambda p: p.get_by_text("Add image", exact=False),
            lambda p: p.get_by_role("button", name="Add image", exact=False),
            lambda p: p.locator("button:has-text('Add image')"),
            lambda p: p.locator("[aria-label*='Add image' i], [title*='Add image' i]"),
        ):
            try:
                loc = getter(page)
                if loc.count() > 0 and loc.first.is_visible(timeout=200):
                    add_image_loc = loc.first
                    break
            except Exception:
                pass
        if add_image_loc is not None:
            break
        page.wait_for_timeout(250)

    if add_image_loc is None:
        shot = take_screenshot(page, "editor_add_image_not_found")
        return "failed", f"Add image tile not found screenshot={shot}"

    print("[IMAGE] Add image tile found")

    last_error = ""
    for attempt in range(1, 3):
        try:
            add_image_loc.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass
        try:
            print("[IMAGE] File chooser triggered")
            print(f"[IMAGE] Uploading image: {path}")
            with page.expect_file_chooser(timeout=7000) as fc_info:
                add_image_loc.click(timeout=3000)
            fc_info.value.set_files(str(path))
            # Condition-based wait for preview
            deadline2 = time.time() + 5
            while time.time() < deadline2:
                if _image_upload_preview_visible(page, before_count):
                    print("[IMAGE] Image preview detected")
                    return "uploaded", f"via click attempt {attempt}: {path}"
                page.wait_for_timeout(200)
            last_error = f"preview not visible after attempt {attempt}"
            print(f"[IMAGE] {last_error}")
        except Exception as exc:
            last_error = str(exc)
            shot = take_screenshot(page, f"editor_image_upload_failed_{attempt}")
            print(f"[IMAGE] Upload attempt {attempt} failed: {exc} screenshot={shot}")
        page.wait_for_timeout(400)

    # Final fallback: direct file input after chooser failed
    try:
        file_inputs = page.locator("input[type='file']")
        if file_inputs.count() > 0:
            print(f"[IMAGE] File input fallback: {path}")
            file_inputs.first.set_input_files(str(path))
            deadline3 = time.time() + 3
            while time.time() < deadline3:
                if _image_upload_preview_visible(page, before_count):
                    print("[IMAGE] Image preview detected")
                    return "uploaded", f"via file input fallback: {path}"
                page.wait_for_timeout(200)
    except Exception as exc:
        last_error = f"{last_error}; file input fallback: {exc}"

    shot = take_screenshot(page, "editor_image_upload_failed")
    return "failed", f"image upload failed: {last_error} screenshot={shot}"


def _find_and_download_product_image(row: dict) -> str | None:
    """
    Search for a product image online and return a local file path.

    Priority:
    1. Existing local_image_path on the row
    2. Existing image_url on the row
    3. Brave Search: brand+model product page → extract main product image
    4. Brave Search: brand+name → fallback
    """
    # Use pre-downloaded image if available
    existing = str(row.get("local_image_path", "") or row.get("Local Image Path", "") or "").strip()
    if existing and Path(existing).exists():
        print(f"[IMAGE] Using existing local image: {existing}")
        return existing

    # Use product_dict-style lookup (image_url already set)
    local = download_product_image(row)
    if local:
        return local

    # Need to search — requires Brave API key
    try:
        from src.brave_search import BRAVE_API_KEY, search_product_candidates
    except ImportError:
        print("[IMAGE] brave_search not available — cannot search for image")
        return None
    if not BRAVE_API_KEY:
        print("[IMAGE] BRAVE_API_KEY not set — cannot search for image")
        return None

    brand = str(row.get("Brand", "") or "").strip()
    model = str(row.get("Model/SKU", "") or "").strip()
    name = str(row.get("Product Name", "") or "").strip()
    product_url = str(row.get("Product URL", "") or row.get("product_url", "") or "").strip()

    # If we already have a product URL, try to extract image from that page
    if product_url:
        try:
            resp = requests.get(product_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            img_url, source = _discover_image_url_from_html(resp.text, product_url)
            if img_url and _image_url_is_usable(img_url):
                fake_row = {**row, "image_url": img_url}
                local = download_product_image(fake_row)
                if local:
                    print(f"[IMAGE] Found image from product URL {product_url}: {img_url}")
                    return local
        except Exception as exc:
            print(f"[IMAGE] Failed to extract image from product_url: {exc}")

    # Build search query: prefer brand+model, fall back to brand+name
    if brand and model:
        query = f"{brand} {model}"
    elif brand and name:
        query = f"{brand} {name}"
    elif model:
        query = model
    elif name:
        query = name
    else:
        print("[IMAGE] Insufficient identifiers to search for product image")
        return None

    print(f"[IMAGE] Searching for product image: {query!r}")
    try:
        results = search_product_candidates(query, brand)
    except Exception as exc:
        print(f"[IMAGE] Brave Search failed: {exc}")
        return None

    if not results:
        print("[IMAGE] No search results — cannot find product image")
        return None

    # Try each result until we find a usable image
    SKIP_IMAGE_DOMAINS = {"shutterstock.com", "istockphoto.com", "gettyimages.com", "alamy.com"}
    for result in results[:5]:
        url = result.url
        if any(d in url for d in SKIP_IMAGE_DOMAINS):
            continue
        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            img_url, source = _discover_image_url_from_html(resp.text, url)
            if img_url and _image_url_is_usable(img_url):
                fake_row = {**row, "image_url": img_url}
                local = download_product_image(fake_row)
                if local:
                    print(f"[IMAGE] Found product image via Brave ({source}): {img_url}")
                    return local
        except Exception as exc:
            print(f"[IMAGE] Failed to extract image from {url}: {exc}")
            continue

    print(f"[IMAGE] No image found for: {query!r}")
    return None


def _editor_is_closed(page, initial_url: str, done_locator=None) -> bool:
    """
    Return True when the product editor / Details drawer appears to be gone.

    Three independent signals — any one is sufficient:
    1. Drawer selector no longer matches (original behavior)
    2. Page URL changed (full-page editor navigated away)
    3. The Done button we just clicked is no longer visible
    """
    if not _details_drawer_is_open(page):
        return True
    try:
        if page.url != initial_url:
            return True
    except Exception:
        pass
    if done_locator is not None:
        try:
            if not done_locator.is_visible(timeout=100):
                return True
        except Exception:
            return True  # locator detached → editor gone
    return False


def _click_done_and_wait_drawer_close(page) -> tuple[bool, str]:
    print("[DETAILS] Attempting Done click")
    initial_url = page.url

    # ── Fast-path: editor already closed ─────────────────────────────────────
    if not _details_drawer_is_open(page):
        print("[DETAILS] Details panel already closed")
        print("[DETAILS] List view detected")
        print("[SCH Automation] Editor closed")
        return True, "editor already closed (list view)"

    # ── Pre-click stabilization ───────────────────────────────────────────────
    # Blur any focused input so validation doesn't block the Done button.
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
    except Exception:
        pass
    try:
        page.keyboard.press("Tab")
        page.wait_for_timeout(150)
    except Exception:
        pass

    # ── Build candidate list ──────────────────────────────────────────────────
    def _done_candidates():
        drawer = _get_details_drawer(page)
        scope = drawer if drawer is not None else page
        candidates: list[tuple[object, str]] = []
        selectors = [
            ("role_exact",   lambda r: r.get_by_role("button", name="Done", exact=True)),
            ("role_fuzzy",   lambda r: r.get_by_role("button", name="Done", exact=False)),
            ("button_text",  lambda r: r.locator("button:has-text('Done')")),
            ("button_xpath", lambda r: r.locator(
                "xpath=.//button[normalize-space()='Done' or .//*[normalize-space()='Done']]"
            )),
            ("text_exact",   lambda r: r.get_by_text("Done", exact=True)),
            ("aria_label",   lambda r: r.locator("[aria-label='Done' i]")),
        ]
        print(f"[DETAILS] Searching for Done — drawer_scoped={drawer is not None}")
        for label, getter in selectors:
            for root, root_label in ((scope, "scoped"), (page, "page")):
                try:
                    loc = getter(root)
                    for i in range(min(loc.count(), 4)):
                        candidates.append((loc.nth(i), f"{root_label}::{label}[{i}]"))
                except Exception:
                    pass
        return candidates

    last_error = ""
    for pass_num in range(1, 4):
        if pass_num > 1:
            print(f"[DETAILS] Done not confirmed; scroll + retry pass={pass_num}")
            _scroll_details_drawer_to_bottom(page)
            page.wait_for_timeout(300)

        candidates = _done_candidates()
        if not candidates:
            last_error = f"no Done candidates found pass={pass_num}"
            print(f"[DETAILS] {last_error}")
            page.wait_for_timeout(400)
            continue

        for locator, desc in candidates:
            try:
                locator.wait_for(state="visible", timeout=2000)
            except Exception:
                continue
            # Skip disabled buttons; wait briefly and retry on next pass
            try:
                if not locator.is_enabled(timeout=500):
                    last_error = f"{desc} visible but disabled"
                    print(f"[DETAILS] {last_error}")
                    continue
            except Exception:
                pass
            try:
                locator.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass

            print(f"[DETAILS] Done button found — attempt {pass_num} via {desc}")
            # Try normal click → force click → JS click
            clicked = False
            for click_fn, click_label in (
                (lambda: locator.click(timeout=3000),         "click"),
                (lambda: locator.click(timeout=3000, force=True), "force"),
                (lambda: locator.evaluate("(el) => el.click()"),  "js"),
            ):
                try:
                    click_fn()
                    clicked = True
                    break
                except Exception as exc:
                    last_error = f"{click_label} on {desc}: {exc}"
                    print(f"[DETAILS] Done {click_label} failed: {exc}")

            if not clicked:
                continue

            # Wait for editor to close using three independent signals
            close_deadline = time.time() + 10
            while time.time() < close_deadline:
                if _editor_is_closed(page, initial_url, locator):
                    print(f"[DETAILS] Done click confirmed — {desc}")
                    print("[SCH Automation] Done clicked")
                    print("[SCH Automation] Editor closed")
                    return True, f"Done via {desc}; editor closed"
                page.wait_for_timeout(250)

            last_error = f"{desc} clicked but editor did not close within 10 s"
            print(f"[DETAILS] {last_error}")
            # Continue to next candidate / pass rather than giving up immediately

    # ── Keyboard fallback: Enter if focus is on Done ──────────────────────────
    try:
        focused_done = bool(page.evaluate("""() => {
            const el = document.activeElement;
            if (!el) return false;
            const text = (el.textContent || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase();
            return text === 'done' || (el.tagName === 'BUTTON' && text.includes('done'));
        }"""))
        if focused_done:
            print("[DETAILS] Pressing Enter (focused on Done)")
            page.keyboard.press("Enter")
            deadline = time.time() + 5
            while time.time() < deadline:
                if _editor_is_closed(page, initial_url):
                    print("[SCH Automation] Done clicked")
                    print("[SCH Automation] Editor closed")
                    return True, "Done via keyboard Enter; editor closed"
                page.wait_for_timeout(250)
    except Exception as exc:
        last_error = f"{last_error}; Enter fallback: {exc}"

    # ── Diagnostic dump on final failure ─────────────────────────────────────
    try:
        visible_btns = page.locator("button:visible").all_inner_texts()[:10]
        print(f"[DETAILS] Done failed — visible buttons: {visible_btns}")
        disabled = [
            t for t, loc in (
                (t, page.get_by_role("button", name=t))
                for t in ["Done", "Save", "Submit"]
            )
            if loc.count() > 0 and not loc.first.is_enabled(timeout=200)
        ]
        if disabled:
            print(f"[DETAILS] Disabled buttons: {disabled}")
    except Exception:
        pass

    return False, f"Done button not clicked: {last_error or 'no candidates found'}"


def fill_details_drawer(page, row_data: dict, upload_product_images: bool = True) -> dict:
    """
    Fill Programa's open Details drawer/modal and click Done.

    Returns a per-field result dict. Product Name failure is a hard stop; other
    field failures are logged and the helper continues when safe.
    """
    results: dict[str, str] = {}
    if not _details_drawer_is_open(page):
        results["details_opened"] = "failed: drawer/modal is not open"
        results["failure_step"] = "drawer_not_open"
        results["screenshot"] = take_screenshot(page, "details_drawer_not_open")
        _dump_details_drawer_diagnostics(page, "drawer_not_open")
        return results

    results["details_opened"] = "ok"
    _log_details_panel_inputs(page)
    _dump_details_drawer_diagnostics(page, "drawer_opened")

    if upload_product_images:
        print("[IMAGE] Image upload enabled")
        status, detail = upload_product_image_to_details_panel(page, row_data)
        results["Image Upload"] = f"{status}: {detail}"
        row_data["image_upload_status"] = status
        if status == "uploaded":
            print("[IMAGE] Uploaded image successfully")
        else:
            print(f"[IMAGE] Image upload skipped/failed: {detail}")
    else:
        results["Image Upload"] = "skipped: disabled"
        row_data["image_upload_status"] = "skipped: disabled"
        print("[IMAGE] Image upload skipped: disabled")

    top_fields: list[tuple[str, list[str], str, bool]] = [
        ("Product Name", SCHEDULE_FIELD_LABELS.get("Product Name", ["Product name", "Product Name"]), row_data.get("Product Name", ""), True),
        ("Description", ["Product description", "Product details", "Description"], row_data.get("Description", ""), False),
        ("Doc Code", ["Doc code", "Doc Code"], row_data.get("Doc Code", ""), False),
        ("Product Details", ["Product details", "Product Details"], row_data.get("Product Details", ""), False),
        ("Quantity", SCHEDULE_FIELD_LABELS.get("Quantity", ["Qty", "Quantity"]), row_data.get("Quantity", ""), False),
        ("Brand", SCHEDULE_FIELD_LABELS.get("Brand", ["Brand"]), row_data.get("Brand", ""), False),
        ("SKU", SCHEDULE_FIELD_LABELS.get("Model/SKU", ["SKU", "Model/SKU", "Model", "Serial", "Serial / Model Number"]), row_data.get("Model/SKU", ""), False),
        ("Lead Time", ["Lead time", "Lead Time"], row_data.get("Lead Time", ""), False),
        ("Product URL", SCHEDULE_FIELD_LABELS.get("Product URL", ["Product url", "Product URL", "Product link", "URL"]), row_data.get("Product URL", ""), False),
        ("Price", SCHEDULE_FIELD_LABELS.get("Price", ["Price", "Cost"]), row_data.get("Price", ""), False),
    ]

    for field_key, labels, value, required in top_fields:
        value = str(value or "").strip()
        if not value:
            results[field_key] = "skipped (blank)"
            continue
        ok, desc = _fill_field_in_details_panel(page, labels, value)
        results[field_key] = "ok" if ok else f"failed: {desc}"
        print(f"[DETAILS] {field_key} filled={ok} — {desc}")
        if required and not ok:
            if "field not found" in desc:
                results["failure_step"] = "product_name_input_not_found"
            else:
                results["failure_step"] = "product_name_fill_failed"
            results["screenshot"] = take_screenshot(page, "details_product_name_failed")
            results["hard_stop"] = f"{results['failure_step']}; drawer left open for inspection"
            _dump_details_drawer_diagnostics(page, results["failure_step"])
            return results

    # Guard: stop filling if the editor closed during the field-fill loop above
    if not _details_drawer_is_open(page):
        print("[DETAILS] Details panel closed during top-field fill — List view detected")
        done_ok, done_desc = _click_done_and_wait_drawer_close(page)
        results["Done"] = "ok" if done_ok else f"failed: {done_desc}"
        results["drawer_closed"] = "yes" if done_ok else "no"
        results.setdefault("early_exit", "editor_closed_after_top_fields")
        return results

    supplier = str(row_data.get("Supplier", "") or "").strip()
    if supplier:
        ok, desc = _select_dropdown_in_details_panel(page, ["Supplier", "Who Bought From", "Vendor"], supplier)
        if not ok:
            ok, desc = _fill_field_in_details_panel(page, ["Supplier", "Who Bought From", "Vendor"], supplier)
        results["Supplier"] = "ok" if ok else f"skipped: {desc}"
        print(f"[DETAILS] Supplier attempted={ok} — {desc}")
    else:
        results["Supplier"] = "skipped (blank)"

    # Guard: stop filling if the editor closed during supplier fill
    if not _details_drawer_is_open(page):
        print("[DETAILS] Details panel closed before spec fields — List view detected")
        done_ok, done_desc = _click_done_and_wait_drawer_close(page)
        results["Done"] = "ok" if done_ok else f"failed: {done_desc}"
        results["drawer_closed"] = "yes" if done_ok else "no"
        results.setdefault("early_exit", "editor_closed_before_specs")
        return results

    specs_visible = _scroll_details_drawer_to_text(page, ["Product Specifications", "Height", "Depth", "Width"])
    results["scroll_specs"] = "ok" if specs_visible else "not visible after scroll"

    finish, color = _finish_color_values(
        str(row_data.get("Finish / Color", "") or row_data.get("Finish", "") or "")
    )
    spec_fields: list[tuple[str, list[str], str]] = [
        ("Height", SCHEDULE_FIELD_LABELS.get("H", ["Height", "H (in)", "H"]), row_data.get("H", "")),
        ("Depth", SCHEDULE_FIELD_LABELS.get("D", ["Depth", "D (in)", "D"]), row_data.get("D", "")),
        ("Width", SCHEDULE_FIELD_LABELS.get("W", ["Width", "W (in)", "W"]), row_data.get("W", "")),
        ("Length", SCHEDULE_FIELD_LABELS.get("L", ["Length", "L (in)", "L"]), row_data.get("L", "")),
        ("Color", SCHEDULE_FIELD_LABELS.get("Color", ["Color", "Colour"]), color),
        ("Finish", SCHEDULE_FIELD_LABELS.get("Finish", ["Finish"]), finish),
        ("Material", SCHEDULE_FIELD_LABELS.get("Material", ["Material", "Materials"]), row_data.get("Material", "")),
    ]
    for field_key, labels, value in spec_fields:
        value = str(value or "").strip()
        if not value:
            results[field_key] = "skipped (blank)"
            continue
        ok, desc = _fill_field_in_details_panel(page, labels, value)
        results[field_key] = "ok" if ok else f"failed: {desc}"
        print(f"[DETAILS] {field_key} filled={ok} — {desc}")

    category = str(row_data.get("Category", "") or "").strip()
    if category:
        ok, desc = _select_dropdown_in_details_panel(
            page,
            ["Product category", "Product Category", "Select category", "Category"],
            category,
        )
        results["Category"] = "ok" if ok else f"skipped: {desc}"
        print(f"[DETAILS] Category attempted={ok} — {desc}")
    else:
        results["Category"] = "skipped (blank)"

    # Guard: stop filling if the editor closed during spec/category fills
    if not _details_drawer_is_open(page):
        print("[DETAILS] Details panel closed before notes — List view detected")
        done_ok, done_desc = _click_done_and_wait_drawer_close(page)
        results["Done"] = "ok" if done_ok else f"failed: {done_desc}"
        results["drawer_closed"] = "yes" if done_ok else "no"
        results.setdefault("early_exit", "editor_closed_before_notes")
        return results

    notes_visible = _scroll_details_drawer_to_text(page, ["Important information", "Notes", "Internal notes"])
    results["scroll_notes"] = "ok" if notes_visible else "not visible after scroll"

    notes = str(row_data.get("Notes", "") or "").strip()
    note_fields = [
        ("Important Information", ["Important information", "Add any important information here..."], notes),
        ("Notes", ["Notes", "Add any additional notes here..."], notes),
        ("Internal Notes", ["Internal notes", "Add any internal notes here..."], row_data.get("Internal Notes", "")),
    ]
    for field_key, labels, value in note_fields:
        value = str(value or "").strip()
        if not value:
            results[field_key] = "skipped (blank)"
            continue
        ok, desc = _fill_field_in_details_panel(page, labels, value)
        results[field_key] = "ok" if ok else f"failed: {desc}"
        print(f"[DETAILS] {field_key} filled={ok} — {desc}")

    done_ok, done_desc = _click_done_and_wait_drawer_close(page)
    results["Done"] = "ok" if done_ok else f"failed: {done_desc}"
    results["drawer_closed"] = "yes" if done_ok else "no"
    if not done_ok:
        results["failure_step"] = "done_button_failed"
        results["screenshot"] = take_screenshot(page, "details_done_failed")
        _dump_details_drawer_diagnostics(page, "done_button_failed")
    return results


def _save_close_details_panel(page) -> bool:
    """Click Save/Apply/Done inside the open panel, or press Escape. Returns True if a button was found."""
    for name in ("Save", "Apply", "Done", "OK", "Update", "Submit", "Close"):
        try:
            btn = page.get_by_role("button", name=name)
            if btn.count() > 0 and btn.first.is_visible(timeout=400):
                btn.first.click(timeout=2000)
                page.wait_for_timeout(800)
                print(f"[DETAILS] Panel closed via button {name!r}")
                return True
        except Exception:
            pass
    # Fallback: Escape
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
        print("[DETAILS] Panel closed via Escape")
    except Exception:
        pass
    return False


def _fill_row_via_details_panel(
    page,
    row_locator,
    field_values: dict,
    product_name: str,
) -> tuple[bool, dict]:
    """
    Primary entry path: open the Details drawer and fill fields inside it only.
    """
    print("[DETAILS] ══ BEGIN _fill_row_via_details_panel ══")
    opened, open_desc = _open_details_panel(page, row_locator)
    if not opened:
        print(f"[DETAILS] Failed — reason: {open_desc}")
        failure_step = open_desc.split("|", 1)[0].strip() or "drawer_not_open"
        return False, {
            "details_panel": f"failed: {open_desc}",
            "failure_step": failure_step,
        }

    shot = take_screenshot(page, "details_panel_opened")
    print(f"[DETAILS] Panel opened. screenshot={shot}")

    field_results = fill_details_drawer(page, field_values)
    field_results["details_button"] = open_desc
    product_name_status = field_results.get("Product Name", "")
    done_status = field_results.get("Done", "")
    drawer_closed = field_results.get("drawer_closed") == "yes"
    pn_ok = (
        str(field_values.get("Product Name", "") or "").strip() == ""
        or product_name_status == "ok"
    )
    ok = pn_ok and done_status == "ok" and drawer_closed
    print(f"[DETAILS] ══ END _fill_row_via_details_panel — ok={ok} results={field_results} ══")
    return ok, field_results


def open_new_product_details(page, section_name: str, product_name: str, index: int = 1, total: int = 1):
    """
    Create a custom product inside section_name and open its Details panel.

    Product data is not typed here. This function only creates the blank product
    row/card, clicks that row's Details button, and verifies the side panel.
    """
    cp_ok, cp_reason, row_locator, before, after, shot = _create_custom_product_in_section(
        page, section_name, index, total
    )
    if not cp_ok:
        return False, cp_reason, row_locator, before, after, shot

    print(f"[SCH Automation] Opening Details for product: {product_name!r}")
    opened, open_desc = _open_details_panel(page, row_locator)
    if not opened:
        shot = take_screenshot(page, f"details_open_failed_{_normalise_section_name(section_name)[:24]}")
        return False, f"details panel did not open for new product row: {open_desc}", row_locator, before, after, shot

    drawer = _get_details_drawer(page)
    panel_text = ""
    try:
        panel_text = (drawer.inner_text(timeout=1500) if drawer is not None else "") or ""
    except Exception:
        panel_text = ""
    if "product details" not in panel_text.lower() and "product name" not in panel_text.lower():
        shot = take_screenshot(page, f"details_panel_unconfirmed_{_normalise_section_name(section_name)[:24]}")
        return False, "details panel opened but Product Details/Product name text was not confirmed", row_locator, before, after, shot

    shot_open = take_screenshot(page, f"details_panel_open_{_normalise_section_name(section_name)[:24]}")
    print(f"[SCH Automation] Opened Details panel for new product row: {product_name!r} screenshot={shot_open}")
    return True, f"{cp_reason}; details opened via {open_desc}", row_locator, before, after, shot_open


def fill_details_panel(page, product: dict, upload_product_images: bool | None = None) -> dict:
    """Fill product fields inside the already-open Programa Details panel only."""
    if upload_product_images is None:
        upload_product_images = bool(product.get("upload_product_images", True))
    return fill_details_drawer(page, product, upload_product_images=upload_product_images)


def _process_schedule_row(
    page, row: dict, auto_done: bool, index: int, total: int,
    skip_nav: bool = False, target_section: str = "",
    upload_product_images: bool = True,
    photo_index: int | None = None,
    photo_total: int | None = None,
) -> dict:
    """
    Enter a non-URL row via the target section's inline Custom Product button.

    Runs inside an already-open, already-logged-in, already-project-navigated browser.

    Flow:
      1. Open the Schedule file (unless skip_nav=True)
      2. Navigate to target section
      3. Click the inline Custom Product button inside the target section
      4. Identify the newest row/card
      5. Open Details
      6. Fill fields inside the Details drawer only
      7. Click Done and wait for the drawer to close
    """
    print("[PROCESS_SCHEDULE_ROW] section-inline Custom Product version active")

    product_name = str(row.get("Product Name", "") or row.get("product_name", "") or row.get("name", "") or "").strip()
    short_name = (product_name[:40] + "…") if len(product_name) > 40 else product_name

    # Extract Material from Notes [Materials: …] tag if present
    notes_raw = remove_notes_row_prefix(row.get("Notes", ""))
    _mat_match = re.search(r'\[Materials:\s*([^\]]+)\]', notes_raw)
    material_val = _mat_match.group(1).strip() if _mat_match else ""
    clean_description, clean_product_details, notes_for_details = _clean_description_and_notes(row, notes_raw)

    # Parse combined dimensions string into separate W / H / D / L values
    dims = parse_dimensions_for_programa(str(row.get("Dimensions", "") or ""))
    width_val = str(row.get("width_in", "") or row.get("Width", "") or dims["width"]).strip()
    height_val = str(row.get("height_in", "") or row.get("Height", "") or dims["height"]).strip()
    depth_val = str(row.get("depth_in", "") or row.get("Depth", "") or dims["depth"]).strip()
    length_val = str(row.get("length_in", "") or row.get("Length", "") or dims["length"]).strip()

    field_values: dict[str, str] = {
        "Product Name": product_name or str(row.get("product_name", "") or row.get("name", "") or "").strip(),
        "Description":  clean_description,
        "Product Details": clean_product_details,
        "Doc Code":     str(row.get("doc_code", "") or row.get("Doc Code", "") or row.get("Document Code", "") or "").strip(),
        "Brand":        str(row.get("Brand", "") or row.get("brand", "") or "").strip(),
        "W":            width_val,
        "H":            height_val,
        "D":            depth_val,
        "L":            length_val,
        "Quantity":     str(row.get("quantity", "") or row.get("Quantity", "") or "").strip(),
        "Supplier":     str(row.get("supplier", "") or row.get("Supplier", "") or "").strip(),
        "Room":         str(row.get("Room", "") or "").strip(),
        "Color":        str(row.get("color", "") or row.get("Color", "") or row.get("Finish / Color", "") or "").strip(),
        "Finish":       str(row.get("finish", "") or row.get("Finish", "") or row.get("Finish / Color", "") or "").strip(),
        "Material":     str(row.get("material", "") or row.get("Material", "") or material_val).strip(),
        "Category":     str(row.get("category", "") or row.get("Product Category", "") or "").strip(),
        "Model/SKU":    str(row.get("sku", "") or row.get("code", "") or row.get("model", "") or row.get("Serial / Model Number", "") or row.get("Model/SKU", "") or "").strip(),
        "Lead Time":    str(row.get("lead_time", "") or row.get("Lead Time", "") or "").strip(),
        "Product URL":  str(row.get("product_url", "") or row.get("Product URL", "") or "").strip(),
        "Price":        str(row.get("price", "") or row.get("Price", "") or "").strip(),
        "Notes":        notes_for_details,
        "Internal Notes": str(row.get("Internal Notes", "") or "").strip(),
        "image_url":    str(row.get("image_url", "") or row.get("Image URL", "") or row.get("Product Image URL", "") or "").strip(),
        "product_url":  str(row.get("product_url", "") or row.get("Product URL", "") or "").strip(),
        "local_image_path": str(row.get("local_image_path", "") or row.get("Local Image Path", "") or "").strip(),
        "image_upload_status": str(row.get("image_upload_status", "") or row.get("Image Upload Status", "") or "").strip(),
        "upload_product_images": upload_product_images,
    }
    # Aliases used by the inline-row fill pass.
    field_values["Color"] = str(row.get("Color", "") or "").strip() or field_values["Color"]
    field_values["Finish"] = str(row.get("Finish", "") or "").strip() or field_values["Finish"]

    field_log: dict[str, str] = {}  # log_label → "ok" | "skipped" | "failed: …"
    print(
        f"[SCH Automation] ── _process_schedule_row START ──"
        f" row={index}/{total} product={product_name!r} section={target_section!r}"
    )

    # ── Step 1: open the Schedule file ────────────────────────────────────────
    if skip_nav:
        nav_ok, nav_method = True, "already_open"
        field_log["schedule_opened"] = "already_open"
        print(f"[SCH Automation] schedule opened: already_open")
    else:
        nav_ok, nav_method = _open_schedule_file(page, index, total)
        field_log["schedule_opened"] = nav_method
        print(f"[SCH Automation] schedule opened: {nav_method}")

    # ── Step 1b: navigate to target section ───────────────────────────────────
    if target_section:
        print(f"[SCH Automation] navigating to section: {target_section!r}")
        found = _navigate_to_section(page, target_section)
        nav_status = "found+scrolled" if found else "NOT FOUND in DOM (proceeding)"
        field_log["section"] = target_section if found else f"{target_section} (not found in DOM)"
        print(f"[SCH Automation] section nav: {nav_status}")
        page.wait_for_timeout(500)

    # ── Step 1c: HTTP fast-path (bypasses UI product creation) ────────────────
    if target_section and _row_is_photo_only(row):
        image_i = photo_index or index
        image_total = photo_total or total
        local_image = _local_image_path_for_row(row)
        print(f"[SCH Automation] Image {image_i}/{image_total}: creating item")
        try:
            from src.programa_api import (
                ProgramaAPIClient,
                extract_section_id,
                extract_session,
            )
            if not local_image or not Path(local_image).exists():
                return make_log_entry(
                    "",
                    "error",
                    f"Image {image_i}/{image_total}: local image not found: {local_image or '(blank)'}",
                    product_name=product_name,
                )
            api_session = extract_session(page)
            if not api_session:
                return make_log_entry(
                    "",
                    "error",
                    f"Image {image_i}/{image_total}: Programa API session could not be extracted",
                    product_name=product_name,
                )
            api_section_id = extract_section_id(page, target_section)
            if not api_section_id:
                return make_log_entry(
                    "",
                    "error",
                    f"Image {image_i}/{image_total}: section id not found for {target_section!r}",
                    product_name=product_name,
                )
            client = ProgramaAPIClient(api_session)
            item_id = client.create_item(api_section_id)
            if not item_id:
                return make_log_entry(
                    "",
                    "error",
                    f"Image {image_i}/{image_total}: item creation failed",
                    product_name=product_name,
                )
            signed_id = client.direct_upload_image(Path(local_image))
            if not signed_id:
                return make_log_entry(
                    "",
                    "error",
                    f"Image {image_i}/{image_total}: direct upload failed",
                    product_name=product_name,
                )
            print(f"[SCH Automation] Image {image_i}/{image_total}: direct upload created")
            print(f"[SCH Automation] Image {image_i}/{image_total}: S3 upload complete")
            update_ok = client.update_item(item_id, {}, signed_id=signed_id)
            if not update_ok:
                return make_log_entry(
                    "",
                    "error",
                    f"Image {image_i}/{image_total}: item image patch failed item_id={item_id}",
                    product_name=product_name,
                )
            print(f"[SCH Automation] Image {image_i}/{image_total}: item patched with image")
            print(f"[SCH Automation] Image {image_i}/{image_total} complete")
            return make_log_entry(
                "",
                "success",
                f"Photo-only API upload complete item_id={item_id}",
                product_name=product_name,
            )
        except Exception as exc:
            return make_log_entry(
                "",
                "error",
                f"Image {image_i}/{image_total}: API photo upload exception: {exc}",
                product_name=product_name,
            )
    elif target_section:
        print(f"[API] Fast-path attempting for product {product_name!r}")
        _api_t0 = time.monotonic()
        try:
            from src.programa_api import (
                extract_session,
                extract_section_id,
                ProgramaAPIClient,
                row_to_api_fields,
            )
            api_session = extract_session(page)
            if api_session:
                api_section_id = extract_section_id(page, target_section)
                if api_section_id:
                    local_img = field_values.get("local_image_path", "")
                    if not local_img and upload_product_images:
                        found_img = _find_and_download_product_image(field_values)
                        if found_img:
                            local_img = found_img
                    api_fields = row_to_api_fields(field_values)
                    api_result = ProgramaAPIClient(api_session).create_and_fill_item(
                        api_section_id, api_fields, local_img or None
                    )
                    if api_result.get("ok"):
                        _api_elapsed = round(time.monotonic() - _api_t0, 1)
                        field_log["api_fast_path"] = (
                            f"ok item={api_result.get('item_id')} "
                            f"image={api_result.get('image_status')} "
                            f"update={api_result.get('update_ok')} "
                            f"elapsed={_api_elapsed}s"
                        )
                        print(
                            f"[API] Fast-path success — item {api_result['item_id']} "
                            f"created, fields patched, image: {api_result['image_status']} "
                            f"({_api_elapsed}s)"
                        )
                        print(f"[SCH Automation] Completed item {index} of {total} via HTTP fast-path")
                        _entry = make_log_entry("", "success", "HTTP fast-path", product_name=product_name)
                        _entry["_path"] = "fast_path"
                        _entry["_elapsed_s"] = _api_elapsed
                        return _entry
                    else:
                        err = api_result.get("error", "unknown")
                        field_log["api_fast_path"] = f"failed: {err}"
                        print(f"[API] Fast-path failed: {err} — falling back to UI automation")
                else:
                    field_log["api_fast_path"] = "skipped: section_id_not_found"
                    print(f"[API] section_id_not_found for {target_section!r} — falling back to UI automation")
            else:
                field_log["api_fast_path"] = "skipped: session_extraction_failed"
                print("[API] session_extraction_failed — falling back to UI automation")
        except Exception as _api_exc:
            field_log["api_fast_path"] = f"exception: {_api_exc}"
            print(f"[API] Fast-path exception: {_api_exc} — falling back to UI automation")

    # ── Step 2: Section inline Custom Product → newest blank row/card → Details
    if target_section:
        cp_ok, cp_reason, row_locator, row_count_before, row_count_after, section_shot = open_new_product_details(
            page, target_section, product_name, index, total
        )
        field_log["custom_product"] = f"{cp_reason} (section rows {row_count_before}→{row_count_after})"
        field_log["custom_product_row_created"] = "yes" if cp_ok else "no"
        field_log["details_opened"] = "yes" if cp_ok else "no"
        if not cp_ok:
            print(f"[SCH Automation] STOP row={index} — inline section Custom Product / Details open failed")
            return make_log_entry(
                "", "error",
                f"Inline section Custom Product / Details step failed: {cp_reason}",
                section_shot,
                product_name=product_name,
            )
    else:
        row_count_before = _count_product_rows(page)
        print(f"[SCH Automation] row count BEFORE Custom Product: {row_count_before}")
        cp_ok, cp_reason = _create_custom_product_row(page, index, total)
        row_count_after = _count_product_rows(page)
        print(
            f"[SCH Automation] row count AFTER Custom Product: {row_count_after} "
            f"(delta={row_count_after - row_count_before}) — {cp_reason}"
        )
        field_log["custom_product"] = f"{cp_reason} (rows {row_count_before}→{row_count_after})"
        field_log["custom_product_row_created"] = "yes" if cp_ok else "no"

        if not cp_ok:
            shot = take_screenshot(page, f"custom_product_failed_{index}")
            print(f"[SCH Automation] STOP row={index} — not filling fields")
            return make_log_entry(
                "", "error",
                f"Custom Product step failed: {cp_reason}",
                shot,
                product_name=product_name,
            )

        row_locator = _find_new_row(page, before_count=row_count_before)

    if row_locator is None:
        field_log["row_locator"] = "not_found"
        print("[SCH Automation] WARNING: new row locator not found — Details targeting cannot be row-scoped")
        take_screenshot(page, f"row_locator_not_found_{index}")
    else:
        try:
            bbox = row_locator.bounding_box()
            field_log["row_locator"] = f"ok bbox={bbox}"
            print(f"[SCH Automation] newest row found — bbox={bbox}")
        except Exception:
            field_log["row_locator"] = "ok (no bbox)"
            print("[SCH Automation] newest row found (bbox unavailable)")

    print("[IMAGE] Product editor opened")

    # ── Step 3a: resolve image path (for all rows) ────────────────────────────
    is_photo_only = _row_is_photo_only(row)
    local_image = field_values.get("local_image_path", "")
    img_status, img_desc = "skipped", "no local_image_path"

    if upload_product_images:
        if not local_image and not is_photo_only:
            # Normal row: try to find an image online if none was pre-downloaded
            found_path = _find_and_download_product_image(field_values)
            if found_path:
                local_image = found_path
                field_values["local_image_path"] = found_path
                field_values["upload_product_images"] = False  # we'll upload below

        if local_image:
            img_status, img_desc = _upload_image_in_editor(page, local_image)
            field_log["Image Upload"] = f"{img_status}: {img_desc}"
            # Prevent double-upload inside fill_details_panel
            field_values["upload_product_images"] = False
        else:
            field_log["Image Upload"] = "skipped: no image available"
            if not is_photo_only:
                print("[IMAGE] No image found for this product — leaving blank")

    # ── Photo-only fast path: skip all field filling ──────────────────────────
    if is_photo_only:
        print(f"[SCH Automation] Photo-only row — skipping field fill")
        if img_status == "failed":
            print(f"[IMAGE] Product editor opened but image upload step failed: {img_desc}")
        _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Photo-only upload…")
        done_ok, done_desc = _click_done_and_wait_drawer_close(page)
        field_log["Done"] = "ok" if done_ok else f"failed: {done_desc}"
        _remove_banner(page)
        if done_ok:
            print(f"[SCH Automation] Completed item {index} of {total}")
            print(f"[SCH Automation] Editor closed")
        else:
            print(f"[SCH Automation] Done click failed for item {index}: {done_desc}")
        if index < total:
            print(f"[SCH Automation] Moving to item {index + 1} of {total}")
        else:
            print(f"[SCH Automation] All photo-only items completed")
        return make_log_entry(
            "",
            "success" if done_ok else "warn",
            f"Photo-only: image={img_status} done={done_ok} | steps: {field_log}",
            product_name=product_name,
        )

    # ── Step 3: fill product fields inside Details panel only ────────────────
    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  {short_name}  ·  Filling Details panel…")
    print(f"[DETAILS] ════════════════════════════════════════════════")
    print(f"[DETAILS] Starting Details-panel-only data entry")
    print(f"[DETAILS] row={index} product={product_name!r}")
    print(f"[DETAILS] row_locator={'found' if row_locator is not None else 'None'}")
    print(f"[DETAILS] ════════════════════════════════════════════════")

    details_field_results = fill_details_panel(page, field_values)
    details_ok = (
        (not field_values.get("Product Name") or details_field_results.get("Product Name") == "ok")
        and details_field_results.get("Done") == "ok"
        and details_field_results.get("drawer_closed") == "yes"
    )
    field_log.update({k: v for k, v in details_field_results.items()})

    if details_ok:
        print(f"[DETAILS] ✓ Details-drawer fill succeeded for row={index}")
        take_screenshot(page, f"details_fields_filled_{index}")
        _remove_banner(page)
        print(f"[SCH Automation] Completed product {index} of {total}")
        print(f"[SCH Automation] Editor closed")
        if index < total:
            print(f"[SCH Automation] Returning to section view")
            print(f"[SCH Automation] Starting product {index + 1} of {total}")
        else:
            print(f"[SCH Automation] All products completed")
        return make_log_entry(
            "",
            "success",
            f"Saved through Details drawer: {short_name} | steps: {field_log}",
            product_name=product_name,
        )

    # ── Failure handling ──────────────────────────────────────────────────────
    failure_step = str(details_field_results.get("failure_step") or "details_drawer_entry_failed")
    shot = details_field_results.get("screenshot") or take_screenshot(page, f"details_drawer_failed_{index}")
    print(f"[DETAILS] ✗ Details-drawer fill failed for row={index}: {failure_step}")

    if failure_step == "done_button_failed":
        # Done click failed — log, try to dismiss the editor, continue to next product
        print(f"[SCH Automation] Done failed for item {index} — dismissing editor, continuing")
        _remove_banner(page)
        # Attempt to close the editor gracefully before the next iteration
        for dismiss in (
            lambda: page.keyboard.press("Escape"),
            lambda: page.keyboard.press("Escape"),
        ):
            try:
                dismiss()
                page.wait_for_timeout(300)
                if not _details_drawer_is_open(page):
                    break
            except Exception:
                pass
        if index < total:
            print(f"[SCH Automation] Moving to item {index + 1} of {total}")
        return make_log_entry(
            "",
            "warn",
            f"Done button failed — product may be partially saved: {short_name} | {field_log}",
            shot,
            product_name=product_name,
        )

    # Fatal failures: block and let user inspect
    if failure_step == "details_button_not_found" and target_section:
        heading = _find_section_heading(page, target_section)
        if heading is not None:
            shot = _dump_target_section(page, heading, target_section, "details_button_not_found_section")
    _remove_banner(page)
    _inject_banner(
        page,
        f"SCH DesignOps  ·  FAILED: {failure_step} — browser paused for inspection.",
    )
    _js_confirm(
        page,
        f"SCH DesignOps — Details Drawer Entry Failed\n\n"
        f"Product: {product_name}\n"
        f"Section: {target_section or '(none)'}\n"
        f"Failure step: {failure_step}\n"
        f"Screenshot: {shot}\n\n"
        "The drawer has been left open where possible. Click OK when done reviewing.",
    )
    _remove_banner(page)
    return make_log_entry(
        "",
        "error",
        f"{failure_step} | product={product_name!r} section={target_section!r} steps: {field_log}",
        shot,
        product_name=product_name,
    )


# ── Debug / single-row helpers ────────────────────────────────────────────────


def _value_visible_in_row(page, row_locator, value: str) -> bool:
    """Return True when value text appears inside the row (or anywhere on the page)."""
    needle = value.strip().lower()
    if not needle:
        return True
    # Try within the row first (fastest, most precise)
    if row_locator is not None:
        try:
            text = row_locator.inner_text(timeout=1500)
            if needle in text.lower():
                return True
        except Exception:
            pass
    # Fallback: page-wide text search
    try:
        return page.get_by_text(value.strip(), exact=False).count() > 0
    except Exception:
        return False


def _find_new_row(page, before_count: int = -1):
    """
    Return a Locator for the newly created schedule row, or None.

    When before_count >= 0, uses it as the 0-based index of the new row
    (Programa appends rows, so new row index == before_count).
    Falls back to the last row when before_count is unknown.
    """
    row_selectors = [
        "tbody tr",
        "[role='grid'] [role='row']",
        "[role='table'] [role='row']",
        "[role='row']",
    ]
    for sel in row_selectors:
        try:
            all_rows = page.locator(sel)
            n = all_rows.count()
            if n == 0:
                continue
            if 0 <= before_count < n:
                loc = all_rows.nth(before_count)  # new row is at index == old count
            else:
                loc = all_rows.last
            if loc.is_visible(timeout=600):
                print(f"[SCH Automation] new row found via '{sel}' at index {before_count if 0 <= before_count < n else 'last'}")
                return loc
        except Exception:
            pass
    return None


def fill_inline_programa_field(
    page,
    row_locator,
    field_label: str,
    value: str,
) -> tuple[bool, str]:
    """
    Fill one inline field in a Programa schedule row.

    Parameters
    ----------
    page        : Playwright page object.
    row_locator : Locator for the specific newly-created row, or None (falls
                  back to page-wide search).
    field_label : Field key from SCHEDULE_FIELD_LABELS (e.g. "W", "Product Name")
                  OR a raw visible label string.
    value       : Value to enter. Skipped when blank.

    Protocol per attempt (up to 3 attempts):
      A. Standard get_by_label / get_by_placeholder — works when Programa renders
         a proper form (modal or side-panel).
      B. Click the label text element to wake the inline editor, then fill the
         editable element that appears.
      C. Column-header bounding-box click — resolves the exact cell in a table
         layout by finding the header column and clicking the same x-position
         in the new row.

    After each fill, verifies the value appears in the row before returning OK.
    Screenshots and terminal logs on failure.

    Returns (success, message).
    """
    str_value = str(value).strip()
    if not str_value:
        return True, "skipped (blank)"

    # Resolve the list of label variants to try
    labels: list[str] = SCHEDULE_FIELD_LABELS.get(field_label, [field_label])

    def _try_fill_active_editor() -> bool:
        """After a click, fill whichever editable element just appeared."""
        for inp_sel in (
            "input:visible",
            "textarea:visible",
            "[contenteditable='true']:visible",
        ):
            try:
                inp = page.locator(inp_sel).last
                if inp.is_visible(timeout=600):
                    inp.click(click_count=3)
                    page.wait_for_timeout(150)
                    inp.type(str_value)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(800)
                    return True
            except Exception:
                pass
        # Also try whatever currently has focus
        try:
            focused = page.locator("input:focus, textarea:focus, [contenteditable='true']:focus")
            if focused.count() > 0:
                focused.first.click(click_count=3)
                page.wait_for_timeout(150)
                focused.first.type(str_value)
                page.keyboard.press("Enter")
                page.wait_for_timeout(800)
                return True
        except Exception:
            pass
        return False

    scope = row_locator if row_locator is not None else page

    for attempt in range(3):
        # ── Pass 0: existing direct input helper ──────────────────────────────
        # Keeps proper form/input support and preserves existing unit-test seam.
        if _fill_field_by_label(page, labels, str_value):
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
            page.wait_for_timeout(800)
            return True, f"ok via direct label-fill (attempt {attempt + 1})"

        # ── Pass A: standard label-based fill ─────────────────────────────────
        for label in labels:
            for getter in (
                lambda lbl: scope.get_by_label(lbl, exact=False),
                lambda lbl: scope.get_by_placeholder(lbl, exact=False),
                lambda lbl: page.get_by_label(lbl, exact=False),      # page-wide fallback
                lambda lbl: page.get_by_placeholder(lbl, exact=False),
            ):
                try:
                    loc = getter(label)
                    if loc.count() > 0 and loc.first.is_visible(timeout=800):
                        loc.first.click(click_count=3)
                        page.wait_for_timeout(150)
                        loc.first.type(str_value)
                        page.keyboard.press("Enter")
                        page.wait_for_timeout(800)
                        if _value_visible_in_row(page, row_locator, str_value):
                            return True, f"ok via label '{label}' (attempt {attempt + 1})"
                except Exception:
                    pass

            # CSS attr selectors (Programa sometimes uses data-label / aria-label on cells)
            safe = label.replace('"', '\\"')
            for attr in ("aria-label", "data-label", "name", "placeholder"):
                for tag in ("input", "textarea", "[contenteditable]"):
                    try:
                        sel = f'{tag}[{attr}*="{safe}" i]'
                        loc = page.locator(sel).first
                        if loc.is_visible(timeout=600):
                            loc.click(click_count=3)
                            page.wait_for_timeout(150)
                            loc.type(str_value)
                            page.keyboard.press("Enter")
                            page.wait_for_timeout(800)
                            if _value_visible_in_row(page, row_locator, str_value):
                                return True, f"ok via {attr} '{label}' (attempt {attempt + 1})"
                    except Exception:
                        pass

        # ── Pass B: click label text element, then fill editor that appears ───
        for label in labels:
            for exact in (True, False):
                try:
                    els = page.get_by_text(label, exact=exact)
                    for i in range(min(els.count(), 5)):
                        try:
                            el = els.nth(i)
                            if not el.is_visible(timeout=500):
                                continue
                            el.click(timeout=2000)
                            page.wait_for_timeout(700)
                            if _try_fill_active_editor():
                                if _value_visible_in_row(page, row_locator, str_value):
                                    return True, f"ok via label-click '{label}' (attempt {attempt + 1})"
                                # Value didn't appear — may have filled wrong cell; continue
                        except Exception:
                            continue
                except Exception:
                    pass

        # ── Pass C: column-header bounding-box → click row cell ───────────────
        for label in labels:
            try:
                safe = label.replace("'", "\\'")
                header = page.locator(
                    f"th:has-text('{safe}'), "
                    f"[role='columnheader']:has-text('{safe}'), "
                    f"[class*='header']:has-text('{safe}')"
                ).first
                if header.is_visible(timeout=600):
                    hbox = header.bounding_box()
                    if not hbox:
                        continue
                    cx = hbox["x"] + hbox["width"] / 2

                    # Click at this x within the new row, or just below the header
                    clicked = False
                    if row_locator is not None:
                        try:
                            rbox = row_locator.bounding_box()
                            if rbox:
                                page.mouse.click(cx, rbox["y"] + rbox["height"] / 2)
                                page.wait_for_timeout(700)
                                clicked = True
                        except Exception:
                            pass
                    if not clicked:
                        page.mouse.click(cx, hbox["y"] + hbox["height"] + 20)
                        page.wait_for_timeout(700)

                    if _try_fill_active_editor():
                        if _value_visible_in_row(page, row_locator, str_value):
                            return True, f"ok via column-header '{label}' (attempt {attempt + 1})"
            except Exception:
                pass

        # Brief pause before retry
        print(
            f"[SCH Debug] fill_inline_programa_field: attempt {attempt + 1}/3 failed "
            f"| field={field_label!r} value={str_value!r}"
        )
        if attempt < 2:
            page.wait_for_timeout(1200)

    return False, f"all 3 attempts failed for field '{field_label}' | labels={labels!r}"


def fill_field_in_row(
    page,
    row_locator,
    field_label: str,
    value: str,
) -> tuple[bool, str]:
    """
    Fill one field scoped exclusively to row_locator.

    Three strategies tried in order, then one retry:
      A. Column-header x-position → click at that x within the row's bounding box.
      B. Cell inside the row containing label text → click → fill editor.
      C. Input/contenteditable inside the row with matching aria-label/data-label/placeholder.

    After each strategy succeeds at filling, waits up to 3 s for the value to appear
    in the row's inner_text before accepting the result (prevents wrong-cell fills).

    row_locator=None: Strategy A still works (clicks just below the header), B and C
    are skipped. Log will show "row_locator=None".

    Returns (success, detail_message).
    """
    str_value = str(value).strip()
    if not str_value:
        return True, "skipped (blank)"

    labels: list[str] = SCHEDULE_FIELD_LABELS.get(field_label, [field_label])

    def _verify() -> bool:
        """Wait up to 3 s for str_value to appear in the row's text."""
        deadline = time.time() + 3
        needle = str_value.lower()
        while time.time() < deadline:
            try:
                src = row_locator if row_locator is not None else page.locator("body")
                text = src.inner_text(timeout=1000).lower()
                if needle in text:
                    return True
            except Exception:
                pass
            page.wait_for_timeout(300)
        return False

    def _fill_editor() -> bool:
        """Fill whichever editor is currently focused or visible in the row."""
        # Focused element (most reliable after a click)
        try:
            focused = page.locator(
                "input:focus, textarea:focus, [contenteditable='true']:focus"
            )
            if focused.count() > 0:
                focused.first.click(click_count=3)
                page.wait_for_timeout(80)
                focused.first.type(str_value)
                page.keyboard.press("Enter")
                page.wait_for_timeout(500)
                return True
        except Exception:
            pass
        # Visible input scoped to the row
        if row_locator is not None:
            for inp_sel in (
                "input:visible",
                "textarea:visible",
                "[contenteditable='true']:visible",
            ):
                try:
                    inp = row_locator.locator(inp_sel).last
                    if inp.count() > 0 and inp.is_visible(timeout=300):
                        inp.click(click_count=3)
                        page.wait_for_timeout(80)
                        inp.type(str_value)
                        page.keyboard.press("Enter")
                        page.wait_for_timeout(500)
                        return True
                except Exception:
                    pass
        # Page-wide last visible input as last resort
        for inp_sel in ("input:visible", "textarea:visible"):
            try:
                inp = page.locator(inp_sel).last
                if inp.is_visible(timeout=300):
                    inp.click(click_count=3)
                    page.wait_for_timeout(80)
                    inp.type(str_value)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(500)
                    return True
            except Exception:
                pass
        return False

    for attempt in range(2):
        # ── Strategy A: column-header x-pos → click row cell at that x ─────────
        for label in labels:
            try:
                safe = label.replace("'", "\\'")
                header = page.locator(
                    f"th:has-text('{safe}'), "
                    f"[role='columnheader']:has-text('{safe}'), "
                    f"[class*='header' i]:has-text('{safe}')"
                ).first
                if not header.is_visible(timeout=600):
                    continue
                hbox = header.bounding_box()
                if not hbox:
                    continue
                cx = hbox["x"] + hbox["width"] / 2
                if row_locator is not None:
                    rbox = row_locator.bounding_box()
                    cy = (rbox["y"] + rbox["height"] / 2) if rbox else (hbox["y"] + hbox["height"] + 20)
                else:
                    cy = hbox["y"] + hbox["height"] + 20
                page.mouse.click(cx, cy)
                page.wait_for_timeout(700)
                print(f"[SCH Debug] fill_field_in_row A: header '{label}' → ({cx:.0f}, {cy:.0f})")
                if _fill_editor() and _verify():
                    return True, f"ok via column-header '{label}' (attempt {attempt + 1})"
            except Exception:
                pass

        # ── Strategy B: label text cell inside the row → click → fill ───────────
        if row_locator is not None:
            for label in labels:
                for exact in (True, False):
                    try:
                        cell = row_locator.get_by_text(label, exact=exact)
                        if cell.count() == 0 or not cell.first.is_visible(timeout=400):
                            continue
                        cell.first.click(timeout=2000)
                        page.wait_for_timeout(700)
                        print(f"[SCH Debug] fill_field_in_row B: row-cell '{label}' exact={exact}")
                        if _fill_editor() and _verify():
                            return True, f"ok via row-cell '{label}' exact={exact} (attempt {attempt + 1})"
                    except Exception:
                        pass

        # ── Strategy C: attr-based input/contenteditable within the row ─────────
        if row_locator is not None:
            for label in labels:
                safe_attr = label.replace('"', '\\"')
                for attr in ("aria-label", "data-label", "name", "placeholder"):
                    for tag in ("input", "textarea", "[contenteditable]"):
                        try:
                            sel = f'{tag}[{attr}*="{safe_attr}" i]'
                            loc = row_locator.locator(sel).first
                            if loc.is_visible(timeout=300):
                                loc.click(click_count=3)
                                page.wait_for_timeout(80)
                                loc.type(str_value)
                                page.keyboard.press("Enter")
                                page.wait_for_timeout(500)
                                print(f"[SCH Debug] fill_field_in_row C: {attr} '{label}'")
                                if _verify():
                                    return True, f"ok via {attr} '{label}' (attempt {attempt + 1})"
                        except Exception:
                            pass

        print(
            f"[SCH Debug] fill_field_in_row: attempt {attempt + 1}/2 all strategies failed "
            f"| field={field_label!r} value={str_value!r} row_locator={'set' if row_locator else 'None'}"
        )
        if attempt == 0:
            page.wait_for_timeout(1000)

    return False, f"all strategies failed | field='{field_label}' labels={labels!r}"


def run_programa_debug_single_row(
    row: dict,
    project_name: str,
    screenshots_dir: str | None = None,
) -> list[dict]:
    """
    Run the full Schedule → Custom Product → field entry flow for ONE row only.

    Debug mode: slow_mo=1000, screenshot after every step, browser stays open
    at the end (until user clicks OK in the alert).

    Parameters
    ----------
    row            : A single intake row dict.
    project_name   : Programa project name to navigate to.
    screenshots_dir: Directory for step screenshots (created if absent).

    Returns a list of step-log dicts with keys:
        step, success, message, screenshot_path.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [{"step": "import", "success": False,
                 "message": "Playwright not installed — run: pip install playwright && playwright install",
                 "screenshot_path": ""}]

    screenshots_dir = screenshots_dir or str(runtime_data_path("enrichment_debug", "programa_steps"))
    os.makedirs(screenshots_dir, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    ensure_dirs()

    steps: list[dict] = []

    def log_step(step_name: str, success: bool, message: str = "", shot: str = "") -> dict:
        entry = {"step": step_name, "success": success, "message": message, "screenshot_path": shot}
        steps.append(entry)
        tag = "OK  " if success else "FAIL"
        print(f"[SCH Debug] {tag} — {step_name}: {message}")
        return entry

    def snap(page, label: str) -> str:
        ts = int(time.time() * 1000)
        path = os.path.join(screenshots_dir, f"{ts}_{label}.png")
        try:
            page.screenshot(path=path, full_page=False)
        except Exception:
            pass
        return path

    # Prepare field values
    product_name = str(row.get("Product Name", "") or "").strip()
    dims = parse_dimensions_for_programa(str(row.get("Dimensions", "") or ""))
    notes_raw = remove_notes_row_prefix(row.get("Notes", ""))
    _mat_match = re.search(r'\[Materials:\s*([^\]]+)\]', notes_raw)
    material_val = _mat_match.group(1).strip() if _mat_match else ""

    # Ordered list of (SCHEDULE_FIELD_LABELS key, value) to fill
    debug_fields: list[tuple[str, str]] = [
        ("Product Name", product_name),
        ("Brand",        str(row.get("Brand", "") or "").strip()),
        ("W",            dims["width"]),
        ("H",            dims["height"]),
        ("D",            dims["depth"]),
        ("Quantity",     str(row.get("Quantity", "") or "").strip()),
        ("Supplier",     str(row.get("Supplier", "") or "").strip()),
        ("Color",        str(row.get("Finish / Color", "") or "").strip()),
        ("Material",     material_val),
    ]

    print(f"[SCH Debug] Starting debug run for: {product_name!r} → project {project_name!r}")
    print(f"[SCH Debug] Screenshots → {screenshots_dir}")
    print(f"[SCH Debug] Fields to fill: {[(k, v) for k, v in debug_fields if v]}")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            slow_mo=1000,
            viewport={"width": 1440, "height": 900},
        )
        page = _select_workspace_page(context)
        if not page.url or page.url in ("about:blank", "chrome://newtab/"):
            page.goto(PROGRAMA_URL, wait_until="domcontentloaded", timeout=30_000)
        _ensure_workspace_url(page, phase="debug-launch")

        try:
            # ── Step 1: Open Programa ──────────────────────────────────────────
            _inject_banner(page, "SCH Debug  ·  Step 1: Opening Programa…")
            page.goto(PROGRAMA_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2000)
            shot = snap(page, "01_opened_programa")
            log_step("open_programa", True, f"URL: {page.url}", shot)

            # Check / wait for login
            if not _is_logged_in(page):
                _inject_banner(page, "SCH Debug  ·  Please log in, then wait…")
                log_step("login_needed", False, "Login form detected — waiting up to 5 min")
                if not _wait_for_login(page, timeout_seconds=300):
                    log_step("login_timeout", False, "Timed out waiting for login")
                    _js_alert(page, "SCH Debug: Login timed out. Close this window.")
                    context.close()
                    return steps
                _remove_banner(page)
                log_step("login_ok", True, "Login successful")

            # ── Step 2: Open project ───────────────────────────────────────────
            _inject_banner(page, f"SCH Debug  ·  Step 2: Opening project '{project_name}'…")
            nav_ok, nav_method = navigate_to_project(page, project_name)
            page.wait_for_timeout(1500)
            shot = snap(page, "02_opened_project")
            log_step("open_project", nav_ok, f"method={nav_method} | project={project_name!r}", shot)

            if not nav_ok:
                _js_confirm(
                    page,
                    f"SCH Debug — Step 2\n\n"
                    f"Could not find project '{project_name}' automatically.\n"
                    "Please open it in Programa, then click OK.",
                )
                log_step("open_project_manual", True, "User opened project manually")

            # ── Step 3: Open Schedule file ─────────────────────────────────────
            _inject_banner(page, "SCH Debug  ·  Step 3: Opening Schedule file…")
            sched_ok, sched_method = _navigate_to_schedule_file(page)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            shot = snap(page, "03_opened_schedule")
            log_step("open_schedule", sched_ok, f"method={sched_method}", shot)

            if not sched_ok:
                _js_confirm(
                    page,
                    "SCH Debug — Step 3\n\n"
                    "Could not open the Schedule file automatically.\n"
                    "Please open it in Programa, then click OK.",
                )
                log_step("open_schedule_manual", True, "User opened Schedule manually")

            # ── Step 4: Click New ──────────────────────────────────────────────
            _inject_banner(page, "SCH Debug  ·  Step 4: Clicking New…")
            new_ok = _click_by_text(page, NEW_ITEM_TEXTS, timeout_ms=5000)
            page.wait_for_timeout(1000)
            shot = snap(page, "04_clicked_new")
            log_step("click_new", new_ok,
                     "New button clicked" if new_ok else f"NOT FOUND — tried: {NEW_ITEM_TEXTS}", shot)

            if not new_ok:
                _js_confirm(
                    page,
                    "SCH Debug — Step 4\n\n"
                    "'New' button not found.\n"
                    "Please click it in Programa, then click OK.",
                )

            # ── Step 5: Click Custom Product ───────────────────────────────────
            _inject_banner(page, "SCH Debug  ·  Step 5: Clicking Custom Product…")
            custom_ok = False

            for role in ("menuitem", "option", "button", "link", "listitem"):
                if custom_ok:
                    break
                for text in CUSTOM_PRODUCT_TEXTS:
                    try:
                        loc = page.get_by_role(role, name=text, exact=False)
                        if loc.count() > 0 and loc.first.is_visible(timeout=1000):
                            loc.first.click(timeout=3000)
                            custom_ok = True
                            print(f"[SCH Debug] Custom Product clicked via role={role!r} text={text!r}")
                            break
                    except Exception:
                        pass

            if not custom_ok:
                for text in CUSTOM_PRODUCT_TEXTS:
                    try:
                        loc = page.get_by_text(text, exact=False)
                        if loc.count() > 0 and loc.first.is_visible(timeout=1000):
                            loc.first.click(timeout=3000)
                            custom_ok = True
                            print(f"[SCH Debug] Custom Product clicked via get_by_text text={text!r}")
                            break
                    except Exception:
                        pass

            page.wait_for_timeout(2000)
            shot = snap(page, "05_clicked_custom_product")
            log_step("click_custom_product", custom_ok,
                     "Custom Product clicked" if custom_ok
                     else f"NOT FOUND — tried: {CUSTOM_PRODUCT_TEXTS}", shot)

            if not custom_ok:
                _js_confirm(
                    page,
                    "SCH Debug — Step 5\n\n"
                    "'Custom Product' not found in dropdown.\n"
                    "Please click it manually, then click OK.",
                )

            # ── Step 6: Confirm blank row appeared ─────────────────────────────
            _inject_banner(page, "SCH Debug  ·  Step 6: Looking for new blank row…")
            page.wait_for_timeout(1500)
            row_loc = _find_new_row(page)
            shot = snap(page, "06_new_row_appeared")
            log_step(
                "new_row_appeared",
                row_loc is not None,
                "New row locator found" if row_loc else "No row locator found — will use page-wide search",
                shot,
            )

            # Print DOM snapshot around the new row to help debug selectors
            if row_loc is not None:
                try:
                    html = row_loc.evaluate("el => el.outerHTML")
                    print(f"[SCH Debug] New row HTML (first 800 chars):\n{str(html)[:800]}")
                except Exception:
                    pass

            # ── Steps 7+: Fill each field ──────────────────────────────────────
            for step_i, (field_key, field_value) in enumerate(debug_fields, start=7):
                if not field_value.strip():
                    log_step(f"fill_{field_key}", True, "skipped (blank)")
                    continue

                labels = SCHEDULE_FIELD_LABELS.get(field_key, [field_key])
                _inject_banner(
                    page,
                    f"SCH Debug  ·  Step {step_i}: {field_key} = {field_value!r}  "
                    f"(labels: {labels[:2]}…)",
                )
                print(f"[SCH Debug] Step {step_i}: filling {field_key!r} = {field_value!r}")
                print(f"[SCH Debug]   Labels tried: {labels}")

                ok, msg = fill_inline_programa_field(page, row_loc, field_key, field_value)
                page.wait_for_timeout(800)
                shot = snap(page, f"{step_i:02d}_fill_{field_key.lower().replace('/', '_')}")
                log_step(f"fill_{field_key}", ok, msg, shot)

                if not ok:
                    print(f"[SCH Debug] FIELD FAILED: {field_key!r} = {field_value!r}")
                    print(f"[SCH Debug] Screenshot saved: {shot}")
                    # Print current page URL and title for context
                    try:
                        print(f"[SCH Debug] Page URL: {page.url}")
                        print(f"[SCH Debug] Page title: {page.title()}")
                    except Exception:
                        pass

        except Exception as exc:
            import traceback as _tb
            tb_str = _tb.format_exc()
            print(f"[SCH Debug] UNHANDLED EXCEPTION:\n{tb_str}")
            try:
                shot = snap(page, "EXCEPTION")
            except Exception:
                shot = ""
            log_step("exception", False, str(exc), shot)

        finally:
            # Always keep browser open until user dismisses the alert
            failed = [s for s in steps if not s["success"]]
            summary = (
                f"SCH Debug complete.\n\n"
                f"Steps run: {len(steps)}\n"
                f"Failed: {len(failed)}\n\n"
                + ("\n".join(f"  FAIL — {s['step']}: {s['message']}" for s in failed)
                   if failed else "All steps passed.")
                + "\n\nClose this window when done reviewing."
            )
            _remove_banner(page)
            _inject_banner(page, f"SCH Debug — Done. {len(failed)} failure(s). See terminal for details.")
            try:
                _js_alert(page, summary)
            except Exception:
                pass
            context.close()

    return steps


# ── Section management ─────────────────────────────────────────────────────────


def _normalise_section_name(name: str) -> str:
    """Lowercase, strip, remove non-alphanumeric chars for fuzzy section matching."""
    cleaned = re.sub(r"[^a-z0-9 ]", " ", str(name or "").lower())
    return " ".join(cleaned.split())


def _get_row_section_name(row: dict) -> str:
    """Return the section name for a row: Product Category, or 'Uncategorized' if blank."""
    cat = str(row.get("section", "") or row.get("Section", "") or row.get("Product Category", "") or row.get("category", "") or "").strip()
    return cat if cat else "Uncategorized"


def _is_probable_section_heading_text(text: str) -> bool:
    """Return True when text looks like a structural section heading, not a product row."""
    value = str(text or "").strip()
    if not value:
        return False
    lower = value.lower()
    if any(
        token in lower
        for token in (
            "custom product",
            "add from url",
            "product from library",
            "product name",
            "details",
            "supplier",
            "qty",
            "width",
            "height",
            "depth",
            "sku",
        )
    ):
        return False
    if "\n" in value and len([line for line in value.splitlines() if line.strip()]) > 2:
        return False
    return len(value) <= 60


def _get_existing_sections(page) -> dict[str, str]:
    """
    Scan the open schedule page for section headings.
    Returns {normalised_name: display_name}.
    """
    selectors = [
        "[class*='section'][class*='header']",
        "[class*='section-title']",
        "[data-type='section']",
        "tr[class*='section']",
        "[role='rowgroup'] [class*='header']",
    ]
    found: dict[str, str] = {}
    for sel in selectors:
        try:
            elems = page.locator(sel)
            for i in range(min(elems.count(), 50)):
                try:
                    text = elems.nth(i).inner_text(timeout=500).strip()
                    if not text:
                        continue
                    lines = [line.strip() for line in text.splitlines() if line.strip()]
                    candidates = lines[:2] if lines else [text]
                    for candidate in candidates:
                        if not _is_probable_section_heading_text(candidate):
                            continue
                        norm = _normalise_section_name(candidate)
                        if norm:
                            found[norm] = candidate
                except Exception:
                    pass
        except Exception:
            pass
    return found


def section_exists(page, section_name: str) -> bool:
    """Return True when section_name exists as an actual schedule section heading."""
    norm = _normalise_section_name(section_name)
    if not norm:
        return False
    print(f"[SCH Automation] Checking if section exists: {section_name}")
    existing = _get_existing_sections(page)
    exists = norm in existing
    if exists:
        print(f"[SCH Automation] Section exists, reusing: {existing[norm]}")
    else:
        print(f"[SCH Automation] Section missing, creating: {section_name}")
    return exists


def _section_exists(page, section_name: str) -> tuple[bool, str]:
    """Exact section existence check used before creating sections."""
    norm = _normalise_section_name(section_name)
    existing = _get_existing_sections(page)
    if norm in existing:
        return True, existing[norm]
    return False, ""


def _empty_schedule_state_detected(page, existing_sections: dict[str, str] | None = None) -> bool:
    """Return True when Programa is showing the blank schedule prompt."""
    existing_sections = existing_sections if existing_sections is not None else _get_existing_sections(page)
    if existing_sections:
        return False
    try:
        loc = page.get_by_text("Add your first product", exact=False)
        if loc.count() > 0 and loc.first.is_visible(timeout=400):
            return True
    except Exception:
        pass
    return len(existing_sections) == 0


def _collect_dropdown_texts(page) -> list[str]:
    """Collect all visible text strings inside any open dropdown / menu."""
    texts: list[str] = []
    candidates = [
        "[role='menu'] *",
        "[role='listbox'] *",
        "[role='menuitem']",
        "[role='option']",
        "[class*='dropdown' i] *",
        "[class*='menu' i] li",
        "[class*='popover' i] *",
        "[data-radix-popper-content-wrapper] *",
    ]
    for sel in candidates:
        try:
            els = page.locator(sel)
            for i in range(min(els.count(), 30)):
                try:
                    t = els.nth(i).inner_text(timeout=300).strip()
                    if t and t not in texts:
                        texts.append(t)
                except Exception:
                    pass
        except Exception:
            pass
    return texts


def _click_section_in_dropdown(page) -> tuple[bool, str]:
    """
    Click the Section item inside the currently open New dropdown.

    Three ordered strategies, each with full diagnostic logging:
      S1: role-based (get_by_role menuitem/option/button) within each dropdown scope,
          matching all NEW_SECTION_TEXTS variants via _robust_click_locator.
      S2: text-match on actionable elements only (button/menuitem/option/li — no div),
          with dispatchEvent synthetic-click as extra fallback after _robust_click_locator.
      S3: bounding-box coordinate click (page.mouse.click at element center).

    Scans ALL visible scopes rather than picking the last-found (which could be a
    broad container capturing inline section controls elsewhere on the page).
    """
    print("[SCH Automation] Clicking Section menu item")

    dropdown_selectors = (
        "[role='menu']",
        "[data-radix-popper-content-wrapper]",
        "div[role='dialog']",
        "[role='listbox']",
        "[class*='popover' i]",
        "[class*='dropdown' i]",
        "[class*='menu' i]",
    )

    # Wait for at least one dropdown container to appear
    deadline = time.time() + 5
    visible_scopes: list[tuple[object, str]] = []
    while time.time() < deadline and not visible_scopes:
        for sel in dropdown_selectors:
            try:
                loc = page.locator(sel)
                for i in range(min(loc.count(), 8)):
                    candidate = loc.nth(i)
                    if candidate.is_visible(timeout=250):
                        visible_scopes.append((candidate, f"{sel}[{i}]"))
            except Exception:
                pass
        if not visible_scopes:
            page.wait_for_timeout(200)

    if not visible_scopes:
        print("[SCH Automation] No dropdown container visible after 5s")

    # ── Strategy 1: role-based within each scope ───────────────────────────────
    for scope, scope_desc in visible_scopes:
        for section_text in NEW_SECTION_TEXTS:
            for role in ("menuitem", "option", "button"):
                try:
                    loc = scope.get_by_role(role, name=section_text, exact=True)
                    if loc.count() == 0:
                        continue
                    candidate = loc.first
                    if not candidate.is_visible(timeout=400):
                        continue
                    try:
                        html = candidate.evaluate("el => el.outerHTML")[:200]
                        bb = candidate.bounding_box()
                        print(
                            f"[SCH Automation] Section S1 candidate "
                            f"role={role} name={section_text!r} scope={scope_desc} "
                            f"bb={bb} html={html}"
                        )
                    except Exception:
                        pass
                    ok, desc = _robust_click_locator(
                        page, candidate,
                        f"section role={role} name={section_text!r} scope={scope_desc}",
                    )
                    if ok:
                        return True, desc
                except Exception:
                    pass

    # ── Strategy 2: text-match on actionable elements only (no div) ────────────
    for scope, scope_desc in visible_scopes:
        items = scope.locator("button, [role='menuitem'], [role='option'], li")
        count = 0
        try:
            count = min(items.count(), 60)
        except Exception:
            pass

        visible_item_texts: list[str] = []
        for i in range(count):
            try:
                item = items.nth(i)
                if item.is_visible(timeout=250):
                    text = (item.inner_text(timeout=300) or "").strip()
                    if text:
                        visible_item_texts.append(text)
            except Exception:
                pass
        print(f"[SCH Automation] Actionable dropdown items in {scope_desc}: {visible_item_texts}")

        for i in range(count):
            try:
                item = items.nth(i)
                if not item.is_visible(timeout=300):
                    continue
                text = (item.inner_text(timeout=300) or "").strip()
                if text not in NEW_SECTION_TEXTS:
                    continue

                try:
                    html = item.evaluate("el => el.outerHTML")[:200]
                    bb = item.bounding_box()
                    actual_tag = ""
                    if bb:
                        cx = bb["x"] + bb["width"] / 2
                        cy = bb["y"] + bb["height"] / 2
                        actual_tag = item.evaluate(
                            f"() => document.elementFromPoint({cx:.1f}, {cy:.1f})?.tagName ?? ''"
                        )
                    print(
                        f"[SCH Automation] Section S2 candidate idx={i} text={text!r} "
                        f"scope={scope_desc} bb={bb} elementFromPoint={actual_tag!r} html={html}"
                    )
                except Exception:
                    pass

                ok, desc = _robust_click_locator(
                    page, item,
                    f"section text={text!r} idx={i} scope={scope_desc}",
                )
                if ok:
                    return True, desc

                # dispatchEvent synthetic click as additional fallback
                try:
                    item.evaluate(
                        "el => { el.scrollIntoView({block:'center'}); "
                        "el.dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true})); }"
                    )
                    return True, f"dispatchEvent click text={text!r} idx={i} scope={scope_desc}"
                except Exception as de:
                    print(f"[SCH Automation] dispatchEvent fallback failed idx={i}: {de}")

            except Exception as exc:
                print(f"[SCH Automation] S2 candidate idx={i} failed: {exc}")

    # ── Strategy 3: bounding-box coordinate click ──────────────────────────────
    for section_text in NEW_SECTION_TEXTS:
        try:
            loc = page.get_by_text(section_text, exact=True)
            for i in range(min(loc.count(), 10)):
                candidate = loc.nth(i)
                if not candidate.is_visible(timeout=300):
                    continue
                try:
                    bb = candidate.bounding_box()
                    if not bb:
                        continue
                    cx = bb["x"] + bb["width"] / 2
                    cy = bb["y"] + bb["height"] / 2
                    actual_tag = candidate.evaluate(
                        f"() => document.elementFromPoint({cx:.1f}, {cy:.1f})?.tagName ?? ''"
                    )
                    print(
                        f"[SCH Automation] Section S3 bounding-box "
                        f"text={section_text!r} idx={i} bb={bb} "
                        f"center=({cx:.1f},{cy:.1f}) elementFromPoint={actual_tag!r}"
                    )
                    candidate.scroll_into_view_if_needed(timeout=1000)
                    page.mouse.click(cx, cy)
                    return True, (
                        f"bounding-box coordinate click text={section_text!r} "
                        f"at ({cx:.1f},{cy:.1f}) elementFromPoint={actual_tag!r}"
                    )
                except Exception as exc:
                    print(
                        f"[SCH Automation] S3 bounding-box text={section_text!r} idx={i} failed: {exc}"
                    )
        except Exception:
            pass

    return False, "all strategies failed (S1: role-based, S2: text-match+dispatchEvent, S3: bounding-box)"


def _section_name_is_approved(page, section_name: str) -> bool:
    """Return True when section_name is one of the product-derived section names."""
    approved = getattr(page, "_sch_approved_section_names", None)
    if approved is None:
        return True
    return _normalise_section_name(section_name) in approved


def _find_new_section_title(page):
    """Return the newest visible Untitled Section title/editor locator, if one exists."""
    title_texts = ["Untitled Section", "Untitled section", "Unnamed section", "Untitled"]
    for text in title_texts:
        for exact in (True, False):
            try:
                loc = page.get_by_text(text, exact=exact)
                visible = []
                for i in range(min(loc.count(), 20)):
                    candidate = loc.nth(i)
                    if candidate.is_visible(timeout=250):
                        visible.append(candidate)
                if visible:
                    return visible[-1], f"text={text!r} exact={exact}"
            except Exception:
                pass

    selectors = [
        'input[value*="Untitled" i]',
        'textarea:has-text("Untitled")',
        '[contenteditable="true"]:has-text("Untitled")',
        '[class*="section" i]:has-text("Untitled")',
        '[role="heading"]:has-text("Untitled")',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            visible = []
            for i in range(min(loc.count(), 20)):
                candidate = loc.nth(i)
                if candidate.is_visible(timeout=250):
                    visible.append(candidate)
            if visible:
                return visible[-1], f"selector={sel!r}"
        except Exception:
            pass

    # Sometimes Programa immediately focuses the newly-created title editor.
    for sel in ("input:focus", "textarea:focus", '[contenteditable="true"]:focus'):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=250):
                return loc, f"focused={sel}"
        except Exception:
            pass

    return None, "not_found"


def _count_untitled_section_headings(page) -> int:
    """Best-effort count of visible Untitled Section headings/editors."""
    seen: set[str] = set()
    count = 0
    title_texts = ["Untitled Section", "Untitled section", "Unnamed section", "Untitled"]

    for text in title_texts:
        try:
            loc = page.get_by_text(text, exact=True)
            for i in range(min(loc.count(), 80)):
                candidate = loc.nth(i)
                if not candidate.is_visible(timeout=150):
                    continue
                bbox = candidate.bounding_box()
                key = (
                    f"{round(bbox['x'])}:{round(bbox['y'])}:"
                    f"{round(bbox['width'])}:{round(bbox['height'])}"
                    if bbox else f"text:{text}:{i}"
                )
                if key in seen:
                    continue
                seen.add(key)
                count += 1
        except Exception:
            pass

    for sel in (
        'input[value*="Untitled" i]',
        '[contenteditable="true"]:has-text("Untitled")',
        '[class*="section" i]:has-text("Untitled")',
        '[role="heading"]:has-text("Untitled")',
    ):
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 80)):
                candidate = loc.nth(i)
                if not candidate.is_visible(timeout=150):
                    continue
                bbox = candidate.bounding_box()
                key = (
                    f"{round(bbox['x'])}:{round(bbox['y'])}:"
                    f"{round(bbox['width'])}:{round(bbox['height'])}"
                    if bbox else f"sel:{sel}:{i}"
                )
                if key in seen:
                    continue
                seen.add(key)
                count += 1
        except Exception:
            pass

    return count


def _wait_for_new_untitled_section(page, untitled_before: int, section_count_before: int):
    """Wait for a newly-created Untitled Section and return its title locator."""
    title_locator = None
    title_desc = "not_found"
    untitled_after = untitled_before
    section_count_after = section_count_before
    print("[SCH Automation] Waiting for Untitled Section")
    deadline = time.time() + 8
    while time.time() < deadline:
        # Check first: Programa may open a blank focused input without showing
        # an "Untitled Section" heading — detect that editor directly.
        editor, editor_desc = _active_section_title_editor(page)
        if editor is not None:
            untitled_after = _count_untitled_section_headings(page)
            section_count_after = len(_get_existing_sections(page))
            title_locator = editor
            title_desc = (
                f"active-editor:{editor_desc}; "
                f"untitled count {untitled_before}->{untitled_after}; "
                f"section count {section_count_before}->{section_count_after}"
            )
            print(f"[SCH Automation] Found active section title editor: {editor_desc}")
            break

        untitled_after = _count_untitled_section_headings(page)
        section_count_after = len(_get_existing_sections(page))
        if untitled_after > untitled_before:
            title_locator, title_desc = _find_new_section_title(page)
            if title_locator is not None:
                title_desc = (
                    f"{title_desc}; untitled count {untitled_before}->{untitled_after}; "
                    f"section count {section_count_before}->{section_count_after}"
                )
                break
        else:
            print(
                f"[SCH Automation] Waiting for first section to appear "
                f"| untitled count {untitled_before}->{untitled_after} "
                f"| section count {section_count_before}->{section_count_after}"
            )
        page.wait_for_timeout(400)

    if title_locator is None and untitled_after > untitled_before:
        title_locator, title_desc = _find_new_section_title(page)
    return title_locator, title_desc, untitled_after, section_count_after


def _keyboard_select_section_from_new_menu(page, arrow_count: int) -> tuple[bool, str]:
    """Reopen New and select Section via keyboard based on the known dropdown order."""
    try:
        print("[SCH Automation] Fallback keyboard navigation used")
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
        except Exception:
            pass
        print("[SCH Automation] Clicking New")
        new_clicked, new_method = _click_new_button(page)
        if not new_clicked:
            return False, f"keyboard fallback could not reopen New menu: {new_method}"
        page.wait_for_timeout(800)
        print("[SCH Automation] New dropdown opened")
        for _ in range(arrow_count):
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(90)
        page.keyboard.press("Enter")
        return True, f"keyboard ArrowDown x{arrow_count} then Enter after {new_method}"
    except Exception as exc:
        return False, f"keyboard fallback ArrowDown x{arrow_count} failed: {exc}"


def _active_section_title_editor(page):
    """
    Return a focused/visible editor suitable for a just-created section title.

    Checks CSS focused-element selectors first, then falls back to
    document.activeElement via JS (most reliable for React/Vue apps where
    Playwright's :focus pseudo-class can lag behind the DOM focus state).
    """
    for sel in (
        "input:focus",
        "textarea:focus",
        '[contenteditable="true"]:focus',
        '[role="textbox"]:focus',
    ):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=250):
                return loc, f"css-focused={sel}"
        except Exception:
            pass

    # JS document.activeElement check — catches cases where :focus CSS lags
    try:
        is_editable = page.evaluate(
            "() => { const el = document.activeElement; "
            "return !!(el && (el.isContentEditable || "
            "el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || "
            "el.getAttribute('role') === 'textbox')); }"
        )
        if is_editable:
            html = page.evaluate("() => document.activeElement?.outerHTML ?? ''")[:200]
            tag = page.evaluate("() => document.activeElement?.tagName?.toLowerCase() ?? ''")
            role = page.evaluate("() => document.activeElement?.getAttribute('role') ?? ''")
            print(
                f"[SCH Automation] document.activeElement is editable: "
                f"tag={tag!r} role={role!r} html={html}"
            )
            loc = page.locator(
                "input:focus, textarea:focus, "
                "[contenteditable='true']:focus, [role='textbox']:focus"
            ).first
            if loc.is_visible(timeout=300):
                return loc, f"activeElement tag={tag!r} role={role!r}"
    except Exception:
        pass

    return None, "no active title editor"


def _write_section_title(page, section_name: str, title_locator=None) -> tuple[bool, str]:
    """
    Rename the new section title. Only writes approved section names.

    This helper intentionally avoids generic "last input on page" fallbacks so
    product names/brands cannot be typed into section headers or other fields.
    """
    if not _section_name_is_approved(page, section_name):
        return False, f"refused unsafe section title: {section_name!r}"

    # ── Entry diagnostics ─────────────────────────────────────────────────────
    try:
        ae_html = page.evaluate("() => document.activeElement?.outerHTML ?? 'none'")[:300]
        print(f"[SCH Automation] _write_section_title entry — activeElement: {ae_html}")
    except Exception:
        pass
    try:
        vis_editors = page.locator(
            "input:visible, textarea:visible, "
            "[contenteditable='true']:visible, [role='textbox']:visible"
        ).all()
        descs = []
        for loc in vis_editors[:8]:
            try:
                ph = loc.get_attribute("placeholder") or loc.get_attribute("aria-label") or ""
                cls = (loc.get_attribute("class") or "")[:40]
                descs.append(ph or cls or "?")
            except Exception:
                descs.append("?")
        print(f"[SCH Automation] Visible editable fields at rename entry: {descs}")
    except Exception:
        pass
    try:
        titles_before = page.locator(
            "h1, h2, h3, [class*='section-title'], [class*='sectionTitle'], [class*='section-name']"
        ).all_inner_texts()[:10]
        print(f"[SCH Automation] Section headings before rename: {titles_before}")
    except Exception:
        pass

    attempts: list[str] = []
    commit_method: list[str] = []

    def _commit() -> None:
        try:
            page.keyboard.press("Enter")
            page.wait_for_timeout(250)
            commit_method.append("Enter")
        except Exception:
            pass
        try:
            page.keyboard.press("Tab")
            page.wait_for_timeout(250)
            commit_method.append("Tab")
        except Exception:
            pass

    def _fill_editor(editor, desc: str) -> tuple[bool, str]:
        try:
            editor.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            pass
        try:
            editor.click(timeout=1500)
            page.wait_for_timeout(100)
            try:
                editor.fill(section_name, timeout=2000)
            except Exception:
                try:
                    page.keyboard.press("Meta+A")
                except Exception:
                    page.keyboard.press("Control+A")
                page.wait_for_timeout(80)
                page.keyboard.insert_text(section_name)
            _commit()
            print(f"[SCH Automation] Section title committed via: {'+'.join(commit_method) or 'unknown'}")
            try:
                titles_after = page.locator(
                    "h1, h2, h3, [class*='section-title'], [class*='sectionTitle'], [class*='section-name']"
                ).all_inner_texts()[:10]
                print(f"[SCH Automation] Section headings after rename: {titles_after}")
            except Exception:
                pass
            return True, desc
        except Exception as exc:
            attempts.append(f"{desc}: {exc}")
            return False, desc

    if title_locator is not None:
        for action_name, action in (
            ("click", lambda loc: loc.click(timeout=1500)),
            ("double_click", lambda loc: loc.dblclick(timeout=1500)),
            ("enter_after_click", lambda loc: (loc.click(timeout=1500), page.keyboard.press("Enter"))),
        ):
            try:
                title_locator.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            try:
                action(title_locator)
                page.wait_for_timeout(250)
                editor, editor_desc = _active_section_title_editor(page)
                if editor is not None:
                    ok, desc = _fill_editor(editor, f"{action_name}->{editor_desc}")
                    if ok:
                        return True, desc
            except Exception as exc:
                attempts.append(f"{action_name}: {exc}")

        # Last section-title-specific attempt: type into the title locator itself.
        ok, desc = _fill_editor(title_locator, "direct title locator")
        if ok:
            return True, desc

    editor, editor_desc = _active_section_title_editor(page)
    if editor is not None:
        ok, desc = _fill_editor(editor, editor_desc)
        if ok:
            return True, desc

    reason = "; ".join(attempts) or "section title editor not found"
    print(f"[SCH Automation] Section created but rename failed: {reason}")
    return False, reason


def create_section(page, section_name: str, log_entries: list[dict] | None = None) -> bool:
    """
    Create a Programa section by New → Section, then rename the new Untitled
    Section header to section_name.

    Later product creation and Details-panel entry are intentionally untouched.
    """
    log_entries = log_entries if log_entries is not None else []
    norm = _normalise_section_name(section_name)
    if not section_name.strip():
        return False
    if not _section_name_is_approved(page, section_name):
        shot = take_screenshot(page, f"section_refused_{norm[:20]}")
        log_entries.append(make_log_entry(
            "", "error",
            f"Refused to type non-section value into section header: {section_name!r}",
            shot,
        ))
        print(f"[SCH Automation] refused unsafe section name: {section_name!r}")
        return False

    existing_before = _get_existing_sections(page)
    if _normalise_section_name(section_name) in existing_before:
        display_name = existing_before[_normalise_section_name(section_name)]
        print(f"[SCH Automation] Section exists, reusing: {display_name}")
        display_name = display_name or section_name
        log_entries.append(make_log_entry(
            "", "info", f"Reusing existing section: {display_name}"
        ))
        return True

    section_count_before = len(existing_before)
    print(f"[SCH Automation] creating section via New → Section: {section_name!r}")
    _inject_banner(page, f"SCH DesignOps  ·  Creating section: {section_name}…")
    shot_before = take_screenshot(page, f"section_before_new_{norm[:20]}")
    log_entries.append(make_log_entry("", "info", f"Before creating section '{section_name}'", shot_before))
    log_entries.append(make_log_entry(
        "", "info",
        f"Section heading count before creation: {section_count_before}",
    ))
    untitled_before = _count_untitled_section_headings(page)
    log_entries.append(make_log_entry(
        "", "info",
        f"Untitled Section count before creation: {untitled_before}",
    ))
    print(f"[SCH Automation] Untitled Section count before creation: {untitled_before}")

    print("[SCH Automation] Clicking New")
    new_clicked, new_method = _click_new_button(page)
    if not new_clicked:
        shot = take_screenshot(page, f"section_no_new_btn_{norm[:20]}")
        log_entries.append(make_log_entry(
            "", "warn",
            f"'New' button not found when creating section '{section_name}' — skipping section. {new_method}",
            shot,
        ))
        _remove_banner(page)
        return False
    page.wait_for_timeout(900)
    shot_menu = take_screenshot(page, f"section_menu_open_{norm[:20]}")
    log_entries.append(make_log_entry("", "info", f"New menu opened for section '{section_name}' via {new_method}", shot_menu))
    print("[SCH Automation] New menu opened")

    print("[SCH Automation] Clicking Section")
    section_clicked, click_method = _click_section_in_dropdown(page)
    if section_clicked:
        print(f"[SCH Automation] Section clicked via: {click_method}")
        page.wait_for_timeout(900)
        shot_section_clicked = take_screenshot(page, f"section_clicked_{norm[:20]}")
        log_entries.append(make_log_entry("", "info", f"Section clicked for '{section_name}' via {click_method}", shot_section_clicked))
    else:
        visible_texts = _collect_dropdown_texts(page)
        log_entries.append(make_log_entry(
            "", "warn",
            f"Scoped click did not select 'Section' for '{section_name}'. "
            f"Visible menu items: {visible_texts}. Trying keyboard fallback.",
        ))
        print(f"[SCH Automation] Scoped Section click failed for {section_name!r}. Visible texts: {visible_texts}")

    title_locator, title_desc, untitled_after, section_count_after = _wait_for_new_untitled_section(
        page, untitled_before, section_count_before
    )

    if title_locator is None:
        for arrow_count in (3, 4):
            ok, keyboard_method = _keyboard_select_section_from_new_menu(page, arrow_count)
            if not ok:
                log_entries.append(make_log_entry("", "warn", keyboard_method))
                continue
            page.wait_for_timeout(900)
            log_entries.append(make_log_entry(
                "", "info",
                f"Section selected with keyboard fallback for '{section_name}' via {keyboard_method}",
            ))
            title_locator, title_desc, untitled_after, section_count_after = _wait_for_new_untitled_section(
                page, untitled_before, section_count_before
            )
            if title_locator is not None:
                click_method = keyboard_method
                break

    if title_locator is None:
        shot = take_screenshot(page, f"section_untitled_not_found_{norm[:20]}")
        visible = _visible_page_text(page, limit=2500)
        log_entries.append(make_log_entry(
            "", "warn",
            f"New menu opened but Section click did not create Untitled Section for '{section_name}' "
            f"({untitled_before}->{untitled_after}). Visible text: {visible}",
            shot,
        ))
        print(
            f"[SCH Automation] New menu opened but Section click did not create Untitled Section for {section_name!r} "
            f"({untitled_before}->{untitled_after}); visible text:\n{visible[:2500]}"
        )
        _remove_banner(page)
        return False

    shot_untitled = take_screenshot(page, f"section_untitled_visible_{norm[:20]}")
    log_entries.append(make_log_entry(
        "", "info",
        f"Newest Untitled Section targeted for '{section_name}' via {title_desc}",
        shot_untitled,
    ))

    print(f"[SCH Automation] Renaming first section to {section_name}")
    renamed, rename_desc = _write_section_title(page, section_name, title_locator)
    if not renamed:
        shot = take_screenshot(page, f"section_rename_failed_{norm[:20]}")
        visible = _visible_page_text(page, limit=2500)
        log_entries.append(make_log_entry(
            "", "warn",
            f"Could not rename new section to '{section_name}': {rename_desc}. Visible text: {visible}",
            shot,
        ))
        print(f"[SCH Automation] section rename failed for {section_name!r}: {rename_desc}")
        _remove_banner(page)
        return False

    # After rename, the Untitled count should drop back down or the desired
    # section heading should appear. Verification below is the source of truth.
    deadline = time.time() + 5
    exists = False
    display_name = ""
    while time.time() < deadline:
        exists, display_name = _section_exists(page, section_name)
        if exists:
            break
        page.wait_for_timeout(400)

    if exists:
        shot = take_screenshot(page, f"section_created_{norm[:20]}")
        log_entries.append(make_log_entry("", "info", f"Created section: {display_name}", shot))
        print(f"[SCH Automation] Section created successfully: {display_name!r}")
        _remove_banner(page)
        return True

    shot = take_screenshot(page, f"section_verify_failed_{norm[:20]}")
    visible = _visible_page_text(page, limit=2500)
    log_entries.append(make_log_entry(
        "", "warn",
        f"Section '{section_name}' was typed but could not be verified. Visible text: {visible}",
        shot,
    ))
    print(f"[SCH Automation] section typed but verification failed: {section_name!r}")
    _remove_banner(page)
    return False


def ensure_section_exists(page, section_name: str, log_entries: list[dict] | None = None) -> bool:
    """Reuse an existing section or create it with the working New → Section flow."""
    log_entries = log_entries if log_entries is not None else []
    norm = _normalise_section_name(section_name)
    log_entries.append(make_log_entry("", "info", f"Checking for existing sections"))
    log_entries.append(make_log_entry("", "info", f"Checking if section exists: {section_name}"))
    print(f"[SCH Automation] Checking for existing sections")
    print(f"[SCH Automation] Checking if section exists: {section_name}")

    existing_sections = _get_existing_sections(page)
    if norm in existing_sections:
        display_name = existing_sections[norm] or section_name
        log_entries.append(make_log_entry("", "info", f"Reusing existing section: {display_name}"))
        print(f"[SCH Automation] Reusing existing section: {display_name}")
        return True

    if _empty_schedule_state_detected(page, existing_sections):
        log_entries.append(make_log_entry("", "info", "No sections found — empty schedule"))
        log_entries.append(make_log_entry("", "info", f"Empty schedule detected — creating first section: {section_name}"))
        print("[SCH Automation] No sections found — empty schedule")
        print(f"[SCH Automation] Empty schedule detected — creating first section: {section_name}")
        return create_section(page, section_name, log_entries)

    log_entries.append(make_log_entry("", "info", f"Section missing, creating: {section_name}"))
    print(f"[SCH Automation] Section missing, creating: {section_name}")
    return create_section(page, section_name, log_entries)


def _ensure_section_exists(page, category: str, log_entries: list[dict]) -> bool:
    """
    Ensure a section named ``category`` is present in the open schedule.
    Creates it via New → Section if missing.

    Returns True on success (already-existing or newly created).
    Returns False when the Section option could not be found in the dropdown
    so the caller can decide whether to skip or continue without the section.
    Never closes the browser.
    """
    norm = _normalise_section_name(category)
    approved = getattr(page, "_sch_approved_section_names", None)
    if approved is not None and norm not in approved:
        log_entries.append(make_log_entry(
            "", "warn",
            f"Refused to type non-section value into section header: {category!r}",
        ))
        print(f"[SCH Automation] refused unsafe section name: {category!r}")
        return False

    return ensure_section_exists(page, category, log_entries)


def _navigate_to_section(page, section_name: str) -> bool:
    """
    Scroll to and click the section heading matching ``section_name``.
    Returns True if found and clicked; False if not found (non-blocking — caller continues).
    """
    container, heading, detail = find_section_container(page, section_name)
    if heading is not None:
        try:
            heading.scroll_into_view_if_needed(timeout=2500)
            page.wait_for_timeout(300)
            heading.click(timeout=1500)
        except Exception:
            pass
        print(f"[SCH Automation] navigated to section: '{section_name}' via {detail}")
        return True

    norm = _normalise_section_name(section_name)
    selectors = [
        "[class*='section'][class*='header']",
        "[class*='section-title']",
        "[data-type='section']",
        "tr[class*='section']",
        "[role='rowgroup'] [class*='header']",
    ]
    for sel in selectors:
        try:
            elems = page.locator(sel)
            for i in range(min(elems.count(), 50)):
                try:
                    el = elems.nth(i)
                    text = el.inner_text(timeout=500).strip()
                    if _normalise_section_name(text) == norm:
                        el.scroll_into_view_if_needed(timeout=2000)
                        page.wait_for_timeout(300)
                        el.click(timeout=2000)
                        page.wait_for_timeout(500)
                        print(f"[SCH Automation] navigated to section: '{section_name}'")
                        return True
                except Exception:
                    pass
        except Exception:
            pass

    # Text-content fallback
    try:
        loc = page.get_by_text(section_name, exact=True)
        if loc.count() > 0 and loc.first.is_visible(timeout=600):
            loc.first.scroll_into_view_if_needed(timeout=2000)
            page.wait_for_timeout(300)
            loc.first.click(timeout=2000)
            page.wait_for_timeout(500)
            print(f"[SCH Automation] navigated to section (text fallback): '{section_name}'")
            return True
    except Exception:
        pass

    print(f"[SCH Automation] section '{section_name}' not found in DOM — proceeding without section nav")
    return False


def _find_section_heading(page, section_name: str):
    """Return the visible heading locator for section_name, or None."""
    norm = _normalise_section_name(section_name)
    selectors = [
        "[class*='section'][class*='header']",
        "[class*='section-title']",
        "[data-type='section']",
        "tr[class*='section']",
        "[role='rowgroup'] [class*='header']",
        "h1", "h2", "h3", "h4", "[role='heading']",
    ]
    for sel in selectors:
        try:
            elems = page.locator(sel)
            for i in range(min(elems.count(), 80)):
                try:
                    el = elems.nth(i)
                    if not el.is_visible(timeout=300):
                        continue
                    text = el.inner_text(timeout=500).strip()
                    lines = [line.strip() for line in text.splitlines() if line.strip()]
                    line_norms = {_normalise_section_name(line) for line in lines}
                    if _normalise_section_name(text) == norm or norm in line_norms:
                        return el
                except Exception:
                    pass
        except Exception:
            pass

    for exact in (True, False):
        try:
            loc = page.get_by_text(section_name, exact=exact)
            for i in range(min(loc.count(), 30)):
                el = loc.nth(i)
                if not el.is_visible(timeout=500):
                    continue
                text = el.inner_text(timeout=500).strip()
                if exact or _normalise_section_name(text) == norm or norm in {
                    _normalise_section_name(line.strip()) for line in text.splitlines() if line.strip()
                }:
                    return el
        except Exception:
            pass
    return None


def find_section_container(page, section_name: str):
    """
    Locate the visible section heading and return the section container/region
    that contains its inline action buttons.

    The returned locator may be an ancestor container when one contains
    "Custom Product", otherwise the heading itself. Downstream helpers also use
    y-bounds, so this remains useful even when Programa renders action rows as
    siblings rather than descendants.
    """
    heading = _find_section_heading(page, section_name)
    if heading is None:
        return None, None, f"section heading not found: {section_name!r}"

    try:
        heading.scroll_into_view_if_needed(timeout=2500)
        page.wait_for_timeout(300)
    except Exception:
        pass

    for ancestor in (
        "xpath=ancestor::*[contains(@class,'section')][1]",
        "xpath=ancestor::*[@role='rowgroup'][1]",
        "xpath=ancestor::*[@role='region'][1]",
        "xpath=ancestor::*[contains(@class,'group')][1]",
        "xpath=ancestor::*[contains(@class,'row')][1]",
        "xpath=ancestor::*[contains(@class,'card')][1]",
        "xpath=ancestor::*[1]",
        "xpath=ancestor::*[2]",
        "xpath=ancestor::*[3]",
        "xpath=ancestor::*[4]",
        "xpath=ancestor::*[5]",
    ):
        try:
            candidate = heading.locator(ancestor)
            if candidate.count() == 0 or not candidate.first.is_visible(timeout=300):
                continue
            text = candidate.first.inner_text(timeout=700)
            if "custom product" in text.lower() or "add from url" in text.lower():
                return candidate.first, heading, f"container via {ancestor}"
        except Exception:
            pass

    return heading, heading, "heading fallback; action row will be found by y-bounds"


def _dump_target_section(page, section_heading, section_name: str, label: str) -> str:
    """Screenshot and dump text/html near the target section for diagnostics."""
    safe = _normalise_section_name(section_name)[:24] or "section"
    shot = take_screenshot(page, f"{label}_{safe}")
    print(f"[SECTION] {label} screenshot={shot}")
    try:
        text = section_heading.inner_text(timeout=1000) if section_heading is not None else ""
        print(f"[SECTION] heading text:\n{text[:1200]}")
    except Exception as exc:
        print(f"[SECTION] heading text dump failed: {exc}")
    try:
        html = section_heading.locator("xpath=ancestor::*[self::tr or @role='row' or contains(@class,'section') or contains(@class,'row')][1]").inner_html(timeout=1200)
        print(f"[SECTION] nearest section/row HTML:\n{html[:5000]}")
    except Exception:
        try:
            html = section_heading.locator("xpath=ancestor::*[1]").inner_html(timeout=1200)
            print(f"[SECTION] nearest parent HTML:\n{html[:5000]}")
        except Exception as exc:
            print(f"[SECTION] HTML dump failed: {exc}")
    return shot


def _section_y_bounds(page, section_heading) -> tuple[float, float]:
    """Return viewport y bounds from this section heading to the next visible section heading."""
    top = 0.0
    bottom = 10_000.0
    try:
        bbox = section_heading.bounding_box()
        if bbox:
            top = float(bbox["y"])
            bottom = top + 4000
    except Exception:
        pass

    try:
        current_text = _normalise_section_name(section_heading.inner_text(timeout=500))
    except Exception:
        current_text = ""

    section_selectors = [
        "[class*='section'][class*='header']",
        "[class*='section-title']",
        "[data-type='section']",
        "tr[class*='section']",
        "[role='rowgroup'] [class*='header']",
        "h1", "h2", "h3", "h4", "[role='heading']",
    ]
    next_tops: list[float] = []
    for sel in section_selectors:
        try:
            elems = page.locator(sel)
            for i in range(min(elems.count(), 80)):
                try:
                    el = elems.nth(i)
                    if not el.is_visible(timeout=200):
                        continue
                    text = _normalise_section_name(el.inner_text(timeout=300))
                    if not text or text == current_text:
                        continue
                    bbox = el.bounding_box()
                    if bbox and float(bbox["y"]) > top + 8:
                        next_tops.append(float(bbox["y"]))
                except Exception:
                    pass
        except Exception:
            pass
    if next_tops:
        bottom = min(next_tops)
    return top, bottom


def _is_locator_between_y(locator, top: float, bottom: float) -> bool:
    try:
        bbox = locator.bounding_box()
        if not bbox:
            return False
        cy = float(bbox["y"]) + float(bbox["height"]) / 2
        return cy > top and cy < bottom
    except Exception:
        return False


def _count_details_buttons_in_section(page, section_heading) -> int:
    top, bottom = _section_y_bounds(page, section_heading)
    count = 0
    for text in DETAILS_BUTTON_TEXTS:
        try:
            loc = page.get_by_text(text, exact=True)
            for i in range(min(loc.count(), 80)):
                el = loc.nth(i)
                if el.is_visible(timeout=150) and _is_locator_between_y(el, top, bottom):
                    count += 1
        except Exception:
            pass
    for sel in DETAILS_BUTTON_SELECTORS:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 80)):
                el = loc.nth(i)
                if el.is_visible(timeout=150) and _is_locator_between_y(el, top, bottom):
                    count += 1
        except Exception:
            pass
    return count


def _find_section_custom_product_button(page, section_heading):
    """Find the Custom Product button/control closest below the target section heading."""
    top, bottom = _section_y_bounds(page, section_heading)
    candidates: list[tuple[float, object, str]] = []
    for text in CUSTOM_PRODUCT_TEXTS:
        try:
            loc = page.get_by_text(text, exact=False)
            for i in range(min(loc.count(), 80)):
                el = loc.nth(i)
                if not el.is_visible(timeout=250):
                    continue
                bbox = el.bounding_box()
                if not bbox:
                    continue
                cy = float(bbox["y"]) + float(bbox["height"]) / 2
                if cy > top and cy < bottom:
                    candidates.append((cy - top, el, f"text={text!r} idx={i}"))
        except Exception:
            pass
    for role in ("button", "link"):
        for text in CUSTOM_PRODUCT_TEXTS:
            try:
                loc = page.get_by_role(role, name=text, exact=False)
                for i in range(min(loc.count(), 40)):
                    el = loc.nth(i)
                    if not el.is_visible(timeout=250):
                        continue
                    bbox = el.bounding_box()
                    if not bbox:
                        continue
                    cy = float(bbox["y"]) + float(bbox["height"]) / 2
                    if cy > top and cy < bottom:
                        candidates.append((cy - top, el, f"role={role} text={text!r} idx={i}"))
            except Exception:
                pass
    if not candidates:
        return None, "not_found"
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1], candidates[0][2]


def _count_product_like_items_in_section(page, section_heading) -> int:
    """Best-effort count of product rows/cards within a section's vertical bounds."""
    top, bottom = _section_y_bounds(page, section_heading)
    selectors = [
        "[role='row']",
        "tr",
        "[class*='product' i]",
        "[class*='card' i]",
        "[class*='item' i]",
        "[class*='rectangle' i]",
    ]
    seen: set[str] = set()
    count = 0
    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 120)):
                el = loc.nth(i)
                if not el.is_visible(timeout=150) or not _is_locator_between_y(el, top, bottom):
                    continue
                text = (el.inner_text(timeout=250) or "").strip().lower()
                if not text:
                    continue
                if any(action in text for action in ("add from url", "product from library", "custom product", "section")) and "product name" not in text:
                    continue
                bbox = el.bounding_box()
                key = f"{round(bbox['x'])}:{round(bbox['y'])}:{round(bbox['width'])}:{round(bbox['height'])}" if bbox else f"{sel}:{i}"
                if key in seen:
                    continue
                seen.add(key)
                count += 1
        except Exception:
            pass
    return max(count, _count_details_buttons_in_section(page, section_heading))


def click_inline_custom_product_for_section(page, section_name: str, index: int = 1, total: int = 1):
    """
    Click the inline Custom Product button inside the target section and wait for
    a blank product row/card to appear in that same section.
    """
    container, heading, container_detail = find_section_container(page, section_name)
    if heading is None:
        shot = take_screenshot(page, f"section_not_found_{_normalise_section_name(section_name)[:24]}")
        return False, f"target section not found: {section_name!r}", None, 0, 0, shot

    print(f"[SCH Automation] target section found: {section_name!r} via {container_detail}")
    shot_before = _dump_target_section(page, heading, section_name, "section_before_inline_custom_product")
    before = _count_product_like_items_in_section(page, heading)
    print(f"[SCH Automation] section product count BEFORE inline Custom Product: {before}")

    button = None
    method = ""
    if container is not None:
        for role in ("button", "link"):
            for text in CUSTOM_PRODUCT_TEXTS:
                try:
                    loc = container.get_by_role(role, name=text, exact=False)
                    for i in range(min(loc.count(), 10)):
                        candidate = loc.nth(i)
                        if candidate.is_visible(timeout=300):
                            button = candidate
                            method = f"container role={role} text={text!r} idx={i}"
                            break
                    if button:
                        break
                except Exception:
                    pass
            if button:
                break
        if button is None:
            for text in CUSTOM_PRODUCT_TEXTS:
                try:
                    loc = container.get_by_text(text, exact=False)
                    for i in range(min(loc.count(), 10)):
                        candidate = loc.nth(i)
                        if candidate.is_visible(timeout=300):
                            button = candidate
                            method = f"container text={text!r} idx={i}"
                            break
                    if button:
                        break
                except Exception:
                    pass

    if button is None:
        button, method = _find_section_custom_product_button(page, heading)

    if button is None:
        shot = _dump_target_section(page, heading, section_name, "section_inline_custom_product_not_found")
        return False, f"inline Custom Product button not found | before_count={before} screenshot={shot}", None, before, before, shot

    _inject_banner(page, f"SCH DesignOps  ·  Item {index}/{total}  ·  Adding Custom Product in {section_name}…")
    ok, click_desc = _robust_click_locator(page, button, f"inline Custom Product for {section_name}", timeout_ms=5000)
    if not ok:
        shot = _dump_target_section(page, heading, section_name, "section_inline_custom_product_click_failed")
        return False, f"inline Custom Product click failed: {click_desc} | method={method}", None, before, before, shot

    print(f"[SCH Automation] inline Custom Product clicked: {method} | {click_desc}")
    page.wait_for_timeout(900)
    shot_after_click = take_screenshot(page, f"section_inline_custom_product_clicked_{_normalise_section_name(section_name)[:24]}")
    print(f"[SCH Automation] after inline Custom Product screenshot: {shot_after_click}")

    row_locator = None
    row_detail = ""
    after = before
    deadline = time.time() + 10
    while time.time() < deadline:
        after = _count_product_like_items_in_section(page, heading)
        row_locator, row_detail = _find_newest_row_in_section(page, heading)
        if row_locator is not None and (after > before or after > 0):
            break
        if after > before:
            row_locator = _find_new_row(page)
            row_detail = f"product-like count {before}->{after}"
            if row_locator is not None:
                break
        page.wait_for_timeout(500)

    print(f"[SCH Automation] section product count AFTER inline Custom Product: {after}")
    if row_locator is None:
        shot = _dump_target_section(page, heading, section_name, "section_inline_new_row_not_found")
        return (
            False,
            f"new blank product row/card not found in section | before={before} after={after} screenshot={shot}",
            None,
            before,
            after,
            shot,
        )

    shot_row = take_screenshot(page, f"section_inline_product_row_created_{_normalise_section_name(section_name)[:24]}")
    print(f"[SCH Automation] newest row found in section: {row_detail} screenshot={shot_row}")
    return True, f"inline Custom Product ok via {method}; newest row: {row_detail}", row_locator, before, after, shot_before


def _find_newest_row_in_section(page, section_heading):
    """Find the newest product row/card inside the target section by locating the last Details button."""
    top, bottom = _section_y_bounds(page, section_heading)
    candidates: list[tuple[float, object, str]] = []
    for text in DETAILS_BUTTON_TEXTS:
        try:
            loc = page.get_by_text(text, exact=True)
            for i in range(min(loc.count(), 80)):
                el = loc.nth(i)
                if not el.is_visible(timeout=250):
                    continue
                bbox = el.bounding_box()
                if not bbox:
                    continue
                cy = float(bbox["y"]) + float(bbox["height"]) / 2
                if cy > top and cy < bottom:
                    candidates.append((cy, el, f"text={text!r} idx={i}"))
        except Exception:
            pass
    for sel in DETAILS_BUTTON_SELECTORS:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 80)):
                el = loc.nth(i)
                if not el.is_visible(timeout=250):
                    continue
                bbox = el.bounding_box()
                if not bbox:
                    continue
                cy = float(bbox["y"]) + float(bbox["height"]) / 2
                if cy > top and cy < bottom:
                    candidates.append((cy, el, f"css={sel!r} idx={i}"))
        except Exception:
            pass
    if not candidates:
        return None, "no Details button in section"
    candidates.sort(key=lambda item: item[0])
    button = candidates[-1][1]
    detail = candidates[-1][2]
    for ancestor in (
        "xpath=ancestor::*[@role='row'][1]",
        "xpath=ancestor::tr[1]",
        "xpath=ancestor::*[contains(@class,'row')][1]",
        "xpath=ancestor::*[contains(@class,'card')][1]",
        "xpath=ancestor::*[contains(@class,'product')][1]",
        "xpath=ancestor::*[contains(@class,'item')][1]",
        "xpath=ancestor::*[1]",
    ):
        try:
            row = button.locator(ancestor)
            if row.count() > 0 and row.first.is_visible(timeout=250):
                return row.first, f"row via {detail} ancestor={ancestor}"
        except Exception:
            pass
    return button, f"button fallback via {detail}"


def _create_custom_product_in_section(page, section_name: str, index: int, total: int):
    """
    Click the Custom Product control below section_name and return the newest row in that section.
    """
    return click_inline_custom_product_for_section(page, section_name, index=index, total=total)


def _build_section_map(
    page, categories: list[str], log_entries: list[dict]
) -> dict[str, str]:
    """
    Phase 2: Ensure every category in ``categories`` has a section in the open schedule.

    For each category:
    - If the section already exists → reuse it.
    - If not → attempt creation via New → Section
    - If creation fails → leave it out of the returned map so the caller can
      halt before product creation.

    Products must not be added until every needed section exists.
    """
    section_map: dict[str, str] = {}
    try:
        page._sch_approved_section_names = {
            _normalise_section_name(cat) for cat in categories if str(cat or "").strip()
        }
    except Exception:
        pass

    try:
        existing_before = list(_get_existing_sections(page).values())
    except Exception:
        existing_before = []
    log_entries.append(make_log_entry("", "info", f"Existing sections found: {existing_before}"))
    log_entries.append(make_log_entry("", "info", f"Needed sections: {categories}"))
    print(f"[SCH Automation] Existing sections found: {existing_before}")
    print(f"[SCH Automation] Needed sections: {categories}")

    for cat in categories:
        norm = _normalise_section_name(cat)
        if not norm:
            continue
        log_entries.append(make_log_entry("", "info", f"Checking if section exists: {cat}"))
        exists, display_existing = _section_exists(page, cat)

        if exists:
            display = display_existing
            log_entries.append(make_log_entry("", "info", f"Reusing existing section: {display}"))
            print(f"[SCH Automation] Reusing existing section: {display}")
            section_map[cat] = display
        else:
            log_entries.append(make_log_entry("", "info", f"Creating missing section: {cat}"))
            print(f"[SCH Automation] Creating missing section: {cat}")
            ok = _ensure_section_exists(page, cat, log_entries)
            if not ok:
                log_entries.append(make_log_entry(
                    "", "error",
                    f"[PHASE 2] Could not create required section '{cat}'. "
                    "Product entry will not start until all sections are ready.",
                ))
                print(f"[SCH Automation] [PHASE 2] section creation failed for '{cat}' — blocking product entry")
            else:
                # Re-scan to get the confirmed display name (DOM may capitalise differently)
                exists, display = _section_exists(page, cat)
                if exists:
                    display = display if display else cat
                    log_entries.append(make_log_entry("", "info", f"[PHASE 2] Section ready: {display!r}"))
                    print(f"[SCH Automation] [PHASE 2] Created section: {display!r}")
                    section_map[cat] = display
                else:
                    log_entries.append(make_log_entry(
                        "", "error",
                        f"[PHASE 2] Section '{cat}' was created but could not be verified. "
                        "Product entry will not start.",
                    ))
                    print(f"[SCH Automation] [PHASE 2] section verification failed for '{cat}' — blocking product entry")

    return section_map


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
    if not _real_integrations_enabled():
        return (
            "Demo mode: Programa login is disabled. No browser window was opened. "
            "Set DEMO_MODE=false and ENABLE_REAL_INTEGRATIONS=true only in a private environment."
        )

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
    single_row_test_mode: bool = False,
    schedule_url: str = "",
    upload_product_images: bool = True,
) -> list[dict]:
    """
    Open a persistent Chrome profile and process each URL row.

    Parameters
    ----------
    rows                 : List of row dicts from the intake table.
    project_name         : Project name (used for logging only when schedule_url is set).
    auto_click_done      : When True, automatically click Done/Save after each item.
    skip_navigation      : When True, skip automatic project navigation and instead
                           show a browser dialog asking the user to confirm they are
                           already inside the correct project.
    single_row_test_mode : When True, only the first row is processed regardless of
                           how many rows are in the list.
    schedule_url         : When provided, navigate directly to this URL after login
                           and skip all project/file navigation phases (Phase 0 & 1).

    Returns the list of log entries (does not write the log to disk).

    Navigation failure
    ------------------
    If skip_navigation is False, schedule_url is empty, and auto-navigation fails,
    the function logs a "nav_failed" entry and returns early (without processing any
    URLs).  The Streamlit UI detects this status and shows the "Continue After Manual
    Project Open" button, which re-calls this function with skip_navigation=True.
    """
    if not _real_integrations_enabled():
        return [
            make_log_entry(
                row.get("Product Name") or row.get("Name of Product") or "",
                "skipped",
                "Demo mode: Programa send is disabled. No browser automation or external API request was made.",
            )
            for row in rows
        ]

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
    original_row_count = len(rows)
    if single_row_test_mode and len(rows) > 1:
        rows = rows[:1]
    print(f"[SCH Automation] rows queued for send: {len(rows)} (original={original_row_count})")
    if schedule_url:
        print(f"[SCH Automation] schedule_url received: {schedule_url}")
    print(f"[IMAGE] Image upload {'enabled' if upload_product_images else 'disabled'}")

    # _stop_reason is set (non-empty) when we must halt before completing all rows.
    # Nothing returns early inside the with-block; instead each phase checks this flag.
    # The finally clause always shows the user a dialog and closes cleanly.
    _stop_reason: str = ""
    _page_ref: list = []  # mutable ref so finally can access page even on launch error

    with sync_playwright() as pw:
        # ── Launch persistent context ─────────────────────────────────────────
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            slow_mo=1000,
            viewport={"width": 1440, "height": 900},
        )
        log_entries.append(make_log_entry(
            "", "info",
            f"[Browser] Opened — profile: {PROFILE_DIR}",
        ))
        print(f"[SCH Automation] [Browser] Opened.")

        # ── Select the right page — prefer an existing workspace tab ──────────
        page = _select_workspace_page(context)
        _page_ref.append(page)
        try:
            print(f"[SCH Automation] selected automation page URL: {page.url}")
        except Exception:
            print("[SCH Automation] selected automation page URL: <unavailable>")

        # If _select_workspace_page gave us a new blank page, navigate to Programa
        current_url = ""
        try:
            current_url = page.url
        except Exception:
            pass
        if not current_url or current_url in ("about:blank", "chrome://newtab/"):
            print(f"[PAGE] New blank page — navigating to Programa")
            try:
                page.goto(PROGRAMA_URL, wait_until="domcontentloaded")
            except Exception as exc:
                print(f"[PAGE] Initial goto error: {exc}")

        # Ensure we are on a workspace URL (not changelog)
        _ensure_workspace_url(page, phase="launch")

        try:
            # ── Check login state ─────────────────────────────────────────────
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
                    shot = take_screenshot(page, "login_timeout")
                    log_entries.append(make_log_entry(
                        "", "error",
                        "Login timed out after 5 minutes.",
                        shot,
                    ))
                    _stop_reason = "login timeout after 5 minutes"
                else:
                    _remove_banner(page)
                    log_entries.append(make_log_entry("", "info", "Login successful — session will be saved."))

            # ── Navigate to the target project / schedule ─────────────────────
            if not _stop_reason:
                if schedule_url:
                    # Direct URL — navigate straight to the schedule page, skip all project/file nav.
                    log_entries.append(make_log_entry(
                        "", "nav_direct",
                        f"[PHASE 0] Direct schedule URL — navigating to: {schedule_url}",
                    ))
                    print(f"[SCH Automation] [PHASE 0] Direct URL: {schedule_url}")
                    nav_ok, nav_detail = _navigate_directly_to_schedule_url(page, schedule_url)
                    if nav_ok:
                        log_entries.append(make_log_entry("", "nav_direct", f"[PHASE 0] {nav_detail}"))
                        print(f"[SCH Automation] [PHASE 0] {nav_detail}")
                        page.bring_to_front()
                    else:
                        shot = take_screenshot(page, "schedule_url_nav_failed")
                        log_entries.append(make_log_entry("", "error", f"[PHASE 0] {nav_detail}", shot))
                        print(f"[SCH Automation] [PHASE 0] direct schedule navigation failed: {nav_detail}")
                        _stop_reason = f"direct schedule navigation failed: {nav_detail}"
                elif skip_navigation:
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
                    _ensure_workspace_url(page, phase="Phase 0 — project nav")
                    _inject_banner(page, f"SCH DesignOps  ·  [Phase 0] Navigating to project: {project_name}…")
                    nav_ok, nav_method = navigate_to_project(page, project_name)
                    _remove_banner(page)

                    if nav_ok:
                        log_entries.append(make_log_entry(
                            "", "nav_success",
                            f"[PHASE 0] Project '{project_name}' found and opened — method: {nav_method}.",
                        ))
                        print(f"[SCH Automation] [PHASE 0] project opened: {project_name!r} method={nav_method}")
                        page.bring_to_front()
                        page.wait_for_timeout(800)
                    else:
                        shot = take_screenshot(page, "nav_failed")
                        log_entries.append(make_log_entry(
                            "", "nav_failed",
                            f"Could not locate project '{project_name}' automatically "
                            f"(tried search, card, and partial-match strategies). "
                            "Use 'Continue After Manual Project Open' in the app to proceed.",
                            shot,
                        ))
                        # Do NOT close here — _stop_reason gates the rest; finally closes.
                        _stop_reason = f"nav_failed: '{project_name}' not found automatically"

            # ══════════════════════════════════════════════════════════════════
            # Phase 1 — Open schedule file
            # Phase 2 — Build / verify sections
            # ══════════════════════════════════════════════════════════════════
            schedule_rows = [r for r in rows if not _is_url_row(r)]
            section_map: dict[str, str] = {}
            schedule_nav_done = False

            if not _stop_reason and schedule_rows:
                unique_categories: list[str] = list(dict.fromkeys(
                    _get_row_section_name(r) for r in schedule_rows
                ))

                if schedule_url:
                    # Direct URL navigation — already on the schedule page; skip Phase 1.
                    log_entries.append(make_log_entry(
                        "", "info",
                        "[PHASE 1] Skipped — direct schedule URL provided, already on schedule page.",
                    ))
                    print(f"[SCH Automation] [PHASE 1] Skipped (direct URL navigation)")
                    schedule_nav_done = True
                else:
                    _ensure_workspace_url(page, phase="Phase 1 — schedule open")
                    log_entries.append(make_log_entry("", "info", "[PHASE 1] Opening Schedule file…"))
                    print(f"[SCH Automation] [PHASE 1] opening schedule")
                    _inject_banner(page, "SCH DesignOps  ·  [Phase 1] Opening Schedule…")
                    _open_schedule_file(page, 1, 1)
                    page.bring_to_front()
                    schedule_nav_done = True
                    log_entries.append(make_log_entry("", "info", "[PHASE 1] Schedule opened."))
                    print(f"[SCH Automation] [PHASE 1] schedule opened")

                log_entries.append(make_log_entry(
                    "", "info",
                    f"[PHASE 2] Sections needed: {unique_categories}",
                ))
                print(f"[SCH Automation] [PHASE 2] verifying/creating {len(unique_categories)} section(s)")
                _inject_banner(
                    page,
                    f"SCH DesignOps  ·  [Phase 2] Verifying/creating {len(unique_categories)} section(s)…",
                )
                section_map = _build_section_map(page, unique_categories, log_entries)
                _remove_banner(page)

                missing_sections: list[str] = []
                for category in unique_categories:
                    display_name = section_map.get(category, "")
                    if not display_name:
                        missing_sections.append(category)
                        continue
                    verified, verified_display = _section_exists(page, display_name)
                    if not verified:
                        verified, verified_display = _section_exists(page, category)
                    if verified:
                        section_map[category] = verified_display or display_name
                    else:
                        missing_sections.append(category)

                if missing_sections:
                    shot = take_screenshot(page, "sections_not_ready")
                    message = (
                        f"[PHASE 2] Required section(s) not ready: {missing_sections}. "
                        "Product entry was not started."
                    )
                    log_entries.append(make_log_entry("", "error", message, shot))
                    print(f"[SCH Automation] {message}")
                    _stop_reason = f"sections not ready: {', '.join(missing_sections)}"
                else:
                    log_entries.append(make_log_entry("", "info", "All sections ready"))
                    print("[SCH Automation] All sections ready")

                log_entries.append(make_log_entry(
                    "", "info",
                    f"[PHASE 2] Complete — {len(section_map)} section(s) mapped: "
                    f"{list(section_map.keys())}",
                ))
                print(
                    f"[SCH Automation] [PHASE 2] complete — "
                    f"{len(section_map)} section(s): {list(section_map.keys())}"
                )

            # ══════════════════════════════════════════════════════════════════
            # Phase 3 — Add products one by one
            # ══════════════════════════════════════════════════════════════════
            if not _stop_reason:
                _ensure_workspace_url(page, phase="Phase 3 — product entry")
                page.bring_to_front()
                _activate_automation_tab(context, page)
                log_entries.append(make_log_entry(
                    "", "info",
                    f"[PHASE 3] Starting — {len(rows)} item(s) queued for schedule '{schedule_url or project_name}'.",
                ))
                print(f"[SCH Automation] [PHASE 3] product entry step reached — rows={len(rows)}")
                if original_row_count > len(rows):
                    log_entries.append(make_log_entry(
                        "", "debug_one_row",
                        f"[PHASE 3] Single-row test mode — processing 1 of {original_row_count} selected row(s).",
                    ))
                    print(f"[SCH Automation] [PHASE 3] single-row test mode: 1 of {original_row_count} row(s)")

                total = len(rows)
                photo_total = sum(1 for row in rows if _row_is_photo_only(row))
                photo_seen = 0
                photo_success = 0
                photo_failed = 0
                # ── per-run stats for API fast-path summary ────────────────────
                _stat_api_success = 0
                _stat_ui_fallback = 0
                _stat_api_times: list[float] = []
                _stat_ui_times: list[float] = []
                _stat_api_fail_reasons: list[str] = []
                if photo_total:
                    msg = f"Bulk image upload: starting {photo_total} images"
                    log_entries.append(make_log_entry("", "info", msg))
                    print(f"[SCH Automation] {msg}")
                for i, row in enumerate(rows, start=1):
                    try:
                        if _is_url_row(row):
                            _row_t0 = time.monotonic()
                            entry = _process_url_row(page, row, auto_click_done, index=i, total=total)
                            schedule_nav_done = bool(schedule_url)
                        else:
                            raw_section = _get_row_section_name(row)
                            target_section = section_map.get(raw_section, raw_section)
                            is_photo_row = _row_is_photo_only(row)
                            if is_photo_row:
                                photo_seen += 1
                            product_name_log = str(row.get("Product Name", "?") or "?").strip()
                            log_entries.append(make_log_entry(
                                "", "info",
                                f"[PHASE 3] Adding item {i} of {total}: {product_name_log!r} → {target_section!r}",
                            ))
                            print(
                                f"[SCH Automation] [PHASE 3] Adding item {i}/{total}: "
                                f"{product_name_log!r} → {target_section!r}"
                            )
                            _row_t0 = time.monotonic()
                            entry = _process_schedule_row(
                                page, row, auto_click_done, index=i, total=total,
                                skip_nav=schedule_nav_done,
                                target_section=target_section,
                                upload_product_images=upload_product_images,
                                photo_index=photo_seen if is_photo_row else None,
                                photo_total=photo_total if is_photo_row else None,
                            )
                            _row_elapsed = round(time.monotonic() - _row_t0, 1)
                            # Tag entry and collect stats
                            if not is_photo_row:
                                if entry.get("_path") == "fast_path":
                                    _stat_api_success += 1
                                    _stat_api_times.append(entry.get("_elapsed_s", _row_elapsed))
                                else:
                                    _stat_ui_fallback += 1
                                    _stat_ui_times.append(_row_elapsed)
                                    reason = ""
                                    msg_text = entry.get("message", "")
                                    if "api_fast_path" in msg_text:
                                        import re as _re
                                        m = _re.search(r"'api_fast_path': '([^']+)'", msg_text)
                                        reason = m.group(1) if m else "see_message"
                                    elif entry.get("status") == "error":
                                        reason = f"row_error: {msg_text[:80]}"
                                    if reason:
                                        _stat_api_fail_reasons.append(reason)
                                        print(f"[API] Fallback reason: {reason}")
                            if entry["status"] not in ("error",):
                                schedule_nav_done = True
                    except Exception as exc:
                        import traceback as _tb
                        shot = ""
                        try:
                            shot = take_screenshot(page, f"row_exception_{i}")
                        except Exception:
                            pass
                        entry = make_log_entry(
                            str(row.get("Product URL", "") or ""),
                            "error",
                            f"Exception on row {i}: {exc}",
                            shot,
                            product_name=str(row.get("Product Name", "") or ""),
                        )
                        print(f"[SCH Automation] ROW EXCEPTION row={i}: {exc!r}\n{_tb.format_exc()}")

                    log_entries.append(entry)
                    if entry["status"] == "error":
                        if _row_is_photo_only(row):
                            photo_failed += 1
                            page.wait_for_timeout(500)
                            continue
                        else:
                            _stop_reason = (
                                f"field entry error on item {i}: "
                                f"{entry.get('message', '')[:160]}"
                            )
                            break
                    elif _row_is_photo_only(row):
                        photo_success += 1
                    page.wait_for_timeout(1200)

                if photo_total:
                    summary = (
                        f"Bulk image upload final summary: {photo_total} images processed, "
                        f"{photo_success} succeeded, {photo_failed} failed"
                    )
                    log_entries.append(make_log_entry("", "info", summary))
                    print(f"[SCH Automation] {summary}")

                # ── API fast-path summary ──────────────────────────────────────
                _normal_rows = _stat_api_success + _stat_ui_fallback
                _avg_api = round(sum(_stat_api_times) / len(_stat_api_times), 1) if _stat_api_times else None
                _avg_ui  = round(sum(_stat_ui_times) / len(_stat_ui_times), 1) if _stat_ui_times else None
                from collections import Counter as _Counter
                _reason_counts = _Counter(_stat_api_fail_reasons)
                _summary_lines = [
                    f"[API] ══ Fast-path summary ══",
                    f"[API] Total rows processed : {total}",
                    f"[API] Normal rows           : {_normal_rows}",
                    f"[API] API fast-path success : {_stat_api_success}",
                    f"[API] UI fallback count     : {_stat_ui_fallback}",
                    f"[API] Avg time API item     : {f'{_avg_api}s' if _avg_api is not None else 'n/a'}",
                    f"[API] Avg time UI item      : {f'{_avg_ui}s' if _avg_ui is not None else 'n/a'}",
                ]
                if _reason_counts:
                    _summary_lines.append("[API] Fallback reasons:")
                    for _reason, _count in _reason_counts.most_common():
                        _summary_lines.append(f"[API]   {_count}x  {_reason}")
                else:
                    _summary_lines.append("[API] Fallback reasons: none (all API or no normal rows)")
                _summary_lines.append(f"[API] ══════════════════════")
                for _line in _summary_lines:
                    print(_line)
                log_entries.append(make_log_entry("", "info", "\n".join(_summary_lines)))

        except Exception as exc:
            import traceback as _tb
            shot = ""
            try:
                shot = take_screenshot(page, "unhandled_exception")
            except Exception:
                pass
            log_entries.append(make_log_entry(
                "", "error",
                f"[Browser] Unhandled exception — automation stopped: {exc}",
                shot,
            ))
            _stop_reason = f"unhandled exception: {exc}"
            print(f"[SCH Automation] UNHANDLED EXCEPTION:\n{_tb.format_exc()}")

        finally:
            # ── Always close with a reason logged ────────────────────────────
            _pg = _page_ref[0] if _page_ref else None

            if _stop_reason:
                print(f"[SCH Automation] [Browser] Closing because: {_stop_reason}")
                log_entries.append(make_log_entry(
                    "", "info",
                    f"[Browser] Closing because: {_stop_reason}",
                ))
                if KEEP_BROWSER_OPEN_ON_FAILURE and _pg is not None:
                    _remove_banner(_pg)
                    _inject_banner(
                        _pg,
                        f"SCH DesignOps  ·  Stopped — review Programa then click OK to close.",
                    )
                    try:
                        _js_alert(
                            _pg,
                            f"SCH DesignOps — Browser Paused\n\n"
                            f"Stopped because: {_stop_reason}\n\n"
                            "Review Programa in this window.\n"
                            "Click OK when ready to close the browser.",
                        )
                    except Exception:
                        pass
                    _remove_banner(_pg)
            else:
                print("[SCH Automation] [Browser] Closing because: automation complete.")
                success_n = sum(1 for e in log_entries if e["status"] == "success")
                filled_n  = sum(1 for e in log_entries if e["status"] == "filled_awaiting_confirm")
                error_n   = sum(1 for e in log_entries if e["status"] == "error")
                log_entries.append(make_log_entry(
                    "", "info",
                    f"[Browser] Closing because: automation complete — "
                    f"saved={success_n} filled={filled_n} errors={error_n}",
                ))
                if _pg is not None:
                    try:
                        take_screenshot(_pg, "automation_complete")
                    except Exception:
                        pass
                    try:
                        _js_alert(
                            _pg,
                            f"SCH DesignOps — Done\n\n"
                            f"Saved: {success_n}   Filled (manual): {filled_n}   Errors: {error_n}\n\n"
                            "Session saved. You may close this window.",
                        )
                    except Exception:
                        pass

            try:
                context.close()  # saves session to PROFILE_DIR
            except Exception:
                pass

    return log_entries


def run_programa_automation(
    rows: list[dict],
    project_name: str = "",
    auto_done: bool = False,
    skip_navigation: bool = False,
    single_row_test_mode: bool = False,
    schedule_url: str = "",
    upload_product_images: bool = True,
) -> tuple[list[dict], str]:
    """
    Full orchestrator — runs the automation and persists a JSON log.
    Returns (log_entries, log_file_path).
    """
    if not _real_integrations_enabled():
        entries = [
            make_log_entry(
                row.get("Product Name") or row.get("Name of Product") or "",
                "skipped",
                "Demo mode: Programa send is disabled. No browser automation or external API request was made.",
            )
            for row in rows
        ]
        return entries, ""

    entries = send_urls_to_programa(
        rows,
        project_name=project_name,
        auto_click_done=auto_done,
        skip_navigation=skip_navigation,
        single_row_test_mode=single_row_test_mode,
        schedule_url=schedule_url,
        upload_product_images=upload_product_images,
    )
    log_label = project_name or (schedule_url.rstrip("/").split("/")[-1] if schedule_url else "intake")
    log_path = save_log(entries, log_label)
    return entries, log_path


# ── Field-entry diagnostic ──────────────────────────────────────────────────────


def run_programa_field_diagnostic(
    row: dict,
    project_name: str,
    out_dir: str | None = None,
) -> list[dict]:
    """
    Step-by-step diagnostic for a single row.

    Runs every stage of the Programa entry workflow individually, screenshots
    after every step, stops at the first failure, and always keeps the browser
    open so you can inspect the UI.

    Steps diagnosed
    ---------------
    1  browser_opened
    2  programa_loaded
    3  project_nav
    4  schedule_opened
    5  section_nav
    6  new_clicked
    7  custom_product_clicked
    8  new_row_detected
    9  product_name_field_located
    10 product_name_cell_clicked
    11 editor_appeared
    12 product_name_typed
    13 product_name_committed

    Returns a list of step-result dicts (same schema as run_programa_debug_single_row).
    Also writes a JSON file to out_dir.
    """
    import json
    import traceback as _tb

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [{"step": "import", "success": False,
                 "message": "Playwright not installed",
                 "screenshot_path": "", "detail": {}}]

    os.makedirs(out_dir, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    ensure_dirs()

    out_dir = out_dir or str(runtime_data_path("programa_diagnostics"))
    steps: list[dict] = []
    product_name = str(row.get("Product Name", "") or "").strip()
    dims = parse_dimensions_for_programa(str(row.get("Dimensions", "") or ""))
    target_section = _get_row_section_name(row)

    def _snap(pg, label: str) -> str:
        ts = int(time.time() * 1000)
        path = os.path.join(out_dir, f"{ts}_{label}.png")
        try:
            pg.screenshot(path=path, full_page=False)
        except Exception:
            pass
        return path

    def _log(name: str, ok: bool, msg: str = "", shot: str = "", detail: dict | None = None) -> dict:
        tag = "OK  " if ok else "FAIL"
        print(f"[Diagnostic] {tag} — {name}: {msg}")
        entry = {"step": name, "success": ok, "message": msg,
                 "screenshot_path": shot, "detail": detail or {}}
        steps.append(entry)
        return entry

    def _save_report() -> str:
        ts_str = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(out_dir, f"diagnostic_{ts_str}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(steps, fh, indent=2, ensure_ascii=False)
        return path

    print(f"[Diagnostic] Starting — product={product_name!r} section={target_section!r}")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            slow_mo=1500,
            viewport={"width": 1440, "height": 900},
        )
        page = _select_workspace_page(context)
        if not page.url or page.url in ("about:blank", "chrome://newtab/"):
            page.goto(PROGRAMA_URL, wait_until="domcontentloaded", timeout=30_000)
        _ensure_workspace_url(page, phase="diagnostic-launch")
        _inject_banner(page, "SCH Diagnostic  ·  Starting…")

        try:
            # ── Step 1: browser opened ────────────────────────────────────────
            _log("browser_opened", True, f"profile={PROFILE_DIR}")

            # ── Step 2: load Programa ─────────────────────────────────────────
            try:
                page.goto(PROGRAMA_URL, wait_until="domcontentloaded", timeout=30_000)
                shot = _snap(page, "02_loaded")
                if not _is_logged_in(page):
                    _inject_banner(page, "SCH Diagnostic  ·  Please log in, then wait…")
                    _log("login_needed", False, "Login form visible — waiting up to 5 min", shot)
                    if not _wait_for_login(page, timeout_seconds=300):
                        _log("login_timeout", False, "Timed out", _snap(page, "02_login_timeout"))
                        raise StopIteration("login timeout")
                    _remove_banner(page)
                    _log("login_ok", True, "Login successful")
                _log("programa_loaded", True, f"url={page.url}", _snap(page, "02_programa"))
            except StopIteration:
                raise
            except Exception as exc:
                _log("programa_loaded", False, str(exc), _snap(page, "02_load_fail"), {"exc": _tb.format_exc()})
                raise StopIteration("programa failed to load")

            # ── Step 3: project navigation ────────────────────────────────────
            _inject_banner(page, f"SCH Diagnostic  ·  Navigating to project '{project_name}'…")
            try:
                nav_ok, nav_method = navigate_to_project(page, project_name)
                shot = _snap(page, "03_project")
                _log("project_nav", nav_ok,
                     f"method={nav_method}" if nav_ok else f"not found — tried: search,card,partial",
                     shot, {"nav_method": nav_method, "url": page.url})
                if not nav_ok:
                    _js_confirm(page,
                        f"Diagnostic — project '{project_name}' not found.\n\n"
                        "Navigate to the project manually, then click OK to continue.")
                    _log("project_nav_manual", True, "User navigated manually")
            except Exception as exc:
                _log("project_nav", False, str(exc), _snap(page, "03_nav_fail"), {"exc": _tb.format_exc()})
                raise StopIteration(f"project nav exception: {exc}")

            page.wait_for_timeout(800)

            # ── Step 4: open schedule file ────────────────────────────────────
            _inject_banner(page, "SCH Diagnostic  ·  Opening Schedule file…")
            try:
                sched_ok, sched_method = _navigate_to_schedule_file(page)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                page.wait_for_timeout(1000)
                shot = _snap(page, "04_schedule")
                _log("schedule_opened", sched_ok,
                     f"method={sched_method}",
                     shot, {"url": page.url})
                if not sched_ok:
                    _js_confirm(page,
                        "Diagnostic — Schedule file not found automatically.\n\n"
                        "Open the Schedule file manually, then click OK to continue.")
                    _log("schedule_manual", True, "User opened schedule manually")
            except Exception as exc:
                _log("schedule_opened", False, str(exc), _snap(page, "04_sched_fail"), {"exc": _tb.format_exc()})
                raise StopIteration(f"schedule nav exception: {exc}")

            # ── Step 5: navigate to section ───────────────────────────────────
            _inject_banner(page, f"SCH Diagnostic  ·  Navigating to section '{target_section}'…")
            try:
                found_section = _navigate_to_section(page, target_section)
                shot = _snap(page, "05_section")
                _log("section_nav", found_section,
                     f"section={target_section!r} {'found' if found_section else 'not found in DOM'}",
                     shot, {"section": target_section})
            except Exception as exc:
                _log("section_nav", False, str(exc), _snap(page, "05_section_fail"), {"exc": _tb.format_exc()})

            page.wait_for_timeout(500)

            # ── Step 6: click New ─────────────────────────────────────────────
            _inject_banner(page, "SCH Diagnostic  ·  Clicking New…")
            before_count = _count_product_rows(page)
            try:
                new_ok = _click_by_text(page, NEW_ITEM_TEXTS, timeout_ms=5000)
                page.wait_for_timeout(1200)
                shot = _snap(page, "06_new_clicked")
                _log("new_clicked", new_ok,
                     "New button clicked" if new_ok else f"NOT found — tried: {NEW_ITEM_TEXTS}",
                     shot, {"texts_tried": NEW_ITEM_TEXTS})
                if not new_ok:
                    raise StopIteration("New button not found")
            except StopIteration:
                raise
            except Exception as exc:
                _log("new_clicked", False, str(exc), _snap(page, "06_new_fail"), {"exc": _tb.format_exc()})
                raise StopIteration(f"new click exception: {exc}")

            # ── Step 7: click Custom Product ──────────────────────────────────
            _inject_banner(page, "SCH Diagnostic  ·  Clicking Custom Product…")
            custom_clicked = False
            clicked_text = ""
            cp_exc = ""
            for text in CUSTOM_PRODUCT_TEXTS:
                if custom_clicked:
                    break
                try:
                    loc = page.get_by_text(text, exact=False)
                    for j in range(min(loc.count(), 8)):
                        try:
                            el = loc.nth(j)
                            if el.is_visible(timeout=500):
                                el.click(timeout=3000)
                                custom_clicked = True
                                clicked_text = text
                                break
                        except Exception:
                            continue
                except Exception as exc:
                    cp_exc = str(exc)
            shot = _snap(page, "07_custom_product")
            _log("custom_product_clicked", custom_clicked,
                 f"via '{clicked_text}'" if custom_clicked else
                 f"NOT found — tried: {CUSTOM_PRODUCT_TEXTS} exc={cp_exc}",
                 shot, {"texts_tried": CUSTOM_PRODUCT_TEXTS, "clicked": clicked_text})
            if not custom_clicked:
                raise StopIteration("Custom Product option not found in dropdown")

            page.wait_for_timeout(600)

            # ── Step 8: wait for new row ──────────────────────────────────────
            _inject_banner(page, "SCH Diagnostic  ·  Waiting for new row…")
            deadline = time.time() + 5
            after_count = before_count
            while time.time() < deadline:
                after_count = _count_product_rows(page)
                if after_count > before_count:
                    break
                page.wait_for_timeout(400)
            row_appeared = after_count > before_count
            shot = _snap(page, "08_new_row")
            _log("new_row_detected", row_appeared,
                 f"rows {before_count}→{after_count}",
                 shot, {"before": before_count, "after": after_count})
            if not row_appeared:
                raise StopIteration(f"Row count did not increase (before={before_count} after={after_count})")

            row_locator = _find_new_row(page, before_count=before_count)
            row_bbox = None
            if row_locator is not None:
                try:
                    row_bbox = row_locator.bounding_box()
                except Exception:
                    pass

            # ── Step 9: locate Product Name field ─────────────────────────────
            _inject_banner(page, "SCH Diagnostic  ·  Locating Product Name field…")
            pn_labels = SCHEDULE_FIELD_LABELS.get("Product Name", ["Product name", "Name"])
            header_found = False
            header_x = None
            for label in pn_labels:
                try:
                    safe = label.replace("'", "\\'")
                    hdr = page.locator(
                        f"th:has-text('{safe}'), "
                        f"[role='columnheader']:has-text('{safe}'), "
                        f"[class*='header' i]:has-text('{safe}')"
                    ).first
                    if hdr.is_visible(timeout=800):
                        hbox = hdr.bounding_box()
                        if hbox:
                            header_found = True
                            header_x = hbox["x"] + hbox["width"] / 2
                            break
                except Exception:
                    pass
            shot = _snap(page, "09_pn_field")
            _log("product_name_field_located", header_found,
                 f"header x={header_x:.0f}" if header_found else f"header not found — labels={pn_labels}",
                 shot, {"labels_tried": pn_labels, "header_x": header_x, "row_bbox": row_bbox})

            # ── Step 10: click the Product Name cell ──────────────────────────
            _inject_banner(page, "SCH Diagnostic  ·  Clicking Product Name cell…")
            cell_clicked = False
            click_detail: dict = {}
            if header_found and header_x is not None:
                try:
                    cy = (
                        (row_bbox["y"] + row_bbox["height"] / 2)
                        if row_bbox else (header_x + 40)
                    )
                    page.mouse.click(header_x, cy)
                    page.wait_for_timeout(700)
                    cell_clicked = True
                    click_detail = {"method": "header_x", "x": header_x, "y": cy}
                except Exception as exc:
                    click_detail = {"method": "header_x", "exc": str(exc)}
            if not cell_clicked and row_locator is not None:
                for label in pn_labels:
                    try:
                        cell = row_locator.get_by_text(label, exact=False)
                        if cell.count() > 0 and cell.first.is_visible(timeout=400):
                            cell.first.click(timeout=2000)
                            page.wait_for_timeout(700)
                            cell_clicked = True
                            click_detail = {"method": "row_cell_text", "label": label}
                            break
                    except Exception as exc:
                        click_detail = {"method": "row_cell_text", "label": label, "exc": str(exc)}
            shot = _snap(page, "10_cell_clicked")
            _log("product_name_cell_clicked", cell_clicked,
                 f"via {click_detail.get('method', '?')}" if cell_clicked else
                 f"NOT clicked — {click_detail}",
                 shot, click_detail)
            if not cell_clicked:
                raise StopIteration("Could not click Product Name cell")

            # ── Step 11: check editor appeared ───────────────────────────────
            _inject_banner(page, "SCH Diagnostic  ·  Checking for editor/input…")
            editor_sel = None
            editor_loc = None
            for sel in (
                "input:focus", "textarea:focus",
                "[contenteditable='true']:focus",
                "input:visible", "textarea:visible",
            ):
                try:
                    loc = page.locator(sel).last
                    if loc.is_visible(timeout=600):
                        editor_sel = sel
                        editor_loc = loc
                        break
                except Exception:
                    pass
            shot = _snap(page, "11_editor")
            _log("editor_appeared", editor_loc is not None,
                 f"selector={editor_sel!r}" if editor_loc else "no input/textarea visible after cell click",
                 shot, {"selector": editor_sel})
            if editor_loc is None:
                raise StopIteration("No editor/input appeared after clicking Product Name cell")

            # ── Step 12: type Product Name ────────────────────────────────────
            _inject_banner(page, f"SCH Diagnostic  ·  Typing Product Name: {product_name!r}…")
            try:
                editor_loc.click(click_count=3)
                page.wait_for_timeout(150)
                editor_loc.type(product_name)
                page.wait_for_timeout(400)
                shot = _snap(page, "12_typed")
                _log("product_name_typed", True, f"typed={product_name!r}", shot)
            except Exception as exc:
                shot = _snap(page, "12_type_fail")
                _log("product_name_typed", False, str(exc), shot, {"exc": _tb.format_exc()})
                raise StopIteration(f"typing failed: {exc}")

            # ── Step 13: commit (Enter/Tab) and verify ────────────────────────
            _inject_banner(page, "SCH Diagnostic  ·  Committing…")
            try:
                page.keyboard.press("Enter")
                page.wait_for_timeout(1000)
            except Exception:
                try:
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
            committed = _value_visible_in_row(page, row_locator, product_name)
            shot = _snap(page, "13_committed")
            _log("product_name_committed", committed,
                 f"'{product_name}' {'visible in row' if committed else 'NOT found in row after commit'}",
                 shot, {"product_name": product_name, "row_locator": row_locator is not None})

            if committed:
                _inject_banner(page, "SCH Diagnostic  ·  Product Name OK — click OK to close.")
                _js_alert(page, f"Diagnostic — PASSED\n\nProduct Name '{product_name}' entered successfully.\n\nClick OK to close the browser.")
            else:
                raise StopIteration(f"Product Name not visible in row after commit")

        except StopIteration as stop:
            # Controlled stop — first failure
            shot = _snap(page, "diag_stopped")
            _log("diagnostic_stopped", False,
                 str(stop), shot,
                 {"product_name": product_name, "target_section": target_section, "url": page.url})
            _remove_banner(page)
            _inject_banner(page, f"SCH Diagnostic  ·  STOPPED: {stop} — inspect, then click OK.")
            try:
                _js_alert(
                    page,
                    f"Diagnostic — Stopped at: {stop}\n\n"
                    f"Product: {product_name}\n"
                    f"Section: {target_section}\n\n"
                    "Inspect the Programa UI, then click OK to close the browser.",
                )
            except Exception:
                pass
            _remove_banner(page)

        except Exception as exc:
            shot = _snap(page, "diag_exception")
            _log("diagnostic_exception", False,
                 str(exc), shot,
                 {"exc": _tb.format_exc(), "product_name": product_name})
            _remove_banner(page)
            _inject_banner(page, "SCH Diagnostic  ·  Unexpected exception — inspect then click OK.")
            try:
                _js_alert(page, f"Diagnostic — Exception:\n{exc}\n\nClick OK to close.")
            except Exception:
                pass
            _remove_banner(page)

        finally:
            report_path = _save_report()
            print(f"[Diagnostic] Report saved: {report_path}")
            try:
                context.close()
            except Exception:
                pass

    return steps
