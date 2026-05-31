from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from bs4 import BeautifulSoup


_BAD_IMAGE_TERMS = (
    "logo",
    "icon",
    "favicon",
    "placeholder",
    "default-meta-image",
    "default_meta_image",
    "sprite",
    "swatch",
    "spinner",
    "loader",
    "blank",
    "transparent",
    "pixel",
    "tracking",
)
_PRODUCT_CONTEXT_TERMS = (
    "gallery",
    "product",
    "media",
    "carousel",
    "pdp",
    "hero",
    "main-image",
    "primary-image",
    "zoom",
)
_IMAGE_ATTRS = (
    "src",
    "data-src",
    "data-original",
    "data-image",
    "data-zoom",
    "data-large",
)
_IMAGE_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp|gif|bmp|avif)(?:[?#]|$)", re.IGNORECASE)
_URL_RE = re.compile(
    r"(?:https?:)?//[^\s\"'<>\\]+|/[A-Za-z0-9_./%~!$&()*+,;=:@-]+\.(?:jpe?g|png|webp|gif|bmp|avif)(?:\?[^\s\"'<>\\]*)?",
    re.IGNORECASE,
)
_BG_RE = re.compile(r"url\((['\"]?)(.*?)\1\)", re.IGNORECASE)


@dataclass
class ImageCandidate:
    url: str
    source_type: str
    source_url: str = ""
    alt_text: str = ""
    title: str = ""
    context: str = ""
    width: int | None = None
    height: int | None = None
    score: int = 0
    confidence: str = "low"
    reasons: list[str] = field(default_factory=list)
    rejection_reason: str = ""
    content_type_valid: bool | None = None

    def as_debug(self) -> dict:
        return {
            "url": self.url,
            "source_type": self.source_type,
            "score": self.score,
            "confidence": self.confidence,
            "alt_text": self.alt_text,
            "title": self.title,
            "context": self.context,
            "width": self.width or "",
            "height": self.height or "",
            "content_type_valid": self.content_type_valid,
            "reasons": list(self.reasons),
            "rejection_reason": self.rejection_reason,
        }


def _str_val(value: object) -> str:
    return "" if value is None else str(value).strip()


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _token_words(value: str) -> list[str]:
    words = re.findall(r"[a-z0-9]{3,}", value.lower())
    return [w for w in words if w not in {"the", "and", "with", "for", "product"}]


def _absolute_image_url(value: object, page_url: str = "") -> str:
    raw = _str_val(value)
    if not raw or raw.startswith("data:"):
        return ""
    raw = raw.replace("\\/", "/").replace("&amp;", "&").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    elif raw.startswith("/") and page_url:
        raw = urllib.parse.urljoin(page_url, raw)
    elif page_url:
        raw = urllib.parse.urljoin(page_url, raw)
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return raw


def _image_like_url(url: str, source_type: str = "") -> bool:
    lower = url.lower()
    if _IMAGE_EXT_RE.search(lower):
        return True
    if any(part in lower for part in ("/image/", "/images/", "/media/", "is/image", "scene7", "cloudinary")):
        return True
    return source_type in {"og:image", "twitter:image", "json_ld_image"}


def _parse_int_attr(value: object) -> int | None:
    match = re.search(r"\d+", _str_val(value))
    return int(match.group(0)) if match else None


def _srcset_entries(srcset: str, page_url: str) -> list[tuple[str, int | None]]:
    entries: list[tuple[str, int | None]] = []
    for part in str(srcset or "").split(","):
        tokens = part.strip().split()
        if not tokens:
            continue
        url = _absolute_image_url(tokens[0], page_url)
        if not url:
            continue
        width: int | None = None
        if len(tokens) > 1:
            w_match = re.match(r"(\d+)w", tokens[1].strip(), re.IGNORECASE)
            if w_match:
                width = int(w_match.group(1))
        entries.append((url, width))
    return entries


def _context_for_tag(tag) -> str:
    chunks: list[str] = []
    current = tag
    depth = 0
    while current is not None and depth < 4:
        for attr in ("id", "class", "data-testid", "aria-label", "role"):
            value = current.get(attr)
            if isinstance(value, list):
                chunks.extend(str(v) for v in value)
            elif value:
                chunks.append(str(value))
        current = current.parent
        depth += 1
    return " ".join(chunks).lower()


def _is_product_context(context: str) -> bool:
    return any(term in context for term in _PRODUCT_CONTEXT_TERMS)


def _looks_bad(candidate: ImageCandidate, row: dict) -> str:
    lower = " ".join([candidate.url, candidate.alt_text, candidate.title, candidate.context]).lower()
    model = _normalize_token(_str_val(row.get("Model/SKU")))
    has_model = bool(model and model in _normalize_token(candidate.url + " " + candidate.alt_text + " " + candidate.title))

    if candidate.width and candidate.height and max(candidate.width, candidate.height) < 100:
        return "tiny image"
    if candidate.url.lower().split("?", 1)[0].endswith(".svg") and not has_model:
        return "svg without model evidence"
    for term in _BAD_IMAGE_TERMS:
        if term in lower and not has_model:
            return f"contains {term}"
    return ""


def _score_candidate(candidate: ImageCandidate, row: dict, page_url: str) -> ImageCandidate:
    brand = _str_val(row.get("Brand"))
    model = _str_val(row.get("Model/SKU"))
    product_name = _str_val(row.get("Product Name"))
    haystack_url = _normalize_token(candidate.url)
    haystack_text = _normalize_token(" ".join([candidate.alt_text, candidate.title, candidate.context]))
    page_domain = urllib.parse.urlparse(page_url).netloc.lower()
    image_domain = urllib.parse.urlparse(candidate.url).netloc.lower()

    score = 0
    reasons: list[str] = []

    model_norm = _normalize_token(model)
    if model_norm and model_norm in haystack_url:
        score += 40
        reasons.append("model in image url")
    elif model_norm and model_norm in haystack_text:
        score += 30
        reasons.append("model in alt/title/context")

    brand_norm = _normalize_token(brand)
    if brand_norm and brand_norm in haystack_url:
        score += 12
        reasons.append("brand in image url")
    elif brand_norm and brand_norm in haystack_text:
        score += 8
        reasons.append("brand in alt/title/context")

    name_hits = 0
    for token in _token_words(product_name):
        if token in candidate.url.lower() or token in (candidate.alt_text + " " + candidate.title).lower():
            name_hits += 1
    if name_hits:
        boost = min(18, name_hits * 6)
        score += boost
        reasons.append("product-name tokens matched")

    if candidate.source_type == "json_ld_image":
        score += 25
        reasons.append("JSON-LD Product image")
    elif candidate.source_type == "og:image":
        score += 35
        reasons.append("OpenGraph image")
    elif candidate.source_type == "twitter:image":
        score += 30
        reasons.append("twitter image")
    elif candidate.source_type in {"next_data", "shopify_json", "embedded_json"}:
        score += 15
        reasons.append(candidate.source_type)

    if _is_product_context(candidate.context):
        score += 20
        reasons.append("product/gallery context")

    if candidate.width and candidate.height:
        area = candidate.width * candidate.height
        if candidate.width >= 300 and candidate.height >= 300:
            score += 15
            reasons.append("large dimensions")
        elif max(candidate.width, candidate.height) < 300:
            score -= 10
            reasons.append("small dimensions")
        if area >= 1_000_000:
            score += 5
            reasons.append("high resolution")
    elif candidate.width and candidate.width >= 300:
        score += 10
        reasons.append("large srcset width")
        if candidate.width >= 1000:
            score += 5
            reasons.append("high-resolution srcset width")
    elif candidate.source_type in {"og:image", "twitter:image", "json_ld_image"}:
        score += 5
        reasons.append("structured metadata")

    if page_domain and (image_domain == page_domain or image_domain.endswith("." + page_domain)):
        score += 12
        reasons.append("same manufacturer/domain")
    elif any(term in image_domain for term in ("scene7", "cloudinary", "akamai", "cdn", "shopify", "images")):
        score += 8
        reasons.append("image CDN")

    rejection = _looks_bad(candidate, row)
    if rejection:
        candidate.rejection_reason = rejection
        score -= 60

    candidate.score = max(0, min(100, score))
    candidate.reasons = reasons
    candidate.confidence = _confidence_for_score(candidate.score)
    return candidate


def _confidence_for_score(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 45:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def _add_candidate(
    candidates: list[ImageCandidate],
    seen: set[str],
    url: object,
    source_type: str,
    page_url: str,
    row: dict,
    *,
    alt_text: str = "",
    title: str = "",
    context: str = "",
    width: int | None = None,
    height: int | None = None,
) -> None:
    absolute = _absolute_image_url(url, page_url)
    if not absolute or not _image_like_url(absolute, source_type):
        return
    normalized = absolute.split("#", 1)[0]
    if normalized in seen:
        return
    seen.add(normalized)
    candidate = ImageCandidate(
        url=absolute,
        source_type=source_type,
        source_url=page_url,
        alt_text=alt_text,
        title=title,
        context=context,
        width=width,
        height=height,
    )
    candidates.append(_score_candidate(candidate, row, page_url))


def _json_image_values(obj, *, parent_key: str = "", product_context: bool = False):
    key = str(parent_key or "").lower()
    if isinstance(obj, dict):
        obj_type = str(obj.get("@type") or obj.get("type") or "").lower()
        product_here = product_context or "product" in obj_type
        for child_key, value in obj.items():
            child_key_l = str(child_key).lower()
            image_context = product_here or any(
                term in child_key_l
                for term in ("image", "images", "media", "photo", "thumbnail", "featured", "gallery")
            )
            yield from _json_image_values(value, parent_key=child_key_l, product_context=image_context)
    elif isinstance(obj, list):
        for value in obj:
            yield from _json_image_values(value, parent_key=key, product_context=product_context)
    elif isinstance(obj, str):
        if any(term in key for term in ("image", "media", "photo", "thumbnail", "gallery", "src", "url")):
            yield obj


def _parse_json_script(text: str):
    stripped = (text or "").strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except Exception:
        pass
    # Some product JSON is assigned to a variable. Try the first object literal.
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except Exception:
            return None
    return None


def _script_source_type(script, text: str) -> str:
    script_id = str(script.get("id") or "").strip()
    script_type = str(script.get("type") or "").strip().lower()
    lower = text.lower()
    if script_id == "__NEXT_DATA__":
        return "next_data"
    if "shopify" in lower:
        return "shopify_json"
    if script_type == "application/ld+json":
        return "json_ld_image"
    return "embedded_json"


def extract_product_image_candidates(html: str, page_url: str = "", row: dict | None = None) -> list[ImageCandidate]:
    row = row or {}
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    candidates: list[ImageCandidate] = []
    seen: set[str] = set()

    # Structured page metadata.
    for prop in ("og:image", "og:image:url", "twitter:image", "twitter:image:src"):
        selector = "property" if prop.startswith("og:") else "name"
        for meta in soup.find_all("meta", attrs={selector: prop}):
            source_type = "og:image" if prop.startswith("og:") else "twitter:image"
            _add_candidate(candidates, seen, meta.get("content"), source_type, page_url, row)

    # Image and picture/source tags.
    for img in soup.find_all("img"):
        context = _context_for_tag(img)
        alt = _str_val(img.get("alt"))
        title = _str_val(img.get("title"))
        width = _parse_int_attr(img.get("width"))
        height = _parse_int_attr(img.get("height"))
        for attr in _IMAGE_ATTRS:
            _add_candidate(
                candidates,
                seen,
                img.get(attr),
                "img",
                page_url,
                row,
                alt_text=alt,
                title=title,
                context=context,
                width=width,
                height=height,
            )
        for url, srcset_width in _srcset_entries(_str_val(img.get("srcset")), page_url):
            _add_candidate(
                candidates,
                seen,
                url,
                "img_srcset",
                page_url,
                row,
                alt_text=alt,
                title=title,
                context=context,
                width=srcset_width or width,
                height=height,
            )

    for source in soup.find_all("source"):
        context = _context_for_tag(source)
        for url, srcset_width in _srcset_entries(_str_val(source.get("srcset")), page_url):
            _add_candidate(
                candidates,
                seen,
                url,
                "source_srcset",
                page_url,
                row,
                context=context,
                width=srcset_width,
            )

    # CSS background images from product/media contexts.
    for tag in soup.find_all(style=True):
        context = _context_for_tag(tag)
        if not _is_product_context(context):
            continue
        for match in _BG_RE.finditer(str(tag.get("style") or "")):
            _add_candidate(candidates, seen, match.group(2), "css_background", page_url, row, context=context)

    # JSON-LD, Next.js, Shopify, and embedded product/config JSON.
    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        if not text:
            continue
        source_type = _script_source_type(script, text)
        parsed = _parse_json_script(text)
        if parsed is not None:
            for value in _json_image_values(parsed):
                _add_candidate(candidates, seen, value, source_type, page_url, row)

        # Fall back to URL scraping for JavaScript object/config blobs.
        if source_type in {"next_data", "shopify_json", "embedded_json"} or any(
            token in text.lower() for token in ("image", "media", "gallery", "product")
        ):
            for match in _URL_RE.finditer(text.replace("\\/", "/")):
                _add_candidate(candidates, seen, match.group(0), source_type, page_url, row)

    return sorted(candidates, key=lambda c: c.score, reverse=True)


def select_best_product_image(
    candidates: list[ImageCandidate],
    product_evidence: object | None = None,
    *,
    content_type_checker: Callable[[str], bool] | None = None,
) -> ImageCandidate | None:
    """Return the best HIGH/MEDIUM candidate, validating content-type if requested."""
    for candidate in sorted(candidates, key=lambda c: c.score, reverse=True):
        if candidate.rejection_reason:
            continue
        if content_type_checker is not None:
            try:
                valid = bool(content_type_checker(candidate.url))
            except Exception:
                valid = False
            candidate.content_type_valid = valid
            if not valid:
                candidate.rejection_reason = "invalid image content-type"
                continue
            candidate.score = min(100, candidate.score + 10)
            candidate.reasons.append("valid image content-type")
            candidate.confidence = _confidence_for_score(candidate.score)
        if candidate.confidence in {"high", "medium"}:
            return candidate
    return None


def top_candidate_diagnostics(candidates: list[ImageCandidate], limit: int = 3) -> list[dict]:
    return [candidate.as_debug() for candidate in sorted(candidates, key=lambda c: c.score, reverse=True)[:limit]]
