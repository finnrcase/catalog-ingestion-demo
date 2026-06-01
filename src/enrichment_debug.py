"""
Diagnostic enrichment pass for debugging why dimensions are not being filled.

Public API
----------
debug_enrich_dataframe(df) -> list[dict]
    Runs the full enrichment pipeline for every row (qualifying or not),
    capturing the result at each step. Does NOT write to the DataFrame.

save_debug_report(traces, out_dir="") -> str
    Serialises the trace list to a timestamped JSON file. Returns the path.

Each trace dict contains:
    product_name, brand, model_sku
    brave_api_key_loaded, anthropic_api_key_loaded
    qualifies, disqualify_reason
    search_query, search_results (list of {url, title, domain_score})
    selected_url, selected_score
    fetch_success, page_text_length, page_text_preview
    dimension_terms_found
    claude_prompt_includes_dimensions
    claude_raw_output, claude_raw_dimensions
    apply_accepted_dimensions (True/False)
    final_dimensions
    failure_reason
"""

from __future__ import annotations

import datetime
import json
import logging
import re

from pathlib import Path

from src.brave_search import BRAVE_API_KEY, search_product_candidates
from src.dimensions import has_complete_3d_dimensions
from src.product_enrichment import (
    ANTHROPIC_API_KEY,
    MIN_USE_SCORE,
    _anthropic,
    _build_extraction_prompt,
    _build_search_query,
    _fetch_page_text,
    _apply_enrichment,
    _qualifies,
    _str_val,
)

from src.runtime_storage import runtime_data_path, write_json_best_effort

_log = logging.getLogger(__name__)

# Regex that recognises dimension-related terms inside page text.
_DIM_TERMS_RE = re.compile(
    r"""
    \b(
      width | height | depth | length |
      w\s*[×x]\s*h | w\s*[×x]\s*d |
      \d+\s*["'″]\s*[whdWHD] |
      \d+\s+[whdWHD]\b
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _check_dim_terms(text: str) -> bool:
    return bool(_DIM_TERMS_RE.search(text))


def _disqualify_reason(row: dict) -> str:
    """Human-readable reason why a row doesn't qualify, or '' if it does qualify."""
    source = _str_val(row.get("Source Type", ""))
    if source == "URL":
        return "Source Type is URL — enrichment skipped for URL rows"
    if source.endswith("_Enriched"):
        return f"Source Type is '{source}' — already enriched"
    if not _str_val(row.get("Brand")):
        return "Brand is blank — enrichment requires Brand"
    if not _str_val(row.get("Model/SKU")):
        return "Model/SKU is blank — enrichment requires a model number"
    from src.product_enrichment import _ENRICHABLE_FIELDS
    blank_or_incomplete = [
        f for f in _ENRICHABLE_FIELDS
        if not _str_val(row.get(f))
        or (f == "Dimensions" and not has_complete_3d_dimensions(_str_val(row.get(f))))
    ]
    if not blank_or_incomplete:
        return "All enrichable fields already complete — nothing to do"
    return ""


def debug_enrich_row(row: dict) -> dict:
    """
    Run the full enrichment pipeline for one row, capturing every intermediate
    result. Does NOT modify the row — returns a trace dict only.
    """
    name  = _str_val(row.get("Product Name"))
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))

    trace: dict = {
        "product_name":                name,
        "brand":                       brand,
        "model_sku":                   model,
        "brave_api_key_loaded":        bool(BRAVE_API_KEY),
        "anthropic_api_key_loaded":    bool(ANTHROPIC_API_KEY),
        "qualifies":                   _qualifies(row),
        "disqualify_reason":           _disqualify_reason(row),
        "search_query":                "",
        "search_results":              [],
        "selected_url":                "",
        "selected_score":              0,
        "fetch_success":               False,
        "page_text_length":            0,
        "page_text_preview":           "",
        "dimension_terms_found":       False,
        "claude_prompt_includes_dimensions": False,
        "claude_raw_output":           "",
        "claude_raw_dimensions":       "",
        "apply_accepted_dimensions":   False,
        "final_dimensions":            _str_val(row.get("Dimensions")),
        "failure_reason":              "",
    }

    # ── Gate 1: qualifies? ─────────────────────────────────────────────────────
    if not trace["qualifies"]:
        trace["failure_reason"] = trace["disqualify_reason"]
        return trace

    # ── Gate 2: API keys ───────────────────────────────────────────────────────
    if not BRAVE_API_KEY:
        trace["failure_reason"] = "BRAVE_API_KEY is not set — search cannot run"
        return trace

    # ── Step 1: Build and run search ───────────────────────────────────────────
    query = _build_search_query(row)
    trace["search_query"] = query

    results = search_product_candidates(query, brand)
    trace["search_results"] = [
        {"url": r.url, "title": r.title[:120], "domain_score": r.domain_score}
        for r in results
    ]

    if not results:
        trace["failure_reason"] = "Brave Search returned 0 results — check BRAVE_API_KEY or query"
        return trace

    best = results[0]
    trace["selected_url"]   = best.url
    trace["selected_score"] = best.domain_score

    if best.domain_score < MIN_USE_SCORE:
        trace["failure_reason"] = (
            f"Best result domain_score={best.domain_score} is below MIN_USE_SCORE={MIN_USE_SCORE} "
            f"— url: {best.url}"
        )
        return trace

    # ── Step 2: Fetch page ─────────────────────────────────────────────────────
    page_text = _fetch_page_text(best.url)
    trace["fetch_success"]       = bool(page_text)
    trace["page_text_length"]    = len(page_text)
    trace["page_text_preview"]   = page_text[:500] if page_text else ""
    trace["dimension_terms_found"] = _check_dim_terms(page_text)

    if not page_text:
        trace["failure_reason"] = f"Could not fetch page content from {best.url}"
        return trace

    # ── Step 3: Claude extraction ──────────────────────────────────────────────
    dims_existing = _str_val(row.get("Dimensions"))
    needs_dims = not dims_existing or not has_complete_3d_dimensions(dims_existing)
    trace["claude_prompt_includes_dimensions"] = needs_dims

    if not ANTHROPIC_API_KEY or _anthropic is None:
        trace["failure_reason"] = "ANTHROPIC_API_KEY not set or 'anthropic' package missing"
        return trace

    try:
        prompt = _build_extraction_prompt(page_text, row)
        client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = msg.content[0].text.strip()
        trace["claude_raw_output"] = raw_text

        cleaned = re.sub(r"```(?:json)?\s*|```", "", raw_text).strip()
        start   = cleaned.find("{")
        end     = cleaned.rfind("}")

        if start == -1 or end == -1:
            trace["failure_reason"] = "Claude response contained no JSON object"
            return trace

        parsed = json.loads(cleaned[start : end + 1])
        raw_dims = _str_val(parsed.get("Dimensions", ""))
        trace["claude_raw_dimensions"] = raw_dims

        if not raw_dims:
            trace["failure_reason"] = (
                "Claude returned empty Dimensions — "
                "either the page had no explicit W×H×D or the model chose not to guess"
            )
        elif not has_complete_3d_dimensions(raw_dims):
            trace["failure_reason"] = (
                f"Claude returned partial dimensions ({raw_dims!r}) — "
                "missing one or more of W/H/D labels; stored as a note, not filled"
            )
        else:
            # What would _apply_enrichment actually store?
            updated = _apply_enrichment(row, parsed, best.url, best.domain_score)
            final = _str_val(updated.get("Dimensions"))
            trace["final_dimensions"]          = final
            trace["apply_accepted_dimensions"] = bool(final)
            if not final:
                trace["failure_reason"] = (
                    "_apply_enrichment did not store dimensions despite Claude returning valid ones — "
                    "check _apply_enrichment logic"
                )
            else:
                trace["failure_reason"] = ""  # success

    except json.JSONDecodeError as exc:
        trace["failure_reason"] = f"JSON parse error on Claude response: {exc}"
    except Exception as exc:
        trace["failure_reason"] = f"Claude API call failed: {exc}"

    return trace


def debug_enrich_dataframe(df) -> list[dict]:
    """
    Run debug_enrich_row for every row in df.
    Rows that don't qualify are still included with qualifies=False.
    """
    traces = []
    for _, row in df.iterrows():
        r = row.to_dict()
        traces.append(debug_enrich_row(r))
    return traces


def save_debug_report(traces: list[dict], out_dir: str = "") -> str:
    """Write traces to a timestamped JSON file. Returns the path."""
    target_dir = runtime_data_path("enrichment_debug") if out_dir in {"", "data/enrichment_debug"} else Path(out_dir)
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = target_dir / f"debug_{ts}.json"
    if write_json_best_effort(path, traces, description="enrichment debug report", ensure_ascii=False):
        return str(path)
    _log.warning("Enrichment debug report was not persisted: %s", path)
    return ""
