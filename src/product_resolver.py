"""Multi-pass verified product resolver.

Given an intake row, this module searches multiple candidate pages/PDFs, fetches
and scores each one, extracts useful upstream facts, and returns only a
HIGH/MEDIUM verified product candidate for automatic enrichment.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.brand_lookup_registry import registry_domains_for_brand
from src.brave_search import search_product_candidates
from src.manufacturer_domains import get_domain_for_brand
from src.product_evidence import (
    normalize_brand,
    normalize_sku,
    product_name_similarity,
)
from src.product_image_extraction import (
    ImageCandidate,
    extract_product_image_candidates,
    score_image_candidates,
)
from src.spec_extraction import (
    DimensionExtractionResult,
    extract_dimensions_from_html,
    extract_dimensions_from_pdf_bytes,
    extract_specs_from_verified_candidate,
)


@dataclass
class ProductCandidate:
    url: str = ""
    domain: str = ""
    title: str = ""
    snippet: str = ""
    html: str = ""
    text: str = ""
    source_type: str = "unknown"
    evidence_score: int = 0
    confidence: str = "none"
    matched_sku: bool = False
    matched_brand: bool = False
    matched_product_name: bool = False
    is_official_domain: bool = False
    rejection_reason: str = ""
    extracted_dimensions: str = ""
    extracted_image_url: str = ""
    extracted_fields: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    pdf_bytes: bytes = b""


@dataclass
class ProductResolutionResult:
    selected: ProductCandidate | None = None
    candidates: list[ProductCandidate] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    queries_tried: list[str] = field(default_factory=list)
    urls_checked: list[str] = field(default_factory=list)
    confidence: str = "none"
    evidence_score: int = 0
    selected_url: str = ""
    rejection_reason: str = ""
    error: str = ""


_MARKETPLACE_DOMAINS = (
    "amazon.",
    "ebay.",
    "etsy.",
    "facebook.",
    "houzz.",
    "instagram.",
    "pinterest.",
    "reddit.",
    "temu.",
    "walmart.",
)
_RETAILER_DOMAINS = (
    "1stdibs.com",
    "build.com",
    "chairish.com",
    "ferguson.com",
    "homedepot.com",
    "lumens.com",
    "lowes.com",
    "perigold.com",
    "wayfair.com",
)
_KNOWN_BRANDS = {
    "arteriors",
    "circa lighting",
    "four hands",
    "kallista",
    "kohler",
    "mcgee",
    "miele",
    "palecek",
    "restoration hardware",
    "scotsman",
    "sub zero",
    "visual comfort",
    "wolf",
}


def resolve_product_page(row: dict, session_cache=None, budget=None) -> ProductResolutionResult:
    mode = _budget_mode(budget)
    queries, official_domains = build_resolver_queries(row, mode=mode)
    candidates: list[ProductCandidate] = []
    urls_checked: list[str] = []
    seen_urls: set[str] = set()
    stop_all = False

    for query in queries:
        if stop_all:
            break
        if budget is not None and not budget.can_search() and not (session_cache is not None and query in session_cache.queries):
            budget.stop("search budget exhausted")
            break
        results = _search(query, row, session_cache, budget)
        for result in results[:5]:
            url = _text(getattr(result, "url", ""))
            key = _normalised_url(url)
            if not url or key in seen_urls:
                continue
            seen_urls.add(key)
            if budget is not None and not _can_fetch(budget):
                budget.stop("page fetch budget exhausted")
                stop_all = True
                break
            candidate = _candidate_from_search_result(result, official_domains)
            _fetch_candidate(candidate, session_cache, budget)
            _score_candidate(candidate, row, official_domains)
            _extract_candidate_assets(candidate, row)
            candidates.append(candidate)
            urls_checked.append(url)
            if candidate.confidence == "high":
                if budget is not None:
                    budget.stop("Stopped early: HIGH confidence official product page found")
                stop_all = True
                break
            if (
                candidate.is_official_domain
                and candidate.confidence == "medium"
                and candidate.extracted_dimensions
                and candidate.extracted_image_url
            ):
                if budget is not None:
                    budget.stop("Stopped early: dimensions and image found from verified product page")
                stop_all = True
                break

    selected = _select_candidate(candidates)
    diagnostics = [_diagnostic_record(candidate) for candidate in candidates]
    if session_cache is not None:
        setattr(session_cache, "product_resolution_diagnostics", diagnostics)

    if not selected:
        reason = getattr(budget, "stopped_reason", "") or "no_high_or_medium_candidate"
        return ProductResolutionResult(
            candidates=candidates,
            diagnostics=diagnostics,
            queries_tried=queries,
            urls_checked=urls_checked,
            rejection_reason=reason,
        )

    return ProductResolutionResult(
        selected=selected,
        candidates=candidates,
        diagnostics=diagnostics,
        queries_tried=queries,
        urls_checked=urls_checked,
        confidence=selected.confidence,
        evidence_score=selected.evidence_score,
        selected_url=selected.url,
    )


def build_resolver_queries(row: dict, mode: str = "standard") -> tuple[list[str], list[str]]:
    brand = _text(row.get("Brand"))
    sku = _text(row.get("Model/SKU") or row.get("SKU") or row.get("_extracted_model_sku"))
    product_name = _text(row.get("Product Name"))
    official_domains = _official_domains(row)
    queries: list[str] = []

    def add(query: str) -> None:
        cleaned = " ".join(query.split()).strip()
        if cleaned and cleaned not in queries:
            queries.append(cleaned)

    primary_domain = official_domains[0] if official_domains else ""
    if primary_domain and sku:
        add(f'site:{primary_domain} "{sku}"')
    if brand and sku:
        add(f'"{brand}" "{sku}" official product page')
    if primary_domain and sku:
        add(f'site:{primary_domain} "{sku}" spec sheet OR dimensions')

    if mode in {"deep", "manual_retry"}:
        for domain in official_domains:
            if sku:
                add(f'site:{domain} "{sku}" specifications')
                add(f'site:{domain} "{sku}" dimensions')
                add(f'site:{domain} "{sku}" product')
                add(f'site:{domain} "{sku}" filetype:pdf')
        if brand and sku:
            add(f'"{brand}" "{sku}" specifications')
            add(f'"{brand}" "{sku}" dimensions')
        if brand and product_name and sku:
            add(f'"{brand}" "{product_name}" "{sku}"')
        if sku and product_name:
            add(f'"{sku}" "{product_name}"')

    if mode == "manual_retry" and brand and product_name:
        add(f'"{brand}" "{product_name}" product image dimensions')
    if mode == "fast":
        queries = queries[:1]
    elif mode == "standard":
        queries = queries[:3]
    return queries, official_domains


def _search(query: str, row: dict, session_cache, budget) -> list:
    if session_cache is not None and query in session_cache.queries:
        return session_cache.queries[query]
    if budget is not None:
        if not budget.can_search():
            return []
        budget.consume_search()
    return search_product_candidates(query, _text(row.get("Brand")), session_cache=session_cache)


def _candidate_from_search_result(result, official_domains: list[str]) -> ProductCandidate:
    url = _text(getattr(result, "url", ""))
    domain = _domain(url)
    official = any(_domain_matches(domain, official_domain) for official_domain in official_domains)
    is_pdf = _is_pdf_url(url)
    source_type = "unknown"
    if official and is_pdf:
        source_type = "manufacturer_pdf"
    elif official:
        source_type = "manufacturer_page"
    elif _is_retailer_domain(domain) and is_pdf:
        source_type = "retailer_pdf"
    elif _is_retailer_domain(domain):
        source_type = "retailer_page"
    return ProductCandidate(
        url=url,
        domain=domain,
        title=_text(getattr(result, "title", "")),
        snippet=_text(getattr(result, "description", "")),
        source_type=source_type,
        is_official_domain=official,
    )


def _fetch_candidate(candidate: ProductCandidate, session_cache, budget) -> None:
    if session_cache is not None and candidate.url in session_cache.urls:
        cached = session_cache.urls[candidate.url]
        if isinstance(cached, bytes):
            candidate.pdf_bytes = cached
        else:
            candidate.html = str(cached or "")
            candidate.text = _html_to_text(candidate.html)
        return
    if budget is not None:
        budget.consume_fetch()
    try:
        resp = httpx.get(
            candidate.url,
            headers={"User-Agent": "SCH-DesignOps/1.0"},
            timeout=12,
            follow_redirects=True,
        )
        content_type = str(resp.headers.get("content-type", "") or "").lower()
        if "pdf" in content_type or _is_pdf_url(str(resp.url)):
            candidate.pdf_bytes = resp.content
            candidate.source_type = candidate.source_type.replace("_page", "_pdf") if "_page" in candidate.source_type else candidate.source_type
            candidate.text = _pdf_text(resp.content)
            if session_cache is not None:
                session_cache.urls[candidate.url] = resp.content
        else:
            candidate.html = resp.text
            candidate.text = _html_to_text(resp.text)
            if session_cache is not None:
                session_cache.urls[candidate.url] = resp.text
    except Exception as exc:
        candidate.rejection_reason = f"fetch_failed:{exc}"


def _score_candidate(candidate: ProductCandidate, row: dict, official_domains: list[str]) -> None:
    if candidate.rejection_reason:
        candidate.confidence = "none"
        return
    if _is_blocked_domain(candidate.domain):
        candidate.confidence = "none"
        candidate.rejection_reason = "blocked_domain"
        return

    sku = _text(row.get("Model/SKU") or row.get("SKU") or row.get("_extracted_model_sku"))
    sku_norm = normalize_sku(sku)
    brand = _text(row.get("Brand"))
    brand_norm = normalize_brand(brand)
    product_name = _text(row.get("Product Name"))
    haystack = " ".join([candidate.url, candidate.title, candidate.snippet, candidate.html, candidate.text])
    haystack_norm = _norm(haystack)
    compact_haystack = re.sub(r"[^a-z0-9]", "", haystack.lower())

    candidate.matched_sku = bool(sku_norm and sku_norm in compact_haystack)
    candidate.matched_brand = _brand_matches(brand_norm, candidate.domain, haystack_norm)
    candidate.matched_product_name = product_name_similarity(product_name, haystack) >= 0.68 if product_name else False
    candidate.is_official_domain = any(_domain_matches(candidate.domain, domain) for domain in official_domains)

    if brand_norm and not candidate.matched_brand and _known_other_brand_present(brand_norm, candidate.domain, haystack_norm):
        candidate.confidence = "none"
        candidate.rejection_reason = "wrong_brand"
        return

    score = 0
    if candidate.matched_sku:
        score += 50
    if candidate.matched_brand:
        score += 15
    if candidate.matched_product_name:
        score += 15
    if candidate.is_official_domain:
        score += 25
    if _has_product_jsonld(candidate.html):
        score += 10
    if _has_spec_table(candidate.html):
        score += 10
    if _has_image_candidates(candidate.html):
        score += 10
    if candidate.source_type == "manufacturer_pdf":
        score += 20

    if sku_norm and not candidate.matched_sku:
        candidate.confidence = "low" if score > 0 else "none"
        candidate.rejection_reason = "sku_not_found"
        candidate.evidence_score = min(score, 59)
        return

    if candidate.is_official_domain and candidate.matched_sku and score >= 80:
        confidence = "high"
    elif candidate.matched_sku and score >= 60:
        confidence = "medium"
    elif score > 0:
        confidence = "low"
    else:
        confidence = "none"

    if candidate.source_type.startswith("retailer") and confidence == "high":
        confidence = "medium"
    candidate.confidence = confidence
    candidate.evidence_score = max(0, min(100, score))
    if confidence == "none":
        candidate.rejection_reason = "insufficient_evidence"


def _extract_candidate_assets(candidate: ProductCandidate, row: dict) -> None:
    if candidate.confidence not in {"high", "medium"}:
        return
    dim_result = (
        extract_dimensions_from_pdf_bytes(candidate.pdf_bytes, row)
        if candidate.pdf_bytes
        else extract_dimensions_from_html(candidate.html, row)
    )
    candidate.extracted_dimensions = dim_result.dimensions
    fields = extract_specs_from_verified_candidate(candidate, row)
    if candidate.extracted_dimensions:
        fields.setdefault("Dimensions", candidate.extracted_dimensions)
    fields.setdefault("dimension_confidence", dim_result.confidence)
    if dim_result.cutout_dimensions:
        fields["cutout_dimensions"] = dim_result.cutout_dimensions

    image_candidates: list[ImageCandidate] = []
    if candidate.html:
        image_candidates = extract_product_image_candidates(candidate.html, candidate.url, row)
        score_image_candidates(image_candidates, row, candidate)
        best_image = _select_image(image_candidates)
        if best_image and best_image.confidence in {"HIGH", "MEDIUM"}:
            candidate.extracted_image_url = best_image.url
            fields["Image URL"] = best_image.url
            fields["image_confidence"] = best_image.confidence
            fields["image_source"] = best_image.source
            fields["image_evidence"] = ";".join(best_image.evidence)
    candidate.extracted_fields = fields
    candidate.diagnostics.update({
        "dimension_confidence": dim_result.confidence,
        "dimension_evidence": dim_result.evidence_text,
        "image_candidates_found": len(image_candidates),
        "image_candidates": [image.__dict__ for image in image_candidates],
    })


def _select_candidate(candidates: list[ProductCandidate]) -> ProductCandidate | None:
    eligible = [candidate for candidate in candidates if candidate.confidence in {"high", "medium"}]
    if not eligible:
        return None
    rank = {"high": 2, "medium": 1}
    return max(
        eligible,
        key=lambda candidate: (
            rank.get(candidate.confidence, 0),
            candidate.evidence_score,
            1 if candidate.is_official_domain else 0,
            1 if candidate.extracted_dimensions else 0,
            1 if candidate.extracted_image_url else 0,
        ),
    )


def _select_image(candidates: list[ImageCandidate]) -> ImageCandidate | None:
    eligible = [candidate for candidate in candidates if not candidate.rejection_reason]
    if not eligible:
        return None
    rank = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
    return max(eligible, key=lambda candidate: (rank.get(candidate.confidence, 0), candidate.score))


def _diagnostic_record(candidate: ProductCandidate) -> dict[str, Any]:
    return {
        "url": candidate.url,
        "domain": candidate.domain,
        "source_type": candidate.source_type,
        "confidence": candidate.confidence,
        "evidence_score": candidate.evidence_score,
        "matched_sku": candidate.matched_sku,
        "matched_brand": candidate.matched_brand,
        "matched_product_name": candidate.matched_product_name,
        "is_official_domain": candidate.is_official_domain,
        "rejection_reason": candidate.rejection_reason,
        "extracted_dimensions": candidate.extracted_dimensions,
        "extracted_image_url": candidate.extracted_image_url,
        "image_candidates_found": candidate.diagnostics.get("image_candidates_found", 0),
    }


def _official_domains(row: dict) -> list[str]:
    brand = _text(row.get("Brand"))
    domains: list[str] = []
    direct = get_domain_for_brand(brand)
    if direct and direct[0] not in domains:
        domains.append(direct[0])
    for domain in registry_domains_for_brand(brand):
        if domain not in domains:
            domains.append(domain)
    return domains


def _can_fetch(budget) -> bool:
    return budget is None or budget.can_fetch()


def _budget_mode(budget) -> str:
    mode = str(getattr(budget, "mode", "standard") or "standard")
    return mode if mode in {"fast", "standard", "deep", "manual_retry"} else "standard"


def _domain(url: str) -> str:
    host = urllib.parse.urlparse(url).hostname or ""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def _domain_matches(domain: str, root: str) -> bool:
    root = root.lower().strip()
    return bool(root and (domain == root or domain.endswith("." + root)))


def _normalised_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    path = re.sub(r"/+$", "", parsed.path or "/")
    return urllib.parse.urlunparse((parsed.scheme.lower(), host, path, "", "", ""))


def _is_pdf_url(url: str) -> bool:
    return urllib.parse.urlparse(url).path.lower().endswith(".pdf")


def _is_blocked_domain(domain: str) -> bool:
    return any(marker in domain for marker in _MARKETPLACE_DOMAINS)


def _is_retailer_domain(domain: str) -> bool:
    return any(_domain_matches(domain, root) for root in _RETAILER_DOMAINS)


def _brand_matches(brand_norm: str, domain: str, haystack_norm: str) -> bool:
    if not brand_norm:
        return False
    compact_brand = brand_norm.replace(" ", "")
    compact_domain = re.sub(r"[^a-z0-9]", "", domain)
    if len(compact_brand) >= 3 and compact_brand in compact_domain:
        return True
    return bool(re.search(rf"(^|\s){re.escape(brand_norm)}(\s|$)", haystack_norm))


def _known_other_brand_present(brand_norm: str, domain: str, haystack_norm: str) -> bool:
    compact_domain = re.sub(r"[^a-z0-9]", "", domain)
    for known in _KNOWN_BRANDS:
        if known == brand_norm:
            continue
        compact_known = known.replace(" ", "")
        if compact_known and compact_known in compact_domain:
            return True
        if re.search(rf"(^|\s){re.escape(known)}(\s|$)", haystack_norm):
            return True
    return False


def _has_product_jsonld(html: str) -> bool:
    return bool(re.search(r'"@type"\s*:\s*"?Product"?', html or "", re.I))


def _has_spec_table(html: str) -> bool:
    return bool(re.search(r"<table|<dl|dimension|width|height|depth", html or "", re.I))


def _has_image_candidates(html: str) -> bool:
    return bool(re.search(r"og:image|twitter:image|<img|srcset|data-src|\"image\"", html or "", re.I))


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _pdf_text(pdf_bytes: bytes) -> str:
    try:
        import fitz

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return "\n".join(page.get_text("text") for page in doc)
    except Exception:
        return ""


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text
