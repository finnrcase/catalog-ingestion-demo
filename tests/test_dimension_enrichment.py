# tests/test_dimension_enrichment.py
from src.dimension_enrichment import (
    DimensionResult,
    BRAND_DOMAIN_TABLE,
    RETAILER_DOMAINS,
    _make_not_found_result,
    _normalize_model_variants,
)


def test_dimension_result_defaults():
    r = DimensionResult()
    assert r.dimensions == ""
    assert r.width == ""
    assert r.height == ""
    assert r.depth == ""
    assert r.length == ""
    assert r.source_url == ""
    assert r.confidence == "none"
    assert r.source_type == "none"
    assert r.status == "not_found"
    assert r.queries_tried == []
    assert r.urls_checked == []
    assert r.evidence_text == ""
    assert r.failure_reason == ""


def test_brand_domain_table_has_known_brands():
    assert BRAND_DOMAIN_TABLE["scotsman"] == "scotsman-ice.com"
    assert BRAND_DOMAIN_TABLE["kohler"] == "kohler.com"
    assert BRAND_DOMAIN_TABLE["wolf"] == "subzero-wolf.com"
    assert BRAND_DOMAIN_TABLE["ge"] == "geappliances.com"


def test_retailer_domains_has_expected_sites():
    assert "build.com" in RETAILER_DOMAINS
    assert "ajmadison.com" in RETAILER_DOMAINS
    assert "homedepot.com" in RETAILER_DOMAINS


def test_make_not_found_result_carries_diagnostics():
    r = _make_not_found_result(
        queries_tried=["q1", "q2"],
        urls_checked=["https://example.com"],
        failure_reason="no results found",
    )
    assert r.status == "not_found"
    assert r.confidence == "none"
    assert r.queries_tried == ["q1", "q2"]
    assert r.failure_reason == "no results found"


def test_normalize_model_no_spaces_unchanged():
    result = _normalize_model_variants("SCN60PA1SU")
    assert result[0] == "SCN60PA1SU"
    # No spaces → no-spaces variant is the same, no dashes variant either
    assert len(result) == 1


def test_normalize_model_with_spaces_generates_variants():
    result = _normalize_model_variants("HV 48 SS")
    assert "HV 48 SS" in result
    assert "HV48SS" in result
    assert "HV-48-SS" in result


def test_normalize_model_suffix_stripped_when_short():
    # Last token "SS" is 2 chars → stripped variant included
    result = _normalize_model_variants("HV48SS")
    assert "HV48SS" in result
    assert "HV48" in result


def test_normalize_model_no_suffix_strip_for_long_token():
    # Last token "PA1SU" is > 3 chars → no suffix stripping
    result = _normalize_model_variants("SCN60PA1SU")
    assert "SCN60" not in result


def test_normalize_model_single_char_suffix_stripped():
    result = _normalize_model_variants("MODEL-W")
    assert "MODEL" in result


def test_normalize_model_deduplicates():
    result = _normalize_model_variants("MODEL")
    assert result == list(dict.fromkeys(result))  # no duplicates, order preserved
