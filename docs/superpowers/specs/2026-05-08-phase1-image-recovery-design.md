# Phase 1 Image Recovery — Design Spec

**Date:** 2026-05-08
**Scope:** Phase 1 only. Image search fallback and approve/reject UI are deferred to Phase 2.

---

## Problem

The current image extraction pipeline (HTML scraping via `extract_image_url`) does not reliably recover product images. Goal shifts from "find image URLs" to "produce correct usable product images." Accuracy is prioritized over completeness — a missing image is better than the wrong product.

## Goal

Add two new image recovery sources alongside the existing URL fetch, gate every recovered image with explicit confidence metadata, and never auto-export a low-confidence image.

## Out of Scope (Phase 2)

- Image search fallback (Brave Image Search)
- Thumbnail-based approve/reject UI
- Frontend (Next.js) UI changes — Phase 1 targets the Streamlit `app.py` and the FastAPI backend only

---

## Architecture

### New module: `src/image_recovery.py`

Self-contained pipeline orchestrating three sources. Each source is independently testable; Phase 2 image search drops in as a fourth.

### New module: `src/image_evidence.py`

Pure-text matching helpers shared by all recovery sources.

### Existing modules touched

- `src/product_enrichment.py` — `recover_images_for_dataframe` becomes a thin delegator to `image_recovery`
- `src/document_parser.py` — adds `_source_pdf_id` and `_source_page_number` to parsed PDF rows
- `src/intake_schema.py` — adds the three internal `_source_*` field names
- `src/programa_export.py` — manifest gains 4 columns, ZIP export validates `local_image_path`
- `app.py` — writes uploaded PDFs to a temp dir and triggers recovery
- `backend/main.py` — `/intake/upload-pdf` and `/intake/recover-images` honor the temp-dir flow

---

## Data model

### `ImageRecoveryResult` dataclass (in `src/image_recovery.py`)

```python
@dataclass
class ImageRecoveryResult:
    image_source: str          # "url" | "pdf_crop" | "page_screenshot" | "manual_upload" | "none"
    confidence: str            # "HIGH" | "MEDIUM" | "LOW" | "NONE"
    evidence: list[str]        # e.g. ["sku_match_on_page", "official_domain", "pdf_page_3"]
    needs_image_review: bool   # auto-derived: confidence != "HIGH"
    image_url: str             # remote URL when source == "url" (else "")
    local_image_filename: str  # e.g. "wolf_mdd30ts.jpg" (else "")
    local_image_path: str      # full path on disk (else "")
    jpeg_bytes: bytes          # populated when an image was recovered (else b"")
    error: str                 # populated only on failure (else "")
```

`jpeg_bytes` is **never** serialized to manifest CSV, diagnostics list, or logs. It exists only to allow the orchestrator to write the file once and to pass bytes to ZIP export.

### Internal row fields (added to `src/intake_schema.py`)

These are plumbing — they appear in working dataframes but are stripped from the user-facing 21-column Programa export. They survive through recovery and are only excluded at the `_row_to_programa_dict` mapping in `programa_export.py`.

- `_source_pdf_id` — SHA1 (12 chars) of the originally uploaded PDF bytes
- `_source_page_number` — 1-indexed PDF page the row was parsed from
- `_source_filename` — original filename for display

These DO appear in `build_programa_debug_dataframe` output.

### Recovered image fields (added to working dataframe)

After `recover_images_for_dataframe` runs:

- `Image URL` — set when `image_source == "url"`
- `local_image_filename`
- `local_image_path`
- `image_source`
- `confidence`
- `evidence` — stored on the row as a semicolon-joined string for CSV-friendly serialization (the dataclass holds it as `list[str]`; conversion happens at the row-write boundary)
- `needs_image_review`

---

## Public API of `src/image_recovery.py`

```python
def recover_from_url(row: dict) -> ImageRecoveryResult: ...
def recover_from_pdf_crop(row: dict, pdf_path: str | Path) -> ImageRecoveryResult: ...
def recover_from_screenshot(row: dict, product_url: str) -> ImageRecoveryResult: ...

def recover_image_for_row(
    row: dict,
    pdf_lookup: dict[str, str] | None = None,   # {pdf_id: pdf_path}
    session_id: str | None = None,
    enable_screenshot: bool = True,
) -> ImageRecoveryResult: ...

def recover_images_for_dataframe(
    df: pd.DataFrame,
    pdf_lookup: dict[str, str] | None = None,
    session_id: str | None = None,
    enable_screenshot: bool = True,
) -> tuple[pd.DataFrame, list[dict]]: ...

def cleanup_old_sessions(max_age_hours: int = 24) -> int: ...
```

`recover_images_for_dataframe`:
- Skips rows that already carry a HIGH-confidence image
- Writes recovered files to `.tmp/uploads/{session_id}/images/`
- Returns the updated dataframe **with** internal `_source_*` columns preserved
- Returns a diagnostics list of dicts; each dict has `row_index`, `product_name`, `brand`, `model_sku`, `image_source`, `confidence`, `evidence`, `error` — and **no `jpeg_bytes` key**
- LOW results: row gets `confidence == "LOW"`, `image_source`, and `evidence` populated; `local_image_filename` and `local_image_path` are empty; nothing written to `.tmp/.../images/`

### Existing `product_enrichment.py` becomes a delegator

```python
def recover_images_for_dataframe(df, pdf_lookup=None, session_id=None, enable_screenshot=True):
    from src.image_recovery import recover_images_for_dataframe as _impl
    return _impl(df, pdf_lookup=pdf_lookup, session_id=session_id, enable_screenshot=enable_screenshot)
```

The function exists for backward import compatibility; default parameters mirror the new module so any caller that doesn't pass `pdf_lookup`/`session_id` simply skips PDF crop. Real callers in `app.py` and `backend/main.py` pass full arguments.

---

## Source-priority decision flow

```
1. Existing valid Image URL on row?
     → recover_from_url
     → if HIGH, return immediately

2. _source_pdf_id present AND pdf_lookup has entry?
     → recover_from_pdf_crop
     → if HIGH, return immediately   (PDF is the user-provided source of truth)
     → if MEDIUM, hold as best-so-far
     → if LOW, hold only if best-so-far is empty (so we still surface in diagnostics)

3. Product URL present AND enable_screenshot?
     → recover_from_screenshot
     → if HIGH, return immediately
     → if MEDIUM, replace held result only when held is LOW or absent
                  (PDF MEDIUM tied with screenshot MEDIUM → PDF wins; PDF was held first)
     → if LOW, ignore unless held is empty

4. Return the held result, or a NONE result if nothing was recovered
```

### LOW handling

LOW results are returned by the orchestrator and DO appear in:
- The dataframe row (`confidence = "LOW"`, `image_source` populated, `evidence` populated)
- The diagnostics list
- The manifest CSV (status `"low_confidence_skipped"`)

LOW results are NOT:
- Written to `.tmp/uploads/{session_id}/images/`
- Copied to the ZIP archive's `images/` directory
- Assigned a `local_image_filename` or `local_image_path` on the row

This way the user sees that something was found at LOW confidence and can investigate, without that image silently shipping to Programa.

---

## Confidence rules

### `recover_from_url`

| Confidence | Trigger |
|---|---|
| HIGH | SKU appears in image URL path **or** SKU appears in fetched page text |
| MEDIUM | Image URL is on the brand's official domain but no SKU evidence |
| LOW | URL valid but unrelated/unknown domain, or no SKU evidence on a generic site |

### `recover_from_pdf_crop`

| Confidence | Trigger |
|---|---|
| HIGH | SKU **or** full product name appears as text on the same PDF page as the cropped image |
| MEDIUM | Crop comes from same PDF document but a different page than the matched text, OR brand-only match |
| LOW | No SKU/name/brand evidence anywhere in the PDF |
| NONE | PDF unreadable, no images on relevant page |

### `recover_from_screenshot`

| Confidence | Trigger |
|---|---|
| HIGH | SKU **or** full product name appears in rendered page text |
| MEDIUM | Screenshot taken from official manufacturer domain, but no exact SKU match |
| LOW | Generic page (search result, category landing) and no domain match |
| NONE | Network/browser error, no usable image element found |

`needs_image_review` is auto-derived as `confidence != "HIGH"`.

---

## PDF crop algorithm

Inputs: parsed row with `_source_page_number` and `_source_pdf_id`; `pdf_path` resolved from `pdf_lookup`.

1. `fitz.open(pdf_path)` → page = `doc[page_number - 1]`
2. Get image objects on page via `page.get_images(full=True)`
3. For each image, get bounding rect via `page.get_image_rects(xref)`
4. Filter:
   - `width × height < 100 × 100` → skip (likely icon)
   - aspect ratio outside `[1:4, 4:1]` → skip (likely banner/divider)
   - rect area smaller than 1% of page area → skip
5. If multiple candidates remain, pick the largest by area
6. Render the page at 200 DPI, crop the candidate's pixel-space rect, save as JPEG via PIL
7. Read page text via `page.get_text("text")` — feed into evidence helpers to score confidence
8. If no images on the row's recorded page, fall back to scanning ±1 adjacent pages (same PDF). A hit on an adjacent page caps confidence at MEDIUM regardless of what evidence text is on that page (because the page-row linkage is no longer exact).

---

## Screenshot algorithm

Inputs: row, product URL.

1. Launch Playwright Chromium (headless), open URL with `wait_until="networkidle"`, 15s timeout
2. Read `page.content()` for evidence text (no OCR needed — HTML text is enough)
3. **Element-selector pass:**
   ```
   selectors = [
     "img[class*=product-image]",
     "[class*=product] img",
     "[class*=gallery] img",
     "[class*=hero] img",
     "[class*=media] img",
     'img[id*="product"]',
   ]
   ```
   First selector that resolves to ≥1 visible element with width × height ≥ 200 × 200 wins; element-screenshot it.
4. **Bounding-box fallback:** evaluate all `<img>` elements via `page.eval_on_selector_all`:
   - Filter: visible, `top < viewport_height` (above the fold), `width × height ≥ 200 × 200`
   - Filter out URLs containing `logo`, `icon`, `sprite`
   - Filter out aspect ratio > 4:1 or < 1:4
   - Pick the largest by area
   - Take full-page screenshot, crop bbox with PIL
5. Convert crop to JPEG via PIL with `ImageOps.exif_transpose` and RGB mode
6. Score confidence using rules above
7. Always close the browser context in a `finally` block

---

## Storage layout

```
.tmp/
  uploads/
    {session_id}/
      pdfs/
        {pdf_id}.pdf
      images/
        wolf_mdd30ts.jpg
        sub_zero_id36r.jpg
```

- `session_id` — UUID4 generated once per Streamlit session, stored in `st.session_state.session_id`. Backend generates server-side and returns to client on first call if no session header is present.
- `pdf_id` — SHA1(pdf_bytes)[:12]; allows dedupe of re-uploaded files
- `.tmp/` is added to `.gitignore`

`cleanup_old_sessions(max_age_hours=24)`:
- Iterates `.tmp/uploads/`
- Deletes session directories whose `mtime` is older than the threshold
- Called from `app.py` startup and from `backend/main.py` startup event handler

---

## Streamlit integration (`app.py`)

**On PDF upload:**
1. Compute `pdf_id = sha1(bytes)[:12]`
2. Ensure `.tmp/uploads/{session_id}/pdfs/` exists
3. Write bytes to `{pdf_id}.pdf`
4. Also store `bytes` in `st.session_state.uploaded_pdfs[pdf_id]` as a fallback if the disk file is missing on later access
5. Call `parse_pdf_rows(bytes, ...)` — parser annotates rows with `_source_pdf_id`, `_source_page_number`, `_source_filename`

**On "Recover Missing Images" button:**
1. Build `pdf_lookup = {pdf_id: f".tmp/uploads/{session_id}/pdfs/{pdf_id}.pdf"}`
2. For any `pdf_id` whose path doesn't exist on disk, write the session-state bytes back out (fallback)
3. Call `recover_images_for_dataframe(df, pdf_lookup=pdf_lookup, session_id=session_id, enable_screenshot=True)`
4. Show diagnostics in the existing Image Status panel — counts by source and confidence, expandable list of LOW/needs-review rows

---

## Backend integration (`backend/main.py`)

`POST /intake/upload-pdf`:
- Accepts multipart PDF + optional `X-Session-Id` header
- Generates session_id if absent and returns it in response
- Writes file to `.tmp/uploads/{session_id}/pdfs/{pdf_id}.pdf`
- Returns `{ session_id, pdf_id, rows: [...] }`

`POST /intake/recover-images`:
- Accepts `{ session_id, rows }`
- Builds `pdf_lookup` from `.tmp/uploads/{session_id}/pdfs/`
- Returns `{ rows: [...], diagnostics: [...] }`

Startup hook (FastAPI lifespan): calls `cleanup_old_sessions(24)` once.

---

## ZIP export changes (`src/programa_export.py`)

### Manifest gains 4 columns

```python
_MANIFEST_COLUMNS = [
    "Product Name", "Brand", "SKU/Model", "Product URL",
    "Original Image URL", "Local Image Filename",
    "Image Status",
    "Image Source",        # new
    "Confidence",          # new
    "Evidence",            # new (joined ; separated)
    "Needs Image Review",  # new (true/false)
    "Error",
]
```

### `export_programa_zip` resolution order per row

```
1. local_image_path on row → validate → use bytes from disk
2. manual_images dict has bytes for this index → use those (status: "manually_uploaded")
3. row has valid public Image URL → download via download_and_convert_image
4. otherwise → status "missing_image_url", no image written
```

### `local_image_path` validation

Before reading bytes from disk:
- File exists
- Resolved (`Path.resolve()`) path is inside `.tmp/uploads/{session_id}/images/`
- File extension is `.jpg` or `.jpeg`
- File size > 0
- Row's `confidence` is not `"LOW"` (LOW images are never copied to `images/` so this is a defensive double-check)

If any check fails: skip the file, manifest status becomes `"invalid_local_path"` with the reason in `Error`.

### LOW-confidence rows

Image is **not** written to `images/`. Manifest row populated with metadata (`Image Source`, `Confidence`, `Evidence`, `Needs Image Review`); `Local Image Filename` is blank; `Image Status = "low_confidence_skipped"`.

### Internal `_source_*` columns

Stripped at `_row_to_programa_dict` boundary — they are not in `PROGRAMA_COLUMNS`, so the standard 21-column export already excludes them. They DO appear when `build_programa_debug_dataframe` is used.

---

## Error handling

- Playwright launch failure → `recover_from_screenshot` returns NONE with `error="browser_unavailable"`. The orchestrator falls through cleanly.
- PyMuPDF cannot open file → `recover_from_pdf_crop` returns NONE with `error="pdf_unreadable"`.
- Network timeout in screenshot → NONE with `error="page_load_timeout"`.
- Disk write failure for image file → result is downgraded: `local_image_path = ""`, but `jpeg_bytes` retained so ZIP export can still include it via the bytes path.
- Cleanup failure (permission, file in use) → logged, suppressed; never fatal.

All failure paths return a populated `ImageRecoveryResult` with `confidence == "NONE"` and a meaningful `error` string. The orchestrator never raises.

---

## Testing strategy

### `tests/test_image_evidence.py` (new)

- SKU normalization: `MDD30TS` matches `mdd-30ts`, `mdd 30 ts`, `MDD30TS.` (trailing punctuation)
- Product name fuzzy match — full vs. partial
- `is_official_domain` against `manufacturer_domains.py`

### `tests/test_image_recovery.py` (new)

**`recover_from_pdf_crop`** — synthetic PDFs built in-test with PyMuPDF + PIL:
- HIGH when SKU appears as text on cropped page
- MEDIUM when crop from PDF but SKU absent
- MEDIUM when image found on adjacent page (page ±1)
- NONE when PDF has no images
- NONE when `pdf_path` missing or unreadable
- Filters tiny images (< 100×100)
- Filters extreme aspect ratios

**`recover_from_screenshot`** — Playwright fully mocked:
- Element-selector hit → HIGH when SKU in mocked page content
- Element-selector miss → bbox fallback path used
- Logo/icon/sprite filenames excluded
- Tiny / extreme-aspect-ratio elements filtered
- Domain not official → MEDIUM cap
- Network timeout → NONE with `error="page_load_timeout"`

**`recover_image_for_row` orchestrator priority:**
- Existing URL HIGH → returned, PDF/screenshot not called
- PDF HIGH → returned, screenshot not called
- PDF MEDIUM + Screenshot HIGH → screenshot wins
- PDF MEDIUM + Screenshot MEDIUM → PDF wins (tie breaker)
- PDF LOW + Screenshot LOW → returns the first LOW (no file written; row records it; manifest shows `low_confidence_skipped`)
- All NONE → NONE result with empty bytes

**`recover_images_for_dataframe`:**
- Skips rows that already have HIGH-confidence image
- Adds confidence columns to df
- Preserves `_source_*` internal columns
- Returns diagnostics list with no `jpeg_bytes` key in any entry

**`cleanup_old_sessions`:**
- Creates fake session dirs with backdated mtimes
- Asserts old (> threshold) removed, fresh ones kept
- Permission errors don't propagate

### `tests/test_image_assets.py` (extend)

- ZIP export skips a row whose `local_image_path` resolves outside `.tmp/uploads/{session_id}/images/` (path traversal guard)
- ZIP export skips a row whose `local_image_path` doesn't exist on disk
- ZIP export skips LOW-confidence rows (image not written to `images/`, status `"low_confidence_skipped"`)
- ZIP export reads from `local_image_path` first when present (no remote download attempted)
- Manifest CSV has the 4 new columns: `Image Source`, `Confidence`, `Evidence`, `Needs Image Review`

### `tests/test_document_parser.py` (extend)

- Parsed rows from a multi-page PDF carry the correct 1-indexed `_source_page_number`
- All rows from one PDF share the same `_source_pdf_id`

### `tests/test_programa_export.py` (extend)

- Internal `_source_pdf_id` / `_source_page_number` / `_source_filename` are excluded from the standard 21-column export
- They appear when `build_programa_debug_dataframe` is used

### Test isolation

- No real network, no real browser, no real Playwright launch — all mocked
- PyMuPDF runs in-process against in-memory bytes built with PIL+fitz
- All tests use `tmp_path` fixture for any disk operations

---

## Migration notes

- `.gitignore` gets a new `.tmp/` line
- Existing rows in active sessions won't have `_source_pdf_id` until re-uploaded; PDF crop is skipped for them and the pipeline falls through to URL/screenshot
- Manifest column additions are backward-compatible — older ZIP consumers ignore unknown columns
- Existing manifest status values (`downloaded`, `fetch_error`, `skipped`, `manually_uploaded`, `missing_image_url`) are preserved; new values added: `low_confidence_skipped`, `invalid_local_path`

---

## Non-goals (Phase 2)

- Image search fallback (Brave Image Search)
- Thumbnail-based approve/reject UI in Streamlit or Next.js
- Cross-session PDF persistence (PDFs are session-scoped)
- OCR for evidence matching (HTML text and PDF text layer are enough)
