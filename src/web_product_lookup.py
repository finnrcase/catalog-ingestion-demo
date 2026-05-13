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
    raw = [
        f'"{brand}" "{model}" product',
        f'"{brand}" "{name}" "{model}"',
        f'"{supplier}" "{name}" "{model}"',
        f'"{brand}" "{name}" dimensions image',
    ]
    queries: list[str] = []
    for query in raw:
        cleaned = " ".join(query.split()).strip()
        if cleaned and cleaned not in queries and cleaned.replace('"', '').strip() != "product":
            queries.append(cleaned)
    return queries


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
        out = ProductPageImageResult(error="no_web_results")
        return WebLookupResult(out, debug)

    page_url = page.selected_url
    debug["selected_product_page_url"] = page_url
    debug["selected_product_page_domain"] = page.selected_domain

    if page.confidence == "NONE":
        debug["web_rejection_reasons"].append(f"best_score_too_low:{page.score}")
        return WebLookupResult(ProductPageImageResult(error="no_confident_product_page"), debug)

    image = extract_image_from_url(page_url, row, source_prefix="official_site")
    debug["image_candidates_found"] = image.debug.get("images_found", 0)
    if image.image_found:
        debug["selected_web_image_url"] = image.image_url
        debug["selected_image_url"] = image.image_url
        debug["selected_image_reason"] = ";".join(image.evidence)
        image.evidence.extend(page.reason.split(";"))
    else:
        debug["web_rejection_reasons"].append(image.error or "no_image_on_selected_page")
    return WebLookupResult(image, debug)
