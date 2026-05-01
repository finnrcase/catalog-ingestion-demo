# SCH DesignOps Intake — Category AI + Document Parser Design

**Date:** 2026-04-24
**Status:** Approved
**Scope:** Two coordinated features — AI category suggestion (batch) and PDF document parser foundation.

---

## 1. Goals

1. Parse PDFs into structured product rows before any AI call, using text/table heuristics.
2. When AI toggle is ON, pass parser output as structured context to Claude for validation and gap-filling.
3. Offer a single-button AI category suggestion for rows with blank `Product Category`, reusable across all source types.
4. Prioritise serial/model number capture — recognised under any of: serial number, model number, SKU, item number, product code, part number, manufacturer number.
5. Never invent dimensions, finish, or brand if not present in the document.

---

## 2. Module map

| Module | Status | Responsibility |
|---|---|---|
| `src/document_parser.py` | New | PDF text/table parsing, no AI. Always runs first in PDF flow. |
| `src/category_ai.py` | New | Batch Claude call for blank-category rows. Single reusable module. |
| `src/ai_extraction.py` | Update | Accept parser structured output as prompt context. |
| `src/intake_schema.py` | Minor update | Two new columns; remove "Couch" from CATEGORIES (alias → "Sofa"). |
| `src/confidence.py` | Unchanged | Already handles Needs Enrichment / Needs Review correctly. |
| `app.py` | Update | Wire parser; add category suggestion UI in AI-Assisted Cleanup. |

---

## 3. Data model changes (`src/intake_schema.py`)

### New columns appended to `ALL_COLUMNS`

| Column | Type | Default | Description |
|---|---|---|---|
| `AI Category Confidence` | int | `0` | Score 0–100 from Claude category suggestion. 0 = not yet run. |
| `Category Source` | str | `"Unknown"` | One of: `Manual`, `AI Suggested`, `Unknown`. |

`make_base_row()` includes both with the defaults above.

### Category list — remove "Couch", keep "Sofa"

Current `CATEGORIES` has both `"Sofa"` and `"Couch"`. Remove `"Couch"` from the canonical list. Any parser, AI response, or alias that returns `"Couch"` is mapped to `"Sofa"` at the normalisation layer (`_normalise_category()` helper, shared by `document_parser.py` and `category_ai.py`).

Existing rows with `Product Category = "Couch"` are unaffected — the data editor selectbox lists the canonical categories, but stored values are not force-changed until the user edits the cell.

**Final CATEGORIES list (14 items):**
Chair, Sofa, Paint, Fabric, Table, Lighting, Plumbing, Hardware, Rug, Artwork, Mirror, Appliance, Accessories, Other

### Category Source assignment rules

- Manual entry form with category selected → `Manual`
- AI category suggestion applied → `AI Suggested`
- All other sources (PDF, URL, PDF_AI) → `Unknown`

---

## 4. Document parser (`src/document_parser.py`)

### Public API

```python
parse_pdf_rows(
    pdf_file,
    project: str,
    room: str,
    supplier: str,
    notes: str,
) -> list[dict]
```

Returns a list of partial row dicts using `make_base_row()` field names. Unknown fields are left at their default (empty string / 1 for quantity). Never invented.

### Serial/model number recognition

The parser treats all of the following labels as the `Model/SKU` field:
- serial number, serial #, s/n
- model number, model #, model
- sku, item #, item number
- product code, part number, part #
- manufacturer number, mfr #, mfr number

When a recognised label is detected, its value is stored in `Model/SKU`. If the original label differs from "Model/SKU" and carries useful context (e.g. the label was "Part #"), it is appended to `Notes` as `[Part #: value]` only when that context would not already be recoverable.

### Parsing strategy

1. Extract full text per page via PyMuPDF `page.get_text("text")`.
2. Attempt table extraction via PyMuPDF `page.find_tables()` (built-in, no extra dependency). For each detected table, treat rows as candidate product lines.
3. For non-table text, split into lines and apply heuristic classification:
   - **Skip row:** matches skip-pattern regex (subtotal, tax, total, freight, deposit, delivery, service plan, balance due, discount, credit, surcharge, handling) — identical to the pattern already in `confidence.py`.
   - **Product row:** has a product name, description, or any recognised model-number label.
4. For each product row, extract:
   - `Product Name`: description or manufacturer + description
   - `Brand`: first capitalised word if it looks like a manufacturer name; otherwise blank
   - `Model/SKU`: from recognised identifier label; otherwise blank
   - `Quantity`: from `qty N`, `x2`, `(2)` patterns; default 1
   - `Price`: from `$\d` or `\d+\.\d{2}` patterns; otherwise blank
   - `Dimensions`, `Finish / Color`: only if clearly labelled in the line; otherwise blank
5. Set `Source Type = "PDF"` and compute initial `Status`:
   - `"Needs Enrichment"` if `Model/SKU` is non-empty AND any IMPORTANT_FIELDS are blank
   - `"Needs Review"` otherwise

### Output used by

- **AI toggle OFF:** parser output fed directly to `build_intake_dataframe()` → `apply_confidence_checks()`
- **AI toggle ON:** parser output passed to `_build_prompt()` in `ai_extraction.py` as structured context

---

## 5. AI extraction update (`src/ai_extraction.py`)

### Revised generate flow (AI toggle ON)

```
pdf_file
  → _read_pdf_text()           raw text (str)
  → parse_pdf_rows()           structured_rows (list[dict])
  → _build_prompt(structured_rows, raw_text, project, room, supplier)
  → Claude
  → _parse_ai_response()
  → _item_to_row() per item
  → DataFrame
```

### Prompt update

A new `STRUCTURED PRE-PARSE` section is prepended to the existing prompt:

```
STRUCTURED PRE-PARSE
The following rows were extracted by a rule-based parser. Use them as a starting
point. Correct errors, fill gaps where the document supports it, and add any rows
the parser missed. Do NOT invent dimensions, finish, or brand.

<pre-parse JSON array>
```

The rest of the prompt (extraction rules, JSON schema, response format) is unchanged.

### Category handling in AI extraction

Claude assigns `product_category` where confident. Blank is acceptable — `category_ai.py` handles batch suggestion in the AI-Assisted Cleanup step.

---

## 6. Category AI module (`src/category_ai.py`)

### Public API

```python
suggest_categories_batch(
    rows: list[dict],
    row_indices: list[int],
) -> tuple[dict[int, dict], str | None]
```

- `rows`: only the rows with blank `Product Category`
- `row_indices`: their integer positions in the full DataFrame (used as `"id"` in the prompt)
- Returns `(suggestions, error_or_None)`
  - `suggestions`: `{row_index: {"category": str, "confidence": int, "reason": str}}`
  - On any failure: `({}, error_string)` — caller leaves the table unchanged

### Prompt contract

Each row is sent with: `product_name`, `brand`, `finish_color`, `model_sku`, `notes`, `product_url` (truncated to 200 chars), and `"id"` = row index.

Response: strict JSON array `[{"id": 0, "category": "Appliance", "confidence": 92, "reason": "Wolf appliance line"}]`

Categories are constrained to the canonical CATEGORIES list (14 items, no "Couch"). Any unrecognised category in the response (including "Couch") is normalised via `_normalise_category()` — "Couch" → "Sofa"; anything else unrecognised → "Other" with confidence 0.

Uses `ANTHROPIC_API_KEY` from `.env`. If key missing, returns `({}, "AI category suggestion requires ANTHROPIC_API_KEY...")`.

### Shared normalisation helper

```python
_CATEGORY_ALIASES = {"couch": "sofa"}

def _normalise_category(raw: str) -> str:
    lower = raw.strip().lower()
    canonical = _CATEGORY_ALIASES.get(lower, lower)
    match = next((c for c in CATEGORIES if c.lower() == canonical), None)
    return match if match else "Other"
```

This helper lives in `category_ai.py` and is imported by `document_parser.py`. It is the single normalisation point for category strings across all modules.

---

## 7. App UI changes (`app.py`)

### Inline note above review table

Shown only when ≥ 1 included row has a blank `Product Category`:

```
ℹ️  Blank categories can be suggested in AI-Assisted Cleanup below.
```

Plain `st.caption()` or `st.info()` — no new container, no style changes.

### AI-Assisted Cleanup section — updated layout

```
[checkbox] Use AI to suggest categories    (default: off)

  [button: Suggest Missing Categories]
    disabled: if toggle off OR no included rows with blank Product Category
    caption: "N rows with blank category" (shown below button when N > 0)

[existing button: Use AI to clean N uncertain rows]   ← unchanged, exact position preserved
```

### Button click handler

1. `st.spinner("Suggesting categories…")`
2. Call `suggest_categories_batch(blank_rows, blank_indices)`
3. On error: `st.error(message)` — table unchanged, no rerun
4. On success:
   - For each returned suggestion: fill `Product Category`, set `AI Category Confidence`, set `Category Source = "AI Suggested"`
   - For rows where `AI Category Confidence < 75`: set `Review Required = True`, `Suggested Action = "Review AI category suggestion"`
   - Call `apply_confidence_checks()` on updated df
   - Save to `st.session_state.intake_df` and `st.rerun()`

### data_editor column config additions

```python
"AI Category Confidence": st.column_config.NumberColumn(
    "Cat. Confidence", width="small", disabled=True,
    min_value=0, max_value=100, format="%d %%"
),
"Category Source": st.column_config.TextColumn(
    "Category Source", width="small", disabled=True
),
```

---

## 8. Error handling summary

| Failure point | Behaviour |
|---|---|
| `ANTHROPIC_API_KEY` missing | Warning shown, no crash, table unchanged |
| PDF has no extractable text | Warning shown, fallback to filename-only row |
| Parser finds no product rows | Empty list returned; AI extraction runs on raw text only if toggle on |
| AI extraction API error | `st.error()` shown, fallback to parser rows (same as today) |
| Category batch API error | `st.error()` shown, table unchanged |
| Category batch returns unknown category | Normalised to "Other" with confidence 0 |
| Category batch returns partial results (some rows missing) | Unmatched rows left with blank category, no error |

---

## 9. Out of scope

- Manufacturer/vendor website lookup (field enrichment from external sources)
- Async/threaded AI calls
- Receipt-specific parsing (line-item totals, tax calculation)
- Sending to Programa after category suggestion
