# Security Checklist

Audit date: 2026-04-30

## What Was Checked

- Whole-repo sensitive string scan for API keys, secrets, tokens, cookies, CSRF/authenticity tokens, AWS/S3 terms, database URLs, Programa references, Retell/Bland references, SCH/Saffron branding, and private-data terms.
- High-risk file inventory for `.env*`, Streamlit secrets, JSON credentials, key/certificate files, PDFs, CSVs, images, browser profiles, screenshots, automation logs, vendor-call logs, build artifacts, nested repositories, and local caches.
- Git state checks:
  - `git status --short`
  - `git diff --cached --stat`
  - `git log --all --full-history --oneline -- .env`
  - `git check-ignore` for local secret/session/artifact paths
- Frontend exposure review for `NEXT_PUBLIC_*` variables and local frontend env files.
- Backend and automation side-effect review for Programa sends, Programa login, Retell/Bland calls, and public-demo defaults.

## What Was Found

- `.env` exists locally and contains real private integration values. It is ignored and must not be committed.
- Local Programa browser profile data existed under `data/browser_profiles/`, including cookie/login/profile databases.
- Local automation JSON logs and screenshots existed under `data/automation_logs/`.
- Local vendor-call records existed under `data/vendor_calls/`.
- Local product image staging existed under `temp/product_images/`.
- Local frontend build/dependency/deployment artifacts existed under `frontend/.next/`, `frontend/node_modules/`, and `frontend/.vercel/`.
- Nested local repository folders existed at `SCHDATAINGEST/` and `SaffronCaseHomes/`.
- SCH/Saffron branding and Programa integration references remain in source code, docs, README, tests, and UI copy because this project is branded as SCH DesignOps Intake.
- No `.env` commits were found in git history by path scan.
- No staged files were present during this audit.

## What Was Changed

- Strengthened ignore coverage for:
  - `.env`, `.env.*`, frontend local env files, and Streamlit secrets
  - local data folders, uploads, exports, downloads, logs, browser profiles, screenshots, and product image staging
  - generated CSV/PDF/XLS/ZIP/database files
  - Playwright auth state, cookies, localStorage/sessionStorage, and storage-state JSON files
  - credential/service-account JSON files and private key/certificate files
  - Python caches/virtualenvs and frontend build/dependency artifacts
  - nested local clones `SCHDATAINGEST/` and `SaffronCaseHomes/`
- Updated `.env.example` and `backend/.env.example` so they contain placeholders and demo-safe defaults only.
- Added demo-safe backend defaults:
  - `DEMO_MODE=true`
  - `ENABLE_REAL_INTEGRATIONS=false`
  - Programa send/login routes return demo responses unless real integrations are explicitly enabled.
  - Vendor-call start/test routes return demo responses unless real integrations are explicitly enabled.
- Added demo-safe Programa automation defaults so direct Streamlit automation calls are skipped unless real integrations are explicitly enabled.
- Removed local generated/private artifacts from the working tree:
  - `data/browser_profiles/`
  - `data/automation_logs/`
  - `data/vendor_calls/`
  - `temp/product_images/`
  - `frontend/node_modules/`
  - `frontend/.next/`
  - `frontend/.vercel/`
  - Python cache folders
  - nested local clones `SCHDATAINGEST/` and `SaffronCaseHomes/`

## Ignored Files And Folders

- `.env`
- `.env.*` except committed example templates
- `frontend/.env.local`
- `frontend/.env.*.local`
- `.streamlit/secrets.toml`
- `data/`
- `uploads/`
- `uploaded_files/`
- `exports/`
- `downloads/`
- `temp/`
- `automation_logs/`
- `logs/`
- `*.log`
- `*.csv`, `*.tsv`, `*.xlsx`, `*.xls`, `*.pdf`, `*.zip`
- `*.sqlite`, `*.sqlite3`, `*.db`, `*.db-journal`, `*.db-wal`, `*.db-shm`
- `playwright/.auth/`
- `playwright-report/`
- `test-results/`
- `storage_state*.json`
- `*.storage-state.json`
- `cookies*.json`
- `localStorage*.json`
- `sessionStorage*.json`
- `*credential*.json`
- `*credentials*.json`
- `*service-account*.json`
- `*service_account*.json`
- `*.pem`, `*.key`, `*.crt`, `*.p12`, `*.pfx`
- `node_modules/`
- `frontend/node_modules/`
- `frontend/.next/`
- `frontend/out/`
- `frontend/.vercel/`
- `dist/`, `build/`, `.cache/`
- `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`
- `.venv/`, `venv/`, `env/`
- `SaffronCaseHomes/`
- `SCHDATAINGEST/`
- `.DS_Store`, `.vscode/`, `.idea/`

## Manual Review Before Public Deployment

- Rotate any local keys if there is any chance they were copied outside `.env`.
- Keep `DEMO_MODE=true` and `ENABLE_REAL_INTEGRATIONS=false` in public demo environments.
- Do not add secrets to `NEXT_PUBLIC_*`; those values are browser-visible.
- Do not use `git add -f` on ignored files or folders.
- Do not upload Chrome profiles, Programa screenshots, automation logs, vendor-call records, uploaded PDFs, receipts, generated CSVs, or product image staging folders.
- Decide whether the public demo is allowed to use SCH/Saffron branding. If not, rebrand README, UI copy, prompts, tests, and docs before making the repo public.
- Review docs under `docs/superpowers/` before public release; they contain internal planning history, local paths, and branded implementation notes.

## Verdict

Safe to push to a private/internal repository with normal `git add` usage.

Not safe for a public demo repository yet if SCH/Saffron branding, internal planning docs, or product/vendor examples must be removed. The code is now demo-safe by default for external side effects, but brand/content sanitization still needs a product decision before public release.
