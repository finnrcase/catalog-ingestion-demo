import os
import streamlit as st
import pandas as pd

from src.styling import inject_css, section_label, PAGE_TITLE_HTML
from src.intake_schema import CATEGORIES, STATUSES
from src.intake import (
    COLUMNS,
    build_intake_dataframe,
    create_manual_row,
    create_pdf_rows,
    create_url_rows,
)
from src.export import get_csv_bytes
from src.programa_automation import open_programa_login_window, run_programa_automation
from src.confidence import apply_confidence_checks
from src.ai_extraction import extract_products_from_pdf_with_ai
from src.document_parser import parse_pdf_rows
from src.category_ai import suggest_categories_batch
from src.product_enrichment import enrich_dataframe, has_complete_3d_dimensions
from src.brave_search import BRAVE_API_KEY as _BRAVE_API_KEY

LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "logo.png")

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

# ── Programa Destination ───────────────────────────────────────────────────────
with st.container(border=True):
    section_label("Programa Destination")
    st.caption(
        "Enter the exact existing Programa project name. "
        "This tool only adds items to existing projects and never creates projects."
    )
    dest_col1, dest_col2 = st.columns([3, 2])

    with dest_col1:
        selected_project = st.text_input(
            "Existing Programa Project / Property",
            placeholder="Type exact Programa project name, e.g. 1 Lily Pond Ln",
        )
    with dest_col2:
        room_options = [
            "", "Living Room", "Master Bedroom", "Guest Bedroom", "Dining Room",
            "Kitchen", "Bathroom", "Office / Study", "Entryway / Foyer",
            "Outdoor / Terrace", "Other",
        ]
        room = st.selectbox("Default Room / Location", options=room_options)

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
            value=False,
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

st.markdown("<div style='height:1.25rem'></div>", unsafe_allow_html=True)

# ── Manual Product Entry ───────────────────────────────────────────────────────
with st.container(border=True):
    section_label("Manual Product Entry")
    st.caption(
        "Enter a product directly. Leave fields blank if unknown — "
        "providing a Serial / Model Number lets the system suggest missing details later."
    )

    with st.form("manual_entry_form", clear_on_submit=True):
        # Row 1: Core identity
        r1c1, r1c2, r1c3, r1c4 = st.columns([3, 2, 2, 2])
        m_name       = r1c1.text_input("Name of Product", placeholder="e.g. Wolf Microwave")
        m_brand      = r1c2.text_input("Brand", placeholder="e.g. Wolf")
        m_dimensions = r1c3.text_input("Dimensions", placeholder='e.g. 30"W × 18"D')
        m_finish     = r1c4.text_input("Finish / Color", placeholder="e.g. Matte Black")

        # Row 2: Classification, serial, quantity, supplier
        r2c1, r2c2, r2c3, r2c4 = st.columns([2, 2, 1, 2])
        m_serial   = r2c1.text_input("Serial / Model Number", placeholder="e.g. MDD30TS")
        m_category = r2c2.selectbox("Category", options=[""] + CATEGORIES)
        m_qty      = r2c3.number_input("Qty", min_value=1, value=1, step=1)
        m_supplier = r2c4.text_input(
            "Who We Bought It From", placeholder="e.g. RH, Article, Custom"
        )

        # Row 3: Location, URL, notes
        r3c1, r3c2, r3c3 = st.columns([2, 3, 3])
        m_location = r3c1.text_input("Location", placeholder="e.g. Kitchen", value=room)
        m_url      = r3c2.text_input("Product URL", placeholder="https://...")
        m_notes    = r3c3.text_input("Notes", placeholder="Any additional context")

        add_manual = st.form_submit_button(
            "Add Manual Item to Intake Table",
            type="primary",
            use_container_width=True,
        )

    if add_manual:
        new_row = create_manual_row(
            project=selected_project,
            room=m_location or room,
            supplier=m_supplier or "",
            notes=m_notes,
            product_name=m_name,
            brand=m_brand,
            dimensions=m_dimensions,
            finish_color=m_finish,
            model_sku=m_serial,
            category=m_category,
            quantity=int(m_qty),
            product_url=m_url,
        )
        new_df = pd.DataFrame([new_row])

        if st.session_state.intake_df is not None:
            combined = pd.concat(
                [st.session_state.intake_df, new_df], ignore_index=True
            )
        else:
            combined = new_df

        st.session_state.intake_df = apply_confidence_checks(combined)
        st.session_state.pending_enrichment = True
        st.rerun()

st.markdown("<div style='height:1.25rem'></div>", unsafe_allow_html=True)

# ── Intake action buttons (PDF + URL path) ─────────────────────────────────────
btn_col, _, clear_col = st.columns([3, 5, 2])

with btn_col:
    generate = st.button("Generate Intake Table", type="primary", use_container_width=True)
with clear_col:
    clear = st.button("Clear Intake", type="secondary", use_container_width=True)

if clear:
    st.session_state.intake_df = None
    st.session_state.automation_results = None
    st.session_state.ai_errors = []
    st.session_state.pending_enrichment = False
    st.session_state.enrichment_errors = []
    st.rerun()

if generate:
    raw_urls = [u.strip() for u in (url_input or "").splitlines() if u.strip()]
    url_rows = create_url_rows(raw_urls, selected_project, room, "", "")
    st.session_state.ai_errors = []

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

    # ── Automatic enrichment pass ──────────────────────────────────────────────
    if st.session_state.pending_enrichment:
        if _BRAVE_API_KEY:
            with st.spinner("Searching manufacturer sources to fill missing product details…"):
                _enriched_df, _enrich_errors = enrich_dataframe(df)
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

    if has_confidence:
        ignored_mask = df.get("Include", pd.Series([True] * total_n)) == False
        review_mask  = (df["Review Required"] == True) & ~ignored_mask
        ready_mask   = (df["Review Required"] == False) & ~ignored_mask

        ignored_n = int(ignored_mask.sum())
        review_n  = int(review_mask.sum())
        ready_n   = int(ready_mask.sum())
        non_ignored_scores = df.loc[~ignored_mask, "Confidence Score"]
        avg_conf = round(non_ignored_scores.mean()) if not non_ignored_scores.empty else 0
    else:
        ignored_n = review_n = 0
        ready_n   = total_n
        avg_conf  = 0

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

    if "Dimensions" in df.columns:
        _included = df.get("Include", pd.Series([True] * len(df))) == True
        _incomplete_dims = _included & df["Dimensions"].apply(
            lambda v: not has_complete_3d_dimensions(str(v or ""))
        )
        _complete_dims = _included & ~_incomplete_dims
        _n_incomplete = int(_incomplete_dims.sum())
        _n_complete = int(_complete_dims.sum())
        st.caption(
            f"Rows with incomplete dimensions: {_n_incomplete} · "
            f"Rows with complete dimensions: {_n_complete}"
        )

    edited_df = st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
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
        },
    )

    st.session_state.intake_df = edited_df

    if rerun_check:
        st.session_state.intake_df = apply_confidence_checks(edited_df)
        st.rerun()

    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

    # ── Export CSV ─────────────────────────────────────────────────────────────
    export_col, _, __ = st.columns([2, 6, 2])
    with export_col:
        included = (
            edited_df[edited_df["Include"] == True]
            if "Include" in edited_df.columns else edited_df
        )
        safe_name = selected_project.strip().replace(" ", "_") or "intake"
        st.download_button(
            label="Export Review CSV",
            data=get_csv_bytes(included),
            file_name=f"{safe_name}_intake.csv",
            mime="text/csv",
            use_container_width=True,
        )

    # ── Missing Dimensions ─────────────────────────────────────────────────────
    if "Dimensions" in edited_df.columns and "Include" in edited_df.columns:
        _inc_mask = edited_df["Include"] == True
        _dim_incomplete_mask = edited_df["Dimensions"].apply(
            lambda v: not has_complete_3d_dimensions(str(v or ""))
        )
        _missing_dim_df = edited_df[_inc_mask & _dim_incomplete_mask]

        if not _missing_dim_df.empty:
            st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
            st.divider()
            section_label("Missing Dimensions")
            st.caption(
                "These items need full width, height, and depth before they can be sent to Programa."
            )

            dim_updates: dict[int, str] = {}
            for idx, row in _missing_dim_df.iterrows():
                with st.container(border=True):
                    meta_col, input_col = st.columns([3, 2])
                    with meta_col:
                        st.markdown(
                            f"**{row.get('Product Name', '') or '—'}**  \n"
                            f"{row.get('Brand', '') or ''}  ·  {row.get('Model/SKU', '') or ''}"
                        )
                        current_dim = str(row.get("Dimensions") or "").strip()
                        if current_dim:
                            st.caption(f"Current: {current_dim}")
                    with input_col:
                        new_val = st.text_input(
                            "Enter Full Dimensions",
                            key=f"dim_input_{idx}",
                            placeholder='36"W × 34.5"H × 24"D',
                            label_visibility="collapsed",
                        )
                        if new_val:
                            dim_updates[idx] = new_val

            save_col, helper_col = st.columns([2, 8])
            with save_col:
                save_dims = st.button(
                    "Save Dimension Updates",
                    type="secondary",
                    use_container_width=True,
                )
            with helper_col:
                st.caption('Enter as W × H × D — for example: 36"W × 34.5"H × 24"D')

            if save_dims and dim_updates:
                _df_copy = st.session_state.intake_df.copy()
                for idx, val in dim_updates.items():
                    if has_complete_3d_dimensions(val):
                        _df_copy.at[idx, "Dimensions"] = val
                st.session_state.intake_df = apply_confidence_checks(_df_copy)
                st.rerun()

    # ── AI-Assisted Cleanup ────────────────────────────────────────────────────
    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
    st.divider()
    section_label("AI-Assisted Cleanup")

    if not _BRAVE_API_KEY:
        st.caption(
            "Product enrichment requires BRAVE_API_KEY — add it to .env and restart the app."
        )

    # Show any previous category AI error
    if st.session_state.get("cat_ai_error"):
        st.error(st.session_state.cat_ai_error, icon="❌")

    # ── Category suggestion ────────────────────────────────────────────────────
    use_cat_ai = st.checkbox(
        "Use AI to suggest categories",
        value=False,
        key="use_cat_ai",
        help="Sends rows with blank Category to Claude for a single batched suggestion call.",
    )

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

    _cat_btn_label = (
        f"Suggest Missing Categories ({_blank_cat_n} row{'s' if _blank_cat_n != 1 else ''})"
        if _blank_cat_n > 0 else "No blank categories"
    )
    cat_col, _, __ = st.columns([2, 6, 2])
    with cat_col:
        cat_clicked = st.button(
            _cat_btn_label,
            type="secondary",
            use_container_width=True,
            disabled=(not use_cat_ai or _blank_cat_n == 0),
        )
    enrich_col, _, __ = st.columns([2, 6, 2])
    with enrich_col:
        enrich_rerun_clicked = st.button(
            "Re-run Enrichment for Needs Enrichment Rows",
            type="secondary",
            use_container_width=True,
            disabled=not _BRAVE_API_KEY,
            help="Re-search manufacturer sources for rows still missing product details.",
        )
    if enrich_rerun_clicked and _BRAVE_API_KEY:
        st.session_state.pending_enrichment = True
        st.rerun()

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

    # ── Uncertain rows cleanup ─────────────────────────────────────────────────
    uncertain_rows = (
        edited_df[(edited_df["Review Required"] == True) & (edited_df["Include"] == True)]
        if "Review Required" in edited_df.columns
        else pd.DataFrame()
    )
    uncertain_n = len(uncertain_rows)

    ai_label = (
        f"Use AI to clean {uncertain_n} uncertain row{'s' if uncertain_n != 1 else ''}"
        if uncertain_n > 0 else "No uncertain rows"
    )
    ai_col, _, __ = st.columns([2, 6, 2])
    with ai_col:
        ai_clicked = st.button(
            ai_label, type="secondary", use_container_width=True,
            disabled=(uncertain_n == 0),
        )
    if ai_clicked:
        st.info("AI cleanup will be added in a future version.", icon="ℹ️")

    # ── Programa Automation ────────────────────────────────────────────────────
    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
    st.divider()
    section_label("Programa Automation")

    # Eligibility: Include=True, not flagged for review, not in terminal statuses,
    # has Product Name, Quantity ≥ 1, Product Category, and complete 3D dimensions.
    _BLOCKED_STATUSES = {"Ignored", "Excluded", "Error"}

    def _is_eligible(row: pd.Series) -> bool:
        if not row.get("Include", False):
            return False
        if row.get("Review Required", False):
            return False
        if str(row.get("Status", "")) in _BLOCKED_STATUSES:
            return False
        if not str(row.get("Product Name", "") or "").strip():
            return False
        try:
            if int(row.get("Quantity", 0) or 0) < 1:
                return False
        except (ValueError, TypeError):
            return False
        if not str(row.get("Product Category", "") or "").strip():
            return False
        if not has_complete_3d_dimensions(str(row.get("Dimensions", "") or "")):
            return False
        return True

    eligible_df = edited_df[edited_df.apply(_is_eligible, axis=1)].copy()

    def _is_url_row(row: pd.Series) -> bool:
        return (
            str(row.get("Source Type", "")) == "URL"
            and bool(str(row.get("Product URL", "") or "").strip())
        )

    url_sendable = eligible_df[eligible_df.apply(_is_url_row, axis=1)].copy()
    schedule_sendable = eligible_df[~eligible_df.apply(_is_url_row, axis=1)].copy()
    total_sendable = len(eligible_df)

    # Blocked included rows: Include=True but failed eligibility
    _included_df = edited_df[edited_df.get("Include", pd.Series([True] * len(edited_df))) == True]
    _blocked_df = _included_df[~_included_df.apply(_is_eligible, axis=1)]

    if not _blocked_df.empty:
        st.markdown("<div style='height:0.25rem'></div>", unsafe_allow_html=True)
        _n_dim = int(_blocked_df["Dimensions"].apply(
            lambda v: not has_complete_3d_dimensions(str(v or ""))
        ).sum()) if "Dimensions" in _blocked_df.columns else 0
        _n_review = int((_blocked_df.get("Review Required", pd.Series([])) == True).sum())
        if _n_dim > 0:
            st.warning(
                f"{_n_dim} item{'s' if _n_dim != 1 else ''} need dimensions before they can be sent.",
                icon="⚠️",
            )
        if _n_review > 0:
            st.warning(
                f"{_n_review} item{'s' if _n_review != 1 else ''} still need review before they can be sent.",
                icon="⚠️",
            )
        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

    # ── Login / setup ──────────────────────────────────────────────────────────
    with st.container(border=True):
        section_label("First-time Setup")
        st.caption(
            "First run? Open a Chrome window to log in as "
            "Assistant@saffroncasehomes.com. Close the window when done — "
            "your session is saved automatically for future runs."
        )
        login_col, _, __ = st.columns([2, 5, 3])
        with login_col:
            open_login = st.button(
                "Open Programa Login Window",
                type="secondary",
                use_container_width=True,
            )

    if open_login:
        with st.spinner("Chrome is open — log in, then close the browser window to continue."):
            try:
                status_msg = open_programa_login_window()
                st.success(status_msg)
            except Exception as exc:
                st.error(f"Could not open Chrome: {exc}")

    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

    # ── Automation controls ────────────────────────────────────────────────────
    auto_col, send_col, _ = st.columns([3, 3, 4])
    with auto_col:
        auto_done = st.checkbox(
            "Auto-click Done after filling each item",
            value=False,
            help="When unchecked (default), the browser pauses after each item so you can review before saving.",
        )
        st.caption(
            "For safety, keep Auto-click Done off during testing. "
            "The automation will pause before final submission."
        )
    with send_col:
        send_label = (
            f"Send {total_sendable} item{'s' if total_sendable != 1 else ''} to Programa"
            if total_sendable > 0 else "No eligible items"
        )
        no_project = not selected_project.strip()
        send_to_programa = st.button(
            send_label,
            type="primary",
            use_container_width=True,
            disabled=(total_sendable == 0 or no_project),
        )

    if no_project:
        st.warning("Enter a Programa project name above before sending.")
    elif total_sendable == 0 and _blocked_df.empty:
        st.warning(
            "No eligible items — check that rows are included, have complete dimensions, "
            "and are not flagged for review."
        )

    if send_to_programa:
        rows_payload = eligible_df.to_dict("records")
        st.session_state.nav_failed = False
        st.session_state.nav_pending_rows = []
        with st.spinner(
            f"Chrome is open — automatically navigating to project '{selected_project}', "
            "then adding items. Follow any on-screen prompts in the browser."
        ):
            try:
                log_entries, log_path = run_programa_automation(
                    rows=rows_payload,
                    selected_project=selected_project,
                    auto_done=auto_done,
                    skip_navigation=False,
                )
                st.session_state.automation_results = {
                    "entries": log_entries,
                    "log_path": log_path,
                }
                if any(e["status"] == "nav_failed" for e in log_entries):
                    st.session_state.nav_failed = True
                    st.session_state.nav_pending_rows = rows_payload
            except Exception as exc:
                from src.automation_logs import make_log_entry
                st.session_state.automation_results = {
                    "entries": [make_log_entry(
                        "", "error",
                        f"Unhandled exception: {exc} — check that Chrome and Playwright are installed.",
                    )],
                    "log_path": "",
                }

    # ── Navigation failure — manual continue flow ──────────────────────────────
    if st.session_state.nav_failed:
        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
        st.warning(
            f"Project **{selected_project}** was not found automatically. "
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
            with st.spinner(
                f"Chrome is open — navigate to '{selected_project}' in Programa, "
                "click OK in the browser dialog, then wait for items to be added."
            ):
                try:
                    log_entries, log_path = run_programa_automation(
                        rows=pending_rows,
                        selected_project=selected_project,
                        auto_done=auto_done,
                        skip_navigation=True,
                    )
                    st.session_state.automation_results = {
                        "entries": log_entries,
                        "log_path": log_path,
                    }
                    st.session_state.nav_pending_rows = []
                except Exception as exc:
                    from src.automation_logs import make_log_entry
                    st.session_state.automation_results = {
                        "entries": [make_log_entry(
                            "", "error",
                            f"Unhandled exception during manual-continue run: {exc}",
                        )],
                        "log_path": "",
                    }

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
                icon="✓",
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

    # ── Footer ─────────────────────────────────────────────────────────────────
    st.markdown("<div style='height:2.5rem'></div>", unsafe_allow_html=True)
    st.markdown(
        '<div style="text-align:center;font-size:0.58rem;color:#C0B4A8;letter-spacing:0.16em;">'
        "SAFFRON CASE HOMES &nbsp;·&nbsp; DESIGNOPS INTAKE v0.4 &nbsp;·&nbsp; INTERNAL USE ONLY"
        "</div>",
        unsafe_allow_html=True,
    )
