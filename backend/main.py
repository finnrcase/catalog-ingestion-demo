from __future__ import annotations

import datetime
import hashlib
import io
import os
import sys
import uuid
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

_TMP_UPLOADS = ROOT / ".tmp" / "uploads"

from src.ai_extraction import extract_products_from_pdf_with_ai
from src.confidence import apply_confidence_checks
from src.document_parser import parse_pdf_rows
from src.eligibility import split_eligible_rows
from src.export import get_csv_bytes
from src.intake import build_intake_dataframe, create_pdf_rows, create_url_rows
from src.intake_schema import CATEGORIES, STATUSES
from src.image_uploader import is_public_https_image_url, upload_image
from src.manufacturer_domains import save_manufacturer_override
from src.notes import remove_notes_row_prefix
from src.image_recovery import cleanup_old_sessions
from src.image_recovery import build_photo_discovery_report
from src.pdf_product_workflow import (
    enrich_pdf_rows_with_official_product_urls,
    normalize_pdf_product_rows,
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


class RowsPayload(BaseModel):
    rows: list[dict] = Field(default_factory=list)
    enrichment_mode: str = "standard"
    force_refresh: bool = False
    use_web_enrichment: bool = True
    include_low_confidence_images: bool = False
    session_id: str | None = None


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
    rows: list[dict] = Field(default_factory=list)


class ImageUploadResponse(BaseModel):
    secure_url: str


class ManufacturerOverridePayload(BaseModel):
    brand: str = ""
    website: str = ""


app = FastAPI(
    title="SCH DesignOps Intake API",
    version="0.1.0",
    description="Extraction, enrichment, validation, export, and Programa automation API.",
)

_origins = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    return {"status": "ok"}


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
    errors: list[str] = []

    for upload in files:
        content = await upload.read()
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

    if not url_rows and not pdf_rows:
        raise HTTPException(status_code=400, detail="Upload a PDF or paste at least one URL.")

    pdf_rows, lookup_errors = enrich_pdf_rows_with_official_product_urls(pdf_rows)
    errors.extend(lookup_errors)

    df = build_intake_dataframe(url_rows, pdf_rows)
    df = apply_confidence_checks(df)
    return _df_response(df, errors)


@app.post("/api/upload-image", response_model=ImageUploadResponse)
async def upload_image_endpoint(file: UploadFile = File(...)) -> ImageUploadResponse:
    content_type = (file.content_type or "").lower()
    filename = (file.filename or "").lower()
    if not (content_type.startswith("image/") or filename.endswith((".jpg", ".jpeg", ".png", ".webp"))):
        raise HTTPException(status_code=400, detail="Only image files can be uploaded.")

    secure_url = upload_image(file.file)
    if not secure_url or not is_public_https_image_url(secure_url):
        raise HTTPException(status_code=502, detail="Cloudinary upload failed or did not return a secure HTTPS URL.")
    return ImageUploadResponse(secure_url=secure_url)


@app.post("/upload-image", response_model=ImageUploadResponse)
async def upload_image_endpoint_alias(file: UploadFile = File(...)) -> ImageUploadResponse:
    return await upload_image_endpoint(file)


@app.post("/intake/validate", response_model=IntakeResponse)
def validate_intake(payload: RowsPayload) -> IntakeResponse:
    df = apply_confidence_checks(pd.DataFrame(payload.rows))
    return _df_response(df)


@app.post("/intake/enrich", response_model=IntakeResponse)
def enrich_intake(payload: RowsPayload) -> IntakeResponse:
    df, errors, dimension_diagnostics = enrich_dataframe(
        pd.DataFrame(payload.rows),
        enrichment_mode=payload.enrichment_mode,
        force_refresh=payload.force_refresh,
        use_web_enrichment=payload.use_web_enrichment,
    )
    if payload.use_web_enrichment:
        session_id = payload.session_id or "default"
        pdfs_dir = _TMP_UPLOADS / session_id / "pdfs"
        pdf_lookup = {f.stem: str(f) for f in pdfs_dir.glob("*.pdf")} if pdfs_dir.exists() else {}
        df, image_diagnostics = recover_images_for_dataframe(
            df,
            pdf_lookup=pdf_lookup,
            session_id=session_id,
            enable_screenshot=True,
        )
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
    file: UploadFile = File(...),
    x_session_id: str | None = Header(default=None),
):
    """
    Persist an uploaded PDF to .tmp/uploads/{session_id}/pdfs/{pdf_id}.pdf
    and return parsed rows.

    The session_id is generated server-side if absent in the X-Session-Id
    header. pdf_id is SHA1[:12] of the bytes (so re-uploads dedupe).
    """
    raw = await file.read()
    session_id = x_session_id or uuid.uuid4().hex[:12]
    pdf_id = hashlib.sha1(raw).hexdigest()[:12]

    pdfs_dir = _TMP_UPLOADS / session_id / "pdfs"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdfs_dir / f"{pdf_id}.pdf"
    if not pdf_path.exists():
        pdf_path.write_bytes(raw)

    rows = parse_pdf_rows(UploadedPDF(file.filename or "upload.pdf", raw))
    return {"session_id": session_id, "pdf_id": pdf_id, "rows": rows}


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
    today = datetime.date.today().isoformat()
    return Response(
        content=export_programa_csv(df),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="programa_import_{today}.csv"'},
    )


@app.post("/export/programa/xlsx")
def export_programa_import_xlsx(payload: RowsPayload) -> Response:
    df = build_programa_import_dataframe(payload.rows)
    today = datetime.date.today().isoformat()
    return Response(
        content=export_programa_xlsx(df),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="programa_import_{today}.xlsx"'},
    )


@app.post("/export/programa/xlsx-with-images")
def export_programa_import_xlsx_with_images(payload: RowsPayload) -> Response:
    """XLSX with same-row embedded product images for Programa's custom importer."""
    today = datetime.date.today().isoformat()
    xlsx_bytes = export_programa_xlsx_with_images(
        payload.rows,
        session_id=payload.session_id,
        include_low_confidence_images=payload.include_low_confidence_images,
    )
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="programa_import_with_images_{today}.xlsx"'},
    )


@app.post("/export/programa/zip")
def export_programa_import_zip(payload: RowsPayload) -> Response:
    """ZIP archive: programa_import.csv + images/ folder + manifest.csv."""
    today = datetime.date.today().isoformat()
    zip_bytes = export_programa_zip(
        payload.rows,
        include_low_confidence_images=payload.include_low_confidence_images,
        session_id=payload.session_id,
    )
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="programa_export_{today}.zip"'},
    )


@app.post("/export/programa/debug-csv")
def export_programa_import_debug_csv(payload: RowsPayload) -> Response:
    df = build_programa_debug_dataframe(payload.rows)
    return Response(
        content=export_programa_csv(df),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="programa-import-debug.csv"'},
    )
