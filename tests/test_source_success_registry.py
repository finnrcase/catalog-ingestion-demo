from __future__ import annotations

from src.source_success_registry import (
    brand_source_hints,
    load_source_registry,
    preferred_source_domains_for_row,
    record_source_failure,
    record_source_success,
    source_success_score,
    successful_urls_for_row,
)


def _row(brand="Wolf", model="MDD30TS", category="Appliances") -> dict:
    return {
        "Brand": brand,
        "Model/SKU": model,
        "Product Category": category,
        "Product Name": "30 Inch Warming Drawer",
    }


def test_brand_source_hints_include_required_appliance_sources():
    assert brand_source_hints("Sub-Zero") == ["subzero-wolf.com"]
    assert "mieleusa.com" in brand_source_hints("Miele")
    assert brand_source_hints("Fisher Paykel") == ["fisherpaykel.com"]


def test_source_success_registry_saves_and_prefers_successful_domain(tmp_path):
    path = tmp_path / "source_success_registry.json"

    record_source_success(
        _row(),
        domain="subzero-wolf.com",
        url="https://subzero-wolf.com/wolf/products/mdd30ts",
        fields_found={"dimensions": True, "image": True, "product_url": True},
        confidence="high",
        path=path,
    )

    data = load_source_registry(path)
    entry = next(iter(data.values()))
    assert entry["successful_domain"] == "subzero-wolf.com"
    assert entry["fields_found"]["dimensions"] is True
    assert entry["fields_found"]["image"] is True
    assert preferred_source_domains_for_row(_row(), path)[0] == "subzero-wolf.com"
    assert successful_urls_for_row(_row(), path) == ["https://subzero-wolf.com/wolf/products/mdd30ts"]


def test_source_failure_downranks_failed_domain(tmp_path):
    path = tmp_path / "source_success_registry.json"
    row = _row("Acme", "AX100", "Appliances")

    record_source_success(row, domain="good.example.com", url="https://good.example.com/ax100", path=path)
    record_source_success(row, domain="bad.example.com", url="https://bad.example.com/ax100", path=path)
    for _ in range(3):
        record_source_failure(row, domain="bad.example.com", reason="no_dimensions", path=path)

    preferred = preferred_source_domains_for_row(row, path)
    assert preferred.index("good.example.com") < preferred.index("bad.example.com")
    assert source_success_score(row, "good.example.com", path) > source_success_score(row, "bad.example.com", path)


def test_marketplace_failures_are_not_stored(tmp_path):
    path = tmp_path / "source_success_registry.json"

    entry = record_source_failure(_row(), domain="amazon.com", reason="marketplace", path=path)

    assert entry is None
    assert load_source_registry(path) == {}
