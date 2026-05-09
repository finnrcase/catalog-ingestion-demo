"""
Text-matching helpers for image recovery confidence scoring.

Public API
----------
sku_appears_in_text(sku, text) -> bool
    Case-insensitive SKU match that tolerates separators (- _ space) inside the SKU.

product_name_appears_in_text(name, text) -> bool
    Returns True when >= 60% of meaningful tokens in `name` appear in `text`.

is_official_domain(url, brand) -> bool
    True when `url` is on (or is a subdomain of) the canonical domain for `brand`
    according to src/manufacturer_domains.py.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from src.manufacturer_domains import get_domain_for_brand


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
# Stop-words excluded from product-name token matching — they're too generic
# to count as evidence on their own. Tokens are post-normalization (lowercase,
# punctuation stripped), so entries here must be alphanumeric only.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "with",
    "in", "on", "to", "by", "inch", "inches", "cm", "mm",
}


def _normalize(text: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    return _NON_ALNUM_RE.sub(" ", (text or "").lower()).strip()


def sku_appears_in_text(sku: str, text: str) -> bool:
    """
    True if `sku` appears in `text` as a whole token, case-insensitive,
    tolerating common separators (- _ space) inserted into the SKU.

    Examples
    --------
    sku_appears_in_text("MDD30TS", "Wolf MDD-30TS")  -> True
    sku_appears_in_text("MDD30TS", "Wolf mdd 30 ts") -> True
    sku_appears_in_text("AB12",    "CAB1234")        -> False  (not a whole token)
    """
    if not sku or not text:
        return False
    sku_norm = re.sub(r"[^a-z0-9]", "", sku.lower())
    if not sku_norm:
        return False
    text_norm = re.sub(r"[^a-z0-9]", "", text.lower())
    # Find all positions and require token-boundary in the original text
    # by also searching with the original separators preserved.
    if sku_norm in text_norm:
        # Re-anchor: walk the text token by token (split on non-alnum),
        # join consecutive tokens, see if `sku_norm` matches a contiguous
        # join boundary aligned with token starts/ends.
        tokens = [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]
        for i in range(len(tokens)):
            joined = ""
            for j in range(i, len(tokens)):
                joined += tokens[j]
                if joined == sku_norm:
                    return True
                if len(joined) > len(sku_norm):
                    break
    return False


def product_name_appears_in_text(name: str, text: str) -> bool:
    """
    True when >= 60% of the meaningful (non-stopword) tokens of `name`
    appear in `text` (case-insensitive, after stripping punctuation).
    """
    if not name or not text:
        return False
    name_tokens = [t for t in _normalize(name).split() if t and t not in _STOPWORDS and len(t) > 1]
    if not name_tokens:
        return False
    text_norm = _normalize(text)
    text_tokens = set(text_norm.split())
    matched = sum(1 for t in name_tokens if t in text_tokens)
    return matched / len(name_tokens) >= 0.6


def is_official_domain(url: str, brand: str) -> bool:
    """True when `url` is on the canonical manufacturer domain for `brand`.

    Note: get_domain_for_brand returns (domain, source) — domain is at index [0].
    """
    if not url or not brand:
        return False
    result = get_domain_for_brand(brand)
    if not result:
        return False
    # get_domain_for_brand returns (domain, source) — domain is at index [0]
    canonical_domain = result[0].lower().strip()
    if not canonical_domain:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    # Exact match or subdomain
    return host == canonical_domain or host.endswith("." + canonical_domain)
