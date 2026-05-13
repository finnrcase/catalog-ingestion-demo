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
            for key in ("name", "description", "sku", "mpn", "model", "color", "material"):
                if item.get(key):
                    chunks.append(f"{key}: {item[key]}")
            props = item.get("additionalProperty") or item.get("additionalProperties") or []
            if isinstance(props, dict):
                props = [props]
            for prop in props:
                if isinstance(prop, dict):
                    name = prop.get("name") or prop.get("propertyID") or ""
                    value = prop.get("value") or prop.get("description") or ""
                    if name or value:
                        chunks.append(f"{name}: {value}")
    return "\n".join(chunks)


def _spec_table_text(soup: BeautifulSoup) -> str:
    chunks: list[str] = []
    spec_hint = re.compile(r"dimension|width|height|depth|length|diameter|finish|color|material|lead|weight|spec", re.I)
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
    return "\n".join(chunks)


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
        dimensions=dims,
        width=parts.get("width", ""),
        height=parts.get("height", ""),
        depth=parts.get("depth", ""),
        length=parts.get("length", ""),
        diameter=parts.get("diameter", ""),
        finish=_first_labeled_value(candidate_text, ("Finish", "Color")),
        material=_first_labeled_value(candidate_text, ("Material", "Materials")),
        lead_time=_first_labeled_value(candidate_text, ("Lead Time", "Availability")),
        weight=_first_labeled_value(candidate_text, ("Weight",)),
        source_url=page_url,
        confidence=confidence,
        evidence=";".join(evidence_bits),
        raw_text=candidate_text[:1000],
        debug={"jsonld_found": bool(jsonld_text), "spec_text_found": bool(table_text)},
    )
