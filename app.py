import datetime
import logging
import os
import re
import hashlib
import time
import uuid
from pathlib import Path

_SESSION_TMP_ROOT = Path(__file__).resolve().parent / ".tmp" / "uploads"

import streamlit as st
import pandas as pd

_logger = logging.getLogger("sch_intake")

from src.styling import inject_css, section_label, PAGE_TITLE_HTML
from src.intake_schema import CATEGORIES, INTERNAL_IMAGE_COLUMNS, STATUSES
from src.intake import (
    COLUMNS,
    build_intake_dataframe,
    create_pdf_rows,
    create_url_rows,
)
from src.export import get_csv_bytes
from src.photo_inventory import (
    MISSING_IMAGE_STATUS,
    analyze_photo_with_ai,
    build_photo_only_bulk_product_names,
    create_photo_only_bulk_rows,
    create_photo_inventory_row,
    is_public_https_image_url,
    upload_image_to_cloudinary,
)
from src.programa_export import (
    build_programa_debug_dataframe,
    build_programa_import_dataframe,
    export_programa_csv,
    export_programa_xlsx,
    export_programa_zip,
    validate_for_export,
)
from src.programa_automation import (
    open_programa_login_window,
    run_programa_automation,
    run_programa_debug_single_row,
    run_programa_field_diagnostic,
)
from src.confidence import apply_confidence_checks
from src.ai_extraction import extract_products_from_pdf_with_ai
from src.document_parser import parse_pdf_rows
from src.category_ai import suggest_categories_batch
from src.notes import remove_notes_row_prefix
from src.product_enrichment import enrich_dataframe, recover_images_for_dataframe, has_complete_3d_dimensions
from src.brave_search import BRAVE_API_KEY as _BRAVE_API_KEY
from src.manufacturer_domains import save_manufacturer_override
from src.enrichment_debug import debug_enrich_dataframe, save_debug_report
from src.vendor_call_agent import (
    build_minimal_call_task,
    build_call_goal,
    build_call_script,
    calls_enabled,
    extract_vendor_specs_from_transcript,
    get_call_status,
    get_call_provider,
    list_bland_personas,
    make_json_safe,
    start_bland_minimal_call,
    start_custom_retell_test_call,
    start_vendor_call,
    test_bland_connection,
    vendor_call_mock_enabled,
)

LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "logo.png")
BULK_PHOTO_DIR = Path("temp/product_images/bulk_uploads")
ACCEPTED_PHOTO_TYPES = ["jpg", "jpeg", "png", "webp", "heic", "heif"]
DISPLAY_COLUMNS = [
    "Include",
    "Confidence Score",
    "Review Required",
    "Suggested Action",
    "Project",
    "Room",
    "Product Name",
    "Brand",
    "Dimensions",
    "Finish / Color",
    "Color",
    "Material",
    "Model/SKU",
    "Product Category",
    "Quantity",
    "Price",
    "Supplier",
    "Product URL",
    "Notes",
    "Source Type",
    "Import Type",
    "Status",
    "Missing Fields",
    "AI Category Confidence",
    "Category Source",
    "Image Filename",
    "Image Upload Status",
    "Image URL",
]


def _safe_uploaded_image_name(filename: str) -> str:
    stem = Path(filename or "product_photo").stem
    suffix = Path(filename or "").suffix.lower()
    if suffix.lstrip(".") not in ACCEPTED_PHOTO_TYPES:
        suffix = ".jpg"
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "product_photo"
    return f"{safe_stem[:90]}{suffix}"


def _save_bulk_photo_upload(uploaded_file) -> dict:
    """Persist one uploaded image for later Programa upload without using it as product data."""
    BULK_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    original_name = str(getattr(uploaded_file, "name", "") or "product_photo")
    safe_name = _safe_uploaded_image_name(original_name)
    digest = hashlib.sha1(f"{original_name}:{time.time_ns()}".encode("utf-8")).hexdigest()[:10]
    path = BULK_PHOTO_DIR / f"{Path(safe_name).stem}_{digest}{Path(safe_name).suffix}"
    with open(path, "wb") as fh:
        fh.write(uploaded_file.getbuffer())
    return {
        "image_filename": original_name,
        "local_image_path": str(path),
        "image_upload_status": "Ready",
    }

# Phase 1 image recovery: prune session dirs older than 24h on app boot.
@st.cache_resource
def _run_phase1_startup_cleanup() -> None:
    try:
        from src.image_recovery import cleanup_old_sessions as _c
    except ImportError:
        return  # image_recovery not available; skip silently
    try:
        _c(max_age_hours=24)
    except Exception as exc:  # pragma: no cover
        # Cleanup failures are non-fatal but should be visible in logs.
        import logging
        logging.getLogger(__name__).warning(
            "[IMAGE RECOVERY] startup cleanup failed: %s", exc
        )

_run_phase1_startup_cleanup()

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SCH DesignOps Intake",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="collapsed",
)

inject_css()


# ── Quality metric badge HTML ──────────────────────────────────────────────────
def _qm(label: str, value, color: str) -> str:
    return (
        f'<div style="background:{color}15;border:1px solid {color}30;'
        f'border-radius:6px;padding:0.65rem 0.5rem;text-align:center;">'
        f'<div style="font-family:\'Cormorant Garamond\',serif;font-size:1.7rem;'
        f'font-weight:600;color:{color};line-height:1;">{value}</div>'
        f'<div style="font-size:0.57rem;letter-spacing:0.15em;text-transform:uppercase;'
        f'color:#9A8E80;margin-top:5px;">{label}</div>'
        f'</div>'
    )


# ── Header ─────────────────────────────────────────────────────────────────────
logo_col, spacer_col, version_col = st.columns([3, 5, 2])

with logo_col:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=190)
    else:
        st.markdown(
            '<div class="sch-fallback-logo">SCH'
            '<div class="sch-fallback-sub">Saffron Case Homes</div></div>',
            unsafe_allow_html=True,
        )

with version_col:
    st.markdown(
        '<div style="text-align:right; padding-top:1.1rem;">'
        '<span class="sch-version">v0.5 &nbsp;·&nbsp; Internal</span>'
        "</div>",
        unsafe_allow_html=True,
    )

st.markdown(
    "<hr style='border-top:1.5px solid #D8CFC4; margin:0.5rem 0 1.75rem 0;'>",
    unsafe_allow_html=True,
)
st.markdown(PAGE_TITLE_HTML, unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────────
if "intake_df" not in st.session_state:
    st.session_state.intake_df = None
if "automation_results" not in st.session_state:
    st.session_state.automation_results = None
if "ai_errors" not in st.session_state:
    st.session_state.ai_errors = []
# nav_failed / nav_pending_rows: set when project auto-navigation fails
if "nav_failed" not in st.session_state:
    st.session_state.nav_failed = False
if "nav_pending_rows" not in st.session_state:
    st.session_state.nav_pending_rows = []
if "cat_ai_error" not in st.session_state:
    st.session_state.cat_ai_error = None
if "pending_enrichment" not in st.session_state:
    st.session_state.pending_enrichment = False
if "enrichment_errors" not in st.session_state:
    st.session_state.enrichment_errors = []
if "use_web_enrichment" not in st.session_state:
    st.session_state.use_web_enrichment = True
if "manual_image_uploads" not in st.session_state:
    st.session_state.manual_image_uploads = {}  # {row_idx: jpeg_bytes}
if "vendor_call_panel" not in st.session_state:
    st.session_state.vendor_call_panel = None
if "vendor_call_results" not in st.session_state:
    st.session_state.vendor_call_results = {}
if "vendor_call_metadata" not in st.session_state:
    st.session_state.vendor_call_metadata = {}
if "vendor_call_extractions" not in st.session_state:
    st.session_state.vendor_call_extractions = {}
if "custom_retell_test_result" not in st.session_state:
    st.session_state.custom_retell_test_result = None

# ── Phase 1 image recovery: session helpers ────────────────────────────────────


def _ensure_session_id() -> str:
    if "session_id" not in st.session_state:
        st.session_state.session_id = uuid.uuid4().hex[:12]
    return st.session_state.session_id


def _save_uploaded_pdf(raw_bytes: bytes, filename: str) -> tuple[str, str]:
    """Save uploaded PDF to .tmp/uploads/{session_id}/pdfs/{pdf_id}.pdf.
    Returns (pdf_id, pdf_path)."""
    sid = _ensure_session_id()
    pdf_id = hashlib.sha1(raw_bytes).hexdigest()[:12]
    pdfs_dir = _SESSION_TMP_ROOT / sid / "pdfs"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdfs_dir / f"{pdf_id}.pdf"
    if not pdf_path.exists():
        pdf_path.write_bytes(raw_bytes)
    if "uploaded_pdfs" not in st.session_state:
        st.session_state.uploaded_pdfs = {}
    st.session_state.uploaded_pdfs[pdf_id] = raw_bytes
    return pdf_id, str(pdf_path)


def _build_pdf_lookup() -> dict[str, str]:
    sid = _ensure_session_id()
    pdfs_dir = _SESSION_TMP_ROOT / sid / "pdfs"
    lookup: dict[str, str] = {}
    if pdfs_dir.exists():
        for f in pdfs_dir.glob("*.pdf"):
            lookup[f.stem] = str(f)
    # Fallback: write any session-state bytes back out if disk file missing.
    for pdf_id, raw in (st.session_state.get("uploaded_pdfs") or {}).items():
        if pdf_id not in lookup:
            pdfs_dir.mkdir(parents=True, exist_ok=True)
            target = pdfs_dir / f"{pdf_id}.pdf"
            target.write_bytes(raw)
            lookup[pdf_id] = str(target)
    return lookup


# ── Programa Destination ───────────────────────────────────────────────────────
with st.container(border=True):
    section_label("Programa Destination")
    st.caption(
        "Open the target schedule in Programa, copy the URL from your browser, and paste it below. "
        "Items will be entered directly into that schedule."
    )
    dest_col1, dest_col2 = st.columns([3, 2])

    with dest_col1:
        schedule_url = st.text_input(
            "Programa Schedule Link",
            placeholder="https://app.programa.design/schedules2/schedules/…",
            help="Open the desired Programa schedule, copy the URL, and paste it here.",
        )
        if schedule_url.strip() and "app.programa.design/schedule" not in schedule_url:
            st.warning("URL doesn't look like a Programa schedule link — double-check it.", icon="⚠️")
        selected_project = st.text_input(
            "Project Name (optional — for logging only)",
            placeholder="e.g. 1 Lily Pond Ln",
        )
    with dest_col2:
        room_options = [
            "", "Living Room", "Master Bedroom", "Guest Bedroom", "Dining Room",
            "Kitchen", "Bathroom", "Office / Study", "Entryway / Foyer",
            "Outdoor / Terrace", "Other",
        ]
        room = st.selectbox("Default Room / Location", options=room_options)

st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

# ── Custom Retell Test Call ───────────────────────────────────────────────────
with st.container(border=True):
    section_label("Custom Retell Test Call")
    st.caption("Use this for demo calls and Retell debugging. This panel only uses Retell.")
    test_col1, test_col2 = st.columns([1.25, 2.75])
    with test_col1:
        custom_test_phone = st.text_input(
            "Phone number to call",
            placeholder="+18005551234",
            key="custom_retell_test_phone",
        )
    with test_col2:
        custom_test_prompt = st.text_area(
            "Call prompt / objective",
            placeholder=(
                "Call this vendor and ask for the dimensions and current price "
                "of Samsung refrigerator model RF23DB9900QD."
            ),
            key="custom_retell_test_prompt",
            height=94,
        )

    missing_retell_config = []
    if not os.getenv("RETELL_PHONE_NUMBER", "").strip():
        missing_retell_config.append("RETELL_PHONE_NUMBER")
    if not os.getenv("RETELL_AGENT_ID", "").strip():
        missing_retell_config.append("RETELL_AGENT_ID")

    start_custom_test = st.button(
        "Start Custom AI Call",
        type="secondary",
        use_container_width=False,
        key="custom_retell_test_start",
    )
    if start_custom_test:
        if not custom_test_phone.strip():
            st.error("Enter a phone number before starting the test call.")
        elif not custom_test_phone.strip().startswith("+"):
            st.error("Phone number must start with +.")
        elif not custom_test_prompt.strip():
            st.error("Enter a call prompt / objective before starting the test call.")
        elif missing_retell_config:
            st.error(f"Retell config incomplete: missing {', '.join(missing_retell_config)}.")
        else:
            with st.spinner("Starting custom Retell call..."):
                st.session_state.custom_retell_test_result = start_custom_retell_test_call(
                    custom_test_phone,
                    custom_test_prompt,
                )

    custom_result = st.session_state.custom_retell_test_result
    if custom_result:
        if custom_result.get("status") in {"call_started", "registered"}:
            st.success("Custom Retell call started.")
            result_cols = st.columns(5)
            result_cols[0].caption("Call ID")
            result_cols[0].write(custom_result.get("call_id") or "Pending")
            result_cols[1].caption("To number")
            result_cols[1].write(custom_result.get("to_number") or custom_test_phone)
            result_cols[2].caption("From number")
            result_cols[2].write(custom_result.get("from_number") or os.getenv("RETELL_PHONE_NUMBER", "").strip())
            result_cols[3].caption("Agent ID used")
            result_cols[3].write(custom_result.get("agent_id") or os.getenv("RETELL_AGENT_ID", "").strip())
            result_cols[4].caption("Provider")
            result_cols[4].write("Retell")
        else:
            st.warning(custom_result.get("message") or "Retell custom test call failed.", icon="⚠️")
            if custom_result.get("missing_config"):
                st.caption("Missing Retell configuration")
                st.json(make_json_safe(custom_result.get("missing_config", [])))
        with st.expander("Custom test diagnostics"):
            debug_info = custom_result.get("debug", {})
            if debug_info:
                st.caption("Endpoint")
                st.code(str(debug_info.get("endpoint", "")))
                st.caption("Sanitized headers")
                st.json(make_json_safe(debug_info.get("headers", {})))
                st.caption("Request body")
                st.json(make_json_safe(debug_info.get("request_body", {})))
                st.caption("Response status")
                st.write(debug_info.get("response_status_code", "No HTTP status returned"))
                response_text = str(debug_info.get("response_text") or "")
                if response_text:
                    st.caption("Raw response")
                    st.code(response_text, language="json")
                st.caption("Parsed response")
                st.json(make_json_safe(debug_info.get("response_body", {})))
            else:
                st.json(make_json_safe(custom_result))

st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

# ── Intake sources ─────────────────────────────────────────────────────────────
left_col, right_col = st.columns(2, gap="medium")

with left_col:
    with st.container(border=True):
        section_label("PDF Upload")
        uploaded_files = st.file_uploader(
            "Upload PDFs",
            type=["pdf"],
            accept_multiple_files=True,
            label_visibility="collapsed",
        )
        if uploaded_files:
            count = len(uploaded_files)
            st.markdown(
                f"<small><strong>{count} file{'s' if count != 1 else ''} ready</strong></small>",
                unsafe_allow_html=True,
            )
            for f in uploaded_files:
                st.caption(f"◆  {f.name}  ·  {round(f.size / 1024, 1)} KB")

        st.markdown("<div style='height:0.6rem'></div>", unsafe_allow_html=True)
        use_ai_pdf = st.checkbox(
            "Use AI to interpret uploaded PDFs",
            value=True,
            help=(
                "AI will review the uploaded file, extract product rows, "
                "suggest titles, descriptions, and categories, "
                "and flag uncertain fields for review."
            ),
        )
        if use_ai_pdf:
            st.caption(
                "AI will review the uploaded file, extract product rows, "
                "suggest titles / descriptions / categories, and flag "
                "uncertain fields for review."
            )

with right_col:
    with st.container(border=True):
        url_tab, photo_tab, bulk_photo_tab = st.tabs([
            "Product URLs",
            "Photo Inventory Upload",
            "Photo-only Bulk Import",
        ])
        with url_tab:
            section_label("Product URLs")
            url_input = st.text_area(
                "URLs",
                height=160,
                placeholder="Paste one URL per line:\n\nhttps://www.rh.com/product/...\nhttps://www.article.com/...",
                label_visibility="collapsed",
            )
            if url_input.strip():
                valid_urls = [u.strip() for u in url_input.splitlines() if u.strip()]
                st.caption(f"{len(valid_urls)} URL{'s' if len(valid_urls) != 1 else ''} detected")

        with photo_tab:
            section_label("Photo Inventory Upload")
            st.caption(
                "Upload handmade or one-off item photos. AI drafts one Programa row per image, "
                "then Cloudinary stores the public Image URL."
            )
            bulk_photo_files = st.file_uploader(
                "Upload inventory photos",
                type=ACCEPTED_PHOTO_TYPES,
                accept_multiple_files=True,
                key="bulk_photo_upload_files",
                label_visibility="collapsed",
            )
            if bulk_photo_files:
                total_size = sum(getattr(f, "size", 0) or 0 for f in bulk_photo_files)
                st.markdown(
                    f"<small><strong>{len(bulk_photo_files)} image{'s' if len(bulk_photo_files) != 1 else ''} ready</strong>"
                    f" · {round(total_size / (1024 * 1024), 2)} MB</small>",
                    unsafe_allow_html=True,
                )
                preview_files = bulk_photo_files[:6]
                preview_cols = st.columns(min(3, len(preview_files)))
                for idx, f in enumerate(preview_files):
                    with preview_cols[idx % len(preview_cols)]:
                        if Path(f.name).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                            st.image(f, caption=f.name, use_container_width=True)
                        else:
                            st.caption(f"◆ {f.name}")
                remaining = len(bulk_photo_files) - len(preview_files)
                if remaining > 0:
                    st.caption(f"+ {remaining} more image{'s' if remaining != 1 else ''}")

            create_photo_products = st.button(
                "AI-Generate Draft Product Rows",
                type="secondary",
                use_container_width=True,
                key="create_bulk_photo_products",
                disabled=not bool(bulk_photo_files),
            )
            if create_photo_products:
                if not bulk_photo_files:
                    st.warning("Upload at least one product photo first.", icon="⚠️")
                else:
                    draft_rows = []
                    progress = st.progress(0, text="Saving product photos…")
                    for idx, photo_file in enumerate(bulk_photo_files, start=1):
                        try:
                            photo_meta = _save_bulk_photo_upload(photo_file)
                            ai_fields, ai_error = analyze_photo_with_ai(
                                photo_meta["local_image_path"],
                                photo_meta["image_filename"],
                            )
                            image_url, upload_error = upload_image_to_cloudinary(photo_meta["local_image_path"])
                            status_bits = [msg for msg in [ai_error, upload_error] if msg]
                            if image_url:
                                photo_meta["image_upload_status"] = "Uploaded"
                            elif upload_error:
                                photo_meta["image_upload_status"] = MISSING_IMAGE_STATUS
                            draft_rows.append(
                                create_photo_inventory_row(
                                    photo_meta,
                                    selected_project,
                                    room,
                                    ai_fields=ai_fields,
                                    image_url=image_url,
                                    status_note=" ".join(status_bits),
                                )
                            )
                        except Exception as exc:
                            st.warning(f"Could not save {photo_file.name}: {exc}", icon="⚠️")
                        progress.progress(idx / len(bulk_photo_files), text=f"Saved {idx} of {len(bulk_photo_files)} photos")
                    progress.empty()
                    if draft_rows:
                        photo_df = pd.DataFrame(draft_rows)
                        if st.session_state.intake_df is not None and not st.session_state.intake_df.empty:
                            combined = pd.concat([st.session_state.intake_df, photo_df], ignore_index=True)
                        else:
                            combined = photo_df
                        st.session_state.intake_df = apply_confidence_checks(combined)
                        st.session_state.automation_results = None
                        st.session_state.pending_enrichment = False
                        st.success(
                            f"Created {len(draft_rows)} photo inventory draft row{'s' if len(draft_rows) != 1 else ''}."
                        )

        with bulk_photo_tab:
            section_label("Photo-only Bulk Import")
            st.caption("Fast path: upload images, host them, and create one Programa row per photo without AI.")
            bulk_only_files = st.file_uploader(
                "Upload photo-only inventory images",
                type=ACCEPTED_PHOTO_TYPES,
                accept_multiple_files=True,
                key="photo_only_bulk_files",
                label_visibility="collapsed",
            )
            bulk_section_options = ["Decor", "Art", "Furniture", "Accessories", "General", "Custom"]
            bulk_section_choice = st.selectbox(
                "Default Section",
                options=bulk_section_options,
                key="photo_only_bulk_section_choice",
            )
            bulk_section_custom = ""
            if bulk_section_choice == "Custom":
                bulk_section_custom = st.text_input(
                    "Custom Section",
                    key="photo_only_bulk_section_custom",
                    placeholder="e.g. Vintage Objects",
                )
            bulk_section = bulk_section_custom.strip() if bulk_section_choice == "Custom" else bulk_section_choice
            bulk_naming_mode = st.selectbox(
                "Naming Mode",
                options=["Filename", "Generated names"],
                key="photo_only_bulk_naming_mode",
            )
            bulk_default_product_name_raw = st.text_input(
                "Default Product Name (applies to all uploaded images)",
                key="photo_only_bulk_default_product_name",
            )
            bulk_default_product_name = bulk_default_product_name_raw.strip()
            bulk_append_sequence = st.checkbox(
                "Append sequence number to name (e.g., Lamp 001, Lamp 002)",
                value=True,
                key="photo_only_bulk_append_sequence",
            )
            bulk_use_ai = st.checkbox(
                "Use AI to describe photos",
                value=False,
                key="photo_only_bulk_use_ai",
                help="Optional slower pass. Default off for fast bulk imports.",
            )

            if bulk_only_files:
                total_size = sum(getattr(f, "size", 0) or 0 for f in bulk_only_files)
                st.caption(
                    f"{len(bulk_only_files)} image{'s' if len(bulk_only_files) != 1 else ''} ready "
                    f"- {round(total_size / (1024 * 1024), 2)} MB"
                )
                preview_files = bulk_only_files[:6]
                preview_cols = st.columns(min(3, len(preview_files)))
                for idx, f in enumerate(preview_files):
                    with preview_cols[idx % len(preview_cols)]:
                        if Path(f.name).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                            st.image(f, caption=f.name, use_container_width=True)
                        else:
                            st.caption(f"• {f.name}")
                if len(bulk_only_files) > len(preview_files):
                    st.caption(f"+ {len(bulk_only_files) - len(preview_files)} more")
                preview_photos = [{"image_filename": str(getattr(f, "name", "") or "")} for f in bulk_only_files]
                preview_names = build_photo_only_bulk_product_names(
                    preview_photos,
                    naming_mode=bulk_naming_mode,
                    default_product_name=bulk_default_product_name,
                    append_sequence=bulk_append_sequence,
                )
                if preview_names:
                    st.caption("Product name preview: " + ", ".join(preview_names[:5]))

            create_bulk_only_rows = st.button(
                "Upload and Create Rows",
                type="secondary",
                use_container_width=True,
                key="create_photo_only_bulk_rows",
                disabled=not bool(bulk_only_files) or not bool(bulk_section.strip()),
            )
            if create_bulk_only_rows:
                if not bulk_only_files:
                    st.warning("Upload at least one photo first.", icon="⚠️")
                elif not bulk_section.strip():
                    st.warning("Choose or enter a default section first.", icon="⚠️")
                else:
                    saved_photos = []
                    image_urls = []
                    upload_statuses = []
                    progress = st.progress(0, text="Uploading photos to public hosting...")
                    for idx, photo_file in enumerate(bulk_only_files, start=1):
                        try:
                            photo_meta = _save_bulk_photo_upload(photo_file)
                            image_url, upload_error = upload_image_to_cloudinary(photo_meta["local_image_path"])
                            saved_photos.append(photo_meta)
                            image_urls.append(image_url)
                            upload_statuses.append("Uploaded" if image_url else MISSING_IMAGE_STATUS)
                            if upload_error:
                                st.warning(upload_error, icon="⚠️")
                        except Exception as exc:
                            st.warning(f"Could not process {photo_file.name}: {exc}", icon="⚠️")
                        progress.progress(
                            idx / len(bulk_only_files),
                            text=f"Uploaded {idx} of {len(bulk_only_files)} photos",
                        )
                    progress.empty()

                    if saved_photos:
                        photo_rows = create_photo_only_bulk_rows(
                            saved_photos,
                            project=selected_project,
                            room=room,
                            section=bulk_section,
                            naming_mode=bulk_naming_mode,
                            image_urls=image_urls,
                            upload_statuses=upload_statuses,
                            default_product_name=bulk_default_product_name,
                            append_sequence=bulk_append_sequence,
                        )
                        if bulk_use_ai:
                            for row, photo_meta in zip(photo_rows, saved_photos):
                                local_path = str(photo_meta.get("local_image_path", "") or "")
                                filename = str(photo_meta.get("image_filename", "") or "")
                                ai_fields, ai_error = analyze_photo_with_ai(local_path, filename)
                                description = str(ai_fields.get("description", "") or "").strip()
                                if description:
                                    row["Notes"] = f"{row['Notes']} {description}"
                                if ai_error:
                                    row["Notes"] = f"{row['Notes']} {ai_error}"
                        photo_df = pd.DataFrame(photo_rows)
                        preview_cols = ["Product Category", "Product Name", "Image URL", "Quantity", "Notes"]
                        st.dataframe(
                            photo_df[[c for c in preview_cols if c in photo_df.columns]],
                            use_container_width=True,
                            hide_index=True,
                        )
                        if st.session_state.intake_df is not None and not st.session_state.intake_df.empty:
                            combined = pd.concat([st.session_state.intake_df, photo_df], ignore_index=True)
                        else:
                            combined = photo_df
                        st.session_state.intake_df = apply_confidence_checks(combined)
                        st.session_state.automation_results = None
                        st.session_state.pending_enrichment = False
                        st.success(
                            f"Created {len(photo_rows)} photo-only bulk import row{'s' if len(photo_rows) != 1 else ''}."
                        )

st.markdown("<div style='height:1.25rem'></div>", unsafe_allow_html=True)

with st.container(border=True):
    _enrich_col, _refresh_col = st.columns(2)
    with _enrich_col:
        st.checkbox(
            "Use web search to find missing product details (recommended)",
            key="use_web_enrichment",
            help=(
                "When enabled, the system searches manufacturer websites to fill missing data. "
                "Turn off to only use uploaded documents and manual input."
            ),
        )
        st.caption(
            "When enabled, searches manufacturer sites to fill missing data."
        )
    with _refresh_col:
        st.checkbox(
            "Force refresh cached product data",
            key="force_refresh_enrichment",
            value=False,
            help=(
                "Ignore previously cached enrichment results and re-fetch all fields. "
                "Use after improving extraction logic or to recover missing images."
            ),
        )
        st.caption(
            "Re-fetches all fields even if previously cached. Slower but recovers missed data."
        )
    st.divider()
    section_label("Manufacturer Override")
    st.caption(
        "Persist a brand-to-domain mapping for enrichment. User-defined domains are used before built-in tables, "
        "discovered cache, and Brave search."
    )
    override_col1, override_col2, override_col3 = st.columns([2, 3, 2])
    with override_col1:
        override_brand = st.text_input("Brand", key="manufacturer_override_brand", placeholder="Scotsman")
    with override_col2:
        override_website = st.text_input(
            "Manufacturer Website",
            key="manufacturer_override_website",
            placeholder="scotsman-ice.com",
        )
    with override_col3:
        st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        save_override = st.button(
            "Save for Future Use",
            type="secondary",
            use_container_width=True,
            disabled=not bool(override_brand.strip()) or not bool(override_website.strip()),
        )
    st.caption("Saved overrides persist across runs and are used immediately by enrichment for matching brands.")
    if save_override:
        try:
            saved_override = save_manufacturer_override(override_brand, override_website)
            st.success(
                f"Saved {saved_override['brand']} → {saved_override['domain']}. "
                "Future enrichment will search this manufacturer website first."
            )
        except ValueError as exc:
            st.warning(str(exc), icon="⚠️")

# ── Intake action buttons (PDF + URL path) ─────────────────────────────────────
gen_col, _ = st.columns([3, 7])
with gen_col:
    generate = st.button("Generate Intake Table", type="primary", use_container_width=True)

if generate:
    raw_urls = [u.strip() for u in (url_input or "").splitlines() if u.strip()]
    url_rows = create_url_rows(raw_urls, selected_project, room, "", "")
    st.session_state.ai_errors = []

    # Phase 1 image recovery: persist PDF bytes for both AI and standard-parser
    # uploads so bytes survive regardless of which parse path is taken.
    # Bytes are persisted for both AI and standard-parser uploads. AI-extracted
    # rows do not carry _source_pdf_id (Phase 2 work), so PDF crop will not
    # fire for them; but the bytes survive for future recovery passes.
    for pdf_file in (uploaded_files or []):
        try:
            _pdf_raw = pdf_file.read()
            pdf_file.seek(0)
            _save_uploaded_pdf(_pdf_raw, getattr(pdf_file, "name", "upload.pdf"))
        except Exception:
            pass  # non-fatal; parse path will handle missing bytes gracefully

    if use_ai_pdf and uploaded_files:
        # ── AI extraction path ─────────────────────────────────────────────────
        with st.spinner(
            f"AI is reading {len(uploaded_files)} PDF{'s' if len(uploaded_files) != 1 else ''}…"
        ):
            ai_dfs = []
            ai_errors = []

            for pdf_file in uploaded_files:
                df_ai, error = extract_products_from_pdf_with_ai(
                    pdf_file, selected_project, room, ""
                )
                if error:
                    ai_errors.append(f"**{pdf_file.name}:** {error}")
                    fallback = create_pdf_rows(
                        [pdf_file], selected_project, room, "", ""
                    )
                    ai_dfs.append(pd.DataFrame(fallback))
                else:
                    ai_dfs.append(df_ai)

            st.session_state.ai_errors = ai_errors

            url_df = pd.DataFrame(url_rows) if url_rows else pd.DataFrame()
            all_frames = ai_dfs + ([url_df] if not url_df.empty else [])

            if not all_frames:
                st.warning(
                    "Nothing to process — please upload at least one PDF "
                    "or paste at least one URL."
                )
            else:
                combined = pd.concat(all_frames, ignore_index=True)
                st.session_state.intake_df = apply_confidence_checks(combined)
                st.session_state.automation_results = None
                st.session_state.pending_enrichment = True

    else:
        # ── Standard path — parser runs first, no AI call ─────────────────────
        parsed_pdf_rows = []
        for pdf_file in (uploaded_files or []):
            try:
                rows = parse_pdf_rows(pdf_file, selected_project, room, "", "")
                if rows:
                    parsed_pdf_rows.extend(rows)
                else:
                    # Parser found nothing — fall back to filename-only row
                    fallback = create_pdf_rows([pdf_file], selected_project, room, "", "")
                    parsed_pdf_rows.extend(fallback)
            except Exception as exc:
                fallback = create_pdf_rows([pdf_file], selected_project, room, "", "")
                parsed_pdf_rows.extend(fallback)
                st.warning(f"Could not parse '{pdf_file.name}': {exc}", icon="⚠️")

        if not url_rows and not parsed_pdf_rows:
            st.warning(
                "Nothing to process — please upload at least one PDF "
                "or paste at least one URL."
            )
        else:
            base_df = build_intake_dataframe(url_rows, parsed_pdf_rows)
            st.session_state.intake_df = apply_confidence_checks(base_df)
            st.session_state.automation_results = None
            st.session_state.pending_enrichment = True

# ── AI extraction error banner ─────────────────────────────────────────────────
if st.session_state.get("ai_errors"):
    for err_msg in st.session_state.ai_errors:
        st.error(err_msg, icon="❌")

# ── Review section ─────────────────────────────────────────────────────────────
if st.session_state.intake_df is not None:
    df: pd.DataFrame = st.session_state.intake_df
    if "Notes" in df.columns:
        df = df.copy()
        df["Notes"] = df["Notes"].apply(remove_notes_row_prefix)
        st.session_state.intake_df = df

    def _is_photo_only_row(row: pd.Series | dict) -> bool:
        value = str(row.get("photo_only", "") or "").strip().lower()
        return (
            value in {"true", "1", "yes"}
            or str(row.get("Import Type", "") or "").strip().lower() == "photo upload"
            or str(row.get("Source Type", "") or "").strip() == "Photo"
        )

    def _quantity_is_missing_or_lt_one(value) -> bool:
        try:
            text = str(value or "").strip()
            if not text or text.lower() in {"nan", "none", "null"}:
                return True
            return int(float(text)) < 1
        except (ValueError, TypeError):
            return True

    def _missing_required_fields(row: pd.Series) -> list[str]:
        """Return column names of required fields that are missing or incomplete."""
        if _is_photo_only_row(row):
            missing = []
            if not str(row.get("Product Name", "") or "").strip():
                missing.append("Product Name")
            if not str(row.get("Product Category", "") or "").strip():
                missing.append("Product Category")
            if not is_public_https_image_url(str(row.get("Image URL", "") or "")):
                missing.append("Image URL")
            return missing
        missing = []
        if not str(row.get("Product Name", "") or "").strip():
            missing.append("Product Name")
        if not str(row.get("Brand", "") or "").strip():
            missing.append("Brand")
        if not has_complete_3d_dimensions(str(row.get("Dimensions", "") or "")):
            missing.append("Dimensions")
        if _quantity_is_missing_or_lt_one(row.get("Quantity")):
            missing.append("Quantity")
        if not str(row.get("Supplier", "") or "").strip():
            missing.append("Supplier")
        if not str(row.get("Room", "") or "").strip():
            missing.append("Room")
        return missing

    def _review_column_for_extracted_field(field: str) -> str:
        normal = field.strip().lower()
        mapping = {
            "dimensions": "Dimensions",
            "width": "Dimensions",
            "height": "Dimensions",
            "depth": "Dimensions",
            "finish": "Finish / Color",
            "color": "Finish / Color",
            "finish / color": "Finish / Color",
            "material": "Notes",
            "lead time": "Notes",
            "price": "Price",
            "sku": "Model/SKU",
            "model number": "Model/SKU",
            "model/sku": "Model/SKU",
            "availability": "Notes",
            "brand": "Brand",
            "supplier": "Supplier",
            "product name": "Product Name",
            "product category": "Product Category",
            "category": "Product Category",
            "quantity": "Quantity",
            "location": "Room",
            "room": "Room",
        }
        return mapping.get(normal, field)

    def _field_is_missing_for_apply(row: pd.Series, column: str) -> bool:
        if column == "Dimensions":
            return not has_complete_3d_dimensions(str(row.get("Dimensions", "") or ""))
        if column == "Quantity":
            return _quantity_is_missing_or_lt_one(row.get("Quantity"))
        return not str(row.get(column, "") or "").strip()

    def _apply_vendor_call_extraction(
        row_idx: int,
        extracted_fields: dict,
        overwrite: bool = False,
    ) -> tuple[pd.DataFrame, list[str], list[str]]:
        if st.session_state.intake_df is None or row_idx not in st.session_state.intake_df.index:
            return st.session_state.intake_df, [], ["No matching product row was found."]
        df_copy = st.session_state.intake_df.copy()
        applied: list[str] = []
        skipped: list[str] = []
        current_row = df_copy.loc[row_idx]
        note_bits: list[str] = []
        for field, detail in extracted_fields.items():
            if not isinstance(detail, dict):
                continue
            value = str(detail.get("value", "") or "").strip()
            if not value:
                continue
            column = _review_column_for_extracted_field(str(field))
            if column not in df_copy.columns:
                skipped.append(str(field))
                continue
            confidence = str(detail.get("confidence", "") or "").lower()
            can_overwrite = overwrite and confidence in {"high", "medium"}
            if not _field_is_missing_for_apply(current_row, column) and not can_overwrite:
                skipped.append(column)
                continue
            if column == "Notes" and field in {"Material", "Lead Time", "Availability"}:
                existing_note = str(df_copy.at[row_idx, "Notes"] or "").strip()
                addition = f"{field}: {value}"
                df_copy.at[row_idx, "Notes"] = f"{existing_note}\n{addition}".strip() if existing_note else addition
            elif column == "Quantity":
                try:
                    df_copy.at[row_idx, column] = int(float(value))
                except (TypeError, ValueError):
                    df_copy.at[row_idx, column] = value
            else:
                df_copy.at[row_idx, column] = value
            applied.append(column)
            evidence = str(detail.get("evidence", "") or "").strip()
            if evidence:
                note_bits.append(f"{column} evidence: {evidence}")
        if applied and "Notes" in df_copy.columns:
            existing_note = str(df_copy.at[row_idx, "Notes"] or "").strip()
            source_note = "Filled from Retell call transcript"
            if source_note not in existing_note:
                note_bits.insert(0, source_note)
            if note_bits:
                df_copy.at[row_idx, "Notes"] = "\n".join([bit for bit in [existing_note, *note_bits] if bit]).strip()
        return apply_confidence_checks(df_copy), sorted(set(applied)), sorted(set(skipped))

    def apply_extracted_specs_to_review_table(
        row_id: int,
        extracted_fields: dict,
        overwrite: bool = False,
    ) -> tuple[pd.DataFrame, list[str], list[str]]:
        return _apply_vendor_call_extraction(row_id, extracted_fields, overwrite=overwrite)

    # ── Automatic enrichment pass ──────────────────────────────────────────────
    if st.session_state.pending_enrichment:
        if st.session_state.get("use_web_enrichment", True) and _BRAVE_API_KEY:
            with st.spinner("Searching manufacturer sources to fill missing product details…"):
                _enriched_df, _enrich_errors, _ = enrich_dataframe(
                    df,
                    use_web_enrichment=st.session_state.get("use_web_enrichment", True),
                    force_refresh=st.session_state.get("force_refresh_enrichment", False),
                )
                st.session_state.intake_df = apply_confidence_checks(_enriched_df)
                st.session_state.enrichment_errors = _enrich_errors
                st.session_state.pending_enrichment = False
            st.rerun()
        else:
            st.session_state.pending_enrichment = False

    if st.session_state.enrichment_errors:
        st.warning(
            f"{len(st.session_state.enrichment_errors)} row(s) could not be enriched — "
            "details added to Notes for those rows.",
            icon="⚠️",
        )

    # ── Image status summary ───────────────────────────────────────────────────
    def _has_valid_image(v) -> bool:
        s = str(v or "").strip()
        return bool(s) and s.lower() not in {"nan", "none", ""}

    if "Image URL" in df.columns:
        _total_n = len(df[df.get("Include", pd.Series([True] * len(df))) != False])
        _with_image = int(df["Image URL"].apply(_has_valid_image).sum())
        _missing_image_count = _total_n - _with_image
    else:
        _total_n = len(df)
        _with_image = 0
        _missing_image_count = _total_n

    with st.container(border=True):
        _img_title_col, _img_action_col = st.columns([5, 3])
        with _img_title_col:
            section_label("Image Status")
        with _img_action_col:
            if _missing_image_count > 0 and _BRAVE_API_KEY:
                if st.button(
                    "Recover Missing Images",
                    type="secondary",
                    use_container_width=True,
                    help="Fetches product pages for rows without Image URL and extracts images.",
                ):
                    with st.spinner(f"Recovering images for {_missing_image_count} row(s)…"):
                        sid = _ensure_session_id()
                        pdf_lookup = _build_pdf_lookup()
                        _recovered_df, _img_diagnostics = recover_images_for_dataframe(
                            df,
                            pdf_lookup=pdf_lookup,
                            session_id=sid,
                            enable_screenshot=True,
                        )
                        st.session_state.intake_df = apply_confidence_checks(_recovered_df)
                        st.session_state.manual_image_uploads = {}
                    _found = sum(1 for d in _img_diagnostics if d.get("confidence") in ("HIGH", "MEDIUM"))
                    if _found:
                        st.success(f"Recovered {_found} of {len(_img_diagnostics)} missing images.")
                    else:
                        st.warning(
                            "No images recovered automatically. "
                            "Check that Product URL is populated, or upload images manually below."
                        )
                    st.rerun()

        _s1, _s2, _s3, _s4 = st.columns(4)
        _s1.metric("Total products", _total_n)
        _s2.metric("Images found", _with_image)
        _s3.metric("Missing images", _missing_image_count, delta=None if _missing_image_count == 0 else f"-{_missing_image_count}", delta_color="inverse")
        # Count manually uploaded this session
        _manually_uploaded = len(st.session_state.get("manual_image_uploads", {}))
        _s4.metric("Manual uploads this session", _manually_uploaded)

        # Per-product upload fallback
        if _missing_image_count > 0:
            st.caption("Upload images manually for products that could not be recovered automatically.")
            _missing_rows = df[df["Image URL"].apply(lambda v: not _has_valid_image(v))] if "Image URL" in df.columns else df

            with st.expander(f"Upload Images for {len(_missing_rows)} Product(s)", expanded=False):
                from src.image_assets import _convert_to_jpeg

                for _row_idx in _missing_rows.index:
                    _row = df.loc[_row_idx]
                    _pname = str(_row.get("Product Name", "")).strip() or f"Row {_row_idx}"
                    _brand = str(_row.get("Brand", "")).strip()
                    _sku = str(_row.get("Model/SKU", "")).strip()

                    _thumb_col, _info_col = st.columns([1, 4])
                    with _info_col:
                        st.markdown(f"**{_pname}**" + (f" — {_brand} {_sku}" if _brand or _sku else ""))
                        _uploaded = st.file_uploader(
                            "Choose image file",
                            type=["jpg", "jpeg", "png", "webp"],
                            key=f"img_upload_{_row_idx}",
                            label_visibility="collapsed",
                        )
                        if _uploaded is not None:
                            try:
                                _jpeg_bytes = _convert_to_jpeg(_uploaded.read())
                                _cloud_url, _cloud_err = upload_image_to_cloudinary(
                                    type("_F", (), {"read": lambda self: _jpeg_bytes, "seek": lambda self, *a: None})()
                                ) if _BRAVE_API_KEY else (None, "Cloudinary not configured")
                                if _cloud_url:
                                    df.at[_row_idx, "Image URL"] = _cloud_url
                                    st.session_state.intake_df = apply_confidence_checks(df)
                                    st.success(f"Uploaded: {_cloud_url}")
                                else:
                                    # Store JPEG locally for ZIP export
                                    st.session_state.manual_image_uploads[int(_row_idx)] = _jpeg_bytes
                                    st.info("Image attached for ZIP export (Cloudinary not configured).")
                            except Exception as _e:
                                st.error(f"Upload failed: {_e}")
                    st.divider()

    st.divider()

    # ── Intake Quality Check ───────────────────────────────────────────────────
    qc_title_col, _, qc_btn_col = st.columns([5, 3, 2])
    with qc_title_col:
        section_label("Intake Quality Check")
    with qc_btn_col:
        rerun_check = st.button(
            "Re-run Check", type="secondary", use_container_width=True,
            help="Re-evaluate confidence scores after editing rows."
        )

    has_confidence = "Review Required" in df.columns and "Confidence Score" in df.columns
    total_n = len(df)

    photo_only_mask = df.apply(_is_photo_only_row, axis=1)
    non_photo_mask = ~photo_only_mask
    photo_only_n = int(photo_only_mask.sum())
    non_photo_df = df[non_photo_mask]
    ignored_mask = df.get("Include", pd.Series([True] * total_n)) == False
    _incl_mask_qc = ~ignored_mask & non_photo_mask
    ignored_n = int(ignored_mask.sum())

    _needs_review_mask = _incl_mask_qc & df.apply(
        lambda r: bool(_missing_required_fields(r)), axis=1
    )
    review_n = int(_needs_review_mask.sum())
    ready_n  = int((_incl_mask_qc & ~_needs_review_mask).sum())

    if has_confidence:
        non_ignored_scores = df.loc[_incl_mask_qc, "Confidence Score"]
        avg_conf = round(non_ignored_scores.mean()) if not non_ignored_scores.empty else 0
    else:
        avg_conf = 0

    if photo_only_n > 0:
        st.info(
            "Photo-only products use Product Name, Section, and hosted Image URL as their required fields.",
            icon="ℹ️",
        )

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.markdown(_qm("Total Rows", total_n, "#4A4540"), unsafe_allow_html=True)
    with c2:
        st.markdown(_qm("Ready", ready_n, "#4A7A5A"), unsafe_allow_html=True)
    with c3:
        st.markdown(_qm("Needs Review", review_n, "#9A7020"), unsafe_allow_html=True)
    with c4:
        st.markdown(_qm("Ignored", ignored_n, "#9E968C"), unsafe_allow_html=True)
    with c5:
        conf_color = "#4A7A5A" if avg_conf >= 75 else "#9A7020" if avg_conf >= 50 else "#B04A3A"
        st.markdown(_qm("Avg Confidence", f"{avg_conf}%", conf_color), unsafe_allow_html=True)

    st.markdown("<div style='height:1.25rem'></div>", unsafe_allow_html=True)

    # ── Programa Automation ────────────────────────────────────────────────────
    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
    st.divider()
    section_label("Programa Automation")
    st.caption("Legacy path — use the CSV/XLSX export below for most imports.")

    # Eligibility: Include=True, not flagged for review, not in terminal statuses,
    # has Product Name, Quantity ≥ 1, Product Category.
    # Dimensions gate is enforced unless the user explicitly bypasses it.
    _BLOCKED_STATUSES = {"Ignored", "Excluded", "Error"}
    allow_missing_dims = st.session_state.get("allow_missing_dims_cb", False)

    def _is_eligible(row: pd.Series) -> bool:
        # Note: Review Required is intentionally NOT a gate here.
        # A row is eligible if it has the required data fields filled in.
        if not row.get("Include", False):
            return False
        if str(row.get("Status", "")) in _BLOCKED_STATUSES:
            return False
        if _is_photo_only_row(row):
            return (
                bool(str(row.get("Product Name", "") or "").strip())
                and bool(str(row.get("Product Category", "") or "").strip())
                and is_public_https_image_url(str(row.get("Image URL", "") or ""))
            )
        if not str(row.get("Product Name", "") or "").strip():
            return False
        try:
            if _quantity_is_missing_or_lt_one(row.get("Quantity")):
                return False
        except (ValueError, TypeError):
            return False
        if not str(row.get("Product Category", "") or "").strip():
            return False
        if not allow_missing_dims and not has_complete_3d_dimensions(str(row.get("Dimensions", "") or "")):
            return False
        return True

    def _block_reason(row: pd.Series) -> str:
        reasons = []
        if not row.get("Include", False):
            reasons.append("Include unchecked")
        if str(row.get("Status", "")) in _BLOCKED_STATUSES:
            reasons.append(f"Status: {row.get('Status', '')}")
        if _is_photo_only_row(row):
            if not str(row.get("Product Name", "") or "").strip():
                reasons.append("No product name")
            if not str(row.get("Product Category", "") or "").strip():
                reasons.append("No category")
            if not is_public_https_image_url(str(row.get("Image URL", "") or "")):
                reasons.append("No hosted image URL")
            return "; ".join(reasons) if reasons else "Unknown"
        if not str(row.get("Product Name", "") or "").strip():
            reasons.append("No product name")
        if _quantity_is_missing_or_lt_one(row.get("Quantity")):
            reasons.append("Quantity < 1")
        if not str(row.get("Product Category", "") or "").strip():
            reasons.append("No category")
        if not allow_missing_dims and not has_complete_3d_dimensions(str(row.get("Dimensions", "") or "")):
            reasons.append("Missing dimensions")
        return "; ".join(reasons) if reasons else "Unknown"

    eligible_df = df[df.apply(_is_eligible, axis=1)].copy()

    def _is_url_row(row: pd.Series) -> bool:
        return (
            str(row.get("Source Type", "")) == "URL"
            and bool(str(row.get("Product URL", "") or "").strip())
        )

    total_sendable = len(eligible_df)
    _n_url = int(eligible_df.apply(_is_url_row, axis=1).sum()) if total_sendable > 0 else 0
    _n_schedule = total_sendable - _n_url

    # Blocked included rows: Include=True but failed eligibility
    _included_mask = df.get("Include", pd.Series([True] * len(df), index=df.index)) == True
    _included_df = df[_included_mask]
    _blocked_df = _included_df[~_included_df.apply(_is_eligible, axis=1)].copy()

    if not _blocked_df.empty:
        _blocked_df["_reason"] = _blocked_df.apply(_block_reason, axis=1)
        st.markdown("<div style='height:0.25rem'></div>", unsafe_allow_html=True)
        _n_dim  = int(_blocked_df["_reason"].str.contains("Missing dimensions").sum())
        _n_cat  = int(_blocked_df["_reason"].str.contains("No category").sum())
        _n_other = len(_blocked_df) - _n_dim - _n_cat
        parts = []
        if _n_dim   > 0: parts.append(f"{_n_dim} missing dimensions")
        if _n_cat   > 0: parts.append(f"{_n_cat} missing category")
        if _n_other > 0: parts.append(f"{_n_other} other")
        st.warning(
            f"{len(_blocked_df)} included item{'s' if len(_blocked_df) != 1 else ''} not yet eligible — "
            + ", ".join(parts) + ". They will be skipped when you send.",
            icon="⚠️",
        )
        st.markdown("<div style='height:0.25rem'></div>", unsafe_allow_html=True)

    # ── Automation controls ────────────────────────────────────────────────────
    auto_col, send_col, _ = st.columns([3, 3, 4])
    with auto_col:
        auto_done = st.checkbox(
            "Auto-click Done after filling each item",
            value=False,
            help="When unchecked (default), the browser pauses after each item so you can review before saving.",
        )
        single_row_test_mode = st.checkbox(
            "Single-row test mode (process first item only)",
            value=False,
            key="single_row_test_mode_cb",
            help="When checked, only the first selected row is sent. Turn off once field entry is confirmed working.",
        )
        if single_row_test_mode:
            st.caption("Test mode on — only the first item will be sent.")
        else:
            st.caption(
                "For safety, keep Auto-click Done off during testing. "
                "The automation will pause before final submission."
            )
        allow_missing_dims = st.checkbox(
            "Allow sending rows with missing dimensions",
            value=False,
            key="allow_missing_dims_cb",
            help="By default, rows without complete W×H×D dimensions are blocked. Check this to override.",
        )
        upload_product_images = st.checkbox(
            "Upload product images to Programa",
            value=True,
            key="upload_product_images_cb",
            help="Find a product image online and upload it into the Programa Details panel when possible.",
        )

    if allow_missing_dims:
        _eligible_non_photo = eligible_df[~eligible_df.apply(_is_photo_only_row, axis=1)].copy()
        _n_missing = int(
            _eligible_non_photo["Dimensions"].apply(
                lambda v: not has_complete_3d_dimensions(str(v or ""))
            ).sum()
        ) if "Dimensions" in _eligible_non_photo.columns else 0
        if _n_missing > 0:
            st.warning(
                f"Dimension bypass is on — {_n_missing} item{'s' if _n_missing != 1 else ''} "
                "with incomplete dimensions will be sent.",
                icon="⚠️",
            )

    _n_included = len(_included_df)
    _has_any_included = _n_included > 0

    with send_col:
        no_schedule_url = not schedule_url.strip()
        if total_sendable > 0:
            send_label = f"Send {total_sendable} item{'s' if total_sendable != 1 else ''} to Programa"
            send_caption = (
                f"{_n_url} via Add-from-URL  ·  {_n_schedule} via Schedule entry"
                if _n_url > 0 else f"{_n_schedule} via Schedule entry"
            )
        elif _has_any_included:
            send_label = "Send to Programa"
            send_caption = f"{_n_included} included · {len(_blocked_df)} blocked — see reasons above"
        else:
            send_label = "Send to Programa"
            send_caption = "Add rows to the intake table first."
        send_to_programa = st.button(
            send_label,
            key="send_to_programa_main",
            type="primary",
            use_container_width=True,
            disabled=(not _has_any_included or no_schedule_url),
        )
        st.caption(send_caption)

    if no_schedule_url and _has_any_included:
        st.warning("Paste the Programa schedule link above before sending.", icon="⚠️")

    # ── Send Button Debug expander ─────────────────────────────────────────────
    with st.expander("Send Button Debug", expanded=False):
        try:
            from src.programa_automation import run_programa_automation as _rpa_check
            _import_ok = True
        except Exception as _ie:
            _import_ok = False
            st.error(f"Import error: {_ie}")
        _btn_enabled = _has_any_included and not no_schedule_url
        st.markdown(f"**button_should_be_enabled:** `{_btn_enabled}`")
        st.markdown(f"**eligible_rows_count:** `{total_sendable}`")
        st.markdown(f"**blocked_rows_count:** `{len(_blocked_df)}`")
        st.markdown(f"**included_rows_count:** `{_n_included}`")
        st.markdown(f"**photo_only_rows_count:** `{photo_only_n}`")
        st.markdown(f"**schedule_url:** `{schedule_url!r}`")
        st.markdown(f"**selected_project:** `{selected_project!r}`")
        st.markdown(f"**allow_missing_dimensions:** `{st.session_state.get('allow_missing_dims_cb', False)}`")
        st.markdown(f"**url_path_count:** `{_n_url}`")
        st.markdown(f"**schedule_path_count:** `{_n_schedule}`")
        st.markdown(f"**programa_automation_importable:** `{_import_ok}`")
        if not _blocked_df.empty and "_reason" in _blocked_df.columns:
            st.markdown("**First 5 blocked row reasons:**")
            for _, _dbr in _blocked_df.head(5).iterrows():
                st.caption(f"• {_dbr.get('Product Name', 'Unnamed')} — {_dbr.get('_reason', '?')}")
        if total_sendable > 0:
            st.markdown("**Eligible rows:**")
            _elig_cols = ["Product Name", "Brand", "Dimensions", "Product Category", "Source Type", "Product URL"]
            _elig_show = [c for c in _elig_cols if c in eligible_df.columns]
            st.dataframe(eligible_df[_elig_show], use_container_width=True, hide_index=True)
        if st.session_state.get("automation_results"):
            st.markdown("**Last automation log:**")
            _last_entries = st.session_state.automation_results.get("entries", [])
            st.dataframe(pd.DataFrame(_last_entries), use_container_width=True, hide_index=True)
        if st.session_state.get("_automation_traceback"):
            st.markdown("**Last error traceback:**")
            st.code(st.session_state["_automation_traceback"])

        st.divider()
        st.markdown("**Single-Row Debug Mode** (slow_mo=1000, screenshots after each step)")
        _first_included = None
        if not _included_df.empty:
            _first_included = _included_df.iloc[0].to_dict()
        if _first_included:
            st.caption(
                f"Test row: **{_first_included.get('Product Name') or '(no name)'}** "
                f"/ {_first_included.get('Brand') or '(no brand)'}  "
                f"· Project: {selected_project!r}"
            )
        _debug_single_disabled = (not _first_included) or (not schedule_url.strip())
        _debug_single_btn = st.button(
            "Test Single Row (Debug)",
            key="debug_single_row_btn",
            type="secondary",
            use_container_width=True,
            disabled=_debug_single_disabled,
            help="Runs one row through the full Schedule → Custom Product → field-entry flow with slow_mo=1000 and a screenshot at every step.",
        )
        if _debug_single_btn and _first_included and schedule_url.strip():
            st.info("Starting single-row debug — Chrome will open shortly. Watch the terminal for step-by-step logs.")
            with st.spinner("Debug run in progress…"):
                try:
                    _debug_steps = run_programa_debug_single_row(
                        _first_included,
                        project_name=selected_project,
                        screenshots_dir="data/enrichment_debug/programa_steps",
                    )
                    _debug_failed = [s for s in _debug_steps if not s["success"]]
                    if _debug_failed:
                        st.warning(f"{len(_debug_failed)} step(s) failed — see details below and terminal output.")
                    else:
                        st.success(f"All {len(_debug_steps)} steps passed.")
                    st.dataframe(pd.DataFrame(_debug_steps), use_container_width=True, hide_index=True)
                except Exception as _dbe:
                    import traceback as _dbtb
                    st.error(f"Debug run error: {_dbe}")
                    st.code(_dbtb.format_exc())

        st.divider()
        st.markdown("**Field Entry Diagnostic** (slow_mo=1500, browser stays open on failure, stops at first failed step)")
        st.caption("Diagnoses exactly where Product Name entry breaks — runs 13 steps with a screenshot at every one.")
        _diag_btn = st.button(
            "Run Programa Diagnostic Test",
            key="programa_field_diagnostic_btn",
            type="secondary",
            use_container_width=True,
            disabled=_debug_single_disabled,
            help="Step-by-step diagnostic for Product Name entry. Browser stays open on failure for inspection.",
        )
        if _diag_btn and _first_included and schedule_url.strip():
            st.info("Starting field diagnostic — Chrome will open. Browser stays open if any step fails.", icon="🔍")
            with st.spinner("Diagnostic running — watch the browser and terminal…"):
                try:
                    _diag_steps = run_programa_field_diagnostic(
                        _first_included,
                        project_name=selected_project,
                        out_dir="data/programa_diagnostics",
                    )
                    _diag_failed = [s for s in _diag_steps if not s["success"]]
                    if _diag_failed:
                        first_fail = _diag_failed[0]
                        st.error(
                            f"Diagnostic stopped at: **{first_fail['step']}** — {first_fail['message']}",
                            icon="🛑",
                        )
                    else:
                        st.success("All diagnostic steps passed — Product Name entry is working.")
                    st.dataframe(pd.DataFrame(_diag_steps), use_container_width=True, hide_index=True)
                    st.caption("Full report saved to data/programa_diagnostics/")
                except Exception as _de:
                    import traceback as _dtb
                    st.error(f"Diagnostic error: {_de}")
                    st.code(_dtb.format_exc())

    if send_to_programa:
        # ── Step 4: Immediate feedback ─────────────────────────────────────────
        st.info("Send button clicked — preparing Programa transfer.", icon="ℹ️")

        rows_payload = eligible_df.to_dict("records")

        if len(rows_payload) == 0:
            st.error(
                "No items are ready to send. Review the required fields above.",
                icon="🚫",
            )
            if not _blocked_df.empty:
                reasons_preview = _blocked_df["_reason"].dropna().head(5).tolist()
                st.markdown("**Blocked reasons (first 5):**")
                for _r in reasons_preview:
                    st.markdown(f"- {_r}")
        else:
            # ── Step 5: Backend call verification logging ──────────────────────
            _logger.info(
                "Programa send triggered — project=%r rows=%d (url=%d schedule=%d)",
                selected_project, len(rows_payload), _n_url, _n_schedule,
            )
            for _row in rows_payload:
                _path = "URL" if _is_url_row(_row) else "Schedule/New"
                _logger.info(
                    "  Sending: %r via %s path",
                    _row.get("Product Name", "?"),
                    _path,
                )
            for _br in (_blocked_df.itertuples() if not _blocked_df.empty else []):
                _logger.info(
                    "  Blocked: %s — %s",
                    getattr(_br, "Product Name", "?"),
                    getattr(_br, "_reason", "?"),
                )

            _dest_label = selected_project or schedule_url
            st.info(
                f"Starting automation — {len(rows_payload)} item{'s' if len(rows_payload) != 1 else ''} "
                f"({_n_url} URL + {_n_schedule} Schedule) → **{_dest_label}**. "
                "Chrome will open shortly.",
                icon="ℹ️",
            )

            st.session_state.nav_failed = False
            st.session_state.nav_pending_rows = []
            st.session_state["_automation_traceback"] = ""
            with st.spinner(
                f"Chrome is open — navigating to schedule, then adding {len(rows_payload)} item(s). "
                "Follow any prompts in the browser window."
            ):
                try:
                    log_entries, log_path = run_programa_automation(
                        rows=rows_payload,
                        project_name=selected_project,
                        auto_done=auto_done,
                        skip_navigation=False,
                        single_row_test_mode=single_row_test_mode,
                        schedule_url=schedule_url,
                        upload_product_images=upload_product_images,
                    )
                    _logger.info(
                        "Automation complete — %d log entries, log_path=%r",
                        len(log_entries), log_path,
                    )
                    st.session_state.automation_results = {
                        "entries": log_entries,
                        "log_path": log_path,
                    }
                    if any(e["status"] == "nav_failed" for e in log_entries):
                        st.session_state.nav_failed = True
                        st.session_state.nav_pending_rows = rows_payload
                except Exception as exc:
                    import traceback as _tb
                    _full_tb = _tb.format_exc()
                    _logger.exception("Unhandled exception in run_programa_automation: %s", exc)
                    print(_full_tb)  # also surface in terminal
                    st.session_state["_automation_traceback"] = _full_tb
                    from src.automation_logs import make_log_entry
                    st.session_state.automation_results = {
                        "entries": [make_log_entry(
                            "", "error",
                            f"Unhandled exception: {exc} — check that Chrome and Playwright are installed.",
                        )],
                        "log_path": "",
                    }
            st.rerun()

    # ── Navigation failure — manual continue flow ──────────────────────────────
    if st.session_state.nav_failed:
        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
        _nav_target = selected_project or "the project"
        st.warning(
            f"**{_nav_target}** was not found automatically. "
            "Please open the correct project in Programa manually, then click **Continue** below.",
            icon="⚠️",
        )
        st.markdown(
            '<div style="font-size:0.78rem;color:#7A7068;margin-bottom:0.75rem;">'
            "The browser closed after the failed navigation attempt. "
            "Clicking Continue will reopen Chrome — navigate to the project inside Programa, "
            "then click OK in the browser dialog to begin adding items."
            "</div>",
            unsafe_allow_html=True,
        )
        cont_col, _ = st.columns([3, 7])
        with cont_col:
            continue_nav = st.button(
                "Continue After Manual Project Open",
                type="primary",
                use_container_width=True,
            )

        if continue_nav:
            st.session_state.nav_failed = False
            pending_rows = st.session_state.nav_pending_rows or []
            _logger.info("Manual-continue triggered — project=%r rows=%d", selected_project, len(pending_rows))
            with st.spinner(
                f"Chrome is open — navigate to the project in Programa, "
                "click OK in the browser dialog, then wait for items to be added."
            ):
                try:
                    log_entries, log_path = run_programa_automation(
                        rows=pending_rows,
                        project_name=selected_project,
                        auto_done=auto_done,
                        skip_navigation=True,
                        single_row_test_mode=single_row_test_mode,
                        schedule_url=schedule_url,
                        upload_product_images=upload_product_images,
                    )
                    _logger.info("Manual-continue complete — %d log entries", len(log_entries))
                    st.session_state.automation_results = {
                        "entries": log_entries,
                        "log_path": log_path,
                    }
                    st.session_state.nav_pending_rows = []
                except Exception as exc:
                    _logger.exception("Unhandled exception in manual-continue: %s", exc)
                    from src.automation_logs import make_log_entry
                    st.session_state.automation_results = {
                        "entries": [make_log_entry(
                            "", "error",
                            f"Unhandled exception during manual-continue run: {exc}",
                        )],
                        "log_path": "",
                    }
            st.rerun()

    # ── Automation Results ─────────────────────────────────────────────────────
    if st.session_state.automation_results:
        results  = st.session_state.automation_results
        entries: list[dict] = results["entries"]
        log_path: str       = results["log_path"]

        success_n = sum(1 for e in entries if e["status"] == "success")
        filled_n  = sum(1 for e in entries if e["status"] == "filled_awaiting_confirm")
        error_n   = sum(1 for e in entries if e["status"] == "error")
        total_run = len(entries)

        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
        if error_n == 0:
            st.success(
                f"Automation complete — {success_n + filled_n} of {total_run} item(s) processed.",
            )
        else:
            st.warning(
                f"Automation finished with {error_n} error(s). "
                f"{success_n + filled_n} of {total_run} item(s) processed.",
                icon="⚠️",
            )

        _results_entries = pd.DataFrame(entries)
        _results_entries["Product"] = _results_entries.apply(
            lambda r: r.get("product_url") or r.get("product_name", ""), axis=1
        )
        results_df = _results_entries[["timestamp", "Product", "status", "message"]]
        st.dataframe(
            results_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "timestamp": st.column_config.TextColumn("Time", width="medium"),
                "Product":   st.column_config.TextColumn("Product", width="large"),
                "status":    st.column_config.TextColumn("Status", width="medium"),
                "message":   st.column_config.TextColumn("Message", width="large"),
            },
        )
        if log_path:
            st.caption(f"Full log saved → {log_path}")

    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
    st.divider()

    # ── Review Table ───────────────────────────────────────────────────────────
    row_count = len(df)
    section_label(f"Review Table · {row_count} item{'s' if row_count != 1 else ''}")

    if "Product Category" in df.columns:
        _blank_cat_preview = int(
            (df["Include"] == True).values.sum()
            if "Include" in df.columns else len(df)
        )
        _blank_cat_preview = int(
            ((df.get("Include", pd.Series([True]*len(df))) == True)
             & (df["Product Category"].isna() | (df["Product Category"].str.strip() == ""))).sum()
        )
        if _blank_cat_preview > 0:
            st.caption(
                f"ℹ️  {_blank_cat_preview} row{'s' if _blank_cat_preview != 1 else ''} with blank "
                "category — suggestions available in AI-Assisted Cleanup below."
            )

    edited_df = st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_order=[c for c in DISPLAY_COLUMNS if c in df.columns],
        column_config={
            # ── Confidence / status columns ──
            "Include": st.column_config.CheckboxColumn(
                "Include", default=True, width="small"
            ),
            "Confidence Score": st.column_config.NumberColumn(
                "Confidence", width="small", disabled=True,
                min_value=0, max_value=100, format="%d %%"
            ),
            "Review Required": st.column_config.CheckboxColumn(
                "Review Req.", width="small",
                help="Uncheck after you have manually reviewed this row."
            ),
            "Suggested Action": st.column_config.TextColumn(
                "Suggested Action", width="large", disabled=True
            ),
            # ── Core product fields ──
            "Project": st.column_config.TextColumn("Project", width="medium"),
            "Room": st.column_config.TextColumn("Location", width="medium"),
            "Product Name": st.column_config.TextColumn("Name of Product", width="large"),
            "Brand": st.column_config.TextColumn("Brand", width="medium"),
            "Dimensions": st.column_config.TextColumn("Dimensions", width="medium"),
            "Finish / Color": st.column_config.TextColumn("Finish / Color", width="medium"),
            "Color": st.column_config.TextColumn("Color", width="medium"),
            "Material": st.column_config.TextColumn("Material", width="medium"),
            "Model/SKU": st.column_config.TextColumn(
                "Serial / Model Number", width="medium"
            ),
            "Product Category": st.column_config.SelectboxColumn(
                "Category", options=CATEGORIES, width="medium"
            ),
            "Quantity": st.column_config.NumberColumn(
                "Qty", min_value=1, step=1, width="small"
            ),
            "Price": st.column_config.TextColumn("Price", width="small"),
            "Supplier": st.column_config.TextColumn(
                "Who We Bought It From", width="medium"
            ),
            "Product URL": st.column_config.LinkColumn("Product URL", width="medium"),
            "Notes": st.column_config.TextColumn("Notes", width="large"),
            # ── Meta columns ──
            "Source Type": st.column_config.TextColumn(
                "Source", width="small", disabled=True
            ),
            "Import Type": st.column_config.TextColumn(
                "Import Type", width="small", disabled=True
            ),
            "Status": st.column_config.SelectboxColumn(
                "Status", options=STATUSES, width="medium"
            ),
            "Missing Fields": st.column_config.TextColumn(
                "Missing Fields", width="large", disabled=True
            ),
            "AI Category Confidence": st.column_config.NumberColumn(
                "Cat. AI Confidence", width="small", disabled=True,
                min_value=0, max_value=100, format="%d %%"
            ),
            "Category Source": st.column_config.TextColumn(
                "Category Source", width="small", disabled=True
            ),
            "Image Filename": st.column_config.TextColumn(
                "Image Filename", width="medium", disabled=True
            ),
            "Image Upload Status": st.column_config.TextColumn(
                "Image Status", width="small", disabled=True
            ),
            "Image URL": st.column_config.LinkColumn("Image URL", width="medium"),
            "Local Image Path": None,
            "photo_only": None,
        },
    )
    for _hidden_col in INTERNAL_IMAGE_COLUMNS:
        if _hidden_col in df.columns and _hidden_col not in edited_df.columns:
            edited_df[_hidden_col] = df[_hidden_col]

    st.session_state.intake_df = edited_df

    if rerun_check:
        st.session_state.intake_df = apply_confidence_checks(edited_df)
        st.rerun()

    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

    # ── Export CSV ─────────────────────────────────────────────────────────────
    _export_col, _, __ = st.columns([2, 6, 2])
    with _export_col:
        included = (
            edited_df[edited_df["Include"] == True]
            if "Include" in edited_df.columns else edited_df
        )
        safe_name = selected_project.strip().replace(" ", "_") or "intake"
        st.download_button(
            label="Download Internal Intake CSV (not for Programa)",
            data=get_csv_bytes(included),
            file_name=f"{safe_name}_internal_intake_not_for_programa.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.warning("Do not upload this file to Programa. Use Export for Programa Import below.", icon="⚠️")

    # ── Export for Programa Import ─────────────────────────────────────────────
    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
    st.divider()
    section_label("Export for Programa Import")

    _export_summary = validate_for_export(included)
    _programa_df = build_programa_import_dataframe(included)
    _today = datetime.date.today().isoformat()

    _export_count = _export_summary["export_count"]
    _skipped = _export_summary["skipped"]
    _missing_section = _export_summary["missing_section"]
    _missing_dims = _export_summary["missing_dimensions"]
    _missing_url = _export_summary["missing_product_url"]
    _missing_img = _export_summary["missing_image_url"]
    _image_url_present = _export_summary["image_url_present"]
    _image_url_total = _export_summary["image_url_total"]

    if _export_count > 0:
        st.success(
            f"✓  {_export_count} row{'s' if _export_count != 1 else ''} ready for export. "
            "Use this file for Programa Import Products.",
            icon=None,
        )
    else:
        st.info("No rows ready for export.", icon=None)

    if _missing_section:
        st.warning(f"Rows missing Section: {len(_missing_section)}", icon=None)
        with st.expander(f"⚠  {len(_missing_section)} row{'s' if len(_missing_section) != 1 else ''} missing Section — using \"General\""):
            for item in _missing_section:
                st.markdown(f"- {item['product_name']}")
    if _missing_dims > 0:
        st.warning(f"⚠  {_missing_dims} row{'s' if _missing_dims != 1 else ''} missing Dimensions", icon=None)
    if _missing_url > 0:
        st.warning(f"⚠  {_missing_url} row{'s' if _missing_url != 1 else ''} missing Product URL", icon=None)
    if _missing_img > 0:
        st.warning(f"⚠  {_missing_img} row{'s' if _missing_img != 1 else ''} missing Image URL", icon=None)
    st.info(f"Image URLs present: {_image_url_present} / {_image_url_total}", icon=None)
    if _skipped:
        with st.expander(f"✕  {len(_skipped)} row{'s' if len(_skipped) != 1 else ''} skipped"):
            for item in _skipped:
                reason = item.get("reason") or "missing Product Name"
                st.markdown(f"- Row {item['index']}: {reason}")

    _dl_col1, _dl_col2, _dl_col3 = st.columns(3)
    with _dl_col1:
        st.download_button(
            label="Download Programa CSV",
            data=export_programa_csv(_programa_df),
            file_name=f"programa_import_{_today}.csv",
            mime="text/csv",
            use_container_width=True,
            disabled=_programa_df.empty,
        )
    with _dl_col2:
        st.download_button(
            label="Download Programa XLSX",
            data=export_programa_xlsx(_programa_df),
            file_name=f"programa_import_{_today}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            disabled=_programa_df.empty,
        )
    with _dl_col3:
        _manual_uploads = st.session_state.get("manual_image_uploads", {})
        _covered_by_manual = len(_manual_uploads)
        _net_missing = max(0, _missing_img - _covered_by_manual)
        if _net_missing > 0:
            st.caption(
                f"ZIP will export without images for {_net_missing} product{'s' if _net_missing != 1 else ''}. "
                "Upload images above or add Image URLs before exporting."
            )
        st.download_button(
            label="Download ZIP (CSV + images)",
            data=export_programa_zip(included, manual_images=_manual_uploads, session_id=_ensure_session_id()),
            file_name=f"programa_export_{_today}.zip",
            mime="application/zip",
            use_container_width=True,
            disabled=not included,
            help="ZIP archive containing the Programa CSV, downloaded product images (JPG), and a per-product image manifest.",
        )

    _include_debug = st.checkbox("Include debug columns", key="programa_debug_export")
    if _include_debug:
        _debug_df = build_programa_debug_dataframe(included)
        st.download_button(
            label="Download Debug CSV",
            data=export_programa_csv(_debug_df),
            file_name=f"programa_import_debug_{_today}.csv",
            mime="text/csv",
            disabled=_debug_df.empty,
        )

    # ── Needs Review ───────────────────────────────────────────────────────────
    _incl_mask_nr = (
        edited_df.get("Include", pd.Series([True] * len(edited_df), index=edited_df.index)) == True
    )
    _needs_review_rows = edited_df[
        _incl_mask_nr & edited_df.apply(lambda r: bool(_missing_required_fields(r)), axis=1)
    ]

    if not _needs_review_rows.empty:
        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
        st.divider()
        section_label("Needs Review")
        st.caption(
            f"{len(_needs_review_rows)} item{'s' if len(_needs_review_rows) != 1 else ''} "
            "with missing required fields — fill in the values below to complete them."
        )

        _FIELD_PLACEHOLDERS = {
            "Product Name": "Enter product name",
            "Brand":        "Enter manufacturer / brand",
            "Dimensions":   "Enter full W × H × D dimensions",
            "Quantity":     "Enter quantity",
            "Supplier":     "Enter who bought this from",
            "Room":         "Enter location",
            "Product Category": "Choose section/category",
            "Image URL":     "Paste hosted image URL",
        }
        _FIELD_LABELS = {
            "Product Name": "Product Name",
            "Brand":        "Brand",
            "Dimensions":   "Dimensions",
            "Quantity":     "Quantity",
            "Supplier":     "Supplier",
            "Room":         "Location",
            "Product Category": "Section",
            "Image URL":     "Image URL",
        }

        def _render_vendor_call_panel(row_idx: int, row_data: pd.Series, missing_field: str) -> None:
            row_dict = row_data.to_dict()
            field_label = _FIELD_LABELS.get(missing_field, missing_field)
            _pname = str(row_dict.get("Product Name", "") or "").strip() or "Unnamed item"
            _brand = str(row_dict.get("Brand", "") or "").strip() or "Not provided"
            _sku = str(row_dict.get("Model/SKU", "") or "").strip() or "Not provided"
            call_key = f"{row_idx}_{missing_field}"
            default_goal = build_call_goal(row_dict, [field_label])

            st.markdown("<div style='height:0.35rem'></div>", unsafe_allow_html=True)
            with st.container(border=True):
                st.markdown("**Vendor Call**")
                info_cols = st.columns(4)
                info_cols[0].caption("Product Name")
                info_cols[0].write(_pname)
                info_cols[1].caption("Brand")
                info_cols[1].write(_brand)
                info_cols[2].caption("SKU / Model")
                info_cols[2].write(_sku)
                info_cols[3].caption("Missing Field Needed")
                info_cols[3].write(field_label)

                phone_col, goal_col = st.columns([1, 2])
                with phone_col:
                    phone_number = st.text_input(
                        "Phone number",
                        key=f"vendor_call_phone_{call_key}",
                        placeholder="+12223334444",
                    )
                    phone_is_e164 = bool(re.fullmatch(r"\+[1-9]\d{7,14}", phone_number.strip()))
                    if phone_number.strip() and not phone_is_e164:
                        st.caption("Use format +1XXXXXXXXXX")
                with goal_col:
                    call_goal = st.text_area(
                        "Call goal",
                        key=f"vendor_call_goal_{call_key}",
                        value=default_goal,
                        height=86,
                    )

                script = build_call_script(
                    row_dict,
                    [field_label],
                    phone_number,
                    custom_goal=call_goal,
                )
                st.caption("Generated call script preview")
                st.info(script)

                provider_ready = calls_enabled()
                provider_name = "mock" if vendor_call_mock_enabled() else get_call_provider()
                if provider_name == "bland":
                    if st.button(
                        "Test Connection",
                        key=f"vendor_call_test_connection_{call_key}",
                        use_container_width=False,
                    ):
                        st.session_state.vendor_call_results[f"{call_key}_connection"] = test_bland_connection()
                    if st.button(
                        "List Bland Personas/Agents",
                        key=f"vendor_call_personas_{call_key}",
                        use_container_width=False,
                    ):
                        st.session_state.vendor_call_results[f"{call_key}_personas"] = list_bland_personas()

                connection_result = st.session_state.vendor_call_results.get(f"{call_key}_connection")
                with st.container(border=True):
                    st.markdown("**Provider Diagnostics**")
                    diag_cols = st.columns(4)
                    account_status = (
                        connection_result.get("account_connection", "not tested")
                        if connection_result else "not tested"
                    )
                    reachable = (
                        connection_result.get("provider_reachable", "unknown")
                        if connection_result else "unknown"
                    )
                    outbound_enabled = (
                        connection_result.get("outbound_enabled", "unknown")
                        if connection_result else "unknown"
                    )
                    billing_status = (
                        connection_result.get("billing_status", "unknown")
                        if connection_result else "unknown"
                    )
                    diag_cols[0].caption("Account connection")
                    diag_cols[0].write(account_status)
                    diag_cols[1].caption("Provider reachable")
                    diag_cols[1].write(reachable)
                    diag_cols[2].caption("Outbound enabled")
                    diag_cols[2].write(outbound_enabled)
                    diag_cols[3].caption("Billing status")
                    diag_cols[3].write(billing_status)
                    if connection_result:
                        response_text = str(connection_result.get("provider_response_text") or "").strip()
                        if response_text:
                            with st.expander("Exact provider response text"):
                                st.code(response_text, language="json")
                    personas_result = st.session_state.vendor_call_results.get(f"{call_key}_personas")
                    if personas_result:
                        with st.expander("Available Bland personas"):
                            if personas_result.get("status") != "connected":
                                st.warning(
                                    "Bland did not allow the app to list personas. "
                                    "Calls can still work, but Alley needs BLAND_PERSONA_ID set manually.",
                                    icon="⚠️",
                                )
                            matched_persona = personas_result.get("matched_persona") or {}
                            st.markdown(
                                "\n".join(
                                    [
                                        f"- Requested agent: `{personas_result.get('requested_agent_name') or 'None'}`",
                                        f"- Alley persona ID: `{personas_result.get('matched_persona_id') or 'Not found'}`",
                                        f"- Alley voice: `{personas_result.get('matched_voice') or 'Bland default / not shown'}`",
                                    ]
                                )
                            )
                            if personas_result.get("env_suggestion"):
                                st.caption("Add this to .env to pin the correct persona:")
                                st.code(str(personas_result["env_suggestion"]), language="bash")
                            if matched_persona:
                                st.caption("Matched Alley config")
                                st.json(make_json_safe(matched_persona))
                            st.json(make_json_safe(personas_result.get("personas", [])))
                            response_text = str(personas_result.get("provider_response_text") or "").strip()
                            if response_text:
                                st.caption("Raw personas response")
                                st.code(response_text, language="json")
                            if personas_result.get("attempts"):
                                st.caption("Persona lookup attempts")
                                st.json(make_json_safe(personas_result.get("attempts", [])))

                st.caption(f"Call recording and transcript will appear here after {provider_name.title()} returns them.")
                placeholder_cols = st.columns(2)
                placeholder_cols[0].info("Recording: pending")
                placeholder_cols[1].info("Transcript: pending review")

                call_cols = st.columns([1.15, 2.85])
                with call_cols[0]:
                    start_call = st.button(
                        "Start AI Call",
                        key=f"vendor_call_start_{call_key}",
                        disabled=not provider_ready or not phone_number.strip() or not phone_is_e164,
                        use_container_width=True,
                    )
                    minimal_call = False
                    minimal_self_call = False
                    if provider_name == "bland":
                        minimal_call = st.button(
                            "Test Bland Minimal Call",
                            key=f"vendor_call_minimal_{call_key}",
                            disabled=not provider_ready or not phone_number.strip() or not phone_is_e164,
                            use_container_width=True,
                            help="Places a real Bland call with only phone_number and task.",
                        )
                        minimal_self_call = st.button(
                            "Minimal Self Call",
                            key=f"vendor_call_minimal_self_{call_key}",
                            disabled=not provider_ready,
                            use_container_width=True,
                            help="Places a real Bland call to +19174990300 with only phone_number and task.",
                        )
                with call_cols[1]:
                    if provider_ready:
                        st.caption(f"Provider: {provider_name}")
                        if provider_name == "bland":
                            st.caption("Minimal test sends only phone_number and task. No from/persona/voice/metadata.")
                        elif provider_name == "retell":
                            st.caption("Uses Retell agent variables and the configured outbound number.")
                        if vendor_call_mock_enabled():
                            st.caption("Mock mode is enabled. No real call will be placed.")
                        if phone_number.strip() and not phone_is_e164:
                            st.caption("Use format +1XXXXXXXXXX")
                    else:
                        st.caption("Call provider not configured.")

                if start_call:
                    result = start_vendor_call(
                        row_dict,
                        [field_label],
                        phone_number,
                        custom_goal=call_goal,
                    )
                    st.session_state.vendor_call_results[call_key] = result
                    if result.get("call_id"):
                        st.session_state.vendor_call_metadata[call_key] = {
                            "call_id": result.get("call_id"),
                            "row_index": row_idx,
                            "product_name": _pname,
                            "brand": _brand,
                            "model": _sku,
                            "missing_fields": [field_label],
                            "phone_number": phone_number.strip(),
                            "provider": result.get("provider", provider_name),
                            "status": "in_progress",
                        }
                if minimal_call:
                    minimal_task = build_minimal_call_task(row_dict, [field_label])
                    result = start_bland_minimal_call(phone_number, minimal_task)
                    st.session_state.vendor_call_results[call_key] = result
                if minimal_self_call:
                    result = start_bland_minimal_call(
                        "+19174990300",
                        "Say hello and confirm this is a test call.",
                    )
                    st.session_state.vendor_call_results[call_key] = result

                result = st.session_state.vendor_call_results.get(call_key)
                if result:
                    status = result.get("status", "unknown")
                    message = result.get("message") or ""
                    call_id = result.get("call_id")
                    if status == "call_started":
                        st.success(f"Call started. Call ID: {call_id}", icon="☎️")
                        if result.get("warning"):
                            st.warning(result["warning"], icon="⚠️")
                        dashboard_name = "Retell" if result.get("provider") == "retell" else "Bland"
                        st.caption(
                            f"To view transcript and recording, open {dashboard_name} Dashboard → Calls, "
                            f"then search for call ID `{call_id}` after the call completes."
                        )
                        if result.get("log_error"):
                            st.caption(f"Call log could not be saved: {result['log_error']}")
                    elif status == "mock_call_completed":
                        st.success(f"Mock call completed. Call ID: {call_id}", icon="☎️")
                        st.info("Recording: mock placeholder  \n\nTranscript: mock transcript available in diagnostics.")
                    else:
                        friendly = (
                            result.get("friendly_message")
                            or result.get("message")
                            or "Provider rejected outbound call. Likely payload, caller number, credits, or account permissions."
                        )
                        st.warning(friendly, icon="⚠️")
                        if result.get("missing_config"):
                            st.caption("Missing Retell configuration")
                            st.json(make_json_safe(result.get("missing_config", [])))
                        if message:
                            st.caption(message)
                        provider_response = result.get("provider_response")
                        debug_info = result.get("debug")
                    provider_response = result.get("provider_response")
                    debug_info = result.get("debug")

                    if call_id:
                        status_cols = st.columns([1.1, 1.1, 2.8])
                        check_result = status_cols[0].button(
                            "Check Call Result",
                            key=f"vendor_call_check_{call_key}",
                            use_container_width=True,
                        )
                        overwrite_existing = status_cols[1].checkbox(
                            "Overwrite existing values if extracted value is higher confidence",
                            key=f"vendor_call_overwrite_{call_key}",
                            value=False,
                        )
                        status_cols[2].caption(f"Call ID: {call_id}")
                        if check_result:
                            call_status = get_call_status(call_id, provider=result.get("provider", provider_name))
                            transcript = str(call_status.get("transcript") or "")
                            extracted_specs = (
                                extract_vendor_specs_from_transcript(transcript, row_dict, [field_label])
                                if transcript
                                else {
                                    "extracted_fields": {},
                                    "unresolved_fields": [field_label],
                                    "notes": "No transcript available yet.",
                                }
                            )
                            st.session_state.vendor_call_extractions[call_key] = {
                                "call_status": make_json_safe(call_status),
                                "extracted_specs": make_json_safe(extracted_specs),
                            }
                            if call_status.get("status") == "call_completed":
                                st.session_state.vendor_call_metadata.get(call_key, {})["status"] = "completed"
                        extraction_bundle = st.session_state.vendor_call_extractions.get(call_key)
                        if extraction_bundle:
                            call_status = extraction_bundle.get("call_status", {})
                            extracted_specs = extraction_bundle.get("extracted_specs", {})
                            transcript = str(call_status.get("transcript") or "")
                            st.caption(f"Call status: {call_status.get('provider_status') or call_status.get('status') or 'unknown'}")
                            recording_url = str(call_status.get("recording_url") or "")
                            if recording_url:
                                st.caption(f"Recording: {recording_url}")
                            if transcript:
                                with st.expander("Transcript preview"):
                                    st.text(transcript[:2500])
                            else:
                                st.info("Transcript is not available yet. Check again after the call ends.")
                            extracted_fields = extracted_specs.get("extracted_fields", {})
                            if extracted_fields:
                                display_rows = [
                                    {
                                        "Field": field,
                                        "Suggested Value": detail.get("value"),
                                        "Confidence": detail.get("confidence"),
                                        "Evidence": detail.get("evidence"),
                                    }
                                    for field, detail in extracted_fields.items()
                                    if isinstance(detail, dict)
                                ]
                                st.caption("Extracted values for review")
                                st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)
                                if st.button(
                                    "Apply Extracted Info to Product Row",
                                    key=f"vendor_call_apply_{call_key}",
                                    use_container_width=True,
                                ):
                                    updated_df, applied, skipped = apply_extracted_specs_to_review_table(
                                        row_idx,
                                        extracted_fields,
                                        overwrite=overwrite_existing,
                                    )
                                    st.session_state.intake_df = updated_df
                                    if applied:
                                        st.success(f"Updated {', '.join(applied)} from vendor call.")
                                    if skipped:
                                        st.caption(f"Skipped existing or unsupported fields: {', '.join(skipped)}")
                                    st.rerun()
                            unresolved = extracted_specs.get("unresolved_fields", [])
                            if unresolved:
                                st.caption(f"Unresolved fields: {', '.join(map(str, unresolved))}")
                            with st.expander("Call result diagnostics"):
                                st.json(
                                    make_json_safe(
                                        {
                                            "call_id": call_id,
                                            "call_status": call_status.get("status"),
                                            "transcript_found": bool(transcript),
                                            "extracted_fields_count": len(extracted_fields or {}),
                                            "unresolved_fields": unresolved,
                                            "provider": call_status.get("provider", result.get("provider")),
                                        }
                                    )
                                )

                    if provider_response or debug_info:
                        with st.expander("Provider diagnostics"):
                            if debug_info:
                                st.caption("Endpoint")
                                st.code(str(debug_info.get("endpoint", "")))
                                st.caption("Auth header name used")
                                st.code(str(debug_info.get("auth_header_name", "")))
                                st.caption("Sanitized headers")
                                st.json(make_json_safe(debug_info.get("headers", {})))
                                st.caption("Request body keys")
                                st.json(make_json_safe(debug_info.get("request_body_keys") or debug_info.get("minimal_payload_fields") or []))
                                provider_label = str(debug_info.get("provider") or result.get("provider") or "provider").title()
                                st.caption(f"Exact JSON body sent to {provider_label}")
                                st.json(make_json_safe(debug_info.get("request_body", {})))
                                attempts = debug_info.get("attempts") or []
                                if attempts:
                                    st.caption("Auth/header attempts")
                                    st.json(make_json_safe(attempts))
                                st.caption("Response status")
                                st.write(debug_info.get("response_status_code", "No HTTP status returned"))
                                st.caption(f"Exact response body from {provider_label}")
                                response_text = str(debug_info.get("response_text") or "")
                                st.code(response_text or "(empty)", language="json")
                                st.caption("Parsed response")
                                st.json(make_json_safe(debug_info.get("response_body", {})))
                                provider_config = (
                                    debug_info.get("provider_config")
                                    or debug_info.get("resolved_agent_fields", {}).get("provider_config")
                                    or result.get("provider_config")
                                )
                                if provider_config:
                                    st.caption("Resolved provider config")
                                    if provider_label.lower() == "retell":
                                        st.markdown(
                                            "\n".join(
                                                [
                                                    "- Provider: `retell`",
                                                    f"- Agent ID used: `{debug_info.get('agent_id_used') or result.get('agent_id') or 'None'}`",
                                                    f"- Phone number used: `{debug_info.get('phone_number_used') or 'None'}`",
                                                    f"- From number used: `{debug_info.get('from_number_used') or 'None'}`",
                                                    f"- Call ID: `{result.get('call_id') or debug_info.get('call_id') or 'None'}`",
                                                ]
                                            )
                                        )
                                    else:
                                        st.markdown(
                                            "\n".join(
                                                [
                                                    f"- Calling from: `{provider_config.get('from') or 'Bland default'}`",
                                                    f"- Requested agent: `{provider_config.get('requested_agent_name') or provider_config.get('agent_name') or 'None'}`",
                                                    f"- Resolved persona ID: `{provider_config.get('resolved_persona_id') or provider_config.get('persona_id') or 'None'}`",
                                                    f"- Resolved voice: `{provider_config.get('resolved_voice') or provider_config.get('voice') or 'Bland default'}`",
                                                    f"- Voice override enabled: `{provider_config.get('voice_override_enabled')}`",
                                                    f"- Voice field sent: `{debug_info.get('voice_field_sent', bool((debug_info.get('request_body') or {}).get('voice')))} `",
                                                    f"- Voice ID used: `{debug_info.get('voice_id_used') or (debug_info.get('request_body') or {}).get('voice') or 'None'}`",
                                                    f"- Persona ID sent: `{debug_info.get('persona_id_sent', bool((debug_info.get('request_body') or {}).get('persona_id')))} `",
                                                    f"- Call ID: `{result.get('call_id') or 'None'}`",
                                                    f"- Fallback used: `{debug_info.get('fallback_used', False)}`",
                                                    f"- Persona publication: `{(provider_config.get('persona_publication') or {}).get('status') or 'unknown'}`",
                                                    f"- Alley applied: `{'yes' if provider_config.get('resolved_persona_id') and not provider_config.get('default_bland_agent_used') else 'no'}`",
                                                ]
                                            )
                                        )
                                    st.json(make_json_safe(provider_config))
                                    if provider_label.lower() != "retell":
                                        publication = provider_config.get("persona_publication") or {}
                                        if publication.get("draft_changes_pending"):
                                            st.warning("Promote Alley to Production in Bland. Draft changes are not guaranteed to affect API calls until promoted.")
                                        if provider_config.get("default_bland_agent_used"):
                                            st.warning("Using Bland default agent/voice because no persona_id, pathway_id, or voice is configured.")
                                        elif provider_config.get("resolved_persona_id"):
                                            st.success(f"Using Bland persona: {provider_config.get('resolved_persona_id')}")
                                        if provider_config.get("caller_number_sent"):
                                            st.caption(f"Calling from: {provider_config.get('from')}")
                                if debug_info.get("configured_agent_rejected"):
                                    st.warning("Alley agent not accepted by provider, used default Bland voice.")
                                    st.caption("Configured agent attempt")
                                    st.json(make_json_safe(debug_info.get("configured_agent_attempt", {})))
                                if debug_info.get("configured_voice_rejected"):
                                    st.warning("Bland rejected the explicit voice override, so the call used the Alley persona without the voice field.")
                                    st.caption("Configured voice attempt")
                                    st.json(make_json_safe(debug_info.get("configured_voice_attempt", {})))
                                if debug_info.get("rejected_attempts"):
                                    st.caption("Rejected Bland payload attempts")
                                    st.json(make_json_safe(debug_info.get("rejected_attempts", [])))
                                st.caption(
                                    "If direct voice override still does not sound like Alley, next step is to create a Bland Pathway "
                                    "using the Alley persona/voice and call with BLAND_USE_PATHWAY=true + BLAND_PATHWAY_ID."
                                )
                            elif provider_response:
                                st.caption("Provider response")
                                st.json(make_json_safe(provider_response))

        review_updates: dict[int, dict[str, str]] = {}

        for idx, row in _needs_review_rows.iterrows():
            missing_fields = _missing_required_fields(row)
            with st.container(border=True):
                meta_col, input_col = st.columns([2, 3])
                with meta_col:
                    _pname = str(row.get("Product Name", "") or "").strip()
                    _brand = str(row.get("Brand", "") or "").strip()
                    _sku   = str(row.get("Model/SKU", "") or "").strip()
                    _ident_parts = [p for p in [_brand, _sku] if p]
                    st.markdown(
                        f"**{_pname or 'Unnamed item'}**"
                        + (f"  \n{'  ·  '.join(_ident_parts)}" if _ident_parts else "")
                    )
                    _current_dim = str(row.get("Dimensions") or "").strip()
                    if "Dimensions" in missing_fields and _current_dim:
                        st.caption(f"Current: {_current_dim}")
                with input_col:
                    review_updates[idx] = {}
                    _can_vendor_lookup = bool(_brand and _sku)
                    for field in missing_fields:
                        field_input_col, phone_col = st.columns([12, 1])
                        with field_input_col:
                            entered = st.text_input(
                                _FIELD_LABELS[field],
                                key=f"nr_{field}_{idx}",
                                placeholder=_FIELD_PLACEHOLDERS[field],
                                label_visibility="collapsed",
                            )
                        with phone_col:
                            st.markdown("<div style='height:0.12rem'></div>", unsafe_allow_html=True)
                            if st.button(
                                "☎",
                                key=f"vendor_call_open_{field}_{idx}",
                                help=f"Call vendor for {_FIELD_LABELS[field]}",
                                use_container_width=True,
                                disabled=not _can_vendor_lookup,
                            ):
                                panel_key = f"{idx}_{field}"
                                current_panel = st.session_state.get("vendor_call_panel")
                                st.session_state.vendor_call_panel = (
                                    None if current_panel == panel_key else panel_key
                                )
                        if entered.strip():
                            review_updates[idx][field] = entered.strip()
                        if st.session_state.get("vendor_call_panel") == f"{idx}_{field}":
                            _render_vendor_call_panel(idx, row, field)

        save_col, _ = st.columns([2, 8])
        with save_col:
            save_review = st.button(
                "Save Review Updates",
                type="secondary",
                use_container_width=True,
            )

        if save_review:
            _df_copy = st.session_state.intake_df.copy()
            for idx, field_map in review_updates.items():
                for field, val in field_map.items():
                    if field == "Quantity":
                        try:
                            _df_copy.at[idx, field] = int(float(val))
                        except (ValueError, TypeError):
                            _df_copy.at[idx, field] = val
                    else:
                        _df_copy.at[idx, field] = val
            st.session_state.intake_df = apply_confidence_checks(_df_copy)
            st.rerun()

    # Show any previous category AI error
    if st.session_state.get("cat_ai_error"):
        st.error(st.session_state.cat_ai_error, icon="❌")

    # ── Compute category/enrichment state (needed by Advanced Tools below) ─────
    _included_mask = edited_df.get("Include", pd.Series([True] * len(edited_df))) == True
    if "Product Category" in edited_df.columns:
        _blank_cat_mask = (
            _included_mask
            & (
                edited_df["Product Category"].isna()
                | (edited_df["Product Category"].str.strip() == "")
            )
        )
    else:
        _blank_cat_mask = pd.Series([False] * len(edited_df))

    _blank_cat_indices = list(edited_df[_blank_cat_mask].index)
    _blank_cat_n = len(_blank_cat_indices)

    # ── Advanced Tools ─────────────────────────────────────────────────────────
    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
    with st.expander("Advanced Tools", expanded=False):
        # Clear Intake
        st.markdown("**Reset**")
        clear_col, _ = st.columns([2, 8])
        with clear_col:
            clear = st.button("Clear Intake", type="secondary", use_container_width=True)
        if clear:
            st.session_state.intake_df = None
            st.session_state.automation_results = None
            st.session_state.ai_errors = []
            st.session_state.pending_enrichment = False
            st.session_state.enrichment_errors = []
            st.rerun()

        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

        # Re-run Enrichment
        st.markdown("**Enrichment**")
        if not _BRAVE_API_KEY:
            st.caption("Product enrichment requires BRAVE_API_KEY — add it to .env and restart.")
        if not st.session_state.get("use_web_enrichment", True):
            st.caption("Web search enrichment is turned off. Uploaded documents and manual input are still available.")
        enrich_col, _ = st.columns([3, 7])
        with enrich_col:
            enrich_rerun_clicked = st.button(
                "Re-run Enrichment",
                type="secondary",
                use_container_width=True,
                disabled=not _BRAVE_API_KEY or not st.session_state.get("use_web_enrichment", True),
                help="Re-search manufacturer sources for rows still missing product details.",
            )
        if enrich_rerun_clicked and _BRAVE_API_KEY and st.session_state.get("use_web_enrichment", True):
            st.session_state.pending_enrichment = True
            st.rerun()

        # ── Enrichment debug ───────────────────────────────────────────────────
        debug_col, _ = st.columns([3, 7])
        with debug_col:
            debug_enrich_clicked = st.button(
                "Debug Enrichment (dimensions)",
                type="secondary",
                use_container_width=True,
                disabled=not _BRAVE_API_KEY,
                help="Traces every step of enrichment for each row and saves results to data/enrichment_debug/.",
            )
        if debug_enrich_clicked:
            with st.spinner("Running enrichment diagnostics — this may take a minute…"):
                _debug_traces = debug_enrich_dataframe(edited_df)
                _debug_path   = save_debug_report(_debug_traces)
            st.success(f"Debug report saved to {_debug_path}")
            _qualifying = [t for t in _debug_traces if t.get("qualifies")]
            if not _qualifying:
                st.warning("No rows qualified for enrichment — check Brand and Model/SKU columns.")
            for _t in _debug_traces:
                _label = _t.get("product_name") or _t.get("brand") or _t.get("model_sku") or "—"
                with st.expander(f"{'OK' if not _t.get('failure_reason') else 'FAIL'} — {_label}", expanded=bool(_t.get("failure_reason"))):
                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.markdown(f"**Brand:** {_t.get('brand') or '—'}")
                        st.markdown(f"**Model/SKU:** {_t.get('model_sku') or '—'}")
                        st.markdown(f"**Qualifies:** {_t.get('qualifies')}")
                        st.markdown(f"**BRAVE_API_KEY loaded:** {_t.get('brave_api_key_loaded')}")
                        st.markdown(f"**ANTHROPIC_API_KEY loaded:** {_t.get('anthropic_api_key_loaded')}")
                        st.markdown(f"**Search query:** `{_t.get('search_query') or '—'}`")
                        st.markdown(f"**Selected URL:** {_t.get('selected_url') or '—'}")
                        st.markdown(f"**Selected score:** {_t.get('selected_score', '—')}")
                    with col_b:
                        st.markdown(f"**Fetch success:** {_t.get('fetch_success')}")
                        st.markdown(f"**Page text length:** {_t.get('page_text_length', 0):,} chars")
                        st.markdown(f"**Dimension terms found:** {_t.get('dimension_terms_found')}")
                        st.markdown(f"**Claude asked for dims:** {_t.get('claude_prompt_includes_dimensions')}")
                        st.markdown(f"**Claude raw dims:** `{_t.get('claude_raw_dimensions') or '(empty)'}`")
                        st.markdown(f"**Apply accepted:** {_t.get('apply_accepted_dimensions')}")
                        st.markdown(f"**Final dimensions:** `{_t.get('final_dimensions') or '(blank)'}`")
                    if _t.get("failure_reason"):
                        st.error(f"Failure reason: {_t['failure_reason']}")
                    _results = _t.get("search_results", [])
                    if _results:
                        st.markdown("**Search results:**")
                        for _r in _results:
                            st.markdown(f"- score={_r['domain_score']} [{_r['title'][:80]}]({_r['url']})")
                    if _t.get("page_text_preview"):
                        st.markdown("**Page text preview (first 500 chars):**")
                        st.code(_t["page_text_preview"], language=None)
                    if _t.get("claude_raw_output"):
                        st.markdown("**Claude raw output:**")
                        st.code(_t["claude_raw_output"], language="json")

        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

        # AI Category suggestion
        st.markdown("**AI Category Suggestions**")
        use_cat_ai = st.checkbox(
            "Use AI to suggest categories",
            value=False,
            key="use_cat_ai",
            help="Sends rows with blank Category to Claude for a single batched suggestion call.",
        )
        _cat_btn_label = (
            f"Suggest Missing Categories ({_blank_cat_n} row{'s' if _blank_cat_n != 1 else ''})"
            if _blank_cat_n > 0 else "No blank categories"
        )
        cat_col, _ = st.columns([3, 7])
        with cat_col:
            cat_clicked = st.button(
                _cat_btn_label,
                type="secondary",
                use_container_width=True,
                disabled=(not use_cat_ai or _blank_cat_n == 0),
            )

        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

        # Programa login
        st.markdown("**Programa Setup**")
        st.caption(
            "First run? Open a Chrome window to log in as "
            "Assistant@saffroncasehomes.com. Session is saved automatically."
        )
        login_col, _ = st.columns([3, 7])
        with login_col:
            open_login = st.button(
                "Open Programa Login Window",
                type="secondary",
                use_container_width=True,
            )

    # Handle Advanced Tool actions that require spinners (outside expander)
    if open_login:
        with st.spinner("Chrome is open — log in, then close the browser window to continue."):
            try:
                status_msg = open_programa_login_window()
                st.success(status_msg)
            except Exception as exc:
                st.error(f"Could not open Chrome: {exc}")

    if cat_clicked and use_cat_ai and _blank_cat_n > 0:
        _blank_rows = edited_df.loc[_blank_cat_indices].to_dict("records")
        with st.spinner(f"Suggesting categories for {_blank_cat_n} row{'s' if _blank_cat_n != 1 else ''}…"):
            _suggestions, _cat_error = suggest_categories_batch(_blank_rows, _blank_cat_indices)
        if _cat_error:
            st.session_state.cat_ai_error = _cat_error
            st.rerun()
        else:
            st.session_state.cat_ai_error = None
            _updated_df = edited_df.copy()
            for _row_idx, _suggestion in _suggestions.items():
                _updated_df.at[_row_idx, "Product Category"] = _suggestion["category"]
                _updated_df.at[_row_idx, "AI Category Confidence"] = _suggestion["confidence"]
                _updated_df.at[_row_idx, "Category Source"] = "AI Suggested"
                if _suggestion["confidence"] < 75:
                    _updated_df.at[_row_idx, "Review Required"] = True
                    _updated_df.at[_row_idx, "Suggested Action"] = "Review AI category suggestion"
            st.session_state.intake_df = apply_confidence_checks(_updated_df)
            st.rerun()

    # ── Footer ─────────────────────────────────────────────────────────────────
    st.markdown("<div style='height:2.5rem'></div>", unsafe_allow_html=True)
    st.markdown(
        '<div style="text-align:center;font-size:0.58rem;color:#C0B4A8;letter-spacing:0.16em;">'
        "SAFFRON CASE HOMES &nbsp;·&nbsp; DESIGNOPS INTAKE v0.4 &nbsp;·&nbsp; INTERNAL USE ONLY"
        "</div>",
        unsafe_allow_html=True,
    )
