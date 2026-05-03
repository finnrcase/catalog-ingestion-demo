# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Backend (FastAPI):**
```bash
pip install -r requirements.txt
playwright install chromium
uvicorn backend.main:app --reload --port 8000
```

**Frontend (Next.js):**
```bash
cd frontend && npm install && npm run dev   # http://localhost:3000
```

**Tests:**
```bash
pytest                          # all tests
pytest tests/test_confidence.py # single test file
pytest tests/test_confidence.py::test_function_name  # single test
```

**Legacy Streamlit (fallback only):**
```bash
streamlit run app.py
```

**Environment:** Copy `.env.example` to `.env`. Required keys: `ANTHROPIC_API_KEY`, `BRAVE_API_KEY`, `PROGRAMA_URL`, `PROGRAMA_BROWSER_PROFILE`.

## Architecture

This is a **production migration** from a Streamlit prototype (`app.py`) to Next.js + FastAPI. Business logic lives in `/src` Python modules and is shared by both the legacy Streamlit app and the new FastAPI backend.

```
frontend/ (Next.js + Tailwind on Vercel)
    components/intake-workspace.tsx  ← entire UI, ~2000 LOC
    lib/api.ts                        ← HTTP client to backend
backend/ (FastAPI on Render/Railway)
    main.py                           ← all API endpoints
src/ (Python business logic — shared by backend and app.py)
    intake_schema.py    ← canonical column list, categories, statuses, IMPORTANT_FIELDS
    intake.py           ← row factory functions
    document_parser.py  ← rule-based PDF extraction
    ai_extraction.py    ← Claude Haiku PDF extraction
    product_enrichment.py ← Brave Search → page fetch → Claude enrichment
    confidence.py       ← 0–100 scoring, REVIEW_THRESHOLD=75
    dimensions.py       ← W×H×D completeness validation
    eligibility.py      ← Programa send gating
    programa_export.py  ← primary Programa output: CSV/XLSX for Programa "Import Products"
    programa_automation.py ← legacy Playwright automation
    vendor_call_agent.py   ← Bland.ai / Retell.ai integration
```

## Key Patterns

**Primary Programa output is CSV/XLSX (`src/programa_export.py`):** `build_programa_import_dataframe()` maps internal intake rows to Programa's 21-column import schema, extracting `[Materials: ...]` tags from Notes, stripping system tags, and parsing labeled dimensions. `programa_automation.py` (Playwright) is the legacy path, labelled as such in the UI.

**Two-path Programa automation (legacy):** URL-sourced rows use Programa's "Add from URL" flow; PDF/AI-sourced rows go through direct Schedule row creation. Source type is tracked via constants in `intake_schema.py` (`SOURCE_MANUAL`, `SOURCE_URL`, `SOURCE_PDF`, `SOURCE_PDF_AI`).

**Confidence gate:** Every row gets a 0–100 confidence score (`src/confidence.py`). Rows below 75 or missing any `IMPORTANT_FIELDS` are flagged for review and blocked from Programa send.

**Eligibility gate (`src/eligibility.py`):** A row must have: non-ignored/excluded status, product name, quantity, category, and complete W×H×D dimensions before it can be sent to Programa.

**Enrichment never overwrites:** `product_enrichment.py` only fills blank fields — it does not overwrite existing values.

**Persistent browser profile:** Playwright automation stores Programa login state in `data/browser_profiles/` so sessions survive backend restarts. Path is set via `PROGRAMA_BROWSER_PROFILE` env var.

**Shared API contract:** `shared/api-contract.md` documents all 10 endpoints. Keep it in sync when modifying `backend/main.py`.

**`intake_schema.py` is the source of truth:** `ALL_COLUMNS`, `IMPORTANT_FIELDS`, `CATEGORIES`, and `STATUSES` are defined there and used across the stack. Change schema here first, then propagate to frontend `lib/types.ts`.
