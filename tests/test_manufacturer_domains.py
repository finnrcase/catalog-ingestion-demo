import json

import pytest

from src.manufacturer_domains import (
    clean_domain,
    get_domain_for_brand,
    is_retailer_domain,
    normalize_brand,
    record_discovered_domain,
    record_verified_domain,
    save_manufacturer_override,
)


def test_normalize_brand_lowercases_and_strips_punctuation():
    assert normalize_brand("  Sub-Zero / Wolf! ") == "sub zero wolf"


def test_clean_domain_strips_scheme_path_and_www():
    assert clean_domain("https://www.scotsman-ice.com/products") == "scotsman-ice.com"


def test_clean_domain_rejects_invalid_hostname():
    with pytest.raises(ValueError):
        clean_domain("not a host")


def test_save_manufacturer_override_persists_user_mapping(tmp_path):
    path = tmp_path / "manufacturer_domain_cache.json"

    entry = save_manufacturer_override("Scotsman", "https://scotsman-ice.com/products", path=path)

    assert entry["brand"] == "scotsman"
    assert entry["domain"] == "scotsman-ice.com"
    assert entry["official_domain"] == "scotsman-ice.com"
    assert entry["source"] == "user"
    assert entry["confidence"] == "high"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["scotsman"]["official_domain"] == "scotsman-ice.com"


def test_user_override_takes_priority_over_discovered_domain(tmp_path):
    path = tmp_path / "manufacturer_domain_cache.json"
    record_discovered_domain("Scotsman", "example-discovered.com", path=path)
    save_manufacturer_override("Scotsman", "scotsman-ice.com", path=path)

    assert get_domain_for_brand("Scotsman", path=path) == ("scotsman-ice.com", "user")


def test_discovered_domain_does_not_replace_user_override(tmp_path):
    path = tmp_path / "manufacturer_domain_cache.json"
    save_manufacturer_override("Scotsman", "scotsman-ice.com", path=path)
    record_discovered_domain("Scotsman", "example-discovered.com", path=path)

    assert get_domain_for_brand("Scotsman", path=path) == ("scotsman-ice.com", "user")


def test_verified_domain_saves_and_reloads(tmp_path):
    path = tmp_path / "manufacturer_domain_cache.json"

    entry = record_verified_domain(
        "Kallista",
        "https://www.kallista.com/products",
        path=path,
        evidence_url="https://www.kallista.com/products/p123",
    )

    assert entry["brand"] == "kallista"
    assert entry["official_domain"] == "kallista.com"
    assert entry["source"] == "verified"
    assert entry["confidence"] == "high"
    assert entry["evidence_url"].endswith("/p123")
    assert get_domain_for_brand("Kallista", path=path) == ("kallista.com", "verified")


def test_discovered_retailer_domain_is_rejected_as_official(tmp_path):
    path = tmp_path / "manufacturer_domain_cache.json"

    entry = record_discovered_domain("Wolf", "https://www.wayfair.com/brand/bnd/wolf.html", path=path)

    assert entry is None
    assert get_domain_for_brand("Wolf", path=path) != ("wayfair.com", "discovered")
    assert is_retailer_domain("www.wayfair.com") is True
