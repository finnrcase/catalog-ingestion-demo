"""Official brand/supplier product-page lookup and scoring."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

from src.brand_lookup_registry import build_brand_search_queries, registry_domains_for_brand
from src.brave_search import search_product_candidates
from src.image_evidence import product_name_appears_in_text, sku_appears_in_text

_BAD_DOMAINS = ("pinterest.", "blogspot.", "reddit.", "houzz.", "amazon.", "ebay.")


@dataclass
class ProductPageLookupResult:
    selected_url: str = ""
    selected_domain: str = ""
    score: int = 0
    confidence: str = "NONE"
    reason: str = ""
    queries_used: list[str] = field(default_factory=list)
    candidate_pages: list[dict] = field(default_factory=list)
    registry_match: bool = False
    registry_domains_checked: list[str] = field(default_factory=list)
    error: str = ""


def _text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def domain_from_url(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _domain_matches(domain: str, root: str) -> bool:
    return domain == root or domain.endswith("." + root)


def build_official_lookup_queries(row: dict) -> tuple[list[str], bool, list[str]]:
    registry_queries, entry = build_brand_search_queries(row)
    brand = _text(row.get("Brand"))
    sku = _text(row.get("Model/SKU") or row.get("SKU"))
    name = _text(row.get("Product Name"))
    supplier = _text(row.get("Supplier"))
    queries = list(registry_queries)

    def add(query: str) -> None:
        cleaned = " ".join(query.split()).strip()
        if cleaned and cleaned not in queries:
            queries.append(cleaned)

    if brand and sku:
        add(f'"{brand}" "{sku}" product')
    if brand and name and sku:
        add(f'"{brand}" "{name}" "{sku}"')
    if supplier and name and sku:
        add(f'"{supplier}" "{name}" "{sku}"')
    if brand and name:
        add(f'"{brand}" "{name}" dimensions')
    return queries, bool(entry), registry_domains_for_brand(brand)


def score_search_result(row: dict, result, registry_domains: list[str] | None = None) -> tuple[int, list[str]]:
    registry_domains = registry_domains or []
    brand = _text(row.get("Brand"))
    supplier = _text(row.get("Supplier"))
    sku = _text(row.get("Model/SKU") or row.get("SKU"))
    name = _text(row.get("Product Name"))
    url = _text(getattr(result, "url", ""))
    domain = domain_from_url(url)
    haystack = f"{getattr(result, 'title', '')} {getattr(result, 'description', '')} {url}"

    score = int(getattr(result, "domain_score", 0) or 0)
    reasons = [f"domain_score:{score}"]
    if registry_domains and any(_domain_matches(domain, d) for d in registry_domains):
        score += 70
        reasons.append("brand_registry_domain")
    brand_slug = re.sub(r"[^a-z0-9]+", "", brand.lower())
    supplier_slug = re.sub(r"[^a-z0-9]+", "", supplier.lower())
    domain_compact = re.sub(r"[^a-z0-9]+", "", domain)
    if brand_slug and len(brand_slug) >= 3 and brand_slug in domain_compact:
        score += 45
        reasons.append("official_brand_domain")
    if supplier_slug and len(supplier_slug) >= 3 and supplier_slug in domain_compact:
        score += 45
        reasons.append("official_supplier_domain")
    if sku and sku_appears_in_text(sku, haystack):
        score += 55
        reasons.append("sku_match")
    if name and product_name_appears_in_text(name, haystack):
        score += 25
        reasons.append("product_name_match")
    path = urllib.parse.urlparse(url).path.lower()
    if re.search(r"product|products|item|sku|spec|detail", path):
        score += 15
        reasons.append("product_like_path")
    if any(bad in domain for bad in _BAD_DOMAINS):
        score -= 80
        reasons.append("bad_domain_penalty")
    if re.search(r"/search|/cart|/account|/login", path):
        score -= 40
        reasons.append("non_product_path_penalty")
    if sku and "sku_match" not in reasons and "brand_registry_domain" not in reasons:
        score -= 20
        reasons.append("missing_sku_penalty")
    return score, reasons


def confidence_from_score(score: int, reasons: list[str]) -> str:
    if score >= 135 and "sku_match" in reasons and any(r in reasons for r in ("brand_registry_domain", "official_brand_domain", "official_supplier_domain")):
        return "HIGH"
    if score >= 95 and any(r in reasons for r in ("brand_registry_domain", "official_brand_domain", "official_supplier_domain")):
        return "MEDIUM"
    if score >= 70:
        return "LOW"
    return "NONE"


def lookup_official_product_page(row: dict, *, session_cache=None, search_fn=None) -> ProductPageLookupResult:
    search_fn = search_fn or search_product_candidates
    queries, registry_match, registry_domains = build_official_lookup_queries(row)
    debug_candidates: list[dict] = []
    best_url = ""
    best_domain = ""
    best_score = -999
    best_reasons: list[str] = []

    for query in queries:
        try:
            results = search_fn(query, _text(row.get("Brand")), session_cache=session_cache)
        except TypeError:
            results = search_fn(query, _text(row.get("Brand")))
        except Exception as exc:
            return ProductPageLookupResult(
                queries_used=queries,
                registry_match=registry_match,
                registry_domains_checked=registry_domains,
                error=str(exc),
            )
        for result in results:
            score, reasons = score_search_result(row, result, registry_domains)
            url = _text(getattr(result, "url", ""))
            record = {"url": url, "score": score, "reasons": ";".join(reasons)}
            debug_candidates.append(record)
            if score > best_score:
                best_url = url
                best_domain = domain_from_url(url)
                best_score = score
                best_reasons = reasons

    confidence = confidence_from_score(best_score, best_reasons) if best_url else "NONE"
    if confidence == "NONE":
        return ProductPageLookupResult(
            score=max(best_score, 0),
            confidence="NONE",
            reason="no_confident_official_page" if best_url else "no_results",
            queries_used=queries,
            candidate_pages=debug_candidates,
            registry_match=registry_match,
            registry_domains_checked=registry_domains,
        )
    return ProductPageLookupResult(
        selected_url=best_url,
        selected_domain=best_domain,
        score=best_score,
        confidence=confidence,
        reason=";".join(best_reasons),
        queries_used=queries,
        candidate_pages=debug_candidates,
        registry_match=registry_match,
        registry_domains_checked=registry_domains,
    )
