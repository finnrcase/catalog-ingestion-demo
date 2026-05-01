from __future__ import annotations

import re

_ROW_PREFIX_RE = re.compile(
    r"^\s*(?:row\s*)?#?\d{1,3}\s*[-–—:.)]\s+(?=[A-Za-z\[\(\"'])",
    re.IGNORECASE,
)


def remove_notes_row_prefix(value: object) -> str:
    """Remove a leading row-number label from user-facing Notes text only."""
    text = str(value or "").strip()
    return _ROW_PREFIX_RE.sub("", text, count=1).strip()
