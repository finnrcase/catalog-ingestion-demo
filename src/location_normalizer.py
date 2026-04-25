"""
Location string normalizer for SCH DesignOps Intake.

Cleans up informal location annotations extracted from vendor PDF sheets,
where location may appear as a messy note such as "Bar - if we can fit it"
or "laundry room floor 2".

Public API
----------
normalize_location(raw_location, default_location="") -> tuple[str, int, str]
    Returns (cleaned_location, confidence_score, reason).
    confidence_score is 0–100; below 75 the caller should flag Review Required.
"""

import re

# Patterns that indicate the location is uncertain or conditional.
# Matched case-insensitively against the raw string.
_UNCERTAINTY_RE = re.compile(
    r"(\s*[-–]\s*if\s+we\s+can\s+fit\s+it\b.*"
    r"|\s+if\s+we\s+can\s+fit\s+it\b.*"
    r"|\s*[-–]\s*if\s+possible\b.*"
    r"|\s+if\s+possible\b.*"
    r"|\s*[-–]\s*pending\b.*"
    r"|\s*[-–]\s*tbd\b.*"
    r"|\s*[-–]\s*maybe\b.*"
    r"|\s+if\s+it\s+fits\b.*)",
    re.IGNORECASE,
)


def normalize_location(
    raw_location: str,
    default_location: str = "",
) -> tuple[str, int, str]:
    """
    Clean an informal location string and return a confidence score.

    Parameters
    ----------
    raw_location     : Raw string from the PDF or AI extraction.
    default_location : Fallback value when raw_location is blank.

    Returns
    -------
    (cleaned_location, confidence_score, reason)
        confidence_score < 75 means the caller should set Review Required = True.
    """
    stripped = (raw_location or "").strip()

    # Empty → use default
    if not stripped:
        if default_location:
            return default_location, 40, "used default location — verify"
        return "", 0, "no location found"

    # Detect and remove uncertainty qualifiers
    had_uncertainty = bool(_UNCERTAINTY_RE.search(stripped))
    cleaned = _UNCERTAINTY_RE.sub("", stripped).strip().strip("-–").strip()

    if not cleaned:
        if default_location:
            return default_location, 40, "used default location — verify"
        return "", 0, "location reduced to empty after cleaning"

    # Title-case the result
    cleaned = cleaned.title()

    if had_uncertainty:
        return cleaned, 65, "location inferred from uncertain note — verify"

    return cleaned, 90, "location extracted from annotation"
