"""Build downloadable enrichment diagnostics."""

from __future__ import annotations

import pandas as pd

from src.image_presence import row_has_image, row_image_status
from src.product_enrichment import build_search_queries, has_enough_search_identity

ENRICHMENT_DEBUG_COLUMNS = [
    "row_id", "product_name", "brand", "model_sku", "supplier",
    "product_url_initial", "source_pdf_filename", "source_page_number",
    "enrichment_ran", "search_enabled", "search_provider",
    "search_query_1", "search_query_2", "search_query_3",
    "search_results_count", "selected_result_url", "selected_result_domain",
    "selected_result_reason", "official_domain_detected", "search_error",
    "brand_registry_match", "brand_registry_domains_checked",
    "brand_search_queries_used", "candidate_pages_found",
    "candidate_page_scores", "selected_product_page_score",
    "selected_product_page_reason", "image_candidates_found",
    "selected_image_url", "selected_image_reason", "dimension_source_url",
    "dimension_confidence", "dimension_evidence", "web_lookup_error",
    "product_page_fetch_ran", "product_page_status_code", "product_page_final_url",
    "product_page_title", "product_page_contains_sku", "product_page_contains_product_name",
    "product_page_error", "dimensions_found", "dimensions_raw_text",
    "dimensions_source", "width_in", "height_in", "depth_in", "length_in",
    "diameter_in", "dimensions_confidence", "dimensions_error",
    "image_found", "image_source", "image_url", "local_image_path",
    "image_filename", "image_confidence", "image_error",
    "final_confidence", "needs_review", "missing_fields", "enrichment_skip_reason",
]


def _text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def build_enrichment_debug_dataframe(
    rows,
    *,
    dimension_diagnostics: list[dict] | None = None,
    image_diagnostics: list[dict] | None = None,
    search_enabled: bool = True,
) -> pd.DataFrame:
    if isinstance(rows, pd.DataFrame):
        row_list = [r.to_dict() for _, r in rows.iterrows()]
    else:
        row_list = list(rows or [])
    dim_by_idx = {d.get("row_index"): d for d in (dimension_diagnostics or [])}
    img_by_idx = {d.get("row_index"): d for d in (image_diagnostics or [])}
    out = []
    for idx, row in enumerate(row_list):
        dim = dim_by_idx.get(idx, {})
        img = img_by_idx.get(idx, {})
        queries = build_search_queries(row)
        missing = []
        if not _text(row.get("Product Name")):
            missing.append("Product Name")
        if not _text(row.get("Dimensions")):
            missing.append("Dimensions")
        if not row_has_image(row):
            missing.append("Image")
        skip_reason = ""
        if search_enabled and not has_enough_search_identity(row):
            skip_reason = "not_enough_identifying_info"
        record = {
            "row_id": _text(row.get("row_id") or row.get("Row ID") or idx),
            "product_name": _text(row.get("Product Name")),
            "brand": _text(row.get("Brand")),
            "model_sku": _text(row.get("Model/SKU")),
            "supplier": _text(row.get("Supplier")),
            "product_url_initial": _text(row.get("Product URL")),
            "source_pdf_filename": _text(row.get("_source_filename")),
            "source_page_number": row.get("_source_page_number") or "",
            "enrichment_ran": bool(dim or img or _text(row.get("Dimension Lookup Status"))),
            "search_enabled": search_enabled,
            "search_provider": "Brave" if search_enabled else "",
            "search_query_1": queries[0] if len(queries) > 0 else "",
            "search_query_2": queries[1] if len(queries) > 1 else "",
            "search_query_3": queries[2] if len(queries) > 2 else "",
            "search_results_count": len(dim.get("urls_checked", []) or []),
            "selected_result_url": dim.get("source_url", "") or _text(row.get("Product URL")),
            "selected_result_domain": dim.get("domain_used", ""),
            "selected_result_reason": dim.get("status", ""),
            "official_domain_detected": bool(dim.get("domain_used")),
            "search_error": dim.get("failure_reason", ""),
            "brand_registry_match": row.get("brand_registry_match", ""),
            "brand_registry_domains_checked": row.get("brand_registry_domains_checked", ""),
            "brand_search_queries_used": row.get("brand_search_queries_used", ""),
            "candidate_pages_found": row.get("candidate_pages_found", ""),
            "candidate_page_scores": row.get("candidate_page_scores", ""),
            "selected_product_page_score": row.get("selected_product_page_score", ""),
            "selected_product_page_reason": row.get("selected_product_page_reason", ""),
            "image_candidates_found": row.get("image_candidates_found", ""),
            "selected_image_url": row.get("selected_image_url", ""),
            "selected_image_reason": row.get("selected_image_reason", ""),
            "dimension_source_url": row.get("dimension_source_url", ""),
            "dimension_confidence": row.get("dimension_confidence", ""),
            "dimension_evidence": row.get("dimension_evidence", ""),
            "web_lookup_error": row.get("web_lookup_error", ""),
            "product_page_fetch_ran": bool(dim.get("urls_checked")),
            "product_page_status_code": "",
            "product_page_final_url": dim.get("source_url", ""),
            "product_page_title": "",
            "product_page_contains_sku": "",
            "product_page_contains_product_name": "",
            "product_page_error": dim.get("failure_reason", ""),
            "dimensions_found": bool(_text(row.get("Dimensions"))),
            "dimensions_raw_text": dim.get("evidence_text", "") or _text(row.get("Dimensions")),
            "dimensions_source": dim.get("source_url", "") or _text(row.get("Dimension Source URL")),
            "width_in": _text(row.get("Width (in)")),
            "height_in": _text(row.get("Height (in)")),
            "depth_in": _text(row.get("Depth (in)")),
            "length_in": _text(row.get("Length (in)")),
            "diameter_in": _text(row.get("Diameter (in)")),
            "dimensions_confidence": dim.get("confidence", "") or _text(row.get("Dimension Confidence")),
            "dimensions_error": dim.get("failure_reason", ""),
            "image_found": row_has_image(row),
            "image_source": img.get("image_source", "") or _text(row.get("image_source")),
            "image_url": img.get("image_url", "") or _text(row.get("Image URL")),
            "local_image_path": img.get("local_image_path", "") or _text(row.get("local_image_path") or row.get("Local Image Path")),
            "image_filename": img.get("image_filename", "") or _text(row.get("Image Filename") or row.get("local_image_filename")),
            "image_confidence": img.get("confidence", "") or _text(row.get("confidence")),
            "image_error": img.get("error", ""),
            "final_confidence": _text(row.get("Confidence Score") or row.get("confidence")),
            "needs_review": _text(row.get("Review Required") or row.get("needs_image_review")),
            "missing_fields": ";".join(missing),
            "enrichment_skip_reason": skip_reason,
        }
        out.append(record)
    return pd.DataFrame(out, columns=ENRICHMENT_DEBUG_COLUMNS)
