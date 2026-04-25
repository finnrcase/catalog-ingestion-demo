"""
Confidence scoring and exception-review layer for SCH DesignOps Intake.

Each row is evaluated and four columns are added or refreshed:
    Confidence Score   int   0–100
    Review Required    bool
    Missing Fields     str   comma-separated list of important blank fields
    Suggested Action   str   short human-readable recommendation

Entry point: apply_confidence_checks(df) → pd.DataFrame
"""

import re
import pandas as pd

from src.intake_schema import IMPORTANT_FIELDS, SOURCE_MANUAL, SOURCE_PDF_AI, SOURCE_URL

# ── Constants ──────────────────────────────────────────────────────────────────

# Column order for the review table — confidence columns surface first
DISPLAY_ORDER: list[str] = [
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
    "Model/SKU",
    "Product Category",
    "Quantity",
    "Price",
    "Supplier",
    "Product URL",
    "Notes",
    "Source Type",
    "Status",
    "Missing Fields",
]

_IGNORED_RE = re.compile(
    r"\b(subtotal|sub[- ]?total|tax|gst|hst|vat|pst|delivery|shipping|freight|"
    r"total|balance( due)?|discount|credit|surcharge|handling|deposit|"
    r"freight & handling|f&h)\b",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"^https?://.{3,}\..{2,}", re.IGNORECASE)

REVIEW_THRESHOLD = 75  # confidence below this triggers Review Required = True


# ── Internal helpers ───────────────────────────────────────────────────────────


def _str(value) -> str:
    return str(value or "").strip()


def _is_valid_url(value) -> bool:
    return bool(value and _URL_RE.match(_str(value)))


def _is_ignored_row(row: dict) -> bool:
    """True if the row looks like a non-product line or is completely blank."""
    source = _str(row.get("Source Type"))
    # These source types are never auto-ignored:
    # - URL: missing product name is expected
    # - PDF_AI: AI already filtered non-product lines
    # - Manual: user explicitly added the row
    if source in (SOURCE_URL, SOURCE_PDF_AI, SOURCE_MANUAL):
        return False

    useful_fields = ("Product Name", "Brand", "Model/SKU", "Product URL", "Supplier")
    if not any(_str(row.get(f)) for f in useful_fields):
        return True  # completely blank row

    name = _str(row.get("Product Name"))
    return bool(name and _IGNORED_RE.search(name))


def _missing_important(row: dict) -> list[str]:
    """Return IMPORTANT_FIELDS that are blank in this row."""
    missing = []
    for field in IMPORTANT_FIELDS:
        if field == "Quantity":
            qty = row.get("Quantity")
            if not qty or _str(qty) in ("", "0"):
                missing.append("Quantity")
        else:
            if not _str(row.get(field)):
                missing.append(field)
    return missing


# ── Public API ─────────────────────────────────────────────────────────────────


def identify_missing_fields(row: dict) -> list[str]:
    """
    Return a list of field names that are blank or invalid for this row.
    For URL rows only the URL itself is checked.
    For all other rows, IMPORTANT_FIELDS are checked.
    """
    source = _str(row.get("Source Type"))

    if source == SOURCE_URL:
        missing = []
        if not _str(row.get("Project")):
            missing.append("Project")
        if not _str(row.get("Room")):
            missing.append("Room")
        if not _is_valid_url(row.get("Product URL")):
            missing.append("Valid URL")
        return missing

    # All non-URL sources: check IMPORTANT_FIELDS + Project
    missing = []
    if not _str(row.get("Project")):
        missing.append("Project")
    missing.extend(_missing_important(row))
    return missing


def calculate_row_confidence(row: dict) -> int:
    """Return a confidence score between 0 and 100."""
    if _is_ignored_row(row):
        return 0

    source = _str(row.get("Source Type"))

    # AI-extracted rows carry their own score from Claude — honour it.
    if source == SOURCE_PDF_AI:
        try:
            return max(0, min(100, int(row.get("Confidence Score", 50))))
        except (TypeError, ValueError):
            return 50

    if source == SOURCE_URL:
        if not _is_valid_url(row.get("Product URL")):
            return 30
        score = 90
        if not _str(row.get("Project")):
            score -= 15
        if not _str(row.get("Room")):
            score -= 10
        return max(0, score)

    # PDF, Manual, and any other source — score against IMPORTANT_FIELDS
    score = 85
    if not _str(row.get("Product Name")):
        score -= 20
    if not _str(row.get("Brand")):
        score -= 15
    qty = row.get("Quantity")
    if not qty or _str(qty) in ("", "0"):
        score -= 10
    if not _str(row.get("Supplier")):
        score -= 10
    if not _str(row.get("Room")):
        score -= 10
    if not _str(row.get("Product Category")):
        score -= 10
    if not _str(row.get("Model/SKU")):
        score -= 5
    if not _str(row.get("Price")):
        score -= 5
    return max(0, score)


def should_require_review(row: dict) -> bool:
    """True if this row needs a human to verify it before sending to Programa."""
    if _is_ignored_row(row):
        return False

    # Manual rows with a Serial/Model Number but missing important fields need
    # enrichment before they are reliable — always flag for review.
    if _str(row.get("Source Type")) == SOURCE_MANUAL:
        if _str(row.get("Model/SKU")) and _missing_important(row):
            return True

    return calculate_row_confidence(row) < REVIEW_THRESHOLD


def classify_row_status(row: dict) -> str:
    """Return a descriptive status label derived from the row's data."""
    if _is_ignored_row(row):
        return "Ignored"

    source = _str(row.get("Source Type"))

    # Manual rows with a serial number but gaps → enrichment path
    if source == SOURCE_MANUAL and _str(row.get("Model/SKU")):
        if _missing_important(row):
            return "Needs Enrichment"

    score = calculate_row_confidence(row)
    if score >= REVIEW_THRESHOLD:
        return "Ready for Programa" if source == SOURCE_URL else "Ready for Review"
    return "Needs Review"


def _suggested_action(row: dict, missing: list[str]) -> str:
    """Build a short, actionable string for the Suggested Action column."""
    if _is_ignored_row(row):
        name = _str(row.get("Product Name"))
        if name:
            label = name[:40] + "…" if len(name) > 40 else name
            return f'Possible subtotal/tax row "{label}" — excluded'
        return "Blank or non-product row — excluded"

    source = _str(row.get("Source Type"))

    if source == SOURCE_URL and not _is_valid_url(row.get("Product URL")):
        return "Invalid or missing URL — please check the link"

    # Manual rows: check for enrichment opportunity and category gap
    if source == SOURCE_MANUAL:
        has_serial = bool(_str(row.get("Model/SKU")))
        mi = _missing_important(row)
        no_category = not _str(row.get("Product Category"))

        if has_serial and mi:
            base = "Use serial/model number to fill missing fields"
            if no_category:
                return base + ". AI category suggestion needed"
            return base

        if no_category and not mi:
            return "AI category suggestion needed"

    if not missing:
        action = "Ready for Programa URL import" if source == SOURCE_URL else "Ready for review"
    elif len(missing) == 1:
        action = f"Missing {missing[0]}"
    elif len(missing) == 2:
        action = f"Missing {missing[0]} and {missing[1]}"
    else:
        action = f"Missing {', '.join(missing[:-1])}, and {missing[-1]}"

    # For PDF_AI rows with blank Dimensions, append a verification note so
    # reviewers know to source dimensions from the manufacturer's spec sheet.
    if (source == SOURCE_PDF_AI
            and not _str(row.get("Dimensions"))
            and "dimension" not in action.lower()):
        dim_note = "Verify dimensions from spec sheet"
        action = dim_note if action == "Ready for review" else f"{action}; {dim_note}"

    return action


def apply_confidence_checks(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add or refresh the four confidence columns on every row.

    - Ignored rows: Include=False, Status='Ignored'
    - Manual rows with Serial/Model but missing important fields:
      Status='Needs Enrichment', Review Required=True
    - All other rows: status preserved from creation; confidence columns updated.

    Returns a new DataFrame; the input is not mutated.
    """
    df = df.copy()

    scores: list[int] = []
    reviews: list[bool] = []
    missing_strs: list[str] = []
    actions: list[str] = []

    for idx, row in df.iterrows():
        r = row.to_dict()
        ignored = _is_ignored_row(r)
        source = _str(r.get("Source Type"))
        score = calculate_row_confidence(r)
        missing = identify_missing_fields(r)
        review = should_require_review(r)
        action = _suggested_action(r, missing)

        if ignored:
            df.at[idx, "Include"] = False
            df.at[idx, "Status"] = "Ignored"
        elif source == SOURCE_MANUAL and _str(r.get("Model/SKU")) and _missing_important(r):
            df.at[idx, "Status"] = "Needs Enrichment"

        scores.append(score)
        reviews.append(review)
        missing_strs.append(", ".join(missing) if missing else "")
        actions.append(action)

    df["Confidence Score"] = scores
    df["Review Required"] = reviews
    df["Missing Fields"] = missing_strs
    df["Suggested Action"] = actions

    # Surface confidence columns early; any extra columns go at the end
    ordered = [c for c in DISPLAY_ORDER if c in df.columns]
    extra = [c for c in df.columns if c not in DISPLAY_ORDER]
    return df[ordered + extra]
