"""Helpers for deterministic extraction from embedded product data.

Manufacturer and commerce pages often hide useful product facts in app-state
JSON rather than visible HTML. These helpers intentionally stay conservative:
they only surface product-shaped data and image-like URLs for already verified
pages, never as independent proof that a page is the right product.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from bs4 import BeautifulSoup


_PRODUCT_KEY_RE = re.compile(
    r"product|sku|model|mpn|variant|vendor|brand|manufacturer|dimension|width|height|depth|"
    r"material|finish|color|colour|description|image|media|spec",
    re.I,
)
_IMAGE_URL_RE = re.compile(
    r"\.(?:jpe?g|png|webp|avif)(?:\?|#|$)|/images?/|/media/|scene7|cloudinary|imgix|cdn",
    re.I,
)
_ASSIGNMENT_RE = re.compile(
    r"(?:window\.)?(?:"
    r"__NEXT_DATA__|__INITIAL_STATE__|__PRELOADED_STATE__|"
    r"__PRODUCT_DATA__|"
    r"ShopifyAnalytics\.meta(?:\.product)?|Shopify\.product|meta|productJson|productJSON|productData|"
    r"product|Product|config|__product"
    r")\s*=\s*",
    re.I,
)


def embedded_product_text(soup: BeautifulSoup) -> str:
    chunks: list[str] = []
    for data in iter_embedded_product_json(soup):
        chunks.extend(_product_text_lines(data))
    return "\n".join(_dedupe(chunks))


def embedded_product_metadata(soup: BeautifulSoup) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for data in iter_embedded_product_json(soup):
        for item in _walk_dicts(data):
            if not _looks_productish(item):
                continue
            _merge_first(metadata, "Product Name", _first_value(item, ("name", "title", "productName", "product_name")))
            _merge_first(metadata, "Brand", _first_value(item, ("brand", "vendor", "manufacturer")))
            _merge_first(metadata, "Model/SKU", _first_value(item, ("sku", "mpn", "model", "modelNumber", "model_number")))
            _merge_first(metadata, "Product Category", _first_value(item, ("category", "productType", "product_type", "type")))
            _merge_first(metadata, "Finish / Color", _first_value(item, ("finish", "color", "colour")))
            _merge_first(metadata, "Material", _first_value(item, ("material", "materials")))
            _merge_first(metadata, "Description", _first_value(item, ("description", "body_html", "bodyHtml", "descriptionHtml")))
    return metadata


def embedded_product_images(soup: BeautifulSoup) -> list[str]:
    values: list[str] = []
    for data in iter_embedded_product_json(soup):
        for item in _walk_values(data):
            if isinstance(item, str):
                value = _clean_text(item)
                if _looks_like_image_url(value):
                    values.append(value)
            elif isinstance(item, dict):
                url = _first_value(item, ("url", "src", "originalSrc", "transformedSrc", "contentUrl", "image", "featured_image"))
                if url and _looks_like_image_url(url):
                    values.append(url)
    return _dedupe(values)


def css_background_image_urls(soup: BeautifulSoup) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    url_re = re.compile(r"url\((['\"]?)(.*?)\1\)", re.I)
    for node in soup.find_all(style=True):
        style = str(node.get("style") or "")
        if "url(" not in style.lower():
            continue
        context = _node_context(node)
        for match in url_re.finditer(style):
            url = _clean_text(match.group(2))
            if url:
                candidates.append((url, context or "inline_style"))
    for style_tag in soup.find_all("style"):
        css = style_tag.string or style_tag.get_text() or ""
        if "url(" not in css.lower():
            continue
        for match in url_re.finditer(css):
            url = _clean_text(match.group(2))
            if url:
                candidates.append((url, "stylesheet_background"))
    return _dedupe_pairs(candidates)


def iter_embedded_product_json(soup: BeautifulSoup) -> list[Any]:
    parsed: list[Any] = []
    for script in soup.find_all("script"):
        raw = script.string or script.get_text() or ""
        if not raw or not _PRODUCT_KEY_RE.search(raw):
            continue

        script_id = str(script.get("id") or "")
        script_type = str(script.get("type") or "")
        if script_id == "__NEXT_DATA__" or "json" in script_type.lower():
            data = _loads_json(raw)
            if data is not None:
                parsed.append(data)

        for data in _assignment_json_objects(raw):
            parsed.append(data)

        for data in _json_parse_objects(raw):
            parsed.append(data)
    return parsed


def _loads_json(value: str) -> Any | None:
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _assignment_json_objects(script: str) -> list[Any]:
    objects: list[Any] = []
    for match in _ASSIGNMENT_RE.finditer(script):
        start = script.find("{", match.end())
        if start == -1:
            continue
        raw = _balanced_json_object(script, start)
        if not raw or not _PRODUCT_KEY_RE.search(raw):
            continue
        data = _loads_json(raw)
        if data is not None:
            objects.append(data)
    return objects


def _json_parse_objects(script: str) -> list[Any]:
    objects: list[Any] = []
    for match in re.finditer(r"JSON\.parse\(\s*(['\"])", script):
        quote = match.group(1)
        start = match.end()
        end = _find_string_end(script, start, quote)
        if end == -1:
            continue
        encoded = script[start:end]
        try:
            decoded = json.loads(f"{quote}{encoded}{quote}")
            data = _loads_json(decoded)
        except Exception:
            data = None
        if data is not None:
            objects.append(data)
    return objects


def _balanced_json_object(text: str, start: int) -> str:
    depth = 0
    in_string = False
    quote = ""
    escape = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                in_string = False
            continue
        if char in {"'", '"'}:
            # JSON objects require double quotes; single-quoted JS objects are
            # intentionally ignored rather than loosely rewritten.
            if char == "'":
                return ""
            in_string = True
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start: idx + 1]
    return ""


def _find_string_end(text: str, start: int, quote: str) -> int:
    escape = False
    for idx in range(start, len(text)):
        char = text[idx]
        if escape:
            escape = False
        elif char == "\\":
            escape = True
        elif char == quote:
            return idx
    return -1


def _walk_values(value: Any):
    yield value
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_values(nested)


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_dicts(nested)


def _looks_productish(item: dict) -> bool:
    keys = " ".join(str(key) for key in item.keys())
    type_value = item.get("@type")
    types = type_value if isinstance(type_value, list) else [type_value]
    if any(str(kind).lower() == "product" for kind in types):
        return True
    return bool(_PRODUCT_KEY_RE.search(keys)) and any(
        key in item
        for key in ("sku", "mpn", "model", "title", "name", "product", "productType", "vendor", "images", "image")
    )


def _product_text_lines(data: Any) -> list[str]:
    lines: list[str] = []
    for item in _walk_dicts(data):
        if not _looks_productish(item):
            continue
        for key, value in item.items():
            key_text = str(key)
            if not _PRODUCT_KEY_RE.search(key_text):
                continue
            value_text = _value_text(value)
            if value_text:
                lines.append(f"{key_text}: {value_text}")
    return lines


def _first_value(item: dict, keys: tuple[str, ...]) -> str:
    lowered = {str(key).lower(): key for key in item.keys()}
    for key in keys:
        original = lowered.get(key.lower())
        if original is None:
            continue
        value = _value_text(item.get(original))
        if value:
            return value
    return ""


def _value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("value", "name", "title", "text", "description", "content", "url", "src"):
            if key in value:
                nested = _value_text(value.get(key))
                if nested:
                    unit = _value_text(value.get("unitText") or value.get("unitCode"))
                    if key == "value" and unit and unit.lower() not in nested.lower():
                        return f"{nested} {_normalise_unit(unit)}"
                    return nested
        return " ".join(_value_text(v) for v in value.values() if _value_text(v))[:800]
    if isinstance(value, list):
        return " ".join(_value_text(item) for item in value if _value_text(item))[:800]
    return _clean_text(value)


def _normalise_unit(value: str) -> str:
    return {"inh": "in", "cmt": "cm", "mmt": "mm"}.get(str(value or "").lower(), str(value or ""))


def _looks_like_image_url(value: str) -> bool:
    if not value or value.startswith("data:"):
        return False
    return bool(_IMAGE_URL_RE.search(value))


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or "")).strip().strip("'\"")
    return "" if text.lower() in {"nan", "none", "null"} else text


def _merge_first(target: dict[str, str], key: str, value: str) -> None:
    if value and not target.get(key):
        target[key] = value


def _node_context(node) -> str:
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


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _dedupe_pairs(values: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for value in values:
        if value[0] and value not in seen:
            seen.add(value)
            out.append(value)
    return out
