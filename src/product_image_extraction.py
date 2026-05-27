"""Product image candidate extraction for verified product pages."""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from src.product_evidence import normalize_sku


@dataclass
class ImageCandidate:
    url: str
    source: str
    width: int | None = None
    height: int | None = None
    alt: str = ""
    title: str = ""
    context: str = ""
    score: int = 0
    confidence: str = "LOW"
    evidence: list[str] = field(default_factory=list)
    rejection_reason: str = ""


_HARD_BAD_HINTS = (
    "logo",
    "icon",
    "sprite",
    "favicon",
    "placeholder",
    "blank",
    "transparent",
    "tracking",
    "pixel",
    "spinner",
    "swatch",
    "thumb-sprite",
    "default-meta-image",
    "default_meta_image",
    "default-image",
    "defaultimage",
    "missing-image",
    "no-image",
    "noimage",
    "badge",
)
_SOFT_BAD_HINTS = (
    "lifestyle",
    "roomscene",
    "room-scene",
    "inspiration",
)
_BAD_HINTS = _HARD_BAD_HINTS + _SOFT_BAD_HINTS
_GENERIC_ALT = {"product", "image", "photo", "view", "front", "side", "back", "detail", "gallery", "main"}
_CONF_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
_KNOWN_BRAND_ALT_TOKENS = {
    "asko",
    "bosch",
    "cove",
    "fisher",
    "ge",
    "jenn",
    "jennair",
    "kallista",
    "kohler",
    "lynx",
    "miele",
    "monogram",
    "scotsman",
    "subzero",
    "thermador",
    "viking",
    "wolf",
}


def extract_product_image_candidates(html: str, page_url: str, row: dict) -> list[ImageCandidate]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[ImageCandidate] = []

    for selector, source in (
        ("meta[property='og:image']", "og_image"),
        ("meta[name='twitter:image']", "twitter_image"),
        ("meta[property='twitter:image']", "twitter_image"),
        ("meta[itemprop='image']", "html_image"),
    ):
        tag = soup.select_one(selector)
        if tag and tag.get("content"):
            candidates.append(ImageCandidate(_abs(tag.get("content"), page_url), source, context="metadata"))

    for value in _jsonld_images(soup):
        candidates.append(ImageCandidate(_abs(value, page_url), "jsonld_image", context="jsonld_product"))

    for source in soup.select("source[srcset]"):
        url, width = _best_srcset(str(source.get("srcset") or ""))
        if url:
            candidates.append(ImageCandidate(_abs(url, page_url), "source_srcset", width=width, context=_context(source)))

    for selector in (
        "[class*=gallery] img",
        "[class*=product] img",
        "[class*=media] img",
        "[class*=carousel] img",
        "[class*=pdp] img",
        "picture img",
        "img",
    ):
        for img in soup.select(selector):
            raw = ""
            source = "html_image"
            width = _parse_int(
                img.get("width")
                or img.get("data-width")
                or img.get("data-image-width")
                or img.get("naturalWidth")
            )
            height = _parse_int(
                img.get("height")
                or img.get("data-height")
                or img.get("data-image-height")
                or img.get("naturalHeight")
            )
            if img.get("srcset"):
                raw, srcset_width = _best_srcset(str(img.get("srcset") or ""))
                width = width or srcset_width
                source = "img_srcset"
            if not raw:
                for attr, attr_source in (
                    ("src", "html_image"),
                    ("data-src", "lazy_image"),
                    ("data-srcset", "lazy_srcset"),
                    ("data-original", "lazy_image"),
                    ("data-zoom", "lazy_image"),
                    ("data-large", "lazy_image"),
                    ("data-zoom-image", "lazy_image"),
                ):
                    if not img.get(attr):
                        continue
                    if "srcset" in attr:
                        raw, srcset_width = _best_srcset(str(img.get(attr) or ""))
                        width = width or srcset_width
                    else:
                        raw = str(img.get(attr))
                    source = attr_source
                    break
            if not raw:
                continue
            context = _context(img)
            if re.search(r"gallery|product|media|carousel|pdp|picture", context, re.I):
                source = "gallery_image"
            candidates.append(ImageCandidate(
                _abs(raw, page_url),
                source,
                width=width,
                height=height,
                alt=str(img.get("alt") or ""),
                title=str(img.get("title") or ""),
                context=context,
            ))

    return _dedupe(candidates)


def select_best_product_image(candidates: list[ImageCandidate], product_evidence) -> ImageCandidate | None:
    page_confidence = str(getattr(product_evidence, "confidence", "") or "").lower()
    page_verified = page_confidence in {"high", "medium"}
    best: ImageCandidate | None = None
    for candidate in candidates:
        if not candidate.evidence and not candidate.rejection_reason:
            _score(candidate, getattr(product_evidence, "row", {}) or {}, page_verified, page_confidence)
        if candidate.rejection_reason:
            continue
        if best is None or (
            _CONF_RANK.get(candidate.confidence, 0),
            candidate.score,
        ) > (
            _CONF_RANK.get(best.confidence, 0),
            best.score,
        ):
            best = candidate
    return best


def score_image_candidates(candidates: list[ImageCandidate], row: dict, product_evidence) -> list[ImageCandidate]:
    page_confidence = str(getattr(product_evidence, "confidence", "") or "").lower()
    page_verified = page_confidence in {"high", "medium"}
    for candidate in candidates:
        _score(candidate, row, page_verified, page_confidence)
    return candidates


def _score(candidate: ImageCandidate, row: dict, page_verified: bool, page_confidence: str) -> None:
    evidence = [candidate.source]
    ok, reason = _valid_url(candidate.url)
    if not ok:
        candidate.rejection_reason = reason
        candidate.evidence = evidence
        return
    if urllib.parse.urlparse(candidate.url).path.lower().endswith(".svg") and not _identity_in_text(
        f"{candidate.url} {candidate.alt} {candidate.title}",
        row,
        require_sku=False,
    ):
        candidate.rejection_reason = "svg_without_product_signal"
        candidate.evidence = evidence
        return
    alt_ok, alt_reason = _valid_alt(candidate, row, page_verified=page_verified)
    if not alt_ok:
        candidate.rejection_reason = alt_reason
        candidate.evidence = evidence
        return

    score = _source_score(candidate.source)
    soft_hint = _soft_bad_hint(f"{candidate.url} {candidate.alt} {candidate.title} {candidate.context}")
    if soft_hint:
        score -= 20
        evidence.append(f"soft_image_hint_penalty:{soft_hint}")
    sku = normalize_sku(row.get("Model/SKU") or row.get("SKU"))
    compact_url = re.sub(r"[^a-z0-9]", "", candidate.url.lower())
    if sku and sku in compact_url:
        score += 40
        evidence.append("sku_in_url")
    if _identity_in_text(f"{candidate.alt} {candidate.title}", row, require_sku=False):
        score += 25
        evidence.append("alt_or_title_product_match")
    if re.search(r"gallery|product|media|carousel|pdp|picture", candidate.context, re.I):
        score += 20
        evidence.append("product_media_container")
    if _large_enough(candidate.width, candidate.height):
        score += 15
        evidence.append("large_image")
    elif candidate.width is not None and candidate.height is not None:
        score -= 25
        evidence.append("small_image_penalty")
    if re.search(r"cdn|images?|media|scene7|cloudinary|imgix", candidate.url, re.I):
        score += 10
        evidence.append("cdn_product_image_url")

    if not page_verified:
        confidence = "LOW"
        evidence.append(f"page_confidence:{page_confidence or 'none'}")
    elif page_confidence == "high" and score >= 85:
        confidence = "HIGH"
    elif score >= 55:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    candidate.score = score
    candidate.confidence = confidence
    candidate.evidence = evidence


def _jsonld_images(soup: BeautifulSoup) -> list[str]:
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
            if isinstance(item.get("@graph"), list):
                stack.extend(item["@graph"])
            types = item.get("@type") if isinstance(item.get("@type"), list) else [item.get("@type")]
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


def _valid_url(url: str) -> tuple[bool, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return False, "not_https"
    lowered = url.lower()
    for hint in _HARD_BAD_HINTS:
        if hint in lowered:
            return False, f"bad_image_hint:{hint}"
    return True, ""


def _valid_alt(candidate: ImageCandidate, row: dict, *, page_verified: bool = False) -> tuple[bool, str]:
    alt = _norm(f"{candidate.alt} {candidate.title}")
    if not alt:
        return True, ""
    for hint in _HARD_BAD_HINTS:
        if hint in alt:
            return False, f"bad_alt_hint:{hint}"
    if _mentions_other_known_brand(alt, row):
        return False, "unrelated_alt_text"
    if _identity_in_text(alt, row, require_sku=False):
        return True, ""
    remaining = [token for token in alt.split() if token not in _GENERIC_ALT]
    if page_verified and re.search(
        r"gallery|product|media|carousel|pdp|picture|hero|primary|main|zoom",
        candidate.context,
        re.I,
    ):
        return True, ""
    if len(remaining) >= 2:
        return False, "unrelated_alt_text"
    return True, ""


def _mentions_other_known_brand(alt_norm: str, row: dict) -> bool:
    row_brand = _norm(str(row.get("Brand") or "")).replace(" ", "")
    compact_alt = alt_norm.replace(" ", "")
    alt_tokens = set(alt_norm.split())
    for token in _KNOWN_BRAND_ALT_TOKENS:
        if token and token in row_brand:
            continue
        if token == "ge":
            if "ge" in alt_tokens:
                return True
            continue
        if token in compact_alt:
            return True
    return False


def _soft_bad_hint(value: str) -> str:
    lowered = str(value or "").lower()
    for hint in _SOFT_BAD_HINTS:
        if hint in lowered:
            return hint
    return ""


def _identity_in_text(text: str, row: dict, *, require_sku: bool) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", text.lower())
    sku = normalize_sku(row.get("Model/SKU") or row.get("SKU"))
    if sku and sku in compact:
        return True
    if require_sku:
        return False
    tokens = set(_norm(text).split())
    for value in (row.get("Product Name"), row.get("Brand")):
        for token in _norm(str(value or "")).split():
            if len(token) > 2 and token in tokens:
                return True
    return False


def _large_enough(width: int | None, height: int | None) -> bool:
    if width is None or height is None:
        return False
    return max(width, height) >= 300 and width * height >= 90_000


def _source_score(source: str) -> int:
    return {
        "jsonld_image": 95,
        "og_image": 85,
        "twitter_image": 35,
        "gallery_image": 35,
        "source_srcset": 60,
        "img_srcset": 28,
        "lazy_srcset": 25,
        "lazy_image": 24,
        "html_image": 15,
    }.get(source, 10)


def _best_srcset(srcset: str) -> tuple[str, int | None]:
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


def _context(node) -> str:
    parts: list[str] = []
    current = node
    for _ in range(5):
        if current is None:
            break
        parts.extend(str(v) for v in (current.get("class") or []))
        if current.get("id"):
            parts.append(str(current.get("id")))
        if current.name:
            parts.append(str(current.name))
        current = current.parent
    return " ".join(parts).lower()


def _dedupe(candidates: list[ImageCandidate]) -> list[ImageCandidate]:
    seen: set[str] = set()
    output: list[ImageCandidate] = []
    for candidate in candidates:
        if not candidate.url or candidate.url in seen:
            continue
        seen.add(candidate.url)
        output.append(candidate)
    return output


def _abs(value: object, page_url: str) -> str:
    return urllib.parse.urljoin(page_url, str(value or "").strip())


def _parse_int(value: object) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
