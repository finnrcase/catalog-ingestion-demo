# SCH DesignOps Intake

Internal intake product for Saffron Case Homes. It converts vendor PDFs, quotes,
receipts, tear sheets, and product URLs into structured Programa-ready product
rows, then sends approved rows into existing Programa projects.

The original Streamlit prototype is preserved as `app.py`. The production
migration now lives beside it:

```text
frontend/                  Next.js + React + Tailwind desktop-style web app
backend/                   FastAPI wrapper around the existing Python logic
shared/api-contract.md     Frontend/backend API contract
src/                       Existing extraction, enrichment, validation, export, automation modules
tests/                     Python tests for core contracts
app.py                     Legacy Streamlit fallback
```

## Current Backend Modules

- `src/intake_schema.py` owns canonical columns, categories, statuses, and base row defaults.
- `src/intake.py` creates rows from URLs, PDFs, and structured manual data.
- `src/document_parser.py` extracts PDF rows without AI.
- `src/ai_extraction.py` extracts PDF rows with Claude when enabled.
- `src/product_enrichment.py` enriches rows with Brave Search, page fetches, and Claude.
- `src/dimensions.py` strictly validates explicit W/H/D dimensions.
- `src/confidence.py` scores rows, flags review needs, and blocks incomplete dimensions.
- `src/eligibility.py` centralizes Programa send eligibility.
- `src/programa_automation.py` runs the two-path Playwright Programa automation.
- `src/automation_logs.py` writes row-level automation logs and screenshots.

## Preview Migration Plan

Current status: preview/demo-ready track. Do not call this production-ready until
the deployed backend passes a controlled end-to-end Programa test.

1. Keep `app.py` available as the legacy fallback while the production UI matures.
2. Use `backend/` as the API boundary for extraction, enrichment, validation, CSV export, and Programa automation.
3. Use `frontend/` as the polished internal desktop product deployed to Vercel Preview.
4. Keep all high-risk business rules in Python first, especially dimension gating and Programa eligibility.
5. Deploy the backend separately on Render, Railway, or Fly.io because Programa automation needs Playwright/Chrome and a persistent browser profile.
6. Point the Vercel frontend at the deployed backend with `NEXT_PUBLIC_API_BASE_URL`.

## Local Development

Install Python dependencies:

```bash
pip install -r requirements.txt
playwright install chromium
```

Run the FastAPI backend:

```bash
uvicorn backend.main:app --reload --port 8000
```

Run the production frontend:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`.

Run the legacy Streamlit app:

```bash
streamlit run app.py
```

## Environment Variables

Copy `.env.example` to `.env` for local Python/Streamlit/backend development.

```env
PROGRAMA_URL=https://app.programa.design/
PROGRAMA_BROWSER_PROFILE=/tmp/sch-data/browser_profiles/programa_assistant
ANTHROPIC_API_KEY=your_anthropic_api_key_here
BRAVE_API_KEY=your_brave_api_key_here
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
FRONTEND_ORIGINS=http://localhost:3000
```

Frontend-only deployments also need:

```env
NEXT_PUBLIC_API_BASE_URL=https://your-backend.example.com
```

## Deployment

### Frontend on Vercel Preview

1. Create a Vercel project from this repo.
2. Set the project root directory to `frontend`.
3. Add `NEXT_PUBLIC_API_BASE_URL` pointing to the deployed backend.
4. Deploy.

### Backend on Render/Railway/Fly.io

See `BACKEND_DEPLOYMENT_CHECKLIST.md` before deploying the backend.

Use the repo root as the backend working directory.

Start command:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

Add environment variables:

- `PROGRAMA_URL`
- `PROGRAMA_BROWSER_PROFILE`
- `ANTHROPIC_API_KEY`
- `BRAVE_API_KEY`
- `FRONTEND_ORIGINS` (comma-separated allowed frontend URLs)

For Programa automation, the backend host must support Playwright and a usable
Chromium/Chrome install. Keep the browser profile path on persistent storage if
you want Programa login sessions to survive restarts.

## Programa Safety Rules

- The system never creates new Programa projects.
- Users must enter an existing Programa project/property.
- Rows are blocked from Programa unless they are included, not review-required,
  not ignored/excluded, have product name, quantity, category, and complete W/H/D
  dimensions.
- URL rows use Programa's Add from URL flow.
- PDF/AI/no-URL rows use direct Schedule-row creation.
- Automation logs include product name, product URL, status, timestamp,
  screenshot path, and error message.

## SCH Logo

Place the official SCH logo at `assets/logo.png` for the legacy Streamlit app.
The new frontend currently uses a polished SCH placeholder mark; replace
`frontend/components/intake-workspace.tsx` `LogoMark` when the final logo asset is available.
