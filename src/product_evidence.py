"""Product-page evidence scoring before enrichment.

This module is intentionally side-effect free: it does not fetch pages, mutate
rows, or write export fields. It only scores already-available page evidence so
callers can decide whether a candidate is safe enough to use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import urlparse

from src.brand_lookup_registry import registry_domains_for_brand
from src.manufacturer_domains import get_domain_for_brand


Confidence = str

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_HTML_TAG_RE = re.compile(r"(?s)<[^>]+>")
_HTML_NOISE_RE = re.compile(r"(?is)<(script|style|noscript).*?</\1>")

_STOPWORDS = {
    "a",
    "an",
    "and",
    "by",
    "for",
    "in",
    "inch",
    "inches",
    "of",
    "on",
    "the",
    "to",
    "with",
}

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
    "mcgee co",
    "miele",
    "palecek",
    "pottery barn",
    "rejuvenation",
    "restoration hardware",
    "rh",
    "scotsman",
    "serena lily",
    "sub zero",
    "visual comfort",
    "west elm",
    "wolf",
}


@dataclass(frozen=True)
class ProductEvidence:
    confidence: Confidence = "none"
    score: int = 0
    matched_sku: bool = False
    matched_brand: bool = False
    matched_product_name: bool = False
    official_domain: bool = False
    domain: str = ""
    evidence_summary: str = ""
    rejection_reason: str = ""


def normalize_sku(value: object) -> str:
    """Normalize SKU/model values for exact matching across punctuation drift."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def normalize_brand(value: object) -> str:
    """Normalize brand names while preserving word boundaries."""
    text = _NON_ALNUM_RE.sub(" ", str(value or "").lower())
    tokens = [
        token
        for token in text.split()
        if token not in {"inc", "llc", "ltd", "co", "company", "the"}
    ]
    return " ".join(tokens)


def product_name_similarity(a: object, b: object) -> float:
    """Return a 0.0-1.0 product-name similarity score."""
    left_tokens = _meaningful_tokens(str(a or ""))
    right_tokens = _meaningful_tokens(str(b or ""))
    if not left_tokens or not right_tokens:
        return 0.0

    left = " ".join(left_tokens)
    right = " ".join(right_tokens)
    overlap = len(set(left_tokens) & set(right_tokens)) / max(len(set(left_tokens)), 1)
    sequence = SequenceMatcher(None, left, right).ratio()
    return max(overlap, sequence)


def score_product_page(
    row: dict,
    url: str,
    html: str,
    title: str = "",
    description: str = "",
) -> ProductEvidence:
    """Score whether a fetched page appears to be the exact product page."""
    domain = _domain_from_url(url)
    page_text = _html_to_text(html)
    haystack = " ".join(part for part in (title, description, page_text, url) if part)
    haystack_norm = _normalized_text(haystack)

    if _is_marketplace_or_sketchy(domain):
        return ProductEvidence(
            confidence="none",
            domain=domain,
            rejection_reason="blocked_marketplace_domain",
            evidence_summary="blocked_marketplace_domain",
        )

    brand = str(row.get("Brand") or "").strip()
    sku = str(row.get("Model/SKU") or row.get("SKU") or row.get("_extracted_model_sku") or "").strip()
    product_name = str(row.get("Product Name") or "").strip()
    brand_norm = normalize_brand(brand)
    sku_norm = normalize_sku(sku)

    matched_sku = bool(sku_norm and _sku_matches(sku_norm, haystack))
    matched_brand = bool(brand_norm and _brand_matches(brand_norm, domain, haystack_norm))
    name_similarity = product_name_similarity(product_name, haystack)
    matched_product_name = name_similarity >= 0.68
    official_domain = _is_official_domain(domain, brand)

    wrong_brand = bool(brand_norm and not matched_brand and _known_other_brand_present(brand_norm, domain, haystack_norm))
    if wrong_brand:
        return ProductEvidence(
            confidence="none",
            score=0,
            matched_sku=matched_sku,
            matched_brand=False,
            matched_product_name=matched_product_name,
            official_domain=official_domain,
            domain=domain,
            evidence_summary="wrong_brand",
            rejection_reason="wrong_brand",
        )

    score = 0
    reasons: list[str] = []
    if matched_sku:
        score += 45
        reasons.append("exact_sku")
    if matched_brand:
        score += 20
        reasons.append("brand")
    if matched_product_name:
        score += 15
        reasons.append("product_name")
    if official_domain:
        score += 20
        reasons.append("official_domain")

    confidence = _confidence_from_evidence(
        score=score,
        matched_sku=matched_sku,
        official_domain=official_domain,
        domain=domain,
        sku_exists=bool(sku_norm),
    )
    rejection_reason = ""
    if confidence == "none":
        rejection_reason = "insufficient_evidence"
    elif sku_norm and not matched_sku:
        rejection_reason = "sku_not_found_on_page"
        reasons.append("sku_not_found")

    return ProductEvidence(
        confidence=confidence,
        score=max(0, min(100, score)),
        matched_sku=matched_sku,
        matched_brand=matched_brand,
        matched_product_name=matched_product_name,
        official_domain=official_domain,
        domain=domain,
        evidence_summary=", ".join(reasons) or "no_positive_evidence",
        rejection_reason=rejection_reason,
    )


def _meaningful_tokens(value: str) -> list[str]:
    return [
        token
        for token in _normalized_text(value).split()
        if len(token) > 1 and token not in _STOPWORDS
    ]


def _normalized_text(value: str) -> str:
    return _NON_ALNUM_RE.sub(" ", str(value or "").lower()).strip()


def _html_to_text(html: str) -> str:
    without_noise = _HTML_NOISE_RE.sub(" ", html or "")
    without_tags = _HTML_TAG_RE.sub(" ", without_noise)
    return re.sub(r"\s+", " ", without_tags).strip()


def _domain_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _domain_matches(domain: str, root: str) -> bool:
    root = root.lower().strip()
    return bool(root and (domain == root or domain.endswith("." + root)))


def _is_marketplace_or_sketchy(domain: str) -> bool:
    return any(marker in domain for marker in _MARKETPLACE_DOMAINS)


def _is_retailer_domain(domain: str) -> bool:
    return any(_domain_matches(domain, retailer) for retailer in _RETAILER_DOMAINS)


def _official_domains_for_brand(brand: str) -> list[str]:
    domains: list[str] = []
    direct = get_domain_for_brand(brand)
    if direct and direct[0] not in domains:
        domains.append(direct[0])
    for domain in registry_domains_for_brand(brand):
        if domain not in domains:
            domains.append(domain)
    return domains


def _is_official_domain(domain: str, brand: str) -> bool:
    if not domain or not brand:
        return False
    return any(_domain_matches(domain, official) for official in _official_domains_for_brand(brand))


def _sku_matches(sku_norm: str, haystack: str) -> bool:
    tokens = [token for token in re.split(r"[^a-z0-9]+", haystack.lower()) if token]
    for i in range(len(tokens)):
        joined = ""
        for token in tokens[i:]:
            joined += token
            if joined == sku_norm:
                return True
            if len(joined) > len(sku_norm):
                break
    return False


def _brand_matches(brand_norm: str, domain: str, haystack_norm: str) -> bool:
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
        in_domain = len(compact_known) >= 3 and compact_known in compact_domain
        in_text = bool(re.search(rf"(^|\s){re.escape(known)}(\s|$)", haystack_norm))
        if in_domain or in_text:
            return True
    return False


def _confidence_from_evidence(
    *,
    score: int,
    matched_sku: bool,
    official_domain: bool,
    domain: str,
    sku_exists: bool,
) -> Confidence:
    if sku_exists and not matched_sku:
        return "low" if score > 0 else "none"
    if score >= 80 and official_domain and matched_sku:
        return "high"
    if score >= 60 and matched_sku:
        return "medium"
    if matched_sku and _is_retailer_domain(domain):
        return "medium"
    if score > 0:
        return "low"
    return "none"
