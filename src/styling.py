import streamlit as st

# Brand palette
# Page bg: #F8F6F1  Card bg: #FFFFFF  Dark text: #2C2118  Mid text: #7A7068
# Muted text: #A8998A  Primary brown: #7A5438  Border: #E6E0D6

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,500;0,600;1,400&family=Inter:wght@300;400;500;600&display=swap');

/* ── Override Streamlit's theme CSS variables at the root ──────────────────
   Streamlit renders most of its own elements using var(--text-color) etc.
   Setting these here propagates colour fixes into elements we can't
   otherwise reach without specificity wars.                                */
:root {
    --text-color:                   #2C2118 !important;
    --font:                         'Inter', sans-serif !important;
    --background-color:             #F8F6F1 !important;
    --secondary-background-color:   #FFFFFF !important;
    --primary-color:                #7A5438 !important;
}

/* ── Base ──────────────────────────────────────────────────────────────── */
html, body {
    font-family: 'Inter', sans-serif;
    color: #2C2118;
    background-color: #F8F6F1;
}
.stApp {
    background-color: #F8F6F1;
    color: #2C2118;
}
.main .block-container {
    padding-top: 0 !important;
    padding-bottom: 3rem !important;
    max-width: 1160px !important;
}

/* ── Blanket text-colour reset ─────────────────────────────────────────────
   Covers all common Streamlit text-bearing elements. !important ensures
   this beats Streamlit's own colour rules when the OS is in dark-mode.   */
.stApp p,
.stApp span,
.stApp div,
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
.stMarkdown,
.stMarkdown p,
.stMarkdown span,
.stMarkdown li,
.element-container,
.element-container p,
.stText,
.stText p,
label,
.stWidgetLabel,
.stWidgetLabel p,
[data-testid="stWidgetLabel"] p,
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] span,
[data-testid="stMarkdownContainer"] li {
    color: #2C2118 !important;
}

/* ── Links ─────────────────────────────────────────────────────────────── */
.stApp a { color: #7A5438 !important; }
.stApp a:hover { color: #8E6245 !important; }

/* ── Sidebar ───────────────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background-color: #F8F6F1;
}
[data-testid="stSidebar"] * {
    color: #2C2118 !important;
}

/* ── Hide Streamlit chrome ─────────────────────────────────────────────── */
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }
header    { visibility: hidden; }

/* ── Header strip ──────────────────────────────────────────────────────── */
.sch-header-strip {
    border-bottom: 1.5px solid #D8CFC4;
    padding: 1.2rem 0 1rem 0;
    margin-bottom: 1.75rem;
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
}
.sch-fallback-logo {
    font-family: 'Cormorant Garamond', serif;
    font-size: 1.5rem;
    font-weight: 500;
    letter-spacing: 0.3em;
    color: #7A5438 !important;
    line-height: 1;
}
.sch-fallback-sub {
    font-size: 0.55rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: #B0A090 !important;
    margin-top: 5px;
}
.sch-version {
    font-size: 0.6rem;
    letter-spacing: 0.12em;
    color: #B0A090 !important;
    text-transform: uppercase;
    padding-bottom: 2px;
}

/* ── Page title ────────────────────────────────────────────────────────── */
.sch-page-title {
    font-family: 'Cormorant Garamond', serif;
    font-size: 2rem;
    font-weight: 500;
    color: #2C2118 !important;
    margin-bottom: 0.2rem;
    letter-spacing: 0.02em;
    line-height: 1.2;
}
.sch-page-subtitle {
    font-size: 0.78rem;
    color: #9A8E80 !important;
    letter-spacing: 0.04em;
    margin-bottom: 2rem;
    line-height: 1.65;
}

/* ── Section labels ────────────────────────────────────────────────────── */
.sch-section-label {
    font-size: 0.6rem;
    font-weight: 600;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: #A8998A !important;
    margin-bottom: 0.85rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid #EAE4DC;
}

/* ── Bordered containers ───────────────────────────────────────────────── */
[data-testid="stVerticalBlockBorderWrapper"] {
    background-color: #FFFFFF !important;
    border: 1px solid #E6E0D6 !important;
    border-radius: 8px !important;
    box-shadow: 0 1px 8px rgba(90, 60, 30, 0.05) !important;
}

/* ── Input / widget labels ─────────────────────────────────────────────── */
.stTextInput   label,
.stTextArea    label,
.stSelectbox   label,
.stNumberInput label,
.stCheckbox    label,
.stRadio       label,
.stMultiSelect label,
[data-testid="stWidgetLabel"] {
    font-size: 0.67rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.12em !important;
    text-transform: uppercase !important;
    color: #7A7068 !important;
}

/* ── Text / textarea inputs ────────────────────────────────────────────── */
.stTextInput input,
.stTextArea  textarea {
    background-color: #FDFCF9 !important;
    border: 1px solid #DAD4C8 !important;
    border-radius: 4px !important;
    color: #2C2118 !important;
    font-size: 0.875rem !important;
    font-family: 'Inter', sans-serif !important;
}
.stTextInput input:focus,
.stTextArea  textarea:focus {
    border-color: #7A5438 !important;
    box-shadow: 0 0 0 1px #7A5438 !important;
}

/* Placeholder text */
.stTextInput  input::placeholder,
.stTextArea   textarea::placeholder,
.stNumberInput input::placeholder {
    color: #C0B4A8 !important;
    opacity: 1 !important;
}

/* ── Number input ──────────────────────────────────────────────────────── */
.stNumberInput input {
    background-color: #FDFCF9 !important;
    border: 1px solid #DAD4C8 !important;
    border-radius: 4px !important;
    color: #2C2118 !important;
    font-size: 0.875rem !important;
    font-family: 'Inter', sans-serif !important;
}

/* ── Selectbox ─────────────────────────────────────────────────────────── */
.stSelectbox > div > div {
    background-color: #FDFCF9 !important;
    border: 1px solid #DAD4C8 !important;
    border-radius: 4px !important;
    color: #2C2118 !important;
}
.stSelectbox > div > div > div,
.stSelectbox [data-baseweb="select"] span,
.stSelectbox [data-baseweb="select"] div {
    color: #2C2118 !important;
}

/* Selectbox / multiselect dropdown popup */
[data-baseweb="popover"],
[data-baseweb="popover"] ul {
    background-color: #FDFCF9 !important;
}
[data-baseweb="popover"] li,
[data-baseweb="popover"] li span,
[data-baseweb="popover"] [role="option"],
[data-baseweb="popover"] [role="option"] span {
    color: #2C2118 !important;
    background-color: #FDFCF9 !important;
}
[data-baseweb="popover"] li:hover,
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="popover"] [aria-selected="true"] {
    background-color: #F0EBE3 !important;
    color: #2C2118 !important;
}

/* ── Multiselect ───────────────────────────────────────────────────────── */
.stMultiSelect > div > div {
    background-color: #FDFCF9 !important;
    border: 1px solid #DAD4C8 !important;
    border-radius: 4px !important;
    color: #2C2118 !important;
}
.stMultiSelect [data-baseweb="tag"] {
    background-color: #EDE6DC !important;
}
.stMultiSelect [data-baseweb="tag"] span {
    color: #2C2118 !important;
}

/* ── Checkbox ──────────────────────────────────────────────────────────── */
.stCheckbox label,
.stCheckbox label span,
.stCheckbox label p,
.stCheckbox [data-testid="stWidgetLabel"],
.stCheckbox [data-testid="stWidgetLabel"] p {
    color: #2C2118 !important;
}

/* ── Radio ─────────────────────────────────────────────────────────────── */
.stRadio label,
.stRadio label span,
.stRadio label p,
.stRadio [data-testid="stWidgetLabel"],
.stRadio [data-testid="stWidgetLabel"] p {
    color: #2C2118 !important;
}

/* ── File uploader ─────────────────────────────────────────────────────── */
[data-testid="stFileUploadDropzone"] {
    background-color: #FDFCF9 !important;
    border: 1.5px dashed #C8C0B4 !important;
    border-radius: 6px !important;
    transition: border-color 0.18s ease !important;
    color: #7A7068 !important;
}
[data-testid="stFileUploadDropzone"] p,
[data-testid="stFileUploadDropzone"] span,
[data-testid="stFileUploadDropzone"] small,
[data-testid="stFileUploadDropzone"] button {
    color: #7A7068 !important;
}
[data-testid="stFileUploadDropzone"]:hover {
    border-color: #7A5438 !important;
}
/* Uploaded file pills */
[data-testid="stFileUploaderFile"] {
    background-color: #F8F6F1 !important;
    border: 1px solid #E6E0D6 !important;
    border-radius: 4px !important;
}
[data-testid="stFileUploaderFile"] p,
[data-testid="stFileUploaderFile"] span,
[data-testid="stFileUploaderFile"] small,
[data-testid="stFileUploaderFileName"],
[data-testid="stFileUploaderFileName"] * {
    color: #2C2118 !important;
}

/* ── Buttons ───────────────────────────────────────────────────────────── */
.stButton > button {
    font-family: 'Inter', sans-serif !important;
    font-size: 0.68rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.15em !important;
    text-transform: uppercase !important;
    border-radius: 4px !important;
    padding: 0.58rem 1.6rem !important;
    transition: all 0.18s ease !important;
    color: #2C2118 !important;
}
.stButton > button[kind="primary"],
.stButton > button[data-testid="baseButton-primary"] {
    background-color: #7A5438 !important;
    color: #FAF8F4 !important;
    border: 1px solid #7A5438 !important;
}
.stButton > button[kind="primary"]:hover,
.stButton > button[data-testid="baseButton-primary"]:hover {
    background-color: #8E6245 !important;
    border-color: #8E6245 !important;
    color: #FAF8F4 !important;
}
.stButton > button[kind="secondary"],
.stButton > button[data-testid="baseButton-secondary"] {
    background-color: transparent !important;
    color: #7A7068 !important;
    border: 1px solid #C8C0B4 !important;
}
.stButton > button[kind="secondary"]:hover,
.stButton > button[data-testid="baseButton-secondary"]:hover {
    background-color: #F0EBE3 !important;
    color: #2C2118 !important;
    border-color: #A8998A !important;
}

/* ── Download button ───────────────────────────────────────────────────── */
.stDownloadButton > button {
    font-family: 'Inter', sans-serif !important;
    font-size: 0.68rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.15em !important;
    text-transform: uppercase !important;
    border-radius: 4px !important;
    padding: 0.58rem 1.6rem !important;
    background-color: #FFFFFF !important;
    color: #7A7068 !important;
    border: 1px solid #C8C0B4 !important;
    transition: all 0.18s ease !important;
}
.stDownloadButton > button:hover {
    background-color: #F0EBE3 !important;
    color: #2C2118 !important;
    border-color: #A8998A !important;
}

/* ── Data frame / editor ───────────────────────────────────────────────── */
[data-testid="stDataFrame"],
[data-testid="stDataEditor"] {
    border: 1px solid #E6E0D6 !important;
    border-radius: 6px !important;
    overflow: hidden !important;
}
/* AG Grid cell text inside data editor */
[data-testid="stDataEditor"] .ag-root-wrapper,
[data-testid="stDataEditor"] .ag-row,
[data-testid="stDataEditor"] .ag-cell {
    color: #2C2118 !important;
}
[data-testid="stDataEditor"] .ag-header-cell-text,
[data-testid="stDataEditor"] .ag-header-cell-label {
    color: #2C2118 !important;
}
[data-testid="stDataEditor"] .ag-cell-value {
    color: #2C2118 !important;
}

/* ── Alerts ────────────────────────────────────────────────────────────── */
.stAlert {
    border-radius: 6px !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 0.82rem !important;
}
/* Alert body text — use inherit so each alert keeps its own theme colour */
div[data-testid="stAlert"] p,
div[data-testid="stAlert"] li {
    color: inherit !important;
}

/* ── Spinner ───────────────────────────────────────────────────────────── */
.stSpinner p,
.stSpinner span,
[data-testid="stSpinner"] p,
[data-testid="stSpinner"] span {
    color: #2C2118 !important;
}

/* ── Captions ──────────────────────────────────────────────────────────── */
.stCaption,
.stCaption p,
[data-testid="stCaptionContainer"] p,
small {
    color: #A8998A !important;
    font-size: 0.74rem !important;
}

/* ── Horizontal rule ───────────────────────────────────────────────────── */
hr {
    border: none !important;
    border-top: 1px solid #E6E0D6 !important;
    margin: 1.25rem 0 !important;
}

/* ── Tooltip / help icon ───────────────────────────────────────────────── */
[data-testid="stTooltipIcon"] svg {
    fill: #A8998A !important;
}
</style>
"""

PAGE_TITLE_HTML = """
<div class="sch-page-title">DesignOps Intake</div>
<div class="sch-page-subtitle">
    Turn quotes, tear sheets, and product links into structured Programa-ready entries.
</div>
"""


def inject_css() -> None:
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def section_label(text: str) -> None:
    st.markdown(
        f'<div class="sch-section-label">{text}</div>', unsafe_allow_html=True
    )
