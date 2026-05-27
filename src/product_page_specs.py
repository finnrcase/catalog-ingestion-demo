"""Generic product-page spec extraction with provenance."""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from src.measurement_parser import combined_dimensions, parse_dimensions


@dataclass
class ProductPageSpecResult:
    product_name: str = ""
    brand: str = ""
    sku: str = ""
    category: str = ""
    color: str = ""
    description: str = ""
    dimensions: str = ""
    width: str = ""
    height: str = ""
    depth: str = ""
    length: str = ""
    diameter: str = ""
    finish: str = ""
    material: str = ""
    lead_time: str = ""
    weight: str = ""
    source_url: str = ""
    confidence: str = "none"
    evidence: str = ""
    raw_text: str = ""
    debug: dict = field(default_factory=dict)


def _text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _domain(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _jsonld_product_text(soup: BeautifulSoup) -> str:
    chunks: list[str] = []
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
            if not any(str(t).lower() == "product" for t in types):
                continue
            for key in ("name", "description", "sku", "mpn", "model", "color", "material", "width", "height", "depth"):
                value = _json_value_text(item.get(key))
                if value:
                    chunks.append(f"{key}: {value}")
            props = item.get("additionalProperty") or item.get("additionalProperties") or []
            if isinstance(props, dict):
                props = [props]
            for prop in props:
                if isinstance(prop, dict):
                    name = prop.get("name") or prop.get("propertyID") or ""
                    value = _json_value_text(prop.get("value") or prop.get("description") or "")
                    if name or value:
                        chunks.append(f"{name}: {value}")
    return "\n".join(chunks)


def _jsonld_product_metadata(soup: BeautifulSoup) -> dict[str, str]:
    """Extract structured Product metadata from JSON-LD without inventing."""
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
            if not any(str(t).lower() == "product" for t in types):
                continue
            brand = item.get("brand", "")
            if isinstance(brand, dict):
                brand = brand.get("name", "")
            metadata = {
                "product_name": _json_value_text(item.get("name")),
                "brand": _json_value_text(brand),
                "sku": _json_value_text(item.get("sku") or item.get("mpn") or item.get("model")),
                "category": _json_value_text(item.get("category") or item.get("productType")),
                "color": _json_value_text(item.get("color")),
                "material": _json_value_text(item.get("material")),
                "description": _json_value_text(item.get("description")),
            }
            return {k: v for k, v in metadata.items() if v}
    return {}


def _spec_table_text(soup: BeautifulSoup) -> str:
    chunks: list[str] = []
    spec_hint = re.compile(r"dimension|width|height|depth|length|diameter|finish|color|material|lead|weight|spec", re.I)
    itemprop = _itemprop_dimension_text(soup)
    if itemprop:
        chunks.append(itemprop)
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True)
        if spec_hint.search(text):
            chunks.append(text)
    for node in soup.find_all(["section", "div", "dl"]):
        classes = " ".join(node.get("class") or [])
        node_id = str(node.get("id") or "")
        if re.search(r"spec|detail|dimension|accordion|product", f"{classes} {node_id}", re.I):
            text = node.get_text(" ", strip=True)
            if spec_hint.search(text):
                chunks.append(text[:2000])
    adjacent = _adjacent_label_value_dimension_text(soup)
    if adjacent:
        chunks.append(adjacent)
    return "\n".join(chunks)


def _json_value_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("value", "name", "text", "description", "content", "unitText"):
            if value.get(key):
                unit = _normalise_unit(value.get("unitText") or value.get("unitCode") or "")
                text = _json_value_text(value.get(key))
                if key == "value" and unit and unit not in text.lower():
                    text = f"{text} {unit}"
                return text
        return " ".join(_json_value_text(v) for v in value.values() if _json_value_text(v))
    if isinstance(value, list):
        return " ".join(_json_value_text(item) for item in value if _json_value_text(item))
    return _text(value)


def _normalise_unit(value: object) -> str:
    unit = str(value or "").strip().lower()
    return {"inh": "in", "cmt": "cm", "mmt": "mm"}.get(unit, unit)


def _itemprop_dimension_text(soup: BeautifulSoup) -> str:
    parts: list[str] = []
    for prop in ("width", "height", "depth"):
        tag = soup.select_one(f'[itemprop="{prop}"]')
        if not tag:
            continue
        value = tag.get("content") or tag.get("value") or tag.get_text(" ", strip=True)
        if value:
            parts.append(f"{prop}: {value}")
    return " ".join(parts)


def _adjacent_label_value_dimension_text(soup: BeautifulSoup) -> str:
    parts: list[str] = []
    label_re = re.compile(r"^(?:overall |product |appliance )?(?:dimensions?|width|height|depth)$", re.I)
    for node in soup.find_all(["div", "span", "li", "p"]):
        label = node.get_text(" ", strip=True)
        if not label or not label_re.fullmatch(label.strip(": ")):
            continue
        sibling = node.find_next_sibling()
        while sibling is not None:
            value = sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
            if value:
                parts.append(f"{label}: {value}")
                break
            sibling = sibling.find_next_sibling() if hasattr(sibling, "find_next_sibling") else None
    return "\n".join(parts)


def _first_labeled_value(text: str, labels: tuple[str, ...]) -> str:
    label_re = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"\b(?:{label_re})\b\s*[:\-]\s*([^|\n;,]+)", text, re.I)
    return match.group(1).strip() if match else ""


def extract_product_page_specs(
    html: str,
    page_url: str,
    row: dict,
    *,
    official_domain: bool = False,
    sku_match: bool = False,
    product_name_match: bool = False,
) -> ProductPageSpecResult:
    if not html:
        return ProductPageSpecResult(source_url=page_url, debug={"error": "empty_html"})

    soup = BeautifulSoup(html, "html.parser")
    metadata = _jsonld_product_metadata(soup)
    jsonld_text = _jsonld_product_text(soup)
    table_text = _spec_table_text(soup)
    body_text = soup.get_text(" ", strip=True)
    candidate_text = "\n".join(part for part in (jsonld_text, table_text, body_text[:4000]) if part)
    parts = parse_dimensions(candidate_text)
    dims = combined_dimensions(parts)

    evidence_bits: list[str] = []
    if jsonld_text:
        evidence_bits.append("jsonld_product")
    if table_text:
        evidence_bits.append("spec_table_or_detail")
    if sku_match:
        evidence_bits.append("sku_match")
    if product_name_match:
        evidence_bits.append("product_name_match")
    if official_domain:
        evidence_bits.append(f"official_domain:{_domain(page_url)}")

    confidence = "none"
    if dims:
        if official_domain and sku_match and (jsonld_text or table_text):
            confidence = "high"
        elif official_domain and (product_name_match or table_text):
            confidence = "medium"
        else:
            confidence = "low"

    return ProductPageSpecResult(
        product_name=metadata.get("product_name", "") or _text(soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else ""),
        brand=metadata.get("brand", ""),
        sku=metadata.get("sku", "") or _first_labeled_value(candidate_text, ("SKU", "Model", "Model Number", "MPN")),
        category=metadata.get("category", "") or _first_labeled_value(candidate_text, ("Category", "Type", "Product Type")),
        color=metadata.get("color", "") or _first_labeled_value(candidate_text, ("Color", "Colour")),
        description=metadata.get("description", ""),
        dimensions=dims,
        width=parts.get("width", ""),
        height=parts.get("height", ""),
        depth=parts.get("depth", ""),
        length=parts.get("length", ""),
        diameter=parts.get("diameter", ""),
        finish=_first_labeled_value(candidate_text, ("Finish", "Color")),
        material=metadata.get("material", "") or _first_labeled_value(candidate_text, ("Material", "Materials")),
        lead_time=_first_labeled_value(candidate_text, ("Lead Time", "Availability")),
        weight=_first_labeled_value(candidate_text, ("Weight",)),
        source_url=page_url,
        confidence=confidence,
        evidence=";".join(evidence_bits),
        raw_text=candidate_text[:1000],
        debug={"jsonld_found": bool(jsonld_text), "spec_text_found": bool(table_text)},
    )
