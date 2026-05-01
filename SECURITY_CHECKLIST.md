# Security Checklist

Audit date: 2026-04-26

## What Was Checked

- Working tree file inventory, including ignored files.
- Git-tracked file list.
- Sensitive filename scan for:
  - `.env` and `.env.*`
  - `.streamlit/secrets.toml`
  - credential, token, secret, service-account, key, PEM, P12, and PFX files
  - Chrome/Playwright cookies, login data, local state, preferences, and profile databases
  - generated CSV/PDF/XLSX files
  - screenshots and automation logs
- Sensitive string scan for:
  - `ANTHROPIC_API_KEY`
  - `BRAVE_API_KEY`
  - `PROGRAMA_*`
  - API key, secret, token, cookie, password, authorization, bearer, localStorage, and sessionStorage references
  - common concrete key formats such as Anthropic-style keys, GitHub tokens, Google API keys, Slack tokens, and private key blocks
- Current `HEAD` scan for concrete secret-looking values.
- Git history scan for concrete secret-looking values.
- Git history path scan for `.env`, browser profiles, automation logs, screenshots, PDFs, CSVs, and key/certificate files.
- `.gitignore` behavior check with `git check-ignore`.
- Frontend production dependency audit with `npm audit --omit=dev`.

## What Was Found

- A local `.env` file exists and appears to contain real local configuration values. It is ignored and should never be committed.
- Local Chrome/Programa profile data exists under `data/browser_profiles/`, including cookie/login/profile databases. It is ignored.
- Local automation logs and screenshots exist under `data/automation_logs/`. They are ignored.
- Local frontend build artifacts and dependencies exist under `frontend/.next/` and `frontend/node_modules/`. They are ignored.
- A local nested repository folder exists at `SaffronCaseHomes/`. It is ignored to avoid accidental sub-repo commits.
- No concrete secret-looking values were found in tracked `HEAD` files.
- No concrete secret-looking values were found in searched git history.
- No `.env`, browser profile, automation log, screenshot, PDF, CSV, key, or credential paths appeared in searched git history.
- `npm audit --omit=dev` reported 2 moderate advisories through Next.js' nested PostCSS dependency. This is not a secret-exposure issue; review before production deployment and update Next.js when a safe upstream fix is available.

## What Was Changed

- Strengthened `.gitignore` to cover:
  - real environment files
  - Streamlit secrets
  - Python caches and virtualenvs
  - all local `data/`
  - uploads, exports, downloads
  - generated CSV/XLS/XLSX/PDF/ZIP files
  - SQLite/database files
  - Playwright auth state, browser cookies, and storage-state JSON files
  - credential and service-account JSON files
  - private key/certificate files
  - frontend dependencies and build artifacts
  - local nested `SaffronCaseHomes/` clone
  - editor and OS noise
- Confirmed `.env.example`, `backend/.env.example`, and `frontend/.env.example` remain unignored and contain placeholders only for API keys.

## Ignored Files And Folders

- `.env`
- `.env.*` except committed example templates
- `.streamlit/secrets.toml`
- `data/`
- `uploads/`
- `exports/`
- `downloads/`
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
- `*.pem`, `*.key`, `*.p12`, `*.pfx`
- `frontend/node_modules/`
- `frontend/.next/`
- `frontend/out/`
- `frontend/.vercel/`
- `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`
- `.venv/`, `venv/`, `env/`
- `SaffronCaseHomes/`
- `.DS_Store`, `.vscode/`, `.idea/`

## Manual Review Before Deployment

- Verify GitHub/Vercel/Render/Railway environment variables are set through platform secret managers, not committed files.
- Rotate any local API keys if there is any chance they were copied outside `.env`.
- Do not use `git add -f` on ignored paths.
- Do not upload `data/`, Chrome profile folders, automation screenshots, logs, uploaded vendor PDFs, receipts, or generated CSV exports.
- Review any future sample fixtures before committing them; use synthetic data only.
- Review the current frontend dependency audit before production deployment. Do not apply the suggested major downgrade automatically.
- For deployment, create fresh platform environment variables for:
  - `ANTHROPIC_API_KEY`
  - `BRAVE_API_KEY`
  - `PROGRAMA_URL`
  - `PROGRAMA_BROWSER_PROFILE`
  - `FRONTEND_ORIGINS`
  - `NEXT_PUBLIC_API_BASE_URL`

## Verdict

Safe to push with normal `git add` / `git commit` usage. Do not force-add ignored local files.
