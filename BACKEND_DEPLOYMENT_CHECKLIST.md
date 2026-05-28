# Backend Deployment Checklist

Status: preview/demo preparation only. Do not call this production-ready until a
controlled end-to-end Programa run has passed on the deployed backend.

## Target Host

Deploy the FastAPI backend to Render, Railway, Fly.io, or another Python host
that supports:

- Long-running Python web services
- Playwright/Chromium or Google Chrome
- Persistent disk/storage for the Programa browser profile
- Access to write automation logs and screenshots

Do not deploy the Programa automation backend to Vercel.

## Required Environment Variables

Set these in the backend host secret manager:

```env
PROGRAMA_URL=https://app.programa.design/
PROGRAMA_BROWSER_PROFILE=/persistent/programa_assistant
ANTHROPIC_API_KEY=<set in platform secrets>
BRAVE_API_KEY=<set in platform secrets>
FRONTEND_ORIGINS=https://your-vercel-preview-url.vercel.app,https://your-production-domain.example
MAX_UPLOAD_BYTES=104857600
SCH_TMP_UPLOAD_ROOT=/tmp/sch-designops/uploads
UPLOAD_STORAGE_PROVIDER=supabase
REQUIRE_PERSISTENT_UPLOAD_STORAGE=true
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<set in platform secrets>
SUPABASE_STORAGE_BUCKET=sch-intake-uploads
```

Never commit real values to the repo.

## Persistent Browser Profile Strategy

Programa login/session state is stored in the browser profile directory.

- Use a persistent disk/volume path for `PROGRAMA_BROWSER_PROFILE`.
- Do not use the repo `data/` directory in production unless it is backed by
  persistent, private storage.
- Treat the profile as sensitive because it can contain cookies, local storage,
  login databases, and Programa session data.
- Restrict host-level access to the storage volume.
- If a session looks compromised, delete the profile and log in again.

## Playwright / Chrome Requirements

The backend host must install browser dependencies before running automation.

Recommended setup:

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m playwright install-deps chromium
```

If using Google Chrome instead of bundled Chromium, confirm the host image has
Chrome installed and that `src/programa_automation.py` can launch it.

## Start Command

```bash
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

## Health Check

Use:

```text
GET /health
```

Expected response:

```json
{ "status": "ok" }
```

## Logs And Screenshots Storage

Automation logs and screenshots can include private project, vendor, product, or
client information.

- Store logs/screenshots in private backend storage only.
- Do not expose the log directory as public static assets.
- Do not commit `data/automation_logs/`.
- Set a retention policy before production use.
- Consider external private object storage for long-term retention.

## Uploaded PDF Storage

Production uploads should not rely only on the host filesystem. Configure
Supabase Storage or equivalent object storage for uploaded PDFs:

- Create a private bucket matching `SUPABASE_STORAGE_BUCKET`.
- Keep `SUPABASE_SERVICE_ROLE_KEY` on the backend only.
- Set `UPLOAD_STORAGE_PROVIDER=supabase`.
- Set `REQUIRE_PERSISTENT_UPLOAD_STORAGE=true` once the bucket is verified.
- Use `/tmp` through `SCH_TMP_UPLOAD_ROOT` only for parser working files.

## Controlled End-To-End Test Flow

Before calling the backend production-ready:

1. Deploy backend to the selected host.
2. Set all required environment variables.
3. Confirm `GET /health` from a browser and from Vercel preview.
4. Confirm CORS allows the Vercel preview URL.
5. Open the frontend preview.
6. Enter one existing Programa test project/property.
7. Upload one sanitized test PDF or paste one safe product URL.
8. Generate the intake table.
9. Fix dimensions until the row is no longer review-required.
10. Send one row to Programa with auto-save off.
11. Confirm no new Programa project is created.
12. Confirm the Schedule/Add from URL path writes the expected fields.
13. Review generated logs/screenshots for sensitive data handling.
14. Delete test data if it should not persist.

## Preview/Demo Gate

Preview/demo-ready means:

- Frontend preview deploys on Vercel.
- Frontend can reach the local or deployed FastAPI base URL.
- Backend health/schema endpoints work.
- Core Python tests pass.

Production-ready additionally requires the controlled end-to-end Programa test
above and a reviewed logging/storage policy.
