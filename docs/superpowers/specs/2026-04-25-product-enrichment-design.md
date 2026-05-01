# Product Enrichment Design
# SCH DesignOps Intake — v0.6

## Goal

After PDF/receipt/manual intake creates rows, automatically search for official
manufacturer product pages and fill any blank fields (Product Name, Dimensions,
Finish / Color, Product Category, Product URL, Notes/materials) before the user
sends rows to Programa. Human review always happens before Programa entry.

---

## Scope

- Brave Search API as the discovery engine (primary).
- httpx for page fetching (no Playwright in v1).
- Claude (haiku-4-5) for structured field extraction from page text.
- Automatic enrichment triggered immediately after intake; manual re-run button
  for retries.
- No new schema columns — all enrichment data maps to existing fields.
- Blank-only updates; never overwrite user-entered or PDF-extracted data.
- If BRAVE_API_KEY is missing, enrichment is silently skipped with a UI caption.

---

## New Files

### `src/brave_search.py`

Single responsibility: call the Brave Web Search API and return ranked results.

```
SearchResult(title, url, description, domain_score)

search_product_candidates(query: str, brand: str = "") -> list[SearchResult]
```

**Domain scoring (0–100):**

| Condition | Delta |
|-----------|-------|
| Domain contains brand slug (e.g. "wolf" in "wolfappliance.com") | +40 |
| Domain is in PREFERRED_DOMAINS list | +20 |
| Domain is in SKIP_DOMAINS list (Amazon, eBay, Reddit, Pinterest, Houzz forums, etc.) | −60 |
| Base | 50 |

PREFERRED_DOMAINS includes known manufacturer and design-trade vendor sites:
subzero-wolf.com, wolfappliance.com, miele.com, mieleusa.com, kohler.com,
kallista.com, brizo.com, dornbracht.com, waterworks.com, rh.com, article.com,
rejuvenation.com, cb2.com, crateandbarrel.com, westelm.com, visualcomfort.com,
circalighting.com, hudsonvalleylighting.com, scotsman-ice.com,
thermador.com, jenn-air.com, vikingrange.com, bertazzoni.com, ilve.com.

SKIP_DOMAINS: amazon.com, ebay.com, walmart.com, target.com, homedepot.com,
lowes.com, reddit.com, pinterest.com, yelp.com, houzz.com.

Returns up to 5 results sorted by domain_score descending.

---

### `src/product_enrichment.py`

Single responsibility: orchestrate search → fetch → extract → blank-only fill.

```
ENRICHABLE_FIELDS = [
    "Product Name", "Dimensions", "Finish / Color",
    "Product Category", "Product URL",
]

MIN_USE_SCORE   = 40   # below this: skip, leave Notes note
MIN_CONF_SCORE  = 60   # below this: fill fields but force Review Required = True

def _qualifies(row: dict) -> bool
def _build_search_query(row: dict) -> str
def _fetch_page_text(url: str) -> str          # httpx, 10 s timeout, 6 000 char cap
def _build_extraction_prompt(page_text, row) -> str
def _extract_with_claude(page_text, row) -> dict   # returns {field: value}
def _apply_enrichment(row, extracted, source_url, domain_score) -> dict

def enrich_row(row: dict) -> tuple[dict, str | None]
def enrich_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]
```

**Qualifying row criteria (`_qualifies`):**
- `Brand` non-empty AND `Model/SKU` non-empty.
- At least one of `Product Name`, `Dimensions`, `Finish / Color`,
  `Product Category` is blank.
- `Source Type` does NOT end in `_Enriched` (prevents re-enriching on auto-pass).
- `Source Type` is not `URL` (URL rows have no product metadata to search).

**Search query (`_build_search_query`):**
`"{Brand} {Model/SKU} {Product Name} specifications official"` — trailing tokens
trimmed if empty. Example: `"Wolf MDD30TS specifications official"`.

**Page fetch (`_fetch_page_text`):**
`httpx.get()` with a browser-like User-Agent, 10 s timeout, SSL verification on.
HTML stripped to plain text via `html2text` (preferred; preserves structure).
First 6 000
characters used. On any exception: return empty string (caller handles as no-result).

**Claude extraction prompt:**
- Lists which fields are currently blank in the row.
- Instructs Claude to return ONLY a flat JSON object with those keys.
- If a field is not visible in the page, value must be `""` — never invent.
- Model: `claude-haiku-4-5-20251001`, max_tokens 512.

**Blank-only fill (`_apply_enrichment`):**
- Each ENRICHABLE_FIELD is applied only if current row value is empty.
- `Product URL`: set to source URL if row's Product URL is blank.
- `Notes`: materials appended as `[Materials: ...]` only if meaningful and not
  already expressed in `Finish / Color`. Existing Notes content is preserved.
- `Source Type`: set to `"{original_source}_Enriched"` (e.g. `PDF_Enriched`).
- If domain_score < MIN_CONF_SCORE: `Review Required = True`,
  `Suggested Action = "Enriched from low-confidence source — verify fields"`.
- If domain_score < MIN_USE_SCORE: no fields changed; Notes appended with
  `[Enrichment: no confident source found]`; Status stays `Needs Enrichment`.
- Rate limiting: `time.sleep(0.5)` between rows.

**`enrich_row`:**
1. Call `_build_search_query`.
2. Call `search_product_candidates(query, brand)`.
3. Pick highest-scoring result. If best score < MIN_USE_SCORE → no-result path.
4. Call `_fetch_page_text(url)`. If empty → no-result path.
5. Call `_extract_with_claude(page_text, row)`.
6. Call `_apply_enrichment(row, extracted, url, score)`.
7. Return `(updated_row, None)` or `(row, error_string)` on exception.

**`enrich_dataframe`:**
- Filter qualifying rows with `_qualifies`.
- For each: call `enrich_row`; catch all exceptions, append to error list, leave
  row unchanged on failure.
- Return `(updated_df, errors)`.

---

## Modified Files

### `app.py`

**Session state additions:**
```python
"pending_enrichment": False   # set True after any intake path adds rows
"enrichment_errors":  []      # shown as warning after enrichment pass
```

**Enrichment trigger (automatic):**
After every intake path appends rows to `st.session_state.intake_df`, set:
```python
st.session_state.pending_enrichment = True
```

At the top of the review-table section (before `st.data_editor` renders):
```python
if st.session_state.pending_enrichment and BRAVE_API_KEY:
    with st.spinner("Searching manufacturer sources…"):
        df, errors = enrich_dataframe(st.session_state.intake_df)
        st.session_state.intake_df = df
        st.session_state.enrichment_errors = errors
        st.session_state.pending_enrichment = False
```

If `BRAVE_API_KEY` is empty, `st.caption("Product enrichment requires
BRAVE_API_KEY — add it to .env and restart.")` is shown below the table.

**Re-run button (manual):**
In the AI-Assisted Cleanup section, alongside "Suggest Missing Categories":
```
"Re-run Enrichment for Needs Enrichment Rows"
```
On click: set `pending_enrichment = True`, call `st.rerun()`.

**Error display:**
After the review table:
```python
if st.session_state.enrichment_errors:
    st.warning(f"{len(errors)} row(s) could not be enriched — details in Notes.")
```

### `.env.example`

Add:
```
BRAVE_API_KEY=your_brave_api_key_here
```

---

## Field Mapping Summary

| Extracted from page | Target column | Rule |
|---|---|---|
| Formal product title | `Product Name` | Fill if blank |
| Dimensions / specs | `Dimensions` | Fill if blank |
| Finish or color | `Finish / Color` | Fill if blank |
| Product category | `Product Category` | Normalise via `_normalise_category`; fill if blank |
| Source page URL | `Product URL` | Fill if blank |
| Materials | `Notes` | Append as `[Materials: ...]` only if adds info not in Finish/Color |
| Enrichment provenance | `Source Type` | Append `_Enriched` suffix |

---

## Safety Rules

1. Never overwrite a non-empty field.
2. Rate limit: 0.5 s between rows.
3. 10 s httpx timeout; any fetch error → no-result path (no crash).
4. Any exception in `enrich_row` is caught; row is left unchanged; error is
   logged to the errors list.
5. Low-confidence results (score 40–59) fill fields but force `Review Required`.
6. No-source results (score < 40) leave all fields unchanged.
7. `_Enriched` Source Type suffix prevents re-enriching on the automatic pass.
8. All enriched rows remain in the review table; user must inspect before
   sending to Programa.
9. If `BRAVE_API_KEY` is missing, enrichment block is skipped; app does not crash.

---

## Testing

`tests/test_product_enrichment.py`:
- `_qualifies`: URL rows skipped, already-enriched skipped, blank-SKU skipped,
  qualifying row accepted.
- `_build_search_query`: Brand + SKU + partial Name → correct query string.
- `_apply_enrichment`: blank fields filled, non-blank fields preserved, low-score
  forces Review Required, no-score leaves fields unchanged, Source Type gets suffix.
- `_extract_with_claude`: JSON parse succeeds; empty-string fields returned as
  empty string (not `None`).

`tests/test_brave_search.py`:
- Domain scoring: manufacturer domain scores high, Amazon scores low, brand-slug
  match adds 40.
- `search_product_candidates` with missing API key returns empty list (no crash).
