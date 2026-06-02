from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
import pandas as pd

from src.dimensions import has_complete_3d_dimensions
from src.source_memory import save_successful_source_from_row

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_INPUT_COST_PER_1K = 0.00015
DEFAULT_OUTPUT_COST_PER_1K = 0.00060
DEFAULT_MAX_OUTPUT_TOKENS = 2200


@dataclass
class FurtherEnrichmentResult:
    dataframe: pd.DataFrame
    errors: list[str]
    diagnostics: list[dict[str, Any]]
    stage_timings: dict[str, Any]


def _str(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, ""))
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= 0 else default


def _model_name() -> str:
    return (
        os.getenv("OPENAI_FURTHER_ENRICHMENT_MODEL")
        or os.getenv("OPENAI_MODEL")
        or DEFAULT_MODEL
    ).strip()


def openai_integration_status() -> dict[str, Any]:
    """Return backend-safe OpenAI configuration status without exposing secrets."""
    configured = bool(os.getenv("OPENAI_API_KEY", "").strip())
    return {
        "provider": "OpenAI",
        "status": "Connected" if configured else "Not Configured",
        "configured": configured,
        "model": _model_name(),
        "further_enrichment_supported": True,
    }


def _is_https_url(value: object) -> bool:
    text = _str(value).lower()
    return text.startswith("https://") and " " not in text


def _confidence_text(value: object) -> str:
    text = _str(value).lower()
    if not text:
        return ""
    if text in {"high", "medium", "low", "none"}:
        return text
    try:
        score = float(text.rstrip("%"))
        if score > 1:
            score /= 100
        if score >= 0.8:
            return "high"
        if score >= 0.6:
            return "medium"
        if score > 0:
            return "low"
    except ValueError:
        pass
    return text


def _high_confidence(row: dict[str, Any], *fields: str) -> bool:
    for field in fields:
        if _confidence_text(row.get(field)) == "high":
            return True
    try:
        score = float(row.get("Confidence Score") or 0)
        if score >= 0.9:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _low_confidence(row: dict[str, Any]) -> bool:
    confidence_values = [
        _confidence_text(row.get("Dimension Confidence")),
        _confidence_text(row.get("dimension_confidence")),
        _confidence_text(row.get("image_confidence")),
        _confidence_text(row.get("selected_product_url_confidence")),
        _confidence_text(row.get("product_url_confidence")),
    ]
    if any(value in {"low", "none"} for value in confidence_values if value):
        return True
    try:
        score = float(row.get("Confidence Score") or 0)
        return 0 < score < 0.75
    except (TypeError, ValueError):
        return False


def _is_review_only_charge(row: dict[str, Any]) -> bool:
    import_type = _str(row.get("Import Type")).lower()
    return import_type in {"unresolved_charge", "manual_review_charge"}


def _needs_further_enrichment(row: dict[str, Any]) -> bool:
    if row.get("Include") is False or _is_review_only_charge(row):
        return False
    missing_dimensions = not has_complete_3d_dimensions(row.get("Dimensions"))
    missing_image = not _is_https_url(row.get("Image URL"))
    return missing_dimensions or missing_image or _low_confidence(row)


def _missing_fields(row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not has_complete_3d_dimensions(row.get("Dimensions")):
        missing.append("dimensions")
    if not _is_https_url(row.get("Image URL")):
        missing.append("image")
    if not _str(row.get("Product URL")):
        missing.append("product_page_url")
    if not _str(row.get("spec_sheet_url") or row.get("Spec Sheet URL")):
        missing.append("spec_sheet_url")
    if _low_confidence(row):
        missing.append("low_confidence")
    return missing


def _row_prompt_payload(index: int, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_id": str(index),
        "requested_focus": _str(row.get("deep_retry_focus") or row.get("requested_missing_fields") or "missing_fields"),
        "missing_fields": _missing_fields(row),
        "brand": _str(row.get("Brand")),
        "model_sku": _str(row.get("Model/SKU")),
        "product_name": _str(row.get("Product Name")),
        "description_or_notes": _str(row.get("Description") or row.get("Notes"))[:900],
        "category": _str(row.get("Product Category")),
        "supplier": _str(row.get("Supplier")),
        "existing_dimensions": _str(row.get("Dimensions")),
        "existing_image_url": _str(row.get("Image URL")),
        "existing_product_url": _str(row.get("Product URL") or row.get("selected_product_url")),
        "dimension_source_url": _str(row.get("dimension_source_url") or row.get("Dimension Source URL")),
        "image_source_url": _str(row.get("image_source_url") or row.get("Image Source URL")),
        "spec_sheet_url": _str(row.get("spec_sheet_url") or row.get("Spec Sheet URL")),
        "confidence": _str(row.get("Confidence Score")),
    }


def _response_schema() -> dict[str, Any]:
    return {
        "name": "sch_further_enrichment",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "row_id": {"type": "string"},
                            "dimensions": {"type": ["string", "null"]},
                            "width_in": {"type": ["number", "string", "null"]},
                            "height_in": {"type": ["number", "string", "null"]},
                            "depth_in": {"type": ["number", "string", "null"]},
                            "image_url": {"type": ["string", "null"]},
                            "product_page_url": {"type": ["string", "null"]},
                            "spec_sheet_url": {"type": ["string", "null"]},
                            "confidence": {"type": "string", "enum": ["high", "medium", "low", "none"]},
                            "dimension_confidence": {"type": "string", "enum": ["high", "medium", "low", "none"]},
                            "image_confidence": {"type": "string", "enum": ["high", "medium", "low", "none"]},
                            "source_links": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "type": {"type": "string"},
                                        "url": {"type": "string"},
                                        "confidence": {"type": "string"},
                                        "notes": {"type": "string"},
                                    },
                                    "required": ["type", "url", "confidence", "notes"],
                                },
                            },
                            "notes": {"type": ["string", "null"]},
                        },
                        "required": [
                            "row_id",
                            "dimensions",
                            "width_in",
                            "height_in",
                            "depth_in",
                            "image_url",
                            "product_page_url",
                            "spec_sheet_url",
                            "confidence",
                            "dimension_confidence",
                            "image_confidence",
                            "source_links",
                            "notes",
                        ],
                    },
                },
            },
            "required": ["rows"],
        },
    }


def _build_prompt(rows: list[dict[str, Any]]) -> str:
    payload = {"rows": rows}
    return (
        "You are helping SCH build Programa-ready product schedule rows. "
        "Research or reason only about the listed incomplete rows. Return JSON matching the schema. "
        "Respect each row's requested_focus: for dimensions, prioritize verified W x H x D and source links; "
        "for image, prioritize a verified product image URL and source page; for missing_fields, target only missing or low-confidence fields. "
        "Prioritize verified manufacturer/spec sources over retailers. Do not invent dimensions, image URLs, or source links. "
        "If a field cannot be verified from a reliable source, leave it null and set confidence to low or none. "
        "Never suggest replacing existing high-confidence fields. Use inches for width_in, height_in, depth_in when available.\n\n"
        f"{json.dumps(payload, ensure_ascii=True)}"
    )


def estimate_further_enrichment_cost(rows: list[dict[str, Any]], *, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS) -> float:
    prompt = _build_prompt(rows)
    input_tokens = max(1, math.ceil(len(prompt) / 4))
    input_rate = _float_env("OPENAI_FURTHER_INPUT_COST_PER_1K", DEFAULT_INPUT_COST_PER_1K)
    output_rate = _float_env("OPENAI_FURTHER_OUTPUT_COST_PER_1K", DEFAULT_OUTPUT_COST_PER_1K)
    return (input_tokens / 1000 * input_rate) + (max_output_tokens / 1000 * output_rate)


def _pack_rows_under_budget(
    candidates: list[dict[str, Any]],
    max_cost_usd: float,
    max_cost_per_item_usd: float | None = None,
) -> tuple[list[dict[str, Any]], float, int]:
    packed: list[dict[str, Any]] = []
    estimated = 0.0
    per_item_cap = max_cost_per_item_usd if max_cost_per_item_usd and max_cost_per_item_usd > 0 else None
    for candidate in candidates:
        trial = [*packed, candidate]
        trial_cost = estimate_further_enrichment_cost(trial)
        trial_per_item = trial_cost / max(len(trial), 1)
        per_item_ok = per_item_cap is None or trial_per_item <= per_item_cap
        if trial_cost <= max_cost_usd and per_item_ok:
            packed = trial
            estimated = trial_cost
        else:
            if not packed:
                estimated = trial_cost
            break
    if estimated > max_cost_usd:
        return [], estimated, len(candidates)
    return packed, estimated, max(0, len(candidates) - len(packed))


def _call_openai(rows: list[dict[str, Any]], *, max_cost_usd: float) -> tuple[dict[str, Any], dict[str, Any]]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    model = _model_name()
    max_output_tokens = int(os.getenv("OPENAI_FURTHER_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS)) or DEFAULT_MAX_OUTPUT_TOKENS)
    estimate = estimate_further_enrichment_cost(rows, max_output_tokens=max_output_tokens)
    if estimate > max_cost_usd:
        raise RuntimeError(f"Estimated OpenAI cost {estimate:.4f} exceeds further enrichment cap {max_cost_usd:.4f}.")

    response = httpx.post(
        OPENAI_CHAT_COMPLETIONS_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only valid structured JSON. Be conservative. "
                        "Missing data is better than unsupported product data."
                    ),
                },
                {"role": "user", "content": _build_prompt(rows)},
            ],
            "temperature": 0.1,
            "max_tokens": max_output_tokens,
            "response_format": {"type": "json_schema", "json_schema": _response_schema()},
        },
        timeout=float(os.getenv("OPENAI_FURTHER_TIMEOUT_SECONDS", "60") or 60),
    )
    response.raise_for_status()
    payload = response.json()
    content = payload.get("choices", [{}])[0].get("message", {}).get("content") or "{}"
    parsed = json.loads(content)
    usage = payload.get("usage") or {}
    input_tokens = float(usage.get("prompt_tokens") or 0)
    output_tokens = float(usage.get("completion_tokens") or 0)
    actual_cost = (
        input_tokens / 1000 * _float_env("OPENAI_FURTHER_INPUT_COST_PER_1K", DEFAULT_INPUT_COST_PER_1K)
        + output_tokens / 1000 * _float_env("OPENAI_FURTHER_OUTPUT_COST_PER_1K", DEFAULT_OUTPUT_COST_PER_1K)
    )
    return parsed, {
        "model": model,
        "estimated_cost_usd": estimate,
        "actual_cost_usd": actual_cost or estimate,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }


def _source_url_for_type(source_links: list[dict[str, Any]], source_type: str) -> str:
    normalized = source_type.lower()
    for link in source_links:
        if normalized in _str(link.get("type")).lower() and _is_https_url(link.get("url")):
            return _str(link.get("url"))
    return ""


def _accepted(confidence: object) -> bool:
    return _confidence_text(confidence) in {"high", "medium"}


def _append_further_note(existing: object, text: str) -> str:
    base = _str(existing)
    if not text:
        return base
    block = f"Further enrichment:\n{text.strip()}"
    if block in base:
        return base
    return f"{base}\n\n{block}".strip() if base else block


def _apply_result(row: dict[str, Any], result: dict[str, Any], cost: float) -> tuple[dict[str, Any], list[str]]:
    updated = dict(row)
    filled: list[str] = []
    source_links = result.get("source_links") if isinstance(result.get("source_links"), list) else []
    confidence = _confidence_text(result.get("confidence"))
    dimension_confidence = _confidence_text(result.get("dimension_confidence") or confidence)
    image_confidence = _confidence_text(result.get("image_confidence") or confidence)

    updated["further_enrichment_used"] = "true"
    updated["further_enrichment_status"] = "review_only"
    updated["further_enrichment_confidence"] = confidence or "none"
    updated["further_enrichment_sources"] = json.dumps(source_links, ensure_ascii=True)
    updated["further_enrichment_cost_estimate"] = f"{cost:.4f}"
    updated["further_enrichment_model"] = _model_name()

    dimensions = _str(result.get("dimensions"))
    if dimensions and _accepted(dimension_confidence) and not _high_confidence(updated, "Dimension Confidence", "dimension_confidence"):
        if not has_complete_3d_dimensions(updated.get("Dimensions")):
            updated["Dimensions"] = dimensions
            updated["dimension_confidence"] = dimension_confidence
            updated["Dimension Confidence"] = dimension_confidence
            updated["dimension_parse_method"] = "further_enrichment_ai"
            updated["dimension_source_url"] = (
                _source_url_for_type(source_links, "dimension")
                or _str(result.get("spec_sheet_url"))
                or _str(result.get("product_page_url"))
                or _str(updated.get("dimension_source_url"))
            )
            filled.append("Dimensions")

    dimensions_are_protected = has_complete_3d_dimensions(updated.get("Dimensions")) and _high_confidence(
        updated,
        "Dimension Confidence",
        "dimension_confidence",
    )
    if not dimensions_are_protected:
        for target, source_key in (("Width (in)", "width_in"), ("Height (in)", "height_in"), ("Depth (in)", "depth_in")):
            value = _str(result.get(source_key))
            if value and not _str(updated.get(target)):
                updated[target] = value
                filled.append(target)

    image_url = _str(result.get("image_url"))
    existing_image_conf = _confidence_text(updated.get("image_confidence"))
    can_write_image = not _is_https_url(updated.get("Image URL")) or existing_image_conf in {"low", "none"}
    if _is_https_url(image_url) and _accepted(image_confidence) and can_write_image:
        updated["Image URL"] = image_url
        updated["image_confidence"] = image_confidence
        updated["image_source_url"] = _source_url_for_type(source_links, "image") or _str(result.get("product_page_url")) or image_url
        filled.append("Image URL")

    product_page_url = _str(result.get("product_page_url"))
    if _is_https_url(product_page_url) and (not _str(updated.get("Product URL")) or _confidence_text(updated.get("product_url_confidence")) in {"low", "none"}):
        updated["Product URL"] = product_page_url
        updated["selected_product_url"] = product_page_url
        updated["selected_product_url_confidence"] = confidence or "medium"
        filled.append("Product URL")

    spec_sheet_url = _str(result.get("spec_sheet_url")) or _source_url_for_type(source_links, "spec")
    if _is_https_url(spec_sheet_url) and not _str(updated.get("spec_sheet_url")):
        updated["spec_sheet_url"] = spec_sheet_url
        filled.append("spec_sheet_url")

    note_parts = []
    if result.get("notes"):
        note_parts.append(_str(result.get("notes")))
    for link in source_links[:6]:
        url = _str(link.get("url"))
        if url:
            note_parts.append(f"{_str(link.get('type')) or 'Source'}: {url} ({_str(link.get('confidence')) or confidence or 'unrated'})")
    if note_parts:
        updated["Notes"] = _append_further_note(updated.get("Notes"), "\n".join(note_parts))

    updated["further_enrichment_fields_filled"] = ", ".join(filled)
    updated["further_enrichment_status"] = "updated" if filled else "no_verified_fields"
    if not filled:
        updated["further_enrichment_error"] = "AI returned no high/medium confidence fields safe to write."

    return updated, filled


def further_enrich_dataframe(
    df: pd.DataFrame,
    *,
    enabled: bool,
    max_cost_usd: float = 0.25,
    max_cost_per_item_usd: float | None = 0.05,
    openai_call: Any | None = None,
) -> FurtherEnrichmentResult:
    result_df = df.copy()
    if result_df.empty:
        return FurtherEnrichmentResult(result_df, [], [], {"further_enrichment_rows_considered": 0})

    for column in (
        "further_enrichment_used",
        "further_enrichment_status",
        "further_enrichment_error",
        "further_enrichment_fields_filled",
        "further_enrichment_sources",
        "further_enrichment_cost_estimate",
        "further_enrichment_model",
    ):
        if column not in result_df.columns:
            result_df[column] = ""

    row_records = result_df.fillna("").to_dict("records")
    candidate_rows = [
        _row_prompt_payload(index, row)
        for index, row in enumerate(row_records)
        if _needs_further_enrichment(row)
    ]

    diagnostics: list[dict[str, Any]] = []
    errors: list[str] = []
    if not enabled:
        return FurtherEnrichmentResult(
            result_df,
            [],
            [{"status": "disabled", "candidate_rows": len(candidate_rows)}],
            {
                "further_enrichment_enabled": False,
                "further_enrichment_rows_considered": len(candidate_rows),
                "further_enrichment_rows_sent": 0,
                "further_enrichment_cost_usd": 0.0,
            },
        )

    max_cost_usd = max(0.0, min(float(max_cost_usd or 0), 5.0))
    if not candidate_rows:
        return FurtherEnrichmentResult(
            result_df,
            [],
            [{"status": "no_incomplete_rows"}],
            {
                "further_enrichment_enabled": True,
                "further_enrichment_rows_considered": 0,
                "further_enrichment_rows_sent": 0,
                "further_enrichment_cost_usd": 0.0,
            },
        )

    max_cost_per_item_usd = max(0.0, min(float(max_cost_per_item_usd or 0), 1.0))
    packed_rows, estimated_cost, skipped_budget = _pack_rows_under_budget(
        candidate_rows,
        max_cost_usd,
        max_cost_per_item_usd=max_cost_per_item_usd,
    )
    if not packed_rows:
        message = "Further enrichment skipped: estimated OpenAI cost would exceed the configured cap."
        for candidate in candidate_rows:
            index = int(candidate["row_id"])
            result_df.at[index, "further_enrichment_used"] = "false"
            result_df.at[index, "further_enrichment_status"] = "skipped_budget_cap"
            result_df.at[index, "further_enrichment_error"] = message
        return FurtherEnrichmentResult(
            result_df,
            [message],
            [{"status": "skipped_budget_cap", "estimated_cost_usd": estimated_cost, "candidate_rows": len(candidate_rows)}],
            {
                "further_enrichment_enabled": True,
                "further_enrichment_rows_considered": len(candidate_rows),
                "further_enrichment_rows_sent": 0,
                "further_enrichment_rows_skipped_budget": len(candidate_rows),
                "further_enrichment_cost_usd": 0.0,
                "further_enrichment_estimated_cost_usd": estimated_cost,
                "further_enrichment_max_cost_per_item_usd": max_cost_per_item_usd,
            },
        )

    try:
        call = openai_call or _call_openai
        payload, usage = call(packed_rows, max_cost_usd=max_cost_usd)
    except Exception as exc:
        message = f"Further enrichment skipped: {exc}"
        errors.append(message)
        for candidate in packed_rows:
            index = int(candidate["row_id"])
            result_df.at[index, "further_enrichment_used"] = "false"
            result_df.at[index, "further_enrichment_status"] = "openai_unavailable"
            result_df.at[index, "further_enrichment_error"] = message
        return FurtherEnrichmentResult(
            result_df,
            errors,
            [{"status": "openai_unavailable", "error": message, "rows_sent": len(packed_rows)}],
            {
                "further_enrichment_enabled": True,
                "further_enrichment_rows_considered": len(candidate_rows),
                "further_enrichment_rows_sent": len(packed_rows),
                "further_enrichment_rows_skipped_budget": skipped_budget,
                "further_enrichment_cost_usd": 0.0,
                "further_enrichment_estimated_cost_usd": estimated_cost,
                "further_enrichment_max_cost_per_item_usd": max_cost_per_item_usd,
            },
        )

    rows_by_id = {
        _str(item.get("row_id")): item
        for item in payload.get("rows", [])
        if isinstance(item, dict)
    }
    total_fields = 0
    updated_rows = 0
    actual_cost = float(usage.get("actual_cost_usd") or estimated_cost or 0)
    per_row_cost = actual_cost / max(len(packed_rows), 1)
    for candidate in packed_rows:
        row_id = _str(candidate["row_id"])
        index = int(row_id)
        ai_result = rows_by_id.get(row_id)
        if not ai_result:
            result_df.at[index, "further_enrichment_status"] = "no_ai_result"
            result_df.at[index, "further_enrichment_error"] = "OpenAI did not return a result for this row."
            continue
        updated_row, filled = _apply_result(dict(result_df.loc[index].fillna("")), ai_result, per_row_cost)
        for key, value in updated_row.items():
            if key not in result_df.columns:
                result_df[key] = ""
            result_df.at[index, key] = value
        if filled:
            updated_rows += 1
            total_fields += len(filled)
            try:
                saved = save_successful_source_from_row(updated_row, notes="Further enrichment AI result saved after review-safe writeback.")
                if saved:
                    result_df.at[index, "knowledge_base_updated"] = "true"
                    result_df.at[index, "knowledge_base_source_used"] = saved.get("product_page_url") or saved.get("dimension_source_url") or saved.get("image_source_url") or ""
            except Exception as exc:  # best effort only
                result_df.at[index, "further_enrichment_error"] = f"Knowledge base save failed: {exc}"

    for candidate in candidate_rows[len(packed_rows):]:
        index = int(candidate["row_id"])
        result_df.at[index, "further_enrichment_status"] = "skipped_budget_cap"
        result_df.at[index, "further_enrichment_error"] = "Skipped because further enrichment cap was reached."

    diagnostics.append(
        {
            "status": "complete",
            "rows_considered": len(candidate_rows),
            "rows_sent": len(packed_rows),
            "rows_updated": updated_rows,
            "fields_filled": total_fields,
            "rows_skipped_budget": skipped_budget,
            "model": usage.get("model") or _model_name(),
            "estimated_cost_usd": usage.get("estimated_cost_usd") or estimated_cost,
            "actual_cost_usd": actual_cost,
        }
    )
    return FurtherEnrichmentResult(
        result_df,
        errors,
        diagnostics,
        {
            "further_enrichment_enabled": True,
            "further_enrichment_rows_considered": len(candidate_rows),
            "further_enrichment_rows_sent": len(packed_rows),
            "further_enrichment_rows_updated": updated_rows,
            "further_enrichment_fields_filled": total_fields,
            "further_enrichment_rows_skipped_budget": skipped_budget,
            "further_enrichment_cost_usd": actual_cost,
            "further_enrichment_cost_per_row_usd": round(actual_cost / max(len(packed_rows), 1), 6),
            "further_enrichment_estimated_cost_usd": usage.get("estimated_cost_usd") or estimated_cost,
            "further_enrichment_max_cost_per_item_usd": max_cost_per_item_usd,
            "further_enrichment_model": usage.get("model") or _model_name(),
            "ai_calls_used": 1 if packed_rows else 0,
        },
    )
