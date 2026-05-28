"""Resilient PDF parsing pipeline with strategy fallback and telemetry."""

from __future__ import annotations

import hashlib
import os
import resource
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src.document_parser import _parse_table_rows, _row_from_line
from src.pdf_item_normalizer import build_quote_item_rows

SOFT_TIMEOUT_SECONDS = 30
HARD_TIMEOUT_SECONDS = 120


class PdfParseTimeoutError(TimeoutError):
    pass


class PdfParseCancelledError(RuntimeError):
    pass


@dataclass
class ParserAttempt:
    parser: str
    status: str
    duration_seconds: float
    page_count: int = 0
    extracted_text_length: int = 0
    rows_found: int = 0
    ocr_triggered: bool = False
    memory_mb: float = 0.0
    error: str = ""
    stack: str = ""


@dataclass
class PdfParseResult:
    rows: list[dict] = field(default_factory=list)
    attempts: list[ParserAttempt] = field(default_factory=list)
    parser_used: str = ""
    status: str = "failed"
    page_count: int = 0
    extracted_text_length: int = 0
    ocr_triggered: bool = False
    error: str = ""


def parse_pdf_file_resilient(
    pdf_path: str | Path,
    *,
    filename: str = "",
    project: str = "",
    room: str = "",
    supplier: str = "",
    notes: str = "",
    soft_timeout: int = SOFT_TIMEOUT_SECONDS,
    hard_timeout: int = HARD_TIMEOUT_SECONDS,
    cancel_check: Callable[[], bool] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> PdfParseResult:
    """Parse a PDF from disk using deterministic strategies before OCR fallback.

    Strategy names intentionally match the product requirement:
    1. ``pdf-parse``: PyMuPDF table/text extraction, row heuristics.
    2. ``pdfjs``: alternate PyMuPDF rawdict/block text traversal, row heuristics.
    3. ``ocr``: optional Tesseract OCR when text extraction produces no text.
    """
    pdf_path = Path(pdf_path)
    started = time.monotonic()
    result = PdfParseResult()
    parsed_text_lengths: list[int] = []

    for parser_name, parser_func in (
        ("pdf-parse", _parse_with_pymupdf_tables),
        ("pdfjs", _parse_with_pymupdf_blocks),
    ):
        _raise_if_cancelled(cancel_check)
        if time.monotonic() - started >= hard_timeout:
            result.error = "hard_timeout"
            break
        if status_callback:
            status_callback("parsing")
        attempt = _run_attempt(
            parser_name,
            parser_func,
            pdf_path,
            filename=filename,
            project=project,
            room=room,
            supplier=supplier,
            notes=notes,
            started=started,
            soft_timeout=soft_timeout,
            hard_timeout=hard_timeout,
            cancel_check=cancel_check,
        )
        result.attempts.append(attempt)
        result.page_count = max(result.page_count, attempt.page_count)
        parsed_text_lengths.append(attempt.extracted_text_length)
        result.extracted_text_length = max(parsed_text_lengths or [0])
        if attempt.status == "complete" and attempt.rows_found:
            result.rows = attempt.extra_rows  # type: ignore[attr-defined]
            result.parser_used = parser_name
            result.status = "complete"
            return result
        if attempt.status == "timeout":
            result.error = attempt.error or "timeout"

    if not result.rows and result.extracted_text_length <= 20:
        _raise_if_cancelled(cancel_check)
        if status_callback:
            status_callback("ocr_fallback")
        attempt = _run_attempt(
            "ocr",
            _parse_with_ocr_placeholder,
            pdf_path,
            filename=filename,
            project=project,
            room=room,
            supplier=supplier,
            notes=notes,
            started=started,
            soft_timeout=soft_timeout,
            hard_timeout=hard_timeout,
            cancel_check=cancel_check,
        )
        attempt.ocr_triggered = True
        result.ocr_triggered = True
        result.attempts.append(attempt)
        result.page_count = max(result.page_count, attempt.page_count)
        result.extracted_text_length = max(result.extracted_text_length, attempt.extracted_text_length)
        if attempt.status == "complete" and attempt.rows_found:
            result.rows = attempt.extra_rows  # type: ignore[attr-defined]
            result.parser_used = "ocr"
            result.status = "complete"
            return result

    if not result.rows and not result.error:
        result.error = "no_parseable_product_rows"
    result.status = "failed" if result.error else "complete"
    return result


def _run_attempt(
    parser_name: str,
    parser_func,
    pdf_path: Path,
    *,
    filename: str,
    project: str,
    room: str,
    supplier: str,
    notes: str,
    started: float,
    soft_timeout: int,
    hard_timeout: int,
    cancel_check: Callable[[], bool] | None,
) -> ParserAttempt:
    attempt_started = time.monotonic()
    rows: list[dict] = []
    page_count = 0
    text_length = 0
    try:
        rows, page_count, text_length = parser_func(
            pdf_path,
            filename=filename,
            project=project,
            room=room,
            supplier=supplier,
            notes=notes,
            started=started,
            soft_timeout=soft_timeout,
            hard_timeout=hard_timeout,
            cancel_check=cancel_check,
        )
        status = "complete"
        error = ""
        stack = ""
    except PdfParseCancelledError:
        raise
    except PdfParseTimeoutError as exc:
        status = "timeout"
        error = str(exc)
        stack = traceback.format_exc(limit=8)
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        stack = traceback.format_exc(limit=12)

    attempt = ParserAttempt(
        parser=parser_name,
        status=status,
        duration_seconds=round(time.monotonic() - attempt_started, 3),
        page_count=page_count,
        extracted_text_length=text_length,
        rows_found=len(rows),
        memory_mb=_memory_mb(),
        error=error,
        stack=stack,
    )
    attempt.extra_rows = rows  # type: ignore[attr-defined]
    return attempt


def _parse_with_pymupdf_tables(
    pdf_path: Path,
    *,
    filename: str,
    project: str,
    room: str,
    supplier: str,
    notes: str,
    started: float,
    soft_timeout: int,
    hard_timeout: int,
    cancel_check: Callable[[], bool] | None,
) -> tuple[list[dict], int, int]:
    import fitz

    pdf_id = _file_sha1(pdf_path)
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    text_length = 0
    with fitz.open(str(pdf_path)) as doc:
        page_count = doc.page_count
        for page_index, page in enumerate(doc):
            _check_time(started, soft_timeout, hard_timeout, page_index)
            _raise_if_cancelled(cancel_check)
            page_number = page_index + 1
            table_rows = _parse_table_rows(page, project, room, supplier, notes)
            if table_rows:
                for row in table_rows:
                    _append_row(rows, seen, row, pdf_id, page_number, filename)
                continue
            text = page.get_text("text") or ""
            text_length += len(text)
            grouped_rows = build_quote_item_rows(
                text.splitlines(),
                project=project,
                room=room,
                supplier=supplier,
                notes=notes,
            )
            if grouped_rows:
                for row in grouped_rows:
                    _append_row(rows, seen, row, pdf_id, page_number, filename)
                continue
            for line in text.splitlines():
                row = _row_from_line(line.strip(), project, room, supplier, notes)
                if row is not None:
                    _append_row(rows, seen, row, pdf_id, page_number, filename)
    return rows, page_count, text_length


def _parse_with_pymupdf_blocks(
    pdf_path: Path,
    *,
    filename: str,
    project: str,
    room: str,
    supplier: str,
    notes: str,
    started: float,
    soft_timeout: int,
    hard_timeout: int,
    cancel_check: Callable[[], bool] | None,
) -> tuple[list[dict], int, int]:
    import fitz

    pdf_id = _file_sha1(pdf_path)
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    text_length = 0
    with fitz.open(str(pdf_path)) as doc:
        page_count = doc.page_count
        for page_index, page in enumerate(doc):
            _check_time(started, soft_timeout, hard_timeout, page_index)
            _raise_if_cancelled(cancel_check)
            text = page.get_text("blocks") or []
            lines: list[str] = []
            for block in text:
                if len(block) >= 5:
                    block_text = str(block[4] or "")
                    text_length += len(block_text)
                    lines.extend(block_text.splitlines())
            if not lines:
                raw = page.get_text("rawdict")
                raw_text = str(raw)
                text_length += len(raw_text)
                lines = raw_text.splitlines()
            page_number = page_index + 1
            grouped_rows = build_quote_item_rows(
                lines,
                project=project,
                room=room,
                supplier=supplier,
                notes=notes,
            )
            if grouped_rows:
                for row in grouped_rows:
                    _append_row(rows, seen, row, pdf_id, page_number, filename)
                continue
            for line in lines:
                row = _row_from_line(line.strip(), project, room, supplier, notes)
                if row is not None:
                    _append_row(rows, seen, row, pdf_id, page_number, filename)
    return rows, page_count, text_length


def _parse_with_ocr_placeholder(
    pdf_path: Path,
    *,
    filename: str,
    project: str,
    room: str,
    supplier: str,
    notes: str,
    started: float,
    soft_timeout: int,
    hard_timeout: int,
    cancel_check: Callable[[], bool] | None,
) -> tuple[list[dict], int, int]:
    """OCR hook, used only after deterministic text extraction produces no text."""
    import fitz

    try:
        import pytesseract
        from PIL import Image
    except Exception as exc:
        raise RuntimeError(
            "OCR fallback unavailable: install pytesseract and the Tesseract binary "
            "or configure a managed OCR service."
        ) from exc

    pdf_id = _file_sha1(pdf_path)
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    text_length = 0
    _raise_if_cancelled(cancel_check)
    _check_time(started, soft_timeout, hard_timeout, 0)
    with fitz.open(str(pdf_path)) as doc:
        page_count = doc.page_count
        for page_index, page in enumerate(doc):
            _check_time(started, soft_timeout, hard_timeout, page_index)
            _raise_if_cancelled(cancel_check)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            text = pytesseract.image_to_string(image) or ""
            text_length += len(text)
            page_number = page_index + 1
            grouped_rows = build_quote_item_rows(
                text.splitlines(),
                project=project,
                room=room,
                supplier=supplier,
                notes=notes,
            )
            if grouped_rows:
                for row in grouped_rows:
                    _append_row(rows, seen, row, pdf_id, page_number, filename)
                continue
            for line in text.splitlines():
                row = _row_from_line(line.strip(), project, room, supplier, notes)
                if row is not None:
                    _append_row(rows, seen, row, pdf_id, page_number, filename)
        return rows, page_count, text_length


def _append_row(rows: list[dict], seen: set[tuple[str, str]], row: dict, pdf_id: str, page_number: int, filename: str) -> None:
    key = (
        str(row.get("Product Name", "")).lower().strip(),
        str(row.get("Model/SKU", "")).lower().strip(),
    )
    if key in seen:
        return
    seen.add(key)
    row["_source_pdf_id"] = pdf_id
    row["_source_page_number"] = page_number
    row["_source_filename"] = filename
    rows.append(row)


def _file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def _check_time(started: float, soft_timeout: int, hard_timeout: int, page_index: int) -> None:
    elapsed = time.monotonic() - started
    if elapsed >= hard_timeout:
        raise PdfParseTimeoutError(f"hard timeout after {hard_timeout}s")
    if elapsed >= soft_timeout and page_index > 0:
        raise PdfParseTimeoutError(f"soft timeout after {soft_timeout}s")


def _raise_if_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise PdfParseCancelledError("parse cancelled")


def _memory_mb() -> float:
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if os.name == "posix" and rss > 10_000_000:
            return round(rss / (1024 * 1024), 2)
        return round(rss / 1024, 2)
    except Exception:
        return 0.0
