"""Official brand/supplier product-page lookup and scoring."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

import httpx

from src.brand_lookup_registry import build_brand_search_queries, registry_domains_for_brand
from src.brave_search import search_product_candidates
from src.image_evidence import product_name_appears_in_text, sku_appears_in_text
from src.manufacturer_domains import get_domain_for_brand

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


@dataclass
class ProductPageValidationResult:
    valid: bool = False
    reason: str = ""
    status_code: int = 0
    content_type: str = ""
    sku_match: bool = False
    brand_match: bool = False
    product_name_match: bool = False
    generic_page: bool = False


def _text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def domain_from_url(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _domain_matches(domain: str, root: str) -> bool:
    return domain == root or domain.endswith("." + root)


def _manufacturer_domains_for_row(row: dict) -> list[str]:
    brand = _text(row.get("Brand"))
    domains: list[str] = []
    direct = get_domain_for_brand(brand)
    if direct:
        domains.append(direct[0])
    for domain in registry_domains_for_brand(brand):
        if domain not in domains:
            domains.append(domain)
    return domains


def build_official_lookup_queries(row: dict) -> tuple[list[str], bool, list[str]]:
    brand = _text(row.get("Brand"))
    sku = _text(row.get("Model/SKU") or row.get("SKU"))
    name = _text(row.get("Product Name"))
    supplier = _text(row.get("Supplier"))
    registry_queries, entry = build_brand_search_queries(row)
    registry_domains = _manufacturer_domains_for_row(row)
    queries: list[str] = []

    def add(query: str) -> None:
        cleaned = " ".join(query.split()).strip()
        if cleaned and cleaned not in queries:
            queries.append(cleaned)

    # PDF manufacturer lookup flow: use extracted SKU/model as the primary
    # search key, prefer confirmed manufacturer domains, and only save Product
    # URL after page validation when validate_pages=True.
    if brand and sku:
        add(f"{brand} {sku} official product page")
        add(f"{brand} {sku} dimensions")
    for domain in registry_domains:
        if sku:
            add(f"site:{domain} {sku}")
    for query in registry_queries:
        add(query)
    if brand and sku:
        add(f'"{brand}" "{sku}" product')
    if brand and name and sku:
        add(f'"{brand}" "{name}" "{sku}"')
    if supplier and name and sku:
        add(f'"{supplier}" "{name}" "{sku}"')
    if brand and name:
        add(f'"{brand}" "{name}" dimensions')
    return queries, bool(entry), registry_domains


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
    if any(reason.startswith("page_validation_failed") for reason in reasons):
        return "NONE"
    if score >= 135 and "sku_match" in reasons and any(r in reasons for r in ("brand_registry_domain", "official_brand_domain", "official_supplier_domain")):
        return "HIGH"
    if score >= 95 and any(r in reasons for r in ("brand_registry_domain", "official_brand_domain", "official_supplier_domain")):
        return "MEDIUM"
    if score >= 70:
        return "LOW"
    return "NONE"


_GENERIC_PATH_RE = re.compile(
    r"(^|/)(search|results|category|categories|collections?|catalog|shop|browse|cart|account|login)(/)?$"
    r"|/(search|results)(/|$)",
    re.IGNORECASE,
)


def _html_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _brand_appears(row: dict, url: str, page_text: str, registry_domains: list[str]) -> bool:
    brand = _text(row.get("Brand"))
    supplier = _text(row.get("Supplier"))
    domain = domain_from_url(url)
    if registry_domains and any(_domain_matches(domain, d) for d in registry_domains):
        return True
    for value in (brand, supplier):
        slug = re.sub(r"[^a-z0-9]+", "", value.lower())
        if len(slug) >= 3 and slug in re.sub(r"[^a-z0-9]+", "", domain):
            return True
        if value and re.search(re.escape(value), page_text, re.IGNORECASE):
            return True
    return False


def _looks_generic_page(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/").lower()
    query = parsed.query.lower()
    if not path and not query:
        return True
    if _GENERIC_PATH_RE.search("/" + path):
        return True
    return "search" in query or "q=" in query


def validate_official_product_page(
    row: dict,
    url: str,
    *,
    registry_domains: list[str] | None = None,
    timeout: float = 8.0,
) -> ProductPageValidationResult:
    """Fetch and validate an official product page before saving Product URL."""
    registry_domains = registry_domains or _manufacturer_domains_for_row(row)
    sku = _text(row.get("Model/SKU") or row.get("SKU") or row.get("_extracted_model_sku"))
    name = _text(row.get("Product Name"))
    generic_page = _looks_generic_page(url)
    if not sku:
        return ProductPageValidationResult(reason="missing_sku", generic_page=generic_page)

    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": "SCH-DesignOps/1.0"},
            timeout=timeout,
            follow_redirects=True,
        )
    except Exception as exc:
        return ProductPageValidationResult(reason=f"fetch_failed:{exc}", generic_page=generic_page)

    content_type = str(resp.headers.get("content-type", "") or "").lower()
    status_code = int(getattr(resp, "status_code", 0) or 0)
    if status_code != 200:
        return ProductPageValidationResult(
            reason=f"status_{status_code}",
            status_code=status_code,
            content_type=content_type,
            generic_page=generic_page,
        )
    if content_type and "html" not in content_type and "text/" not in content_type:
        return ProductPageValidationResult(
            reason=f"non_html_content:{content_type}",
            status_code=status_code,
            content_type=content_type,
            generic_page=generic_page,
        )

    page_text = _html_text(getattr(resp, "text", "") or "")
    sku_match = sku_appears_in_text(sku, page_text)
    brand_match = _brand_appears(row, url, page_text, registry_domains)
    name_match = product_name_appears_in_text(name, page_text) if name else True

    missing = []
    if generic_page:
        missing.append("generic_page")
    if not sku_match:
        missing.append("sku_not_on_page")
    if not brand_match:
        missing.append("brand_not_confirmed")
    if not name_match:
        missing.append("product_name_not_similar")

    return ProductPageValidationResult(
        valid=not missing,
        reason="validated" if not missing else ";".join(missing),
        status_code=status_code,
        content_type=content_type,
        sku_match=sku_match,
        brand_match=brand_match,
        product_name_match=name_match,
        generic_page=generic_page,
    )


def lookup_official_product_page(
    row: dict,
    *,
    session_cache=None,
    search_fn=None,
    validate_pages: bool = False,
) -> ProductPageLookupResult:
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
            if validate_pages and url:
                validation = validate_official_product_page(
                    row,
                    url,
                    registry_domains=registry_domains,
                )
                if validation.valid:
                    score += 40
                    reasons.append("page_validation_ok")
                else:
                    score -= 100
                    reasons.append(f"page_validation_failed:{validation.reason}")
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
