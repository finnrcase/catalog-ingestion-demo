"""Product page image extraction for SCH image recovery.

This module only evaluates images that are tied to a known product/supplier
page. It avoids generic image search thumbnails and records rejection reasons
so the debug CSV can explain why a page did not yield a usable image.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup

from src.embedded_product_data import css_background_image_urls, embedded_product_images
from src.image_evidence import (
    is_official_domain,
    product_name_appears_in_text,
    sku_appears_in_text,
)

USER_AGENT = "Mozilla/5.0 (compatible; SCH-Intake/1.0)"

_BAD_IMAGE_HINTS = (
    "logo", "icon", "sprite", "favicon", "placeholder", "blank",
    "loading", "transparent", "tracking", "pixel", "spinner",
    "default-meta-image", "default_meta_image", "default-image",
    "defaultimage", "missing-image", "no-image", "noimage",
    "swatch", "badge", "lifestyle", "roomscene", "room-scene",
    "inspiration",
)
_MIN_DIMENSION = 300
_MIN_AREA = 300 * 300
_ASPECT_MIN = 0.2
_ASPECT_MAX = 5.0
_GENERIC_ALT_TOKENS = {
    "front", "side", "back", "detail", "details", "gallery", "hero",
    "image", "main", "photo", "product", "view",
}


@dataclass
class ProductPageImageResult:
    image_found: bool = False
    image_url: str = ""
    image_source: str = "none"
    confidence: str = "NONE"
    evidence: list[str] = field(default_factory=list)
    error: str = ""
    debug: dict = field(default_factory=dict)


def _str_val(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _absolute_url(src: str, page_url: str) -> str:
    src = _str_val(src)
    if not src:
        return ""
    return urllib.parse.urljoin(page_url, src)


def _is_https_url(url: str) -> bool:
    return urllib.parse.urlparse(url).scheme == "https"


def _filename_has_bad_hint(url: str) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    filename = path.rsplit("/", 1)[-1]
    for hint in _BAD_IMAGE_HINTS:
        if hint in filename:
            return hint
    return ""


def _passes_url_filter(url: str) -> tuple[bool, str]:
    if not _is_https_url(url):
        return False, "not_https"
    bad_hint = _filename_has_bad_hint(url)
    if bad_hint:
        return False, f"bad_filename_hint:{bad_hint}"
    return True, ""


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


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


def _url_has_product_signal(url: str, row: dict) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", url.lower())
    sku = _str_val(row.get("Model/SKU") or row.get("SKU"))
    if sku and sku_appears_in_text(sku, url):
        return True
    return any(token in compact for token in _identity_tokens(row, product_only=True))


def _alt_text_ok(alt: str, row: dict) -> tuple[bool, str]:
    alt_norm = _norm(alt)
    if not alt_norm:
        return True, ""
    for hint in _BAD_IMAGE_HINTS:
        if hint in alt_norm:
            return False, f"bad_alt_hint:{hint}"
    if _url_has_product_signal(alt, row):
        return True, ""
    remaining = [token for token in alt_norm.split() if token not in _GENERIC_ALT_TOKENS]
    if len(remaining) >= 2:
        return False, "unrelated_alt_text"
    return True, ""


def _context_from_node(node) -> str:
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


def _has_product_media_context(context: str) -> bool:
    return bool(re.search(r"gallery|product|media|carousel|pdp|picture|hero|zoom|main", context, re.I))


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def _passes_dimension_filter(width: int | None, height: int | None) -> tuple[bool, str]:
    if width is None or height is None:
        return True, ""
    if width <= 1 or height <= 1:
        return False, "tracking_pixel"
    if max(width, height) < _MIN_DIMENSION or (width * height) < _MIN_AREA:
        return False, f"too_small:{width}x{height}"
    ratio = width / height if height else 0
    if ratio < _ASPECT_MIN or ratio > _ASPECT_MAX:
        return False, f"extreme_aspect:{ratio:.2f}"
    return True, ""


def _score_candidate(
    *,
    row: dict,
    page_url: str,
    page_text: str,
    source_kind: str,
    image_url: str,
    alt_text: str = "",
    context: str = "",
) -> tuple[str, int, list[str], str]:
    brand = _str_val(row.get("Brand"))
    sku = _str_val(row.get("Model/SKU") or row.get("SKU"))
    product_name = _str_val(row.get("Product Name"))
    supplier = _str_val(row.get("Supplier"))

    evidence: list[str] = [source_kind]
    score = {
        "jsonld_image": 90,
        "og_image": 75,
        "twitter_image": 65,
        "embedded_json_image": 88,
        "gallery_image": 80,
        "source_srcset": 65,
        "img_srcset": 60,
        "lazy_image": 55,
        "lazy_srcset": 55,
        "css_background_image": 50,
        "html_image": 40,
    }.get(source_kind, 35)
    domain_text = urllib.parse.urlparse(page_url).netloc.lower().replace("-", "").replace(".", "")
    brand_slug = re.sub(r"[^a-z0-9]", "", brand.lower())
    supplier_slug = re.sub(r"[^a-z0-9]", "", supplier.lower())
    official = bool(
        (brand and is_official_domain(page_url, brand))
        or (supplier and is_official_domain(page_url, supplier))
        or (brand_slug and len(brand_slug) >= 3 and brand_slug in domain_text)
        or (supplier_slug and len(supplier_slug) >= 3 and supplier_slug in domain_text)
    )
    sku_hit = bool(sku and (sku_appears_in_text(sku, page_text) or sku_appears_in_text(sku, image_url)))
    name_hit = bool(product_name and product_name_appears_in_text(product_name, page_text))
    image_signal = _url_has_product_signal(image_url, row)
    alt_ok, alt_reason = _alt_text_ok(alt_text, row)

    if not alt_ok:
        return "NONE", 0, evidence, alt_reason

    if image_url.lower().split("?", 1)[0].endswith(".svg") and not image_signal:
        return "NONE", 0, evidence, "svg_without_product_signal"

    if official:
        score += 20
        evidence.append("official_or_supplier_domain")
    if sku_hit:
        score += 40
        evidence.append("sku_match")
    if name_hit:
        score += 15
        evidence.append("product_name_match")
    if image_signal:
        score += 35
        evidence.append("image_url_product_signal")
    if _has_product_media_context(context):
        score += 20
        evidence.append("product_media_context")

    if official and sku_hit and source_kind in {"jsonld_image", "gallery_image", "source_srcset", "img_srcset", "lazy_image", "lazy_srcset"}:
        return "HIGH", score, evidence, ""
    if official and sku_hit and image_signal and source_kind in {"og_image", "twitter_image"}:
        return "HIGH", score, evidence, ""
    if official and (sku_hit or name_hit or source_kind in {"og_image", "twitter_image", "jsonld_image", "gallery_image"}):
        return "MEDIUM", score, evidence, ""
    if sku_hit or name_hit or image_signal:
        return "MEDIUM", score, evidence, ""
    return "LOW", score, evidence, ""


def fetch_page_html(url: str) -> tuple[str, int | None, str]:
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=12,
            follow_redirects=True,
        )
        return resp.text, resp.status_code, ""
    except Exception as exc:
        return "", None, str(exc)


def _jsonld_image_values(data) -> list[str]:
    values: list[str] = []
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
        candidates = image if isinstance(image, list) else [image]
        for candidate in candidates:
            if isinstance(candidate, dict):
                candidate = candidate.get("url") or candidate.get("contentUrl")
            if candidate:
                values.append(str(candidate))
    return values


def _srcset_best(srcset: str) -> str:
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
            width = int(float(bits[1][:-1]) * 1000)
        if width >= best_width:
            best_url = bits[0]
            best_width = width
    return best_url


def extract_product_page_image(
    html: str,
    page_url: str,
    row: dict,
    *,
    source_prefix: str = "product_url",
) -> ProductPageImageResult:
    debug = {
        "fetch_status": None,
        "images_found": 0,
        "selected_image": "",
        "rejection_reasons": [],
        "image_candidates": [],
    }
    if not html:
        return ProductPageImageResult(error="empty_html", debug=debug)

    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    candidates: list[tuple[str, str, int | None, int | None, str, str]] = []

    for selector in [
        ("meta[property='og:image']", "og_image"),
        ("meta[name='twitter:image']", "twitter_image"),
        ("meta[property='twitter:image']", "twitter_image"),
        ("meta[itemprop='image']", "html_image"),
    ]:
        tag = soup.select_one(selector[0])
        if tag and tag.get("content"):
            candidates.append((selector[1], str(tag.get("content")), None, None, "", "metadata"))

    for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except Exception:
            continue
        for value in _jsonld_image_values(data):
            candidates.append(("jsonld_image", value, None, None, "", "jsonld_product"))

    for value in embedded_product_images(soup):
        candidates.append(("embedded_json_image", value, None, None, "", "embedded_product_json"))

    for value, context in css_background_image_urls(soup):
        candidates.append(("css_background_image", value, None, None, "", context))

    for source in soup.select("source[srcset], source[data-srcset]"):
        raw_srcset = str(source.get("srcset") or source.get("data-srcset") or "")
        src = _srcset_best(raw_srcset)
        if not src:
            continue
        context = _context_from_node(source)
        kind = "source_srcset" if not _has_product_media_context(context) else "gallery_image"
        candidates.append((kind, src, None, None, "", context))

    for img in soup.find_all("img"):
        src = ""
        kind = "html_image"
        if img.get("srcset"):
            src = _srcset_best(str(img.get("srcset")))
            kind = "img_srcset"
        if not src:
            for attr, attr_kind in (
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
                    raw = str(img.get(attr))
                    src = _srcset_best(raw) if "srcset" in attr else raw
                    kind = attr_kind
                    break
        if not src:
            continue
        context = _context_from_node(img)
        if _has_product_media_context(context):
            kind = "gallery_image"
        alt_text = " ".join([str(img.get("alt") or ""), str(img.get("title") or "")]).strip()
        candidates.append((kind, src, _parse_int(img.get("width")), _parse_int(img.get("height")), alt_text, context))

    debug["images_found"] = len(candidates)
    best: ProductPageImageResult | None = None
    for kind, raw_url, width, height, alt_text, context in candidates:
        url = _absolute_url(raw_url, page_url)
        candidate_record = {
            "image_url": url,
            "source_page_url": page_url,
            "source_domain": urllib.parse.urlparse(page_url).netloc.lower(),
            "extraction_method": kind,
            "width": width,
            "height": height,
            "alt": alt_text,
            "context": context,
            "score": 0,
            "confidence": "NONE",
            "rejection_reason": "",
            "confidence_reason": "",
        }
        ok, reason = _passes_url_filter(url)
        if not ok:
            debug["rejection_reasons"].append(f"{raw_url}: {reason}")
            candidate_record["rejection_reason"] = reason
            debug["image_candidates"].append(candidate_record)
            continue
        ok, reason = _passes_dimension_filter(width, height)
        if not ok:
            debug["rejection_reasons"].append(f"{url}: {reason}")
            candidate_record["rejection_reason"] = reason
            debug["image_candidates"].append(candidate_record)
            continue

        confidence, score, evidence, rejection_reason = _score_candidate(
            row=row,
            page_url=page_url,
            page_text=page_text,
            source_kind=kind,
            image_url=url,
            alt_text=alt_text,
            context=context,
        )
        if rejection_reason:
            debug["rejection_reasons"].append(f"{url}: {rejection_reason}")
            candidate_record["rejection_reason"] = rejection_reason
            debug["image_candidates"].append(candidate_record)
            continue
        candidate_record["score"] = score
        candidate_record["confidence"] = confidence
        candidate_record["confidence_reason"] = ";".join(evidence)
        debug["image_candidates"].append(candidate_record)
        source_suffix = {
            "og_image": "og_image",
            "twitter_image": "og_image",
            "jsonld_image": "jsonld_image",
            "embedded_json_image": "jsonld_image",
            "gallery_image": "html_image",
            "source_srcset": "html_image",
            "img_srcset": "html_image",
            "lazy_image": "html_image",
            "lazy_srcset": "html_image",
            "css_background_image": "html_image",
            "html_image": "html_image",
        }.get(kind, "html_image")
        result = ProductPageImageResult(
            image_found=True,
            image_url=url,
            image_source=f"{source_prefix}_{source_suffix}",
            confidence=confidence,
            evidence=evidence + [f"page:{page_url}"],
            debug=debug,
        )
        current_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(confidence, 0)
        best_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(best.confidence, 0) if best else 0
        best_score = 0
        if best is not None:
            for record in debug["image_candidates"]:
                if record.get("image_url") == best.image_url:
                    best_score = int(record.get("score") or 0)
                    break
        if best is None or (current_rank, score) > (best_rank, best_score):
            best = result
        if confidence == "HIGH" and score >= 160:
            break

    if best is None:
        return ProductPageImageResult(error="no_usable_product_images", debug=debug)
    debug["selected_image"] = best.image_url
    best.debug = debug
    return best


def extract_image_from_url(
    page_url: str,
    row: dict,
    *,
    source_prefix: str = "product_url",
) -> ProductPageImageResult:
    html, status, error = fetch_page_html(page_url)
    result = extract_product_page_image(html, page_url, row, source_prefix=source_prefix)
    result.debug["fetch_status"] = status
    if error and not result.error:
        result.error = error
    if error and not result.image_found:
        result.error = f"fetch_failed:{error}"
    return result
