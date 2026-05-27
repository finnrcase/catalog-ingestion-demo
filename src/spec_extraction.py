"""Verified product spec and dimension extraction.

The functions here are upstream enrichment helpers. They never write export
columns directly; callers decide whether evidence is strong enough to fill a row.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup

from src.dimensions import has_complete_3d_dimensions
from src.measurement_parser import combined_dimensions, parse_dimensions


@dataclass
class DimensionExtractionResult:
    dimensions: str = ""
    width: str = ""
    height: str = ""
    depth: str = ""
    length: str = ""
    unit: str = "in"
    raw_dimensions_text: str = ""
    dimensions_source_url: str = ""
    confidence_score: int = 0
    confidence: str = "none"
    source_type: str = "none"
    evidence_text: str = ""
    cutout_dimensions: str = ""
    used_shipping_dimensions: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)


_DIMENSION_LABEL_RE = re.compile(
    r"dimensions?|product dimensions?|overall dimensions?|appliance dimensions?|"
    r"size|product size|overall size|measurements?|product measurements?|"
    r"width|height|depth|\bW\b|\bH\b|\bD\b",
    re.IGNORECASE,
)
_CUTOUT_RE = re.compile(r"cut[\s-]?out|rough opening|opening dimensions", re.IGNORECASE)
_SHIPPING_RE = re.compile(r"shipping|package|packaged|carton|box dimensions", re.IGNORECASE)
_SPEC_HINT_RE = re.compile(
    r"dimension|size|measurement|width|height|depth|length|diameter|finish|color|material|spec|sku|model",
    re.IGNORECASE,
)
_AXIS_TOKEN_RE = r"(?:W|Width|D|Depth|H|Height|L|Length)"
_AXIS_ORDER_RE = re.compile(
    rf"\b(?P<a>{_AXIS_TOKEN_RE})\b\s*(?:x|×|by|/)\s*"
    rf"\b(?P<b>{_AXIS_TOKEN_RE})\b\s*(?:x|×|by|/)\s*"
    rf"\b(?P<c>{_AXIS_TOKEN_RE})\b(?:\s*(?:x|×|by|/)\s*\b(?P<d>{_AXIS_TOKEN_RE})\b)?",
    re.IGNORECASE,
)
_VALUE_TRIPLE_RE = re.compile(
    r"(?P<a>\d+(?:\.\d+)?(?:\s+\d+/\d+)?|\d+/\d+)\s*(?P<unit_a>\"|in\.?|inches|inch|ft\.?|feet|foot|cm|mm)?\s*(?:x|×|by|/)\s*"
    r"(?P<b>\d+(?:\.\d+)?(?:\s+\d+/\d+)?|\d+/\d+)\s*(?P<unit_b>\"|in\.?|inches|inch|ft\.?|feet|foot|cm|mm)?\s*(?:x|×|by|/)\s*"
    r"(?P<c>\d+(?:\.\d+)?(?:\s+\d+/\d+)?|\d+/\d+)\s*(?P<unit_c>\"|in\.?|inches|inch|ft\.?|feet|foot|cm|mm)?"
    r"(?:\s*(?:x|×|by|/)\s*(?P<d>\d+(?:\.\d+)?(?:\s+\d+/\d+)?|\d+/\d+)\s*(?P<unit_d>\"|in\.?|inches|inch|ft\.?|feet|foot|cm|mm)?)?",
    re.IGNORECASE,
)


def extract_dimensions_from_html(html: str, row: dict) -> DimensionExtractionResult:
    if not html:
        return DimensionExtractionResult(diagnostics={"error": "empty_html"})

    soup = BeautifulSoup(html, "html.parser")
    chunks, cutout_chunks = _html_dimension_chunks(soup)
    return _extract_dimensions_from_chunks(
        chunks,
        row,
        source_type="html",
        cutout_chunks=cutout_chunks,
    )


def extract_dimensions_from_pdf_bytes(pdf_bytes: bytes, row: dict) -> DimensionExtractionResult:
    if not pdf_bytes:
        return DimensionExtractionResult(source_type="pdf", diagnostics={"error": "empty_pdf"})
    try:
        import fitz  # PyMuPDF

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            text = "\n".join(page.get_text("text") for page in doc)
    except Exception as exc:
        return DimensionExtractionResult(source_type="pdf", diagnostics={"error": f"pdf_parse_failed:{exc}"})

    chunks = _text_dimension_chunks(text)
    cutout_chunks = [chunk for chunk in chunks if _CUTOUT_RE.search(chunk)]
    return _extract_dimensions_from_chunks(
        chunks,
        row,
        source_type="pdf",
        cutout_chunks=cutout_chunks,
    )


def extract_specs_from_verified_candidate(candidate, row: dict) -> dict:
    """Extract structured specs from a verified candidate object."""
    fields: dict[str, Any] = {}
    html = str(getattr(candidate, "html", "") or "")
    text = str(getattr(candidate, "text", "") or "")
    pdf_bytes = getattr(candidate, "pdf_bytes", b"") or b""
    if html:
        fields.update(_extract_html_metadata(html))
        dim_result = extract_dimensions_from_html(html, row)
    elif pdf_bytes:
        dim_result = extract_dimensions_from_pdf_bytes(pdf_bytes, row)
    else:
        dim_result = _extract_dimensions_from_chunks(
            _text_dimension_chunks(text),
            row,
            source_type=str(getattr(candidate, "source_type", "") or "text"),
            cutout_chunks=[],
        )

    if dim_result.dimensions:
        fields["Dimensions"] = dim_result.dimensions
        fields["width"] = dim_result.width
        fields["height"] = dim_result.height
        fields["depth"] = dim_result.depth
        fields["length"] = dim_result.length
        fields["dimension_confidence"] = dim_result.confidence
        fields["dimension_evidence"] = dim_result.evidence_text
    if dim_result.cutout_dimensions:
        fields["cutout_dimensions"] = dim_result.cutout_dimensions

    return {k: v for k, v in fields.items() if v not in ("", None, [])}


def _html_dimension_chunks(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    chunks: list[str] = []
    cutout_chunks: list[str] = []

    jsonld_text, _metadata = _jsonld_product_data(soup)
    if jsonld_text:
        chunks.append(jsonld_text)
        if _CUTOUT_RE.search(jsonld_text):
            cutout_chunks.append(jsonld_text)

    itemprop_chunk = _itemprop_dimension_chunk(soup)
    if itemprop_chunk:
        chunks.append(itemprop_chunk)

    meta_chunk = _meta_dimension_chunk(soup)
    if meta_chunk:
        chunks.append(meta_chunk)

    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
            if not cells:
                continue
            row_text = ": ".join(cells[:2]) if len(cells) >= 2 else cells[0]
            rows.append(row_text)
            if _SPEC_HINT_RE.search(row_text):
                chunks.append(row_text)
                if _CUTOUT_RE.search(row_text):
                    cutout_chunks.append(row_text)
        text = "\n".join(rows)
        if _SPEC_HINT_RE.search(text):
            chunks.append(text)
            if _CUTOUT_RE.search(text):
                cutout_chunks.append(text)

    for dl in soup.find_all("dl"):
        entries = []
        children = list(dl.find_all(["dt", "dd"], recursive=False))
        for i in range(0, len(children), 2):
            key = children[i].get_text(" ", strip=True) if i < len(children) else ""
            value = children[i + 1].get_text(" ", strip=True) if i + 1 < len(children) else ""
            entries.append(f"{key}: {value}".strip(": "))
        text = "\n".join(entries)
        if _SPEC_HINT_RE.search(text):
            chunks.append(text)
            if _CUTOUT_RE.search(text):
                cutout_chunks.append(text)

    adjacent_chunk = _adjacent_label_value_dimension_chunk(soup)
    if adjacent_chunk:
        chunks.append(adjacent_chunk)
        if _CUTOUT_RE.search(adjacent_chunk):
            cutout_chunks.append(adjacent_chunk)

    for node in soup.find_all(["section", "div", "li"]):
        classes = " ".join(node.get("class") or [])
        node_id = str(node.get("id") or "")
        text = node.get_text(" ", strip=True)
        if re.search(r"spec|detail|dimension|accordion|product", f"{classes} {node_id}", re.I) and _SPEC_HINT_RE.search(text):
            chunks.append(text[:2500])
            if _CUTOUT_RE.search(text):
                cutout_chunks.append(text[:2500])

    body = soup.get_text(" ", strip=True)
    chunks.extend(_text_dimension_chunks(body[:8000]))
    return _dedupe(chunks), _dedupe(cutout_chunks)


def _extract_html_metadata(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    _jsonld_text, metadata = _jsonld_product_data(soup)
    h1 = soup.select_one("h1")
    if h1 and not metadata.get("Product Name"):
        metadata["Product Name"] = h1.get_text(" ", strip=True)
    for selector, field in (
        ("meta[property='og:title']", "Product Name"),
        ("meta[name='twitter:title']", "Product Name"),
        ("meta[property='og:description']", "Description"),
        ("meta[name='description']", "Description"),
    ):
        tag = soup.select_one(selector)
        if tag and tag.get("content") and not metadata.get(field):
            metadata[field] = str(tag.get("content") or "").strip()
    return metadata


def _jsonld_product_data(soup: BeautifulSoup) -> tuple[str, dict[str, str]]:
    chunks: list[str] = []
    metadata: dict[str, str] = {}
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
            types = item.get("@type") if isinstance(item.get("@type"), list) else [item.get("@type")]
            if not any(str(t).lower() == "product" for t in types):
                continue
            brand = item.get("brand", "")
            if isinstance(brand, dict):
                brand = brand.get("name", "")
            metadata.update({
                "Product Name": _json_value_text(item.get("name")),
                "Brand": _json_value_text(brand),
                "Model/SKU": _json_value_text(item.get("sku") or item.get("mpn") or item.get("model")),
                "Product Category": _json_value_text(item.get("category") or item.get("productType")),
                "Finish / Color": _json_value_text(item.get("color")),
                "Material": _json_value_text(item.get("material")),
                "Description": _json_value_text(item.get("description")),
            })
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
    return "\n".join(chunks), {k: v for k, v in metadata.items() if v}


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


def _itemprop_dimension_chunk(soup: BeautifulSoup) -> str:
    parts: list[str] = []
    for prop in ("width", "height", "depth"):
        tag = soup.select_one(f'[itemprop="{prop}"]')
        if not tag:
            continue
        value = tag.get("content") or tag.get("value") or tag.get_text(" ", strip=True)
        if value:
            parts.append(f"{prop}: {value}")
    return " ".join(parts)


def _meta_dimension_chunk(soup: BeautifulSoup) -> str:
    parts: list[str] = []
    hint = re.compile(r"dimension|size|measurement|width|height|depth|product:width|product:height|product:depth", re.I)
    for tag in soup.find_all("meta"):
        name = _text(tag.get("name") or tag.get("property") or tag.get("itemprop"))
        content = _text(tag.get("content"))
        if name and content and hint.search(name):
            parts.append(f"{name}: {content}")
    return "\n".join(parts)


def _adjacent_label_value_dimension_chunk(soup: BeautifulSoup) -> str:
    parts: list[str] = []
    label_re = re.compile(
        r"^(?:overall |product |appliance )?(?:dimensions?|size|measurements?|width|height|depth)$",
        re.I,
    )
    for node in soup.find_all(["div", "span", "li", "p"]):
        label = node.get_text(" ", strip=True)
        if not label or not label_re.fullmatch(label.strip(": ")):
            continue
        value = _next_text(node)
        if value:
            parts.append(f"{label}: {value}")
    return "\n".join(parts)


def _next_text(node) -> str:
    sibling = node.find_next_sibling()
    while sibling is not None:
        text = sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
        if text:
            return text
        sibling = sibling.find_next_sibling() if hasattr(sibling, "find_next_sibling") else None
    return ""


def _text_dimension_chunks(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or ""))
    chunks: list[str] = []
    for match in re.finditer(_DIMENSION_LABEL_RE, normalized):
        start = max(0, match.start() - 120)
        end = min(len(normalized), match.end() + 240)
        chunks.append(normalized[start:end])
    return chunks or [normalized[:4000]]


def _extract_dimensions_from_chunks(
    chunks: list[str],
    row: dict,
    *,
    source_type: str,
    cutout_chunks: list[str],
) -> DimensionExtractionResult:
    non_shipping = [chunk for chunk in chunks if not _SHIPPING_RE.search(chunk)]
    shipping = [chunk for chunk in chunks if _SHIPPING_RE.search(chunk)]
    search_sets = [(non_shipping, False), (shipping, True)]

    cutout_dimensions = ""
    for chunk in cutout_chunks:
        if _CUTOUT_RE.search(chunk):
            cutout_dimensions = _combined_if_complete(_parse_dimension_parts(chunk))
            if cutout_dimensions:
                break

    for candidates, used_shipping in search_sets:
        for chunk in candidates:
            if _CUTOUT_RE.search(chunk) and not used_shipping:
                continue
            parts = _parse_dimension_parts(_normalise_dimension_text(chunk))
            dimensions = _combined_if_complete(parts)
            if not dimensions:
                continue
            if not _parts_are_plausible(parts):
                continue
            confidence = "medium" if used_shipping else "high"
            confidence_score = 65 if used_shipping else 90
            return DimensionExtractionResult(
                dimensions=dimensions,
                width=parts.get("width", ""),
                height=parts.get("height", ""),
                depth=parts.get("depth", ""),
                length=parts.get("length", ""),
                unit="in",
                raw_dimensions_text=chunk[:500],
                confidence_score=confidence_score,
                confidence=confidence,
                source_type=source_type,
                evidence_text=chunk[:500],
                cutout_dimensions=cutout_dimensions,
                used_shipping_dimensions=used_shipping,
                diagnostics={"chunks_checked": len(chunks), "method": "dimension_chunks"},
            )

    return DimensionExtractionResult(
        source_type=source_type,
        cutout_dimensions=cutout_dimensions,
        diagnostics={"chunks_checked": len(chunks), "failure_reason": "complete_w_h_d_not_found"},
    )


def _normalise_dimension_text(text: str) -> str:
    value = str(text or "")
    value = value.replace("×", "x").replace(" by ", " x ")
    value = re.sub(r"\bWxDxH\b", "W x D x H", value, flags=re.I)
    value = re.sub(r"\bHxWxD\b", "H x W x D", value, flags=re.I)
    value = re.sub(r"\bWxHxD\b", "W x H x D", value, flags=re.I)
    return value


def _parse_dimension_parts(text: str) -> dict[str, str]:
    ordered = _parse_axis_ordered_dimensions(text)
    if _has_w_h_d(ordered):
        return ordered
    return parse_dimensions(text)


def _parse_axis_ordered_dimensions(text: str) -> dict[str, str]:
    value = _normalise_dimension_text(text)
    result = {"width": "", "height": "", "depth": "", "length": "", "diameter": ""}
    for order_match in _AXIS_ORDER_RE.finditer(value):
        labels = [
            _axis_key(order_match.group(name))
            for name in ("a", "b", "c", "d")
            if order_match.group(name)
        ]
        if len(labels) < 3 or len(set(labels[:3])) < 3:
            continue

        window = value[order_match.end(): order_match.end() + 160]
        value_match = _VALUE_TRIPLE_RE.search(window)
        if not value_match:
            before = value[max(0, order_match.start() - 160): order_match.start()]
            matches = list(_VALUE_TRIPLE_RE.finditer(before))
            value_match = matches[-1] if matches else None
        if not value_match:
            continue

        units = [
            _text(value_match.group(f"unit_{name}"))
            for name in ("a", "b", "c", "d")
            if value_match.group(name)
        ]
        fallback_unit = next((unit for unit in units if unit), "")
        for idx, label in enumerate(labels):
            group_name = ("a", "b", "c", "d")[idx]
            raw_num = _text(value_match.group(group_name))
            if not raw_num:
                continue
            raw_unit = _text(value_match.group(f"unit_{group_name}")) or fallback_unit
            converted = _dimension_number_to_inches(raw_num, raw_unit)
            if converted and not result.get(label):
                result[label] = converted
        if _has_w_h_d(result):
            return result
    return result


def _axis_key(value: object) -> str:
    text = _text(value).lower()
    if text.startswith("w"):
        return "width"
    if text.startswith("h"):
        return "height"
    if text.startswith("d"):
        return "depth"
    if text.startswith("l"):
        return "length"
    return ""


def _dimension_number_to_inches(raw: str, unit: str = "") -> str:
    number = _fraction_to_float(raw)
    if number is None:
        return ""
    unit_l = unit.lower().strip().strip(".")
    if unit_l in {"ft", "feet", "foot", "'"}:
        number *= 12
    elif unit_l == "cm":
        number /= 2.54
    elif unit_l == "mm":
        number /= 25.4
    return f"{round(number, 4):.4f}".rstrip("0").rstrip(".")


def _fraction_to_float(raw: str) -> float | None:
    total = 0.0
    seen = False
    for part in str(raw or "").strip().split():
        if "/" in part:
            try:
                num, den = part.split("/", 1)
                if float(den) == 0:
                    return None
                total += float(num) / float(den)
                seen = True
            except Exception:
                return None
        else:
            try:
                total += float(part)
                seen = True
            except Exception:
                return None
    return total if seen else None


def _has_w_h_d(parts: dict[str, str]) -> bool:
    return bool(parts.get("width") and parts.get("height") and parts.get("depth"))


def _parts_are_plausible(parts: dict[str, str]) -> bool:
    values: list[float] = []
    for key in ("width", "height", "depth"):
        raw = parts.get(key)
        if not raw:
            return False
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return False
        values.append(value)
    return all(0.1 <= value <= 600 for value in values)


def _combined_if_complete(parts: dict[str, str]) -> str:
    if not _parts_are_plausible(parts):
        return ""
    dims = combined_dimensions(parts)
    return dims if has_complete_3d_dimensions(dims) else ""


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            output.append(cleaned)
    return output


def _text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text
