# Dimensions Gate & Programa Entry Design

**Date:** 2026-04-25  
**Status:** Approved  
**Feature area:** Confidence scoring · Dimensions review UI · Programa automation

---

## Goal

Rows cannot be sent to Programa unless they contain full W × H × D dimensions and pass all existing review gates. Once a row is complete and approved, it is sent to Programa using one of two automation paths: the existing "Add from URL" flow for URL rows, or a new Schedule-file entry flow for all other rows.

---

## Architecture

The feature is a two-part pipeline. Part 1 tightens the data gate: `apply_confidence_checks` marks any row with missing or partial dimensions as requiring review, a new "Missing Dimensions" UI section lets the user correct them row-by-row, and the Programa send button hard-blocks any row that still fails the gate. Part 2 extends the Playwright automation: a new `_process_schedule_row` function handles non-URL rows via the Schedule file, while URL rows continue through the existing `_process_url_row`. Both paths share login, project navigation, and logging infrastructure.

---

## Part 1 — Dimensions Review Gate

### 1.1 Schema change: `IMPORTANT_FIELDS`

**File:** `src/intake_schema.py`

Add `"Dimensions"` to `IMPORTANT_FIELDS`. This is the single source of truth used by `_missing_important` and `identify_missing_fields` to decide what counts as a complete row.

```python
IMPORTANT_FIELDS: list[str] = [
    "Product Name",
    "Brand",
    "Dimensions",       # ← new
    "Quantity",
    "Supplier",
    "Room",
    "Product Category",
]
```

### 1.2 Confidence scoring: `_missing_important`

**File:** `src/confidence.py`  
**Function:** `_missing_important(row) -> list[str]`

The current implementation checks whether each IMPORTANT_FIELDS value is blank. Dimensions needs a richer check: a non-empty value is still treated as missing if it does not satisfy `has_complete_3d_dimensions`.

Updated logic for the Dimensions field:

```python
if field == "Dimensions":
    dims = _str(row.get("Dimensions"))
    if not dims or not has_complete_3d_dimensions(dims):
        missing.append("Dimensions")
```

All other fields keep the existing blank-check logic.

`has_complete_3d_dimensions` is imported from `src.product_enrichment` (already present in the codebase).

### 1.3 Suggested Action message

**File:** `src/confidence.py`  
**Function:** `_suggested_action`

The existing dim-note tail already appends a message when Dimensions is incomplete for PDF_AI rows. The message text changes from the current "Verify full W×H×D dimensions from official spec sheet" to:

> **"Enter full W × H × D dimensions before Programa upload"**

This applies to all source types (not just PDF_AI) — since Dimensions is now an `IMPORTANT_FIELDS` member, the missing-fields path in `_suggested_action` will surface it for any source. The explicit dim-note tail in `_suggested_action` (which targeted `SOURCE_PDF_AI` only) is removed; the standard missing-fields logic now handles all cases uniformly. Note: `_suggested_action` has early-return branches for Manual rows with enrichment opportunities — those branches fire before the missing-fields block and are unaffected by this change.

### 1.4 `identify_missing_fields`

**File:** `src/confidence.py`  
**Function:** `identify_missing_fields`

No logic change needed. Because `_missing_important` now flags incomplete Dimensions, `identify_missing_fields` (which calls `_missing_important`) will include `"Dimensions"` in the returned list automatically for non-URL rows. URL rows are checked separately and do not require dimensions.

### 1.5 "Missing Dimensions" UI section

**File:** `app.py`  
**Position:** Between the Review Table and AI-Assisted Cleanup.

This section only renders when at least one included row fails the dimensions check.

**Title:** `"Missing Dimensions"`

**Caption:**  
`"These items need full width, height, and depth before they can be sent to Programa."`

**Display:** One card/row per item with incomplete dimensions, showing:
- Product Name (read-only label)
- Brand (read-only label)
- Model / SKU (read-only label)
- Current Dimensions (read-only, shown in muted text if partial; empty if blank)
- **Enter Full Dimensions** — `st.text_input`, placeholder: `36"W × 34.5"H × 24"D`

**Button:** `"Save Dimension Updates"` — on click, writes each user-entered value back to `st.session_state.intake_df`, re-runs `apply_confidence_checks`, and calls `st.rerun()`. Only non-empty entries that satisfy `has_complete_3d_dimensions` are written back; invalid entries are silently left unchanged (the row stays flagged).

A helper line below the button:  
`'Enter as W × H × D — for example: 36"W × 34.5"H × 24"D'`

When all included rows have complete dimensions, this section does not render.

---

## Part 2 — Programa Two-Path Automation

### 2.1 Eligibility logic

**File:** `app.py`

A row is eligible to send to Programa if ALL of the following are true:
- `Include == True`
- `Review Required == False`
- `Status` not in `["Ignored", "Excluded", "Error"]`
- `Product Name` is non-empty
- `Quantity` ≥ 1
- `Product Category` is non-empty
- `has_complete_3d_dimensions(Dimensions)` is True
- A Programa project name has been entered

Eligible rows are split into two groups:
- **URL path:** `Source Type == "URL"` and `Product URL` is a non-empty valid-looking string
- **Schedule path:** all other eligible rows (PDF_AI, Manual, URL rows without a valid Product URL)

Blocked included rows (failed any gate) surface a plain count message above the send button:  
`"3 items need dimensions before they can be sent."`  
or  
`"2 items still need review before they can be sent."`

The existing detail table for blocked URL rows is retained. No technical field names are exposed in the count message itself.

### 2.2 New Playwright function: `_process_schedule_row`

**File:** `src/programa_automation.py`

Called once per Schedule-path row. Runs inside an already-open, already-logged-in, already-project-navigated browser context.

**New selector constants** (added at module level alongside the existing URL-flow constants):

```python
SCHEDULE_TEXTS = ["Schedule", "Schedules", "Project Schedule", "schedule"]

NEW_ITEM_TEXTS = ["New", "+ New", "New item", "Add item", "Add row", "+ Add", "Add"]

SCHEDULE_FIELD_LABELS: dict[str, list[str]] = {
    "Product Name":   ["Product name", "Name", "Product title", "Title", "Item name", "Item"],
    "Description":    ["Product details", "Description", "Details", "Product description", "Notes"],
    "Brand":          ["Brand", "Manufacturer", "Maker"],
    "Dimensions":     ["Dimensions", "Dimension", "Size", "W/L/H/D", "W x H x D"],
    "Quantity":       ["Quantity", "Qty", "Qty.", "Amount"],
    "Supplier":       ["Supplier", "Vendor", "Bought from", "Who we bought it from", "Source"],
    "Color":          ["Color", "Colour", "Finish color", "Finish / Color"],
    "Finish":         ["Finish", "Finish type", "Surface finish"],
    "Material":       ["Material", "Materials", "Construction"],
    "Notes":          ["Notes", "Note", "Comments", "Additional notes"],
}
```

**Sequence:**

1. Locate and click the Schedule tab:  
   Try each text in `SCHEDULE_TEXTS` using `_click_by_text`.  
   **On failure:** take screenshot → log `schedule_nav_failed` → show browser dialog:  
   *"Please open the Schedule file for this project in Programa, then click OK."*  
   User navigates manually and clicks OK. Automation continues.

2. Click "New":  
   Try each text in `NEW_ITEM_TEXTS` using `_click_by_text`.  
   **On failure:** take screenshot → log `new_item_failed` → show browser dialog:  
   *"Please click New to add a product row in Programa, then click OK."*  
   User clicks OK. Automation continues.

3. Wait 1 200 ms for the blank row/form to appear.

4. Fill fields (in order):  
   For each entry in `SCHEDULE_FIELD_LABELS`, call `_fill_field_by_label(page, labels, value)`.  
   Fields with empty values are silently skipped.  
   Field values sourced from the row dict:

   | Field key | Row column |
   |-----------|-----------|
   | Product Name | `Product Name` |
   | Description | `Notes` (reused — descriptive notes go here) |
   | Brand | `Brand` |
   | Dimensions | `Dimensions` |
   | Quantity | `Quantity` |
   | Supplier | `Supplier` |
   | Color | `Finish / Color` |
   | Finish | `Finish / Color` (same value; different labels in Programa) |
   | Material | *(extracted from Notes if `[Materials: …]` tag present; else empty)* |
   | Notes | `Notes` |

5. Wait 500 ms.

6. If `auto_done` is False:  
   Show browser dialog: *"Item [N of total]: [Product Name]. Form filled — please review it in Programa, then click Done. Click OK here when ready for the next item."*  
   Return `make_log_entry(product_name=..., product_url=..., status="filled_awaiting_confirm", ...)`.

   If `auto_done` is True:  
   `_click_by_text(page, DONE_TEXTS)`.  
   Return `make_log_entry(..., status="success", ...)`.

### 2.3 Updated orchestrator

**File:** `src/programa_automation.py`  
**Function:** `send_rows_to_programa`

Current signature and login/nav/logging behaviour are preserved. The per-row loop is split:

```python
for i, row in enumerate(rows, start=1):
    if _is_url_row(row):
        entry = _process_url_row(page, row, auto_done, i, total)
    else:
        entry = _process_schedule_row(page, row, auto_done, i, total)
    log_entries.append(entry)
    page.wait_for_timeout(1200)
```

`_is_url_row(row)` returns True when `Source Type == "URL"` and `Product URL` is non-empty.

`run_programa_automation` is unchanged (wraps `send_rows_to_programa` + `save_log`).

The `app.py` send button passes the combined list (URL + Schedule eligible rows) as a single `rows` payload. The orchestrator handles the split internally, keeping the app layer simple.

### 2.4 Log schema extension

**File:** `src/automation_logs.py`  
**Function:** `make_log_entry`

Add `product_name: str = ""` parameter. The log entry dict gains a `"product_name"` key. Existing callers are unaffected (parameter is keyword with default).

Updated schema:
```
timestamp        ISO-8601 string
product_name     str   (used for non-URL rows; empty for URL rows)
product_url      str   (used for URL rows; empty for non-URL rows)
status           "success" | "filled_awaiting_confirm" | "filled_no_save"
                 | "error" | "skipped" | "schedule_nav_failed" | "new_item_failed"
message          str
screenshot_path  str
```

The results table in `app.py` already reads `product_url` — it is updated to show `product_name` when `product_url` is empty, using a computed display column.

### 2.5 App UI changes — Programa section

**File:** `app.py`

- Remove the "PDF direct entry will be added in a future version" stub.
- Eligibility mask replaces the current URL-only mask (see §2.1).
- Send button label: `"Send [N] item(s) to Programa"` (N = total eligible across both paths).
- Below the button, if any included rows are blocked: plain count message (see §2.1).
- The automation results table gains a "Product" column showing `product_name` or `product_url` (whichever is non-empty).
- `nav_failed` / manual-continue flow is preserved unchanged.
- A new `schedule_nav_failed` session state key is added; if the Schedule navigation fails the mid-session fallback dialog handles it inside the browser (no new Streamlit UI needed — the dialog is in Playwright, not Streamlit).

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `src/intake_schema.py` | Add "Dimensions" to IMPORTANT_FIELDS |
| Modify | `src/confidence.py` | `_missing_important` dim check; update suggested action text; remove PDF_AI-only dim-note tail |
| Modify | `tests/test_confidence.py` | Update for new message; add dim missing-fields tests |
| Modify | `src/automation_logs.py` | Add `product_name` to log entry |
| Modify | `tests/test_automation_logs.py` | Tests for updated schema (create if absent) |
| Modify | `src/programa_automation.py` | `_process_schedule_row`, `_is_url_row`, selector constants, orchestrator routing |
| Modify | `tests/test_programa_automation.py` | Tests for `_process_schedule_row`, `_is_url_row` (create if absent) |
| Modify | `app.py` | Missing Dimensions section; eligibility logic; send button; results table |

---

## Error Handling & Fallback Summary

| Step | On failure | Fallback |
|------|-----------|---------|
| Project navigation | Auto-navigate fails | Existing `nav_failed` → "Continue After Manual Project Open" button |
| Schedule navigation | Click fails | Browser dialog: "Open Schedule manually, then click OK" |
| New item button | Click fails | Browser dialog: "Click New manually, then click OK" |
| Field fill | Label not found | Silently skipped; logged in message field |
| Done button (auto) | Click fails | Logged as `filled_no_save`; next item continues |

---

## Testing Notes

- `_missing_important` is tested via `tests/test_confidence.py` — new cases: partial dim string counts as missing; blank Dimensions counts as missing; complete 3D dim does not.
- `_suggested_action` tests updated for the new message text.
- `_process_schedule_row` is tested in `tests/test_programa_automation.py` with a mocked Playwright page — verifying: Schedule tab click attempted; "New" clicked; each field filled with correct value; log entry returned with correct status.
- `_is_url_row` tested with URL/non-URL source combinations.
- `make_log_entry` tested with and without `product_name`.
- No UI tests — `app.py` changes are verified manually.

---

## Out of Scope

- Creating Programa projects (hard guard remains in `_is_create_project_text`)
- Editing or deleting existing Programa items
- Multi-project send in a single session
- Handling Programa pagination or multi-page Schedule views
- Price field (not a Programa Schedule input field)
