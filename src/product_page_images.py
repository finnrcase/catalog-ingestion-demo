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

from src.image_evidence import (
    is_official_domain,
    product_name_appears_in_text,
    sku_appears_in_text,
)

USER_AGENT = "Mozilla/5.0 (compatible; SCH-Intake/1.0)"

_BAD_IMAGE_HINTS = (
    "logo", "icon", "sprite", "favicon", "placeholder", "blank",
    "loading", "transparent", "tracking", "pixel", "spinner",
)
_MIN_DIMENSION = 120
_MIN_AREA = 120 * 120
_ASPECT_MIN = 0.2
_ASPECT_MAX = 5.0


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
) -> tuple[str, list[str]]:
    brand = _str_val(row.get("Brand"))
    sku = _str_val(row.get("Model/SKU") or row.get("SKU"))
    product_name = _str_val(row.get("Product Name"))
    supplier = _str_val(row.get("Supplier"))

    evidence: list[str] = [source_kind]
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

    if official:
        evidence.append("official_or_supplier_domain")
    if sku_hit:
        evidence.append("sku_match")
    if name_hit:
        evidence.append("product_name_match")

    if official and sku_hit and source_kind in {"og_image", "twitter_image", "jsonld_image", "gallery_image"}:
        return "HIGH", evidence
    if official and (name_hit or source_kind in {"og_image", "twitter_image", "jsonld_image", "gallery_image"}):
        return "MEDIUM", evidence
    if sku_hit or name_hit:
        return "MEDIUM", evidence
    return "LOW", evidence


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
    candidates: list[tuple[str, str, int | None, int | None]] = []

    for selector in [
        ("meta[property='og:image']", "og_image"),
        ("meta[name='twitter:image']", "twitter_image"),
        ("meta[property='twitter:image']", "twitter_image"),
        ("meta[itemprop='image']", "html_image"),
    ]:
        tag = soup.select_one(selector[0])
        if tag and tag.get("content"):
            candidates.append((selector[1], str(tag.get("content")), None, None))

    for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except Exception:
            continue
        for value in _jsonld_image_values(data):
            candidates.append(("jsonld_image", value, None, None))

    for img in soup.find_all("img"):
        src = ""
        if img.get("srcset"):
            src = _srcset_best(str(img.get("srcset")))
        if not src:
            for attr in ("src", "data-src", "data-original", "data-image", "data-zoom-image"):
                if img.get(attr):
                    src = str(img.get(attr))
                    break
        if not src:
            continue
        classes = " ".join(img.get("class") or [])
        kind = "gallery_image" if re.search(r"product|gallery|hero|carousel|zoom|main", classes, re.I) else "html_image"
        candidates.append((kind, src, _parse_int(img.get("width")), _parse_int(img.get("height"))))

    debug["images_found"] = len(candidates)
    best: ProductPageImageResult | None = None
    for kind, raw_url, width, height in candidates:
        url = _absolute_url(raw_url, page_url)
        candidate_record = {
            "image_url": url,
            "source_page_url": page_url,
            "source_domain": urllib.parse.urlparse(page_url).netloc.lower(),
            "extraction_method": kind,
            "width": width,
            "height": height,
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

        confidence, evidence = _score_candidate(
            row=row,
            page_url=page_url,
            page_text=page_text,
            source_kind=kind,
            image_url=url,
        )
        candidate_record["confidence_reason"] = ";".join(evidence)
        debug["image_candidates"].append(candidate_record)
        source_suffix = {
            "og_image": "og_image",
            "twitter_image": "og_image",
            "jsonld_image": "jsonld_image",
            "gallery_image": "html_image",
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
        if best is None or {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(confidence, 0) > {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(best.confidence, 0):
            best = result
        if confidence == "HIGH":
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
