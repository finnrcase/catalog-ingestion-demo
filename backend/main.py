from __future__ import annotations

import datetime
import hashlib
import io
import logging
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_DEFAULT_TMP_UPLOADS = Path("/tmp/sch-designops/uploads") if os.getenv("VERCEL") else ROOT / ".tmp" / "uploads"
_TMP_UPLOADS = Path(os.getenv("SCH_TMP_UPLOAD_ROOT", str(_DEFAULT_TMP_UPLOADS))).expanduser()
_MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
logger = logging.getLogger(__name__)
_STARTED_AT = time.time()
_PDF_PARSE_EXECUTOR = ThreadPoolExecutor(max_workers=int(os.getenv("PDF_PARSE_WORKERS", "2")))
_PDF_JOBS_LOCK = threading.Lock()
_PDF_JOBS: dict[str, "PdfParseJob"] = {}

from src.ai_extraction import extract_products_from_pdf_with_ai
from src.confidence import apply_confidence_checks
from src.document_parser import parse_pdf_rows
from src.eligibility import split_eligible_rows
from src.export import get_csv_bytes
from src.intake import build_intake_dataframe, create_pdf_rows, create_photo_rows, create_url_rows
from src.intake_schema import CATEGORIES, STATUSES
from src.image_uploader import is_public_https_image_url, upload_image_with_metadata
from src.manufacturer_domains import save_manufacturer_override
from src.notes import remove_notes_row_prefix
from src.image_recovery import cleanup_old_sessions
from src.image_recovery import build_photo_discovery_report
from src.pdf_product_workflow import (
    enrich_pdf_rows_with_official_product_urls,
    normalize_pdf_product_rows,
)
from src.pdf_parsing_pipeline import (
    HARD_TIMEOUT_SECONDS,
    SOFT_TIMEOUT_SECONDS,
    PdfParseCancelledError,
    parse_pdf_file_resilient,
)
from src.preferred_websites import (
    add_preferred_website,
    delete_preferred_website,
    list_preferred_websites,
    update_preferred_website,
)
from src.persistent_storage import (
    persistent_upload_storage_enabled,
    require_persistent_upload_storage,
    upload_file_to_persistent_storage,
)
from src.product_enrichment import enrich_dataframe, recover_images_for_dataframe
from src.programa_export import (
    CANONICAL_SECTIONS,
    build_programa_debug_dataframe,
    build_programa_import_dataframe,
    export_programa_csv,
    export_programa_xlsx,
    export_programa_xlsx_with_images,
    export_programa_zip,
    generate_programa_export_filename,
    validate_for_export,
)
from src.programa_automation import open_programa_login_window, run_programa_automation
from src.vendor_call_agent import (
    build_call_script,
    calls_enabled,
    extract_vendor_specs_from_transcript,
    get_call_status,
    get_call_transcript,
    get_call_provider,
    parse_call_transcript_for_missing_values,
    parse_transcript_to_fields,
    prepare_call_payload,
    start_custom_retell_test_call,
    start_vendor_call,
)


class UploadedPDF:
    """Small adapter so existing parser functions can read FastAPI uploads."""

    def __init__(self, name: str, content: bytes):
        self.name = name
        self._buffer = io.BytesIO(content)

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        return self._buffer.read(*args, **kwargs)

    def seek(self, *args: Any, **kwargs: Any) -> int:
        return self._buffer.seek(*args, **kwargs)


@dataclass
class PdfParseJob:
    job_id: str
    session_id: str
    pdf_id: str
    filename: str
    pdf_path: str
    project: str = ""
    room: str = ""
    supplier: str = ""
    notes: str = ""
    status: str = "queued"
    stage: str = "queued"
    rows: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    logs: list[dict] = field(default_factory=list)
    telemetry: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def log(self, stage: str, message: str, **extra: Any) -> None:
        self.stage = stage
        self.updated_at = time.time()
        item = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "stage": stage,
            "message": message,
            **extra,
        }
        self.logs.append(item)
        logger.info("[pdf-parse] job=%s stage=%s %s", self.job_id, stage, message)

    def public(self, include_rows: bool = True, include_logs: bool = False) -> dict:
        payload = {
            "job_id": self.job_id,
            "session_id": self.session_id,
            "pdf_id": self.pdf_id,
            "filename": self.filename,
            "status": self.status,
            "stage": self.stage,
            "rows": self.rows if include_rows else [],
            "errors": self.errors,
            "telemetry": self.telemetry,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "log_count": len(self.logs),
        }
        if include_logs:
            payload["logs"] = self.logs
        return payload


class RowsPayload(BaseModel):
    rows: list[dict] = Field(default_factory=list)
    enrichment_mode: str = "fast"
    force_refresh: bool = False
    use_web_enrichment: bool = True
    include_low_confidence_images: bool = False
    session_id: str | None = None
    targeted_retry_mode: str = "conservative"
    max_extra_retries_per_item: int | None = None
    max_extra_cost_per_row: float | None = None
    max_extra_cost_per_run: float | None = None


class ProgramaPayload(RowsPayload):
    project_name: str = ""
    schedule_url: str = ""
    auto_done: bool = False
    allow_blank_fields: bool = False
    upload_product_images: bool = True


class VendorCallPayload(BaseModel):
    row: dict = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    phone_number: str = ""
    custom_goal: str = ""


class CustomRetellTestCallPayload(BaseModel):
    phone_number: str = ""
    custom_prompt: str = ""


class VendorCallTranscriptPayload(BaseModel):
    transcript: str = ""
    missing_fields: list[str] = Field(default_factory=list)
    row: dict = Field(default_factory=dict)


class VendorCallRefreshPayload(BaseModel):
    call_id: str = ""
    missing_fields: list[str] = Field(default_factory=list)
    row: dict = Field(default_factory=dict)


class IntakeResponse(BaseModel):
    rows: list[dict]
    errors: list[str] = Field(default_factory=list)
    eligible_count: int = 0
    blocked_count: int = 0
    dimension_diagnostics: list[dict] = Field(default_factory=list)


class UploadPdfResponse(BaseModel):
    session_id: str
    pdf_id: str
    parse_job_id: str = ""
    status: str = "queued"
    stage: str = "queued"
    rows: list[dict] = Field(default_factory=list)


class PdfParseJobResponse(BaseModel):
    job_id: str
    session_id: str
    pdf_id: str
    filename: str = ""
    status: str = "queued"
    stage: str = "queued"
    rows: list[dict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    telemetry: dict = Field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    log_count: int = 0
    logs: list[dict] | None = None


class ImageUploadResponse(BaseModel):
    secure_url: str
    public_id: str = ""
    width: int = 0
    height: int = 0
    format: str = ""
    bytes: int = 0
    image_upload_status: str = ""
    debug: dict = Field(default_factory=dict)


class ManufacturerOverridePayload(BaseModel):
    brand: str = ""
    website: str = ""


class PreferredWebsitePayload(BaseModel):
    keyword: str = ""
    url: str = ""
    notes: str = ""


app = FastAPI(
    title="SCH DesignOps Intake API",
    version="0.1.0",
    description="Extraction, enrichment, validation, export, and Programa automation API.",
)

_default_origins = "" if os.getenv("ENVIRONMENT", "").lower() == "production" else "http://localhost:3000,http://127.0.0.1:3000"
_origins = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGINS", _default_origins).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Type"],
)


@app.on_event("startup")
def _startup_cleanup():
    try:
        cleanup_old_sessions(max_age_hours=24)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "[IMAGE RECOVERY] startup cleanup failed: %s", exc
        )


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _real_integrations_enabled() -> bool:
    """Public demo safety: external side effects are opt-in only."""
    return _env_flag("ENABLE_REAL_INTEGRATIONS", False) and not _env_flag("DEMO_MODE", True)


def _admin_enrichment_modes_allowed() -> bool:
    if _env_flag("ALLOW_ADMIN_ENRICHMENT_MODES", False):
        return True
    return os.getenv("ENVIRONMENT", "").strip().lower() != "production"


def _demo_mode_response(action: str, rows: int = 0) -> dict:
    return {
        "status": "demo_mode",
        "message": (
            f"Demo mode: {action} is disabled. "
            "No browser automation, phone call, or external API request was made."
        ),
        "rows_received": rows,
    }


def _df_response(
    df: pd.DataFrame,
    errors: list[str] | None = None,
    dimension_diagnostics: list[dict] | None = None,
) -> IntakeResponse:
    df = df.copy()
    if "Notes" in df.columns:
        df["Notes"] = df["Notes"].apply(remove_notes_row_prefix)
    rows = df.fillna("").to_dict("records")
    eligible, blocked = split_eligible_rows(rows)
    return IntakeResponse(
        rows=rows,
        errors=errors or [],
        eligible_count=len(eligible),
        blocked_count=len(blocked),
        dimension_diagnostics=dimension_diagnostics or [],
    )


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _STARTED_AT, 2),
        "cold_start_window": (time.time() - _STARTED_AT) < 30,
        "max_upload_mb": round(_MAX_UPLOAD_BYTES / 1024 / 1024),
        "upload_storage_provider": os.getenv("UPLOAD_STORAGE_PROVIDER", "local").strip().lower() or "local",
    }


@app.get("/warmup")
def warmup() -> dict:
    return {"status": "warm", "uptime_seconds": round(time.time() - _STARTED_AT, 2)}


@app.get("/schema")
def schema() -> dict:
    return {
        "categories": CATEGORIES,
        "sections": CANONICAL_SECTIONS,
        "statuses": STATUSES,
        "reviewFields": [
            "Include",
            "Confidence Score",
            "Review Required",
            "Suggested Action",
            "Project",
            "Room",
            "Product Name",
            "Brand",
            "Dimensions",
            "Quantity",
            "Supplier",
            "Finish / Color",
            "Product Category",
            "Model/SKU",
            "Notes",
        ],
    }


def _upload_file_type(upload: UploadFile) -> str:
    content_type = (upload.content_type or "").lower()
    filename = (upload.filename or "").lower()
    if content_type == "application/pdf" or filename.endswith(".pdf"):
        return "pdf"
    if content_type.startswith("image/") or filename.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif")):
        return "image"
    return "unsupported"


def _log_upload_stage(stage: str, upload: UploadFile, file_type: str = "") -> None:
    logger.info(
        "[upload] stage=%s filename=%s mime=%s classified_type=%s",
        stage,
        upload.filename or "upload",
        upload.content_type or "unknown",
        file_type or "unknown",
    )


def _get_pdf_job(job_id: str) -> PdfParseJob:
    with _PDF_JOBS_LOCK:
        job = _PDF_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="PDF parse job not found.")
    return job


def _store_pdf_job(job: PdfParseJob) -> None:
    with _PDF_JOBS_LOCK:
        _PDF_JOBS[job.job_id] = job


def _enqueue_pdf_parse_job(job: PdfParseJob) -> None:
    job.status = "queued"
    job.log("queued", "PDF parsing queued")
    _store_pdf_job(job)
    _PDF_PARSE_EXECUTOR.submit(_run_pdf_parse_job, job.job_id)


def _run_pdf_parse_job(job_id: str) -> None:
    job = _get_pdf_job(job_id)
    if job.cancel_event.is_set():
        job.status = "cancelled"
        job.log("cancelled", "PDF parsing cancelled before start")
        return
    job.status = "parsing"
    job.log("parsing", "PDF parser started", soft_timeout=SOFT_TIMEOUT_SECONDS, hard_timeout=HARD_TIMEOUT_SECONDS)
    try:
        result = parse_pdf_file_resilient(
            job.pdf_path,
            filename=job.filename,
            project=job.project,
            room=job.room,
            supplier=job.supplier,
            notes=job.notes,
            soft_timeout=SOFT_TIMEOUT_SECONDS,
            hard_timeout=HARD_TIMEOUT_SECONDS,
            cancel_check=job.cancel_event.is_set,
            status_callback=lambda stage: job.log(stage, _status_message_for_stage(stage)),
        )
        if result.rows:
            normalized_rows = normalize_pdf_product_rows(
                result.rows,
                source_rows=result.rows,
                source_filename=job.filename,
            )
            df = apply_confidence_checks(build_intake_dataframe([], normalized_rows))
            if "Notes" in df.columns:
                df["Notes"] = df["Notes"].apply(remove_notes_row_prefix)
            job.rows = df.fillna("").to_dict("records")
        else:
            job.rows = []
        job.telemetry = {
            "parser_used": result.parser_used,
            "parse_status": result.status,
            "page_count": result.page_count,
            "ocr_triggered": result.ocr_triggered,
            "extracted_text_length": result.extracted_text_length,
            "attempts": [
                {key: value for key, value in attempt.__dict__.items() if key != "extra_rows"}
                for attempt in result.attempts
            ],
            "backend_uptime_seconds": round(time.time() - _STARTED_AT, 2),
            "cold_start_window": (time.time() - _STARTED_AT) < 30,
        }
        if result.rows:
            job.status = "complete"
            job.log("complete", "PDF parsing complete", rows=len(result.rows), parser=result.parser_used)
        else:
            job.status = "failed"
            job.errors.append(result.error or "No parseable product rows found.")
            job.log("failed", job.errors[-1], telemetry=job.telemetry)
    except PdfParseCancelledError:
        job.status = "cancelled"
        job.errors.append("PDF parsing cancelled.")
        job.log("cancelled", "PDF parsing cancelled")
    except Exception as exc:
        import traceback

        stack = traceback.format_exc(limit=20)
        job.status = "failed"
        job.errors.append(f"{type(exc).__name__}: {exc}")
        job.telemetry = {
            "parser_used": job.telemetry.get("parser_used", ""),
            "parse_status": "failed",
            "stack": stack,
            "backend_uptime_seconds": round(time.time() - _STARTED_AT, 2),
            "cold_start_window": (time.time() - _STARTED_AT) < 30,
        }
        job.log("failed", job.errors[-1], stack=stack)
    finally:
        job.stage = job.status
        job.updated_at = time.time()


def _status_message_for_stage(stage: str) -> str:
    return {
        "queued": "Queued",
        "parsing": "Parsing",
        "ocr_fallback": "OCR fallback running",
        "complete": "Complete",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }.get(stage, stage)


async def _stream_pdf_upload(file: UploadFile, session_id: str) -> tuple[str, Path, int]:
    pdfs_dir = _TMP_UPLOADS / session_id / "pdfs"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    temp_path = pdfs_dir / f".{uuid.uuid4().hex}.upload"
    digest = hashlib.sha1()
    total_bytes = 0
    with temp_path.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > _MAX_UPLOAD_BYTES:
                try:
                    temp_path.unlink()
                except OSError:
                    pass
                raise HTTPException(
                    status_code=413,
                    detail=f"Uploaded PDF exceeds the {round(_MAX_UPLOAD_BYTES / 1024 / 1024)} MB limit.",
                )
            digest.update(chunk)
            out.write(chunk)
    if total_bytes == 0:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="Uploaded PDF was empty.")
    pdf_id = digest.hexdigest()[:12]
    pdf_path = pdfs_dir / f"{pdf_id}.pdf"
    if pdf_path.exists():
        temp_path.unlink(missing_ok=True)
    else:
        temp_path.replace(pdf_path)
    return pdf_id, pdf_path, total_bytes


async def _read_upload_with_limit(upload: UploadFile, *, max_bytes: int = _MAX_UPLOAD_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename or 'upload'} exceeds the {round(max_bytes / 1024 / 1024)} MB upload limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/intake/generate", response_model=IntakeResponse)
async def generate_intake(
    project: str = Form(""),
    room: str = Form(""),
    urls: str = Form(""),
    use_ai_pdf: bool = Form(True),
    files: list[UploadFile] = File(default=[]),
) -> IntakeResponse:
    raw_urls = [line.strip() for line in urls.splitlines() if line.strip()]
    url_rows = create_url_rows(raw_urls, project, room, "", "")
    pdf_rows: list[dict] = []
    photo_rows: list[dict] = []
    errors: list[str] = []

    for upload in files:
        file_type = _upload_file_type(upload)
        _log_upload_stage("validating", upload, file_type)
        if file_type == "unsupported":
            errors.append(
                f"{upload.filename or 'upload'}: unsupported file type "
                f"({upload.content_type or 'unknown'}). Upload a PDF or image file."
            )
            _log_upload_stage("error", upload, file_type)
            continue

        content = await _read_upload_with_limit(upload)

        if file_type == "image":
            _log_upload_stage("parsing", upload, file_type)
            photo_rows.extend(create_photo_rows(
                [{
                    "image_filename": upload.filename or "product_photo",
                    "image_upload_status": "Ready",
                }],
                project,
                room,
            ))
            _log_upload_stage("complete", upload, file_type)
            continue

        _log_upload_stage("parsing", upload, file_type)
        pdf = UploadedPDF(upload.filename or "upload.pdf", content)

        if use_ai_pdf:
            df_ai, error = extract_products_from_pdf_with_ai(pdf, project, room, "")
            if error:
                errors.append(f"{pdf.name}: {error}")
                try:
                    pdf.seek(0)
                    fallback = parse_pdf_rows(pdf, project, room, "", "")
                except Exception as exc:
                    errors.append(f"{pdf.name}: local PDF parser fallback failed: {exc}")
                    fallback = []
                if not fallback:
                    fallback = create_pdf_rows([pdf], project, room, "", "")
                pdf_rows.extend(normalize_pdf_product_rows(
                    fallback,
                    source_rows=fallback,
                    source_pdf_bytes=content,
                    source_filename=pdf.name,
                ))
            else:
                source_rows: list[dict] = []
                try:
                    pdf.seek(0)
                    source_rows = parse_pdf_rows(pdf, project, room, "", "")
                except Exception:
                    source_rows = []
                pdf_rows.extend(normalize_pdf_product_rows(
                    df_ai.to_dict("records"),
                    source_rows=source_rows,
                    source_pdf_bytes=content,
                    source_filename=pdf.name,
                ))
        else:
            try:
                parsed = parse_pdf_rows(pdf, project, room, "", "")
                rows = parsed or create_pdf_rows([pdf], project, room, "", "")
                pdf_rows.extend(normalize_pdf_product_rows(
                    rows,
                    source_rows=parsed,
                    source_pdf_bytes=content,
                    source_filename=pdf.name,
                ))
            except Exception as exc:
                errors.append(f"{pdf.name}: {exc}")
                fallback = create_pdf_rows([pdf], project, room, "", "")
                pdf_rows.extend(normalize_pdf_product_rows(
                    fallback,
                    source_pdf_bytes=content,
                    source_filename=pdf.name,
                ))
        _log_upload_stage("complete", upload, file_type)

    if not url_rows and not pdf_rows and not photo_rows:
        detail = " ".join(errors) if errors else "Upload a PDF, image, or paste at least one URL."
        raise HTTPException(status_code=400, detail=detail)

    pdf_rows, lookup_errors = enrich_pdf_rows_with_official_product_urls(pdf_rows)
    errors.extend(lookup_errors)

    df = build_intake_dataframe(url_rows, pdf_rows, manual_rows=photo_rows)
    df = apply_confidence_checks(df)
    return _df_response(df, errors)


@app.post("/api/upload-image", response_model=ImageUploadResponse)
async def upload_image_endpoint(file: UploadFile = File(...)) -> ImageUploadResponse:
    content_type = (file.content_type or "").lower()
    filename = (file.filename or "").lower()
    if not (content_type.startswith("image/") or filename.endswith((".jpg", ".jpeg", ".png", ".webp", ".avif"))):
        raise HTTPException(status_code=400, detail="Only image files can be uploaded.")

    content = await _read_upload_with_limit(file, max_bytes=min(_MAX_UPLOAD_BYTES, 25 * 1024 * 1024))
    result = upload_image_with_metadata(io.BytesIO(content))
    secure_url = result.secure_url
    if not secure_url or not is_public_https_image_url(secure_url):
        if result.error == "cloudinary_not_configured":
            raise HTTPException(
                status_code=503,
                detail=(
                    "Cloudinary image upload is not configured. Set CLOUDINARY_CLOUD_NAME, "
                    "CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET, or set CLOUDINARY_URL."
                ),
            )
        detail = result.error or "Cloudinary upload failed or did not return a secure HTTPS URL."
        raise HTTPException(status_code=502, detail=detail)
    return ImageUploadResponse(
        secure_url=secure_url,
        public_id=result.public_id,
        width=result.width,
        height=result.height,
        format=result.format,
        bytes=result.bytes,
        image_upload_status=result.status,
        debug=result.debug,
    )


@app.post("/upload-image", response_model=ImageUploadResponse)
async def upload_image_endpoint_alias(file: UploadFile = File(...)) -> ImageUploadResponse:
    return await upload_image_endpoint(file)


@app.post("/intake/validate", response_model=IntakeResponse)
def validate_intake(payload: RowsPayload) -> IntakeResponse:
    df = apply_confidence_checks(pd.DataFrame(payload.rows))
    return _df_response(df)


@app.post("/intake/enrich", response_model=IntakeResponse)
def enrich_intake(payload: RowsPayload) -> IntakeResponse:
    if payload.enrichment_mode not in {"", "fast"} and not _admin_enrichment_modes_allowed():
        raise HTTPException(status_code=403, detail="Balanced and deep enrichment are admin-only.")
    df, errors, dimension_diagnostics = enrich_dataframe(
        pd.DataFrame(payload.rows),
        enrichment_mode=payload.enrichment_mode,
        force_refresh=payload.force_refresh,
        use_web_enrichment=payload.use_web_enrichment,
        targeted_retry_mode=payload.targeted_retry_mode,
        max_extra_retries_per_item=payload.max_extra_retries_per_item,
        max_extra_cost_per_row=payload.max_extra_cost_per_row,
        max_extra_cost_per_run=payload.max_extra_cost_per_run,
    )
    if payload.use_web_enrichment:
        session_id = payload.session_id or "default"
        pdfs_dir = _TMP_UPLOADS / session_id / "pdfs"
        pdf_lookup = {f.stem: str(f) for f in pdfs_dir.glob("*.pdf")} if pdfs_dir.exists() else {}
        image_recovery_kwargs = {
            "pdf_lookup": pdf_lookup,
            "session_id": session_id,
            "enable_screenshot": payload.enrichment_mode != "fast",
            "enable_web_lookup": payload.enrichment_mode != "fast",
            "max_product_page_fetches": 3 if payload.enrichment_mode == "fast" else None,
        }
        try:
            df, image_diagnostics = recover_images_for_dataframe(df, **image_recovery_kwargs)
        except TypeError as exc:
            if "max_product_page_fetches" not in str(exc):
                raise
            image_recovery_kwargs.pop("max_product_page_fetches", None)
            df, image_diagnostics = recover_images_for_dataframe(df, **image_recovery_kwargs)
        if image_diagnostics:
            dimension_diagnostics = [
                *dimension_diagnostics,
                {
                    "report_type": "photo_discovery",
                    "summary": build_photo_discovery_report(df, image_diagnostics),
                },
                *image_diagnostics,
            ]
    df = apply_confidence_checks(df)
    return _df_response(df, errors, dimension_diagnostics)


@app.post("/intake/upload-pdf", response_model=UploadPdfResponse)
async def upload_pdf(
    project: str = Form(""),
    room: str = Form(""),
    supplier: str = Form(""),
    notes: str = Form(""),
    file: UploadFile = File(...),
    x_session_id: str | None = Header(default=None),
):
    """
    Persist an uploaded PDF to .tmp/uploads/{session_id}/pdfs/{pdf_id}.pdf
    and enqueue parsing in the background.

    The session_id is generated server-side if absent in the X-Session-Id
    header. pdf_id is SHA1[:12] of the bytes (so re-uploads dedupe).
    """
    if _upload_file_type(file) != "pdf":
        raise HTTPException(status_code=400, detail="Only PDF files can be uploaded to this endpoint.")
    session_id = x_session_id or uuid.uuid4().hex[:12]
    pdf_id, pdf_path, total_bytes = await _stream_pdf_upload(file, session_id)
    storage_prefix = os.getenv("UPLOAD_STORAGE_PREFIX", "uploads").strip().strip("/") or "uploads"
    storage_path = f"{storage_prefix}/{session_id}/pdfs/{pdf_id}.pdf"
    storage_result = upload_file_to_persistent_storage(
        pdf_path,
        storage_path,
        content_type=file.content_type or "application/pdf",
    )
    if not storage_result.ok:
        logger.warning("[upload] persistent storage failed provider=%s error=%s", storage_result.provider, storage_result.error)
        if persistent_upload_storage_enabled() and require_persistent_upload_storage():
            raise HTTPException(status_code=502, detail="PDF upload could not be persisted to configured storage.")
    job = PdfParseJob(
        job_id=uuid.uuid4().hex[:12],
        session_id=session_id,
        pdf_id=pdf_id,
        filename=file.filename or "upload.pdf",
        pdf_path=str(pdf_path),
        project=project,
        room=room,
        supplier=supplier,
        notes=notes,
    )
    job.telemetry.update({
        "uploaded_bytes": total_bytes,
        "soft_timeout_seconds": SOFT_TIMEOUT_SECONDS,
        "hard_timeout_seconds": HARD_TIMEOUT_SECONDS,
        "backend_uptime_seconds": round(time.time() - _STARTED_AT, 2),
        "cold_start_window": (time.time() - _STARTED_AT) < 30,
        "upload_storage_provider": storage_result.provider,
        "upload_storage_persisted": storage_result.ok,
        "upload_storage_path": storage_result.object_path,
    })
    job.log("uploading", "Upload stored; parse will run asynchronously", bytes=total_bytes)
    _enqueue_pdf_parse_job(job)
    return {
        "session_id": session_id,
        "pdf_id": pdf_id,
        "parse_job_id": job.job_id,
        "status": job.status,
        "stage": job.stage,
        "rows": [],
    }


@app.get("/intake/pdf-jobs/{job_id}", response_model=PdfParseJobResponse)
def pdf_parse_job_status(job_id: str) -> PdfParseJobResponse:
    job = _get_pdf_job(job_id)
    return PdfParseJobResponse(**job.public(include_rows=True, include_logs=False))


@app.get("/intake/pdf-jobs/{job_id}/logs")
def pdf_parse_job_logs(job_id: str) -> dict:
    job = _get_pdf_job(job_id)
    return job.public(include_rows=False, include_logs=True)


@app.post("/intake/pdf-jobs/{job_id}/retry", response_model=PdfParseJobResponse)
def retry_pdf_parse_job(job_id: str) -> PdfParseJobResponse:
    previous = _get_pdf_job(job_id)
    if previous.status in {"queued", "parsing"}:
        raise HTTPException(status_code=409, detail="PDF parse job is still running.")
    job = PdfParseJob(
        job_id=uuid.uuid4().hex[:12],
        session_id=previous.session_id,
        pdf_id=previous.pdf_id,
        filename=previous.filename,
        pdf_path=previous.pdf_path,
        project=previous.project,
        room=previous.room,
        supplier=previous.supplier,
        notes=previous.notes,
    )
    job.log("queued", f"Retry queued from {previous.job_id}")
    _enqueue_pdf_parse_job(job)
    return PdfParseJobResponse(**job.public(include_rows=True, include_logs=False))


@app.post("/intake/pdf-jobs/{job_id}/cancel", response_model=PdfParseJobResponse)
def cancel_pdf_parse_job(job_id: str) -> PdfParseJobResponse:
    job = _get_pdf_job(job_id)
    job.cancel_event.set()
    if job.status == "queued":
        job.status = "cancelled"
        job.stage = "cancelled"
        job.log("cancelled", "PDF parsing cancelled before worker started")
    else:
        job.log("cancelled", "Cancellation requested")
    return PdfParseJobResponse(**job.public(include_rows=True, include_logs=False))


@app.post("/intake/recover-images", response_model=IntakeResponse)
def recover_images(payload: RowsPayload) -> IntakeResponse:
    """Run a targeted image recovery pass on rows that are missing Image URL.

    Only rows without an Image URL are processed; all others are returned unchanged.
    Diagnostics (per-row status, source, recovered URL) are returned in the
    dimension_diagnostics field for now — the contract may be extended later.
    """
    session_id = payload.session_id or "default"
    pdfs_dir = _TMP_UPLOADS / session_id / "pdfs"
    pdf_lookup = {f.stem: str(f) for f in pdfs_dir.glob("*.pdf")} if pdfs_dir.exists() else {}
    df, diagnostics = recover_images_for_dataframe(
        pd.DataFrame(payload.rows),
        pdf_lookup=pdf_lookup,
        session_id=session_id,
        enable_screenshot=True,
    )
    if diagnostics:
        diagnostics = [
            {
                "report_type": "photo_discovery",
                "summary": build_photo_discovery_report(df, diagnostics),
            },
            *diagnostics,
        ]
    df = apply_confidence_checks(df)
    return _df_response(df, dimension_diagnostics=diagnostics)


@app.post("/manufacturer-override")
def manufacturer_override(payload: ManufacturerOverridePayload) -> dict:
    try:
        entry = save_manufacturer_override(payload.brand, payload.website)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "override": entry}


@app.get("/settings/preferred-websites")
def preferred_websites_list() -> dict:
    return {"entries": list_preferred_websites()}


@app.post("/settings/preferred-websites")
def preferred_websites_create(payload: PreferredWebsitePayload) -> dict:
    try:
        entry = add_preferred_website(keyword=payload.keyword, url=payload.url, notes=payload.notes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "entry": entry, "entries": list_preferred_websites()}


@app.put("/settings/preferred-websites/{entry_id}")
def preferred_websites_update(entry_id: str, payload: PreferredWebsitePayload) -> dict:
    try:
        entry = update_preferred_website(entry_id, keyword=payload.keyword, url=payload.url, notes=payload.notes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "entry": entry, "entries": list_preferred_websites()}


@app.delete("/settings/preferred-websites/{entry_id}")
def preferred_websites_delete(entry_id: str) -> dict:
    deleted = delete_preferred_website(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Preferred website was not found.")
    return {"status": "deleted", "entries": list_preferred_websites()}


@app.post("/programa/eligible")
def programa_eligible(payload: RowsPayload) -> dict:
    eligible, blocked = split_eligible_rows(payload.rows)
    return {"eligible": eligible, "blocked": blocked}


@app.post("/programa/login")
def programa_login() -> dict:
    if not _real_integrations_enabled():
        return _demo_mode_response("Programa login")
    message = open_programa_login_window()
    return {"status": "complete", "message": message}


@app.post("/vendor-call/script")
def vendor_call_script(payload: VendorCallPayload) -> dict:
    if not payload.phone_number.strip():
        raise HTTPException(status_code=400, detail="Enter a phone number before preparing a vendor call.")

    script = build_call_script(
        payload.row,
        payload.missing_fields,
        payload.phone_number,
        custom_goal=payload.custom_goal,
    )
    return {
        "status": "script_ready",
        "script": script,
        "payload": prepare_call_payload(
            payload.row,
            payload.missing_fields,
            payload.phone_number,
            custom_goal=payload.custom_goal,
        ),
    }


@app.get("/vendor-call/status")
def vendor_call_status() -> dict:
    if not _real_integrations_enabled():
        return {
            "enabled": False,
            "provider": "disabled",
            "api_key_configured": False,
            "demo_mode": True,
            "message": "Demo mode: vendor calls are disabled.",
        }
    return {
        "enabled": calls_enabled(),
        "provider": get_call_provider(),
        "api_key_configured": (
            bool(os.getenv("RETELL_API_KEY", "").strip())
            if get_call_provider() == "retell"
            else bool(os.getenv("BLAND_API_KEY", "").strip())
        ),
        "retell_agent_id": os.getenv("RETELL_AGENT_ID", "").strip(),
        "retell_phone_number_configured": bool(os.getenv("RETELL_PHONE_NUMBER", "").strip()),
        "agent_name": os.getenv("BLAND_AGENT_NAME", "Alley").strip() or "Alley",
        "persona_id": os.getenv("BLAND_PERSONA_ID", "").strip(),
        "voice": os.getenv("BLAND_VOICE_ID", "").strip() or os.getenv("BLAND_VOICE", "").strip(),
        "voice_override_enabled": os.getenv("BLAND_USE_VOICE_OVERRIDE", "false").strip().lower() == "true",
        "pathway_enabled": os.getenv("BLAND_USE_PATHWAY", "false").strip().lower() == "true",
        "pathway_id": os.getenv("BLAND_PATHWAY_ID", "").strip(),
        "self_test_phone_configured": bool(os.getenv("VENDOR_CALL_TEST_PHONE", "").strip()),
        "message": "" if calls_enabled() else "Call provider not configured yet.",
    }


@app.post("/vendor-call/start")
def vendor_call_start(payload: VendorCallPayload) -> dict:
    if not _real_integrations_enabled():
        return _demo_mode_response("vendor calls", rows=1)
    if not payload.phone_number.strip():
        raise HTTPException(status_code=400, detail="Enter a phone number before starting a vendor call.")
    result = start_vendor_call(
        payload.row,
        payload.missing_fields,
        payload.phone_number,
        custom_goal=payload.custom_goal,
    )
    return {
        "status": result.get("status"),
        "message": result.get("message"),
        "call_id": result.get("call_id"),
        "provider": result.get("provider", get_call_provider()),
        "agent_name": result.get("agent_name"),
        "provider_config": result.get("provider_config"),
        "task": result.get("task"),
        "record_path": result.get("record_path"),
    }


@app.post("/vendor-call/custom-retell-test")
def vendor_call_custom_retell_test(payload: CustomRetellTestCallPayload) -> dict:
    if not _real_integrations_enabled():
        return _demo_mode_response("custom Retell test calls", rows=1)
    if not payload.phone_number.strip():
        raise HTTPException(status_code=400, detail="Enter a phone number before starting a custom test call.")
    if not payload.phone_number.strip().startswith("+"):
        raise HTTPException(status_code=400, detail="Phone number must start with +.")
    if not payload.custom_prompt.strip():
        raise HTTPException(status_code=400, detail="Enter a call prompt / objective before starting a custom test call.")
    return start_custom_retell_test_call(payload.phone_number, payload.custom_prompt)


@app.post("/vendor-call/refresh")
def vendor_call_refresh(payload: VendorCallRefreshPayload) -> dict:
    if not payload.call_id.strip():
        raise HTTPException(status_code=400, detail="Call ID is required.")
    result = get_call_status(payload.call_id)
    transcript = result.get("transcript", "")
    extracted_specs = (
        extract_vendor_specs_from_transcript(transcript, payload.row, payload.missing_fields)
        if transcript
        else {"extracted_fields": {}, "unresolved_fields": payload.missing_fields, "notes": "No transcript available yet."}
    )
    return {
        "status": result.get("status"),
        "message": result.get("message"),
        "call_id": result.get("call_id"),
        "provider": result.get("provider", get_call_provider()),
        "provider_status": result.get("provider_status"),
        "queue_status": result.get("queue_status"),
        "completed": result.get("completed", False),
        "answered_by": result.get("answered_by"),
        "summary": result.get("summary"),
        "transcript": transcript,
        "recording_url": result.get("recording_url"),
        "call_analysis": result.get("call_analysis"),
        "extracted_values": {
            field: detail.get("value")
            for field, detail in extracted_specs.get("extracted_fields", {}).items()
            if isinstance(detail, dict)
        },
        "extracted_specs": extracted_specs,
        "confidence": 80 if extracted_specs.get("extracted_fields") else 0,
        "review_required": True,
    }


@app.get("/vendor-call/{call_id}/transcript")
def vendor_call_transcript(call_id: str) -> dict:
    return get_call_transcript(call_id)


@app.post("/vendor-call/parse-transcript")
def vendor_call_parse_transcript(payload: VendorCallTranscriptPayload) -> dict:
    extracted_specs = extract_vendor_specs_from_transcript(payload.transcript, payload.row, payload.missing_fields)
    extracted = parse_call_transcript_for_missing_values(payload.transcript, payload.missing_fields)
    return {
        "extracted_values": extracted,
        "extracted_specs": extracted_specs,
        "confidence": 80 if extracted_specs.get("extracted_fields") or extracted else 0,
        "review_required": True,
    }


def _looks_like_programa_schedule_url(value: str) -> bool:
    url = value.strip().lower()
    return url.startswith("https://app.programa.design/") and "/schedules" in url


@app.post("/programa/send")
def send_to_programa(payload: ProgramaPayload) -> dict:
    if not _real_integrations_enabled():
        return {
            **_demo_mode_response("Programa send", rows=len(payload.rows)),
            "allow_blank_fields": payload.allow_blank_fields,
            "entries": [
                {
                    "product_name": row.get("Product Name") or row.get("Name of Product") or "",
                    "status": "skipped",
                    "message": "Demo mode: this row was not sent to Programa.",
                }
                for row in payload.rows
            ],
            "blocked": [],
        }
    if not payload.schedule_url.strip():
        raise HTTPException(status_code=400, detail="Paste the Programa schedule link before sending.")
    if not _looks_like_programa_schedule_url(payload.schedule_url):
        raise HTTPException(status_code=400, detail="Paste a valid Programa schedule link before sending.")

    eligible, blocked = split_eligible_rows(payload.rows, allow_blank_fields=payload.allow_blank_fields)
    if blocked:
        return {
            "status": "blocked",
            "message": "Some rows need review before they can be sent.",
            "allow_blank_fields": payload.allow_blank_fields,
            "eligible": eligible,
            "blocked": blocked,
        }

    entries, log_path = run_programa_automation(
        rows=eligible,
        project_name=payload.project_name,
        auto_done=payload.auto_done,
        skip_navigation=False,
        schedule_url=payload.schedule_url,
        upload_product_images=payload.upload_product_images,
    )
    return {
        "status": "complete",
        "allow_blank_fields": payload.allow_blank_fields,
        "entries": entries,
        "log_path": log_path,
    }


@app.post("/export/csv")
def export_csv(payload: RowsPayload) -> Response:
    df = pd.DataFrame(payload.rows)
    today = datetime.date.today().isoformat()
    return Response(
        content=get_csv_bytes(df),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="internal_intake_not_for_programa_{today}.csv"'},
    )


@app.post("/export/programa/validate")
def export_programa_validate(payload: RowsPayload) -> dict:
    return validate_for_export(payload.rows)


@app.post("/export/programa/csv")
def export_programa_import_csv(payload: RowsPayload) -> Response:
    df = build_programa_import_dataframe(payload.rows)
    filename = generate_programa_export_filename(payload.rows, extension="csv")
    return Response(
        content=export_programa_csv(df),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/export/programa/xlsx")
def export_programa_import_xlsx(payload: RowsPayload) -> Response:
    df = build_programa_import_dataframe(payload.rows)
    filename = generate_programa_export_filename(payload.rows, extension="xlsx")
    return Response(
        content=export_programa_xlsx(df),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/export/programa/xlsx-with-images")
def export_programa_import_xlsx_with_images(payload: RowsPayload) -> Response:
    """XLSX with same-row embedded product images for Programa's custom importer."""
    filename = generate_programa_export_filename(payload.rows, extension="xlsx")
    xlsx_bytes = export_programa_xlsx_with_images(
        payload.rows,
        session_id=payload.session_id,
        include_low_confidence_images=payload.include_low_confidence_images,
    )
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/export/programa/zip")
def export_programa_import_zip(payload: RowsPayload) -> Response:
    """ZIP archive: programa_import.csv + images/ folder + manifest.csv."""
    filename = generate_programa_export_filename(payload.rows, extension="zip", kind="zip")
    zip_bytes = export_programa_zip(
        payload.rows,
        include_low_confidence_images=payload.include_low_confidence_images,
        session_id=payload.session_id,
    )
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/export/programa/debug-csv")
def export_programa_import_debug_csv(payload: RowsPayload) -> Response:
    df = build_programa_debug_dataframe(payload.rows)
    return Response(
        content=export_programa_csv(df),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="programa-import-debug.csv"'},
    )
