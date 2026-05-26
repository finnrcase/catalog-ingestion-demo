"""Verified product-page image candidate extraction and ranking.

This module improves upstream image discovery only. It returns the existing
ProductPageImageResult shape consumed by enrichment/recovery code and does not
change Programa export columns or image export behavior.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

from src.product_evidence import ProductEvidence, normalize_sku
from src.product_page_images import ProductPageImageResult


_BAD_IMAGE_HINTS = (
    "logo",
    "icon",
    "sprite",
    "favicon",
    "placeholder",
    "blank",
    "loading",
    "transparent",
    "tracking",
    "pixel",
    "spinner",
    "default-meta-image",
    "default_meta_image",
    "default-image",
    "defaultimage",
    "missing-image",
    "no-image",
    "noimage",
    "badge",
    "swatch",
    "lifestyle",
    "roomscene",
    "room-scene",
    "inspiration",
)
_GENERIC_ALT_TOKENS = {
    "front",
    "side",
    "back",
    "detail",
    "details",
    "gallery",
    "hero",
    "image",
    "main",
    "photo",
    "product",
    "view",
}
_MIN_DIMENSION = 300
_MIN_AREA = _MIN_DIMENSION * _MIN_DIMENSION
_CONF_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


@dataclass
class _ImageCandidate:
    url: str
    source_kind: str
    width: int | None = None
    height: int | None = None
    alt: str = ""
    context: str = ""
    score: int = 0
    confidence: str = "LOW"
    evidence: list[str] | None = None
    rejection_reason: str = ""


def extract_product_page_image(
    html: str,
    page_url: str,
    row: dict,
    *,
    page_evidence: ProductEvidence | None = None,
    source_prefix: str = "product_url",
) -> ProductPageImageResult:
    """Extract and rank product image candidates from verified product HTML."""
    debug = {
        "images_found": 0,
        "selected_image": "",
        "rejection_reasons": [],
        "image_candidates": [],
        "image_query_used": _image_query_for_row(row, page_url),
    }
    if not html:
        return ProductPageImageResult(error="empty_html", debug=debug)

    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    candidates = _collect_candidates(soup, page_url)
    debug["images_found"] = len(candidates)

    best: _ImageCandidate | None = None
    page_confidence = str(getattr(page_evidence, "confidence", "") or "").lower()
    page_verified = page_confidence in {"high", "medium"}
    for candidate in candidates:
        _score_candidate(candidate, row, page_url, page_text, page_verified, page_confidence)
        record = _candidate_record(candidate, page_url)
        debug["image_candidates"].append(record)
        if candidate.rejection_reason:
            debug["rejection_reasons"].append(f"{candidate.url}: {candidate.rejection_reason}")
            continue
        if best is None or (
            _CONF_RANK.get(candidate.confidence, 0),
            candidate.score,
        ) > (
            _CONF_RANK.get(best.confidence, 0),
            best.score,
        ):
            best = candidate

    if best is None:
        return ProductPageImageResult(error="no_usable_product_images", debug=debug)

    debug["selected_image"] = best.url
    return ProductPageImageResult(
        image_found=True,
        image_url=best.url,
        image_source=f"{source_prefix}_{_source_suffix(best.source_kind)}",
        confidence=best.confidence,
        evidence=(best.evidence or []) + [f"page:{page_url}"],
        debug=debug,
    )


def _collect_candidates(soup: BeautifulSoup, page_url: str) -> list[_ImageCandidate]:
    candidates: list[_ImageCandidate] = []

    for selector, kind in (
        ("meta[property='og:image']", "og_image"),
        ("meta[name='og:image']", "og_image"),
        ("meta[name='twitter:image']", "twitter_image"),
        ("meta[property='twitter:image']", "twitter_image"),
        ("meta[itemprop='image']", "html_image"),
    ):
        tag = soup.select_one(selector)
        if tag and tag.get("content"):
            candidates.append(_ImageCandidate(
                url=_absolute_url(str(tag.get("content")), page_url),
                source_kind=kind,
                context="metadata",
            ))

    for value in _jsonld_image_values(soup):
        candidates.append(_ImageCandidate(
            url=_absolute_url(value, page_url),
            source_kind="jsonld_image",
            context="jsonld_product",
        ))

    for source in soup.select("source[srcset], source[data-srcset]"):
        url, width = _srcset_best(str(source.get("srcset") or source.get("data-srcset") or ""))
        if url:
            candidates.append(_ImageCandidate(
                url=_absolute_url(url, page_url),
                source_kind="source_srcset",
                width=width,
                context=_node_context(source),
            ))

    for img in soup.find_all("img"):
        raw_url = ""
        source_kind = "html_image"
        width = _parse_int(img.get("width"))
        height = _parse_int(img.get("height"))
        if img.get("srcset"):
            raw_url, srcset_width = _srcset_best(str(img.get("srcset") or ""))
            width = width or srcset_width
            source_kind = "img_srcset"
        if not raw_url:
            for attr, kind in (
                ("src", "html_image"),
                ("data-src", "lazy_image"),
                ("data-srcset", "lazy_srcset"),
                ("data-original", "lazy_image"),
                ("data-image", "lazy_image"),
                ("data-zoom", "lazy_image"),
                ("data-large", "lazy_image"),
                ("data-zoom-image", "lazy_image"),
            ):
                if img.get(attr):
                    raw_url = str(img.get(attr))
                    if "srcset" in attr:
                        raw_url, srcset_width = _srcset_best(raw_url)
                        width = width or srcset_width
                    source_kind = kind
                    break
        if not raw_url:
            continue
        context = _node_context(img)
        if _has_gallery_context(img):
            source_kind = "gallery_image"
        candidates.append(_ImageCandidate(
            url=_absolute_url(raw_url, page_url),
            source_kind=source_kind,
            width=width,
            height=height,
            alt=str(img.get("alt") or ""),
            context=context,
        ))

    return _dedupe_candidates(candidates)


def _jsonld_image_values(soup: BeautifulSoup) -> list[str]:
    values: list[str] = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack.extend(item)
                continue
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
            type_value = item.get("@type")
            types = type_value if isinstance(type_value, list) else [type_value]
            is_product = any(str(t).lower() == "product" for t in types)
            if not is_product and "image" not in item:
                continue
            image = item.get("image")
            for candidate in image if isinstance(image, list) else [image]:
                if isinstance(candidate, dict):
                    candidate = candidate.get("url") or candidate.get("contentUrl")
                if candidate:
                    values.append(str(candidate))
    return values


def _score_candidate(
    candidate: _ImageCandidate,
    row: dict,
    page_url: str,
    page_text: str,
    page_verified: bool,
    page_confidence: str,
) -> None:
    evidence: list[str] = [candidate.source_kind]
    url = candidate.url
    ok, reason = _passes_url_filter(url)
    if not ok:
        candidate.rejection_reason = reason
        candidate.evidence = evidence
        return
    if _is_svg_url(url) and not _candidate_has_strong_product_signal(candidate, row):
        candidate.rejection_reason = "svg_without_product_signal"
        candidate.evidence = evidence
        return
    ok, reason = _passes_dimension_filter(candidate.width, candidate.height)
    if not ok:
        candidate.rejection_reason = reason
        candidate.evidence = evidence
        return
    alt_ok, alt_reason = _alt_text_ok(candidate.alt, row)
    if not alt_ok:
        candidate.rejection_reason = alt_reason
        candidate.evidence = evidence
        return

    score = _source_score(candidate.source_kind)
    if _is_product_context(candidate.context):
        score += 25
        evidence.append("product_media_context")
    area_score = _area_score(candidate.width, candidate.height)
    if area_score:
        score += area_score
        evidence.append(f"large_image:{candidate.width or '?'}x{candidate.height or '?'}")

    row_text = _row_identity_text(row)
    url_norm = _norm(url)
    sku = normalize_sku(row.get("Model/SKU") or row.get("SKU"))
    if sku and sku in re.sub(r"[^a-z0-9]", "", url.lower()):
        score += 35
        evidence.append("sku_in_image_url")
    product_slug_hits = _product_slug_hits(row, url_norm)
    if product_slug_hits:
        score += min(25, 8 * product_slug_hits)
        evidence.append("product_terms_in_image_url")
    if row_text and _alt_matches(candidate.alt, row):
        score += 15
        evidence.append("alt_text_match")

    if not page_verified:
        candidate.confidence = "LOW"
        evidence.append(f"page_evidence:{page_confidence or 'none'}")
    elif page_confidence == "high" and score >= 95:
        candidate.confidence = "HIGH"
    elif score >= 65:
        candidate.confidence = "MEDIUM"
    else:
        candidate.confidence = "LOW"

    candidate.score = score
    candidate.evidence = evidence


def _candidate_record(candidate: _ImageCandidate, page_url: str) -> dict[str, Any]:
    return {
        "image_url": candidate.url,
        "source_page_url": page_url,
        "source_domain": urllib.parse.urlparse(page_url).netloc.lower(),
        "extraction_method": candidate.source_kind,
        "width": candidate.width,
        "height": candidate.height,
        "context": candidate.context,
        "score": candidate.score,
        "confidence": candidate.confidence,
        "rejection_reason": candidate.rejection_reason,
        "confidence_reason": ";".join(candidate.evidence or []),
    }


def _absolute_url(src: str, page_url: str) -> str:
    return urllib.parse.urljoin(page_url, str(src or "").strip())


def _srcset_best(srcset: str) -> tuple[str, int | None]:
    best_url = ""
    best_width = -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        width = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            width = _parse_int(bits[1]) or 0
        elif len(bits) > 1 and bits[1].endswith("x"):
            try:
                width = int(float(bits[1][:-1]) * 1000)
            except ValueError:
                width = 0
        if width >= best_width:
            best_url = bits[0]
            best_width = width
    return best_url, best_width if best_width >= 0 else None


def _dedupe_candidates(candidates: list[_ImageCandidate]) -> list[_ImageCandidate]:
    seen: set[str] = set()
    deduped: list[_ImageCandidate] = []
    for candidate in candidates:
        key = candidate.url
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _parse_int(value: object) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _passes_url_filter(url: str) -> tuple[bool, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return False, "not_https"
    lowered = url.lower()
    for hint in _BAD_IMAGE_HINTS:
        if hint in lowered:
            return False, f"bad_image_hint:{hint}"
    return True, ""


def _is_svg_url(url: str) -> bool:
    return urllib.parse.urlparse(url).path.lower().endswith(".svg")


def _candidate_has_strong_product_signal(candidate: _ImageCandidate, row: dict) -> bool:
    sku = normalize_sku(row.get("Model/SKU") or row.get("SKU"))
    compact_url = re.sub(r"[^a-z0-9]", "", candidate.url.lower())
    if sku and sku in compact_url:
        return True
    text = f"{candidate.url} {candidate.alt} {candidate.context}"
    return _alt_matches(text, row)


def _passes_dimension_filter(width: int | None, height: int | None) -> tuple[bool, str]:
    if width is None or height is None:
        return True, ""
    if width <= 1 or height <= 1:
        return False, "tracking_pixel"
    if max(width, height) < _MIN_DIMENSION or (width * height) < _MIN_AREA:
        return False, f"too_small:{width}x{height}"
    return True, ""


def _alt_text_ok(alt: str, row: dict) -> tuple[bool, str]:
    alt_norm = _norm(alt)
    if not alt_norm:
        return True, ""
    for hint in _BAD_IMAGE_HINTS:
        if hint in alt_norm:
            return False, f"bad_alt_hint:{hint}"
    if _alt_matches(alt, row):
        return True, ""
    remaining = [token for token in alt_norm.split() if token not in _GENERIC_ALT_TOKENS]
    if not remaining:
        return True, ""
    if len(remaining) >= 2:
        return False, "unrelated_alt_text"
    return True, ""


def _alt_matches(alt: str, row: dict) -> bool:
    alt_norm = _norm(alt)
    if not alt_norm:
        return False
    sku = normalize_sku(row.get("Model/SKU") or row.get("SKU"))
    if sku and sku in re.sub(r"[^a-z0-9]", "", alt.lower()):
        return True
    for token in _identity_tokens(row):
        if token in alt_norm.split():
            return True
    return False


def _product_slug_hits(row: dict, url_norm: str) -> int:
    return sum(1 for token in _identity_tokens(row, product_only=True) if token in url_norm)


def _row_identity_text(row: dict) -> str:
    return " ".join(_identity_tokens(row))


def _identity_tokens(row: dict, *, product_only: bool = False) -> list[str]:
    values = [row.get("Product Name")]
    if not product_only:
        values.extend([row.get("Brand"), row.get("Supplier")])
    tokens: list[str] = []
    for value in values:
        for token in _norm(str(value or "")).split():
            if len(token) > 2 and token not in {"the", "and", "for", "with", "inch"}:
                tokens.append(token)
    return tokens


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _node_context(node) -> str:
    parts: list[str] = []
    current = node
    for _ in range(4):
        if current is None:
            break
        parts.extend(str(v) for v in (current.get("class") or []))
        if current.get("id"):
            parts.append(str(current.get("id")))
        if current.name:
            parts.append(str(current.name))
        current = current.parent
    return " ".join(parts).lower()


def _has_gallery_context(img) -> bool:
    return bool(re.search(r"gallery|product|media|carousel", _node_context(img), re.I))


def _is_product_context(context: str) -> bool:
    return bool(re.search(r"gallery|product|media|carousel|picture|hero|zoom|main", context, re.I))


def _source_score(source_kind: str) -> int:
    return {
        "jsonld_image": 80,
        "og_image": 75,
        "twitter_image": 70,
        "gallery_image": 70,
        "source_srcset": 65,
        "img_srcset": 60,
        "lazy_srcset": 55,
        "lazy_image": 55,
        "html_image": 40,
    }.get(source_kind, 35)


def _area_score(width: int | None, height: int | None) -> int:
    if width is None or height is None:
        return 0
    area = width * height
    if area >= 1_000_000:
        return 30
    if area >= 500_000:
        return 22
    if area >= 200_000:
        return 14
    if area >= _MIN_AREA:
        return 8
    return 0


def _source_suffix(source_kind: str) -> str:
    return {
        "og_image": "og_image",
        "twitter_image": "og_image",
        "jsonld_image": "jsonld_image",
        "gallery_image": "html_image",
        "source_srcset": "html_image",
        "img_srcset": "html_image",
        "lazy_srcset": "html_image",
        "lazy_image": "html_image",
        "html_image": "html_image",
    }.get(source_kind, "html_image")


def _image_query_for_row(row: dict, page_url: str) -> str:
    parts = [str(row.get("Brand") or "").strip(), str(row.get("Model/SKU") or row.get("SKU") or "").strip()]
    product_name = str(row.get("Product Name") or "").strip()
    domain = urllib.parse.urlparse(page_url).netloc.lower()
    query = " ".join(part for part in parts if part)
    if product_name:
        query = f"{query} {product_name}".strip()
    if domain:
        query = f"{domain} {query}".strip()
    return query
