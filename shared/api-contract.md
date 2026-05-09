# SCH DesignOps Intake API Contract

Frontend base URL is `NEXT_PUBLIC_API_BASE_URL`.

## `GET /health`

Returns:

```json
{ "status": "ok" }
```

## `GET /schema`

Returns category/status options and review-table field names.

## `POST /intake/generate`

Multipart form data:

| Field | Type | Notes |
|---|---|---|
| `project` | string | Existing Programa project/property name |
| `room` | string | Default location |
| `urls` | string | One product URL per line |
| `use_ai_pdf` | boolean | Defaults to `true` |
| `files` | PDF[] | Optional uploaded PDFs |

Returns:

```json
{
  "rows": [],
  "errors": [],
  "eligible_count": 0,
  "blocked_count": 0
}
```

## `POST /intake/validate`

JSON body:

```json
{ "rows": [] }
```

Re-runs confidence, review, and dimension checks.

## `POST /intake/enrich`

JSON body:

```json
{ "rows": [] }
```

Runs Brave/Claude enrichment against qualifying rows, then validates them.

## `POST /programa/eligible`

JSON body:

```json
{ "rows": [] }
```

Returns eligible rows and blocked rows with plain-language eligibility issues.

## `POST /programa/send`

JSON body:

```json
{
  "project_name": "1 Lily Pond Ln",
  "schedule_url": "https://app.programa.design/schedules2/schedules/1421850",
  "auto_done": false,
  "upload_product_images": true,
  "rows": []
}
```

Rules:

- Never creates Programa projects.
- Rows with incomplete W/H/D dimensions are blocked.
- Rows marked review-required are blocked.
- URL rows use Programa's Add from URL flow.
- PDF/AI rows use direct Schedule-row creation.
- `upload_product_images` defaults to true and uploads a discovered/downloaded product image when possible.

Returns automation log entries and log path when complete.

## `POST /export/csv`

JSON body:

```json
{ "rows": [] }
```

Returns `text/csv`.

## `POST /intake/upload-pdf`

Multipart form data:

| Field | Type | Notes |
|---|---|---|
| `file` | PDF | The PDF file to upload |

Optional request header:

| Header | Type | Notes |
|---|---|---|
| `X-Session-Id` | string | Reuse an existing session; generated server-side if absent |

Persists the PDF to `.tmp/uploads/{session_id}/pdfs/{pdf_id}.pdf` (deduped by SHA1[:12] of the file bytes) and returns parsed product rows.

Returns:

```json
{
  "session_id": "abc123",
  "pdf_id": "def456",
  "rows": []
}
```

## `POST /intake/recover-images`

JSON body:

```json
{ "rows": [], "session_id": "abc123" }
```

`session_id` is optional. When provided, the backend looks up PDFs stored under `.tmp/uploads/{session_id}/pdfs/` and uses them as the source for PDF crop recovery. Rows that already have an `Image URL` are skipped.

Returns `IntakeResponse` with recovery diagnostics in `dimension_diagnostics`.
