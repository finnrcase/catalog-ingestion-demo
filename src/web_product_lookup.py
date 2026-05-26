"""Official product-page lookup for image acquisition.

Search is used to find likely manufacturer/supplier product pages. Images are
then extracted from those pages with the same page-grounded logic used for
known Product URLs; random image search thumbnails are intentionally ignored.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field

from src.brave_search import search_product_candidates
from src.image_evidence import is_official_domain, product_name_appears_in_text, sku_appears_in_text
from src.official_product_lookup import (
    build_official_lookup_queries,
    lookup_official_product_page,
    score_search_result,
)
from src.product_page_images import ProductPageImageResult, extract_image_from_url


@dataclass
class WebLookupResult:
    image_result: ProductPageImageResult = field(default_factory=ProductPageImageResult)
    debug: dict = field(default_factory=dict)


def _str_val(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _domain(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def build_image_lookup_queries(row: dict) -> list[str]:
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU") or row.get("SKU"))
    name = _str_val(row.get("Product Name"))
    supplier = _str_val(row.get("Supplier"))
    product_url_domain = _domain(_str_val(row.get("Product URL")))
    raw = [
        f'"{brand}" "{model}" product image',
        f'"{brand}" "{name}" "{model}"',
        f'"{brand}" "{model}" "{name}" product image',
        f'"{supplier}" "{name}" "{model}"',
        f'"{brand}" "{model}" product photo',
        f'"{brand}" "{name}" dimensions image',
    ]
    if product_url_domain and model:
        raw[3:3] = [
            f'site:{product_url_domain} "{model}" product image',
            f'site:{product_url_domain} "{model}" images',
        ]
    queries: list[str] = []
    for query in raw:
        cleaned = " ".join(query.split()).strip()
        if cleaned and cleaned not in queries and cleaned.replace('"', '').strip() != "product":
            queries.append(cleaned)
    return queries


def _confidence_rank(confidence: str) -> int:
    return {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(str(confidence or "").upper(), 0)


def _merge_page_image_debug(debug: dict, image: ProductPageImageResult, *, prefix: str = "web") -> None:
    image_debug = image.debug or {}
    debug["image_candidates_found"] = max(
        int(debug.get("image_candidates_found") or 0),
        int(image_debug.get("images_found") or 0),
    )
    debug.setdefault("web_image_candidates", [])
    debug["web_image_candidates"].extend(list(image_debug.get("image_candidates", []))[:8])
    debug.setdefault("web_image_rejection_reasons", [])
    debug["web_image_rejection_reasons"].extend(list(image_debug.get("rejection_reasons", []))[:8])
    if image.image_found:
        debug[f"{prefix}_selected_image_url"] = image.image_url


def _search_image_candidate_pages(
    row: dict,
    queries: list[str],
    *,
    session_cache=None,
    skip_urls: set[str] | None = None,
    debug: dict,
) -> ProductPageImageResult:
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU") or row.get("SKU"))
    skip_urls = skip_urls or set()
    best = ProductPageImageResult(error="no_image_search_match")
    debug.setdefault("fallback_image_queries_used", [])
    debug.setdefault("fallback_image_candidate_pages", [])

    for query in queries[:3]:
        debug["fallback_image_queries_used"].append(query)
        try:
            results = search_product_candidates(query, brand, session_cache=session_cache)
        except TypeError:
            results = search_product_candidates(query, brand)
        except Exception as exc:
            debug.setdefault("web_rejection_reasons", []).append(f"{query}: search_failed:{exc}")
            continue

        for result in results[:3]:
            url = _str_val(getattr(result, "url", ""))
            if not url or url in skip_urls:
                continue
            skip_urls.add(url)
            score, reasons = _result_score(row, result)
            reason_text = ";".join(reasons)
            record = {
                "query": query,
                "url": url,
                "score": score,
                "reasons": reason_text,
                "selected_image_url": "",
                "image_confidence": "NONE",
                "rejection_reason": "",
            }
            debug["fallback_image_candidate_pages"].append(record)

            # Fallback image search is allowed to use retailers, but not vague
            # pages. Exact model evidence keeps us from grabbing related products.
            if model and "sku_match" not in reasons:
                record["rejection_reason"] = "missing_exact_model_in_result"
                continue
            if score < 70:
                record["rejection_reason"] = "candidate_page_score_too_low"
                continue

            image = extract_image_from_url(url, row, source_prefix="image_search")
            _merge_page_image_debug(debug, image, prefix="fallback")
            record["selected_image_url"] = image.image_url
            record["image_confidence"] = image.confidence
            record["rejection_reason"] = image.error or ""
            if not image.image_found or _confidence_rank(image.confidence) < _confidence_rank("MEDIUM"):
                continue
            if _confidence_rank(image.confidence) > _confidence_rank(best.confidence):
                best = image
                debug["selected_product_page_url"] = url
                debug["selected_product_page_domain"] = _domain(url)
                debug["selected_product_page_score"] = score
                debug["selected_product_page_reason"] = reason_text
            if image.confidence == "HIGH":
                return image

    return best


def _result_score(row: dict, result) -> tuple[int, list[str]]:
    official_queries, _registry_match, domains = build_official_lookup_queries(row)
    score, reasons = score_search_result(row, result, domains)
    if score:
        return score, reasons
    brand = _str_val(row.get("Brand"))
    supplier = _str_val(row.get("Supplier"))
    model = _str_val(row.get("Model/SKU") or row.get("SKU"))
    name = _str_val(row.get("Product Name"))
    haystack = f"{getattr(result, 'title', '')} {getattr(result, 'description', '')} {getattr(result, 'url', '')}"
    score = int(getattr(result, "domain_score", 0) or 0)
    reasons: list[str] = [f"domain_score:{score}"]

    url = getattr(result, "url", "")
    if brand and is_official_domain(url, brand):
        score += 50
        reasons.append("official_brand_domain")
    if supplier and is_official_domain(url, supplier):
        score += 45
        reasons.append("official_supplier_domain")
    if model and sku_appears_in_text(model, haystack):
        score += 35
        reasons.append("sku_match")
    if name and product_name_appears_in_text(name, haystack):
        score += 15
        reasons.append("product_name_match")
    domain = _domain(url)
    if any(bad in domain for bad in ("pinterest.", "amazon.", "ebay.", "walmart.", "blogspot.")):
        score -= 60
        reasons.append("marketplace_or_blog_penalty")
    return score, reasons


def lookup_official_product_image(row: dict, *, session_cache=None) -> WebLookupResult:
    queries = build_image_lookup_queries(row)
    debug = {
        "web_lookup_ran": True,
        "web_queries_used": queries,
        "web_results_found": 0,
        "official_candidate_pages": [],
        "selected_product_page_url": "",
        "selected_product_page_domain": "",
        "selected_web_image_url": "",
        "web_confidence_reason": "",
        "web_rejection_reasons": [],
        "brand_registry_match": False,
        "brand_registry_domains_checked": [],
        "brand_search_queries_used": [],
        "candidate_pages_found": 0,
        "candidate_page_scores": [],
        "selected_product_page_score": 0,
        "selected_product_page_reason": "",
        "image_candidates_found": 0,
        "selected_image_url": "",
        "selected_image_reason": "",
        "image_lookup_queries_used": queries,
        "fallback_image_queries_used": [],
        "fallback_image_candidate_pages": [],
        "web_image_candidates": [],
        "web_image_rejection_reasons": [],
    }

    brand = _str_val(row.get("Brand"))
    page = lookup_official_product_page(
        row,
        session_cache=session_cache,
        search_fn=search_product_candidates,
    )
    debug["brand_registry_match"] = page.registry_match
    debug["brand_registry_domains_checked"] = page.registry_domains_checked
    debug["brand_search_queries_used"] = page.queries_used
    debug["web_queries_used"] = page.queries_used or queries
    debug["candidate_pages_found"] = len(page.candidate_pages)
    debug["web_results_found"] = len(page.candidate_pages)
    debug["candidate_page_scores"] = page.candidate_pages
    debug["selected_product_page_score"] = page.score
    debug["selected_product_page_reason"] = page.reason
    debug["web_confidence_reason"] = page.reason

    if not page.selected_url:
        if page.error:
            debug["web_rejection_reasons"].append(page.error)
        else:
            debug["web_rejection_reasons"].append(page.reason or "no_web_results")
        fallback = _search_image_candidate_pages(
            row,
            queries,
            session_cache=session_cache,
            skip_urls=set(),
            debug=debug,
        )
        return WebLookupResult(fallback if fallback.image_found else ProductPageImageResult(error="no_web_results"), debug)

    page_url = page.selected_url
    debug["selected_product_page_url"] = page_url
    debug["selected_product_page_domain"] = page.selected_domain

    if page.confidence == "NONE":
        debug["web_rejection_reasons"].append(f"best_score_too_low:{page.score}")
        return WebLookupResult(ProductPageImageResult(error="no_confident_product_page"), debug)

    image = extract_image_from_url(page_url, row, source_prefix="official_site")
    _merge_page_image_debug(debug, image, prefix="web")
    debug["image_candidates_found"] = image.debug.get("images_found", 0)
    if image.image_found:
        debug["selected_web_image_url"] = image.image_url
        debug["selected_image_url"] = image.image_url
        debug["selected_image_reason"] = ";".join(image.evidence)
        image.evidence.extend(page.reason.split(";"))
        if image.confidence in {"HIGH", "MEDIUM"}:
            return WebLookupResult(image, debug)
    else:
        debug["web_rejection_reasons"].append(image.error or "no_image_on_selected_page")

    fallback = _search_image_candidate_pages(
        row,
        queries,
        session_cache=session_cache,
        skip_urls={page_url},
        debug=debug,
    )
    if fallback.image_found and _confidence_rank(fallback.confidence) > _confidence_rank(image.confidence):
        debug["selected_web_image_url"] = fallback.image_url
        debug["selected_image_url"] = fallback.image_url
        debug["selected_image_reason"] = ";".join(fallback.evidence)
        return WebLookupResult(fallback, debug)
    return WebLookupResult(image, debug)
