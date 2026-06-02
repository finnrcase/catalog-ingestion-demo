"""Brand-aware source priority helpers for product enrichment.

The enrichment pipeline should spend its first search/fetch attempts on
manufacturer and official specification sources. Retailers remain useful
fallbacks, but they should not outrank a known brand source simply because a
prior run happened to find an image there.
"""

from __future__ import annotations

import urllib.parse


def normalize_brand_key(value: object) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


BRAND_SOURCE_PRIORITY: dict[str, list[str]] = {
    "subzero": ["subzero-wolf.com", "ca.subzero-wolf.com", "subzero.com", "wolfappliance.com"],
    "subzerowolf": ["subzero-wolf.com", "ca.subzero-wolf.com", "subzero.com", "wolfappliance.com"],
    "wolf": ["subzero-wolf.com", "ca.subzero-wolf.com", "wolfappliance.com", "subzero.com"],
    "cove": ["subzero-wolf.com", "ca.subzero-wolf.com"],
    "ge": ["geappliances.com"],
    "geprofile": ["geappliances.com"],
    "geappliances": ["geappliances.com"],
    "bosch": ["bosch-home.com"],
    "miele": ["mieleusa.com", "miele.com"],
    "fisherpaykel": ["fisherpaykel.com"],
    "fisherandpaykel": ["fisherpaykel.com"],
    "lynx": ["lynxgrills.com"],
    "scotsman": ["scotsman-ice.com"],
}


LOW_PRIORITY_DOMAINS: frozenset[str] = frozenset({
    "ajmadison.com",
    "manua.ls",
    "manualslib.com",
    "manuals.plus",
    "plessers.com",
    "appliancesconnection.com",
})


def strip_www(domain: object) -> str:
    text = str(domain or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        try:
            text = urllib.parse.urlparse(text).netloc.lower()
        except Exception:
            text = ""
    if text.startswith("www."):
        text = text[4:]
    return text.strip("/")


def domain_matches(candidate_domain: object, official_domain: object) -> bool:
    candidate = strip_www(candidate_domain)
    official = strip_www(official_domain)
    return bool(candidate and official and (candidate == official or candidate.endswith("." + official)))


def domain_from_url(url: object) -> str:
    try:
        netloc = urllib.parse.urlparse(str(url or "")).netloc.lower()
    except Exception:
        return ""
    return strip_www(netloc)


def domains_for_brand(brand: object, fallback_domain: object = "") -> list[str]:
    """Return prioritized domains for a brand with official sources first."""
    key = normalize_brand_key(brand)
    domains: list[str] = []

    def add(domain: object) -> None:
        clean = strip_www(domain)
        if clean and clean not in domains:
            domains.append(clean)

    for domain in BRAND_SOURCE_PRIORITY.get(key, []):
        add(domain)

    fallback = strip_www(fallback_domain)
    if fallback and fallback not in domains:
        domains.append(fallback)
    return domains


def is_low_priority_domain(domain_or_url: object) -> bool:
    domain = domain_from_url(domain_or_url) if "://" in str(domain_or_url or "") else strip_www(domain_or_url)
    return any(domain_matches(domain, low) for low in LOW_PRIORITY_DOMAINS)


def brand_source_score(domain_or_url: object, brand: object, manufacturer_domain: object = "") -> int:
    """Return an additive ranking score for domain quality."""
    domain = domain_from_url(domain_or_url) if "://" in str(domain_or_url or "") else strip_www(domain_or_url)
    if not domain:
        return 0
    score = 0
    for idx, preferred in enumerate(domains_for_brand(brand, manufacturer_domain)):
        if domain_matches(domain, preferred):
            score += max(35, 70 - idx * 8)
            break
    if is_low_priority_domain(domain):
        score -= 25
    return score
