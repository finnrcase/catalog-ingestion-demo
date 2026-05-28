# SCH DesignOps Intake Vercel Deployment

This repo is deployment-ready as a split application:

- `frontend/` deploys to Vercel as the Next.js app.
- `backend/` remains a FastAPI/Python service deployed to Render, Railway, Fly.io, or another persistent Python host.

Do not move the current Python parser/enrichment/Programa automation backend into Vercel Functions without a separate porting pass. It uses long-running PDF parsing, PyMuPDF/OCR, Playwright, and temporary image/PDF files that are better suited to a persistent Python worker.

## Vercel Frontend Settings

Create the Vercel project with:

- Root Directory: `frontend`
- Framework Preset: Next.js
- Install Command: `npm ci`
- Build Command: `npm run build`
- Development Command: `npm run dev`

`frontend/vercel.json` records these settings for repeatability.

## Required Vercel Environment Variables

Set these in the Vercel project:

```env
NEXT_PUBLIC_API_BASE_URL=https://your-fastapi-backend.example.com
NEXT_PUBLIC_APP_URL=https://your-vercel-domain.example.com
NEXT_PUBLIC_INTERNAL_DEBUG=false
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon-key-if-client-storage-is-added>
```

Only `NEXT_PUBLIC_*` values belong in the Vercel frontend. The Supabase anon
key is acceptable only for client-safe, RLS-protected access. Do not add
service-role keys, Brave keys, Anthropic/OpenAI keys, Cloudinary secrets, Retell
keys, or Programa credentials to the frontend project.

## Backend Production Environment

Set these on the Python backend host, not in Vercel:

```env
ENVIRONMENT=production
DEMO_MODE=true
ENABLE_REAL_INTEGRATIONS=false
FRONTEND_ORIGINS=https://your-vercel-domain.example.com

MAX_UPLOAD_BYTES=104857600
PDF_PARSE_WORKERS=2
SCH_TMP_UPLOAD_ROOT=/tmp/sch-designops/uploads

UPLOAD_STORAGE_PROVIDER=supabase
REQUIRE_PERSISTENT_UPLOAD_STORAGE=true
UPLOAD_STORAGE_PREFIX=sch-intake
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<server-side-only>
SUPABASE_STORAGE_BUCKET=sch-intake-uploads

ANTHROPIC_API_KEY=<server-side-only>
BRAVE_API_KEY=<server-side-only>
CLOUDINARY_CLOUD_NAME=<server-side-only>
CLOUDINARY_API_KEY=<server-side-only>
CLOUDINARY_API_SECRET=<server-side-only>
```

For real Programa automation, flip `DEMO_MODE=false` and `ENABLE_REAL_INTEGRATIONS=true` only after a controlled end-to-end test passes on the deployed backend.

## Supabase Storage

Create a private storage bucket named by `SUPABASE_STORAGE_BUCKET`, for example `sch-intake-uploads`.

The backend stores uploaded PDFs under:

```text
{UPLOAD_STORAGE_PREFIX}/{session_id}/pdfs/{pdf_id}.pdf
```

The parser still uses `SCH_TMP_UPLOAD_ROOT` as temporary local working storage. On serverless-style hosts, set it to `/tmp/...`; on persistent Python hosts, use a private disk path if you want local retries to survive process restarts.

## Validation Checklist

Before production:

1. `npm run lint` passes in `frontend/`.
2. `npm run build` passes in `frontend/`.
3. Backend `GET /health` works from the public internet.
4. Backend `FRONTEND_ORIGINS` includes the Vercel domain.
5. Vercel `NEXT_PUBLIC_API_BASE_URL` points to the backend, not localhost.
6. Upload a small PDF and confirm parse states progress to complete.
7. Upload a larger PDF and confirm it queues instead of blocking the request.
8. Enrich with default Fast mode and confirm cost metrics show low/no AI calls where possible.
9. Export Programa XLSX with images and ZIP to confirm the existing export path still works.
10. Confirm no client bundle contains backend secrets.
