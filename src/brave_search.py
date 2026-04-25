"""
Brave Web Search API client for SCH DesignOps product enrichment.

Public API
----------
BRAVE_API_KEY : str
    Loaded from env. Empty string if not configured.

SearchResult : dataclass
    title, url, description, domain_score (int 0-100).

search_product_candidates(query, brand="") -> list[SearchResult]
    Returns up to 5 results ranked by domain trustworthiness.
    Returns [] if BRAVE_API_KEY is missing or the API call fails.
"""

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

BRAVE_API_KEY: str = os.getenv("BRAVE_API_KEY", "")

_PREFERRED_DOMAINS: frozenset = frozenset({
    "subzero-wolf.com", "wolfappliance.com", "miele.com", "mieleusa.com",
    "kohler.com", "kallista.com", "brizo.com", "dornbracht.com",
    "waterworks.com", "rh.com", "restorationhardware.com", "article.com",
    "rejuvenation.com", "cb2.com", "crateandbarrel.com", "westelm.com",
    "visualcomfort.com", "circalighting.com", "hudsonvalleylighting.com",
    "scotsman-ice.com", "thermador.com", "jennair.com", "vikingrange.com",
    "bertazzoni.com", "ilve.com", "lacanche.com", "dacor.com",
    "monogram.com", "bosch-home.com", "gaggenau.com",
})

_SKIP_DOMAINS: frozenset = frozenset({
    "amazon.com", "amazon.ca", "amazon.co.uk", "ebay.com", "walmart.com",
    "target.com", "homedepot.com", "lowes.com", "reddit.com", "pinterest.com",
    "yelp.com", "houzz.com", "trustpilot.com", "sitejabber.com",
})


@dataclass
class SearchResult:
    title: str
    url: str
    description: str
    domain_score: int


def _extract_domain(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            return netloc[4:]
        return netloc
    except Exception:
        return ""


def _score_domain(url: str, brand: str) -> int:
    domain = _extract_domain(url)
    brand_slug = brand.lower().replace(" ", "").replace("-", "")

    if any(skip in domain for skip in _SKIP_DOMAINS):
        return max(0, 50 - 60)  # → 0 (clamped)

    score = 50
    if brand_slug and brand_slug in domain.replace("-", "").replace(".", ""):
        score += 40
    if any(pref in domain for pref in _PREFERRED_DOMAINS):
        score += 20
    return min(100, max(0, score))


def search_product_candidates(query: str, brand: str = "") -> list:
    """
    Search Brave Web Search and return results ranked by domain trustworthiness.
    Returns an empty list if BRAVE_API_KEY is not set or the request fails.
    """
    if not BRAVE_API_KEY:
        return []

    try:
        encoded = urllib.parse.quote(query)
        req = urllib.request.Request(
            f"https://api.search.brave.com/res/v1/web/search?q={encoded}&count=5",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": BRAVE_API_KEY,
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        raw = data.get("web", {}).get("results", [])
        results = [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                description=r.get("description", ""),
                domain_score=_score_domain(r.get("url", ""), brand),
            )
            for r in raw
            if r.get("url")
        ]
        results.sort(key=lambda r: r.domain_score, reverse=True)
        return results
    except Exception:
        return []
