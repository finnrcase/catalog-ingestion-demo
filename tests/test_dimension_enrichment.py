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


def test_normalize_model_empty_string_returns_empty_list():
    assert _normalize_model_variants("") == []


def test_normalize_model_whitespace_only_returns_empty_list():
    assert _normalize_model_variants("   ") == []


from src.dimension_enrichment import _get_manufacturer_domain, _discovered_domains


def test_get_domain_known_brand():
    assert _get_manufacturer_domain("Scotsman") == "scotsman-ice.com"


def test_get_domain_known_brand_case_insensitive():
    assert _get_manufacturer_domain("KOHLER") == "kohler.com"
    assert _get_manufacturer_domain("kohler") == "kohler.com"


def test_get_domain_wolf_and_subzero():
    assert _get_manufacturer_domain("Wolf") == "subzero-wolf.com"
    assert _get_manufacturer_domain("Sub-Zero") == "subzero-wolf.com"


def test_get_domain_unknown_brand_returns_none_without_search():
    result = _get_manufacturer_domain("UnknownBrandXYZ")
    assert result is None


def test_get_domain_unknown_brand_discovered_via_injected_search():
    import src.dimension_enrichment as _mod
    _mod._discovered_domains.pop("unknownbrandxyz2", None)  # pre-clean
    def _mock_search(query):
        return ["https://unknownbrandxyz.com/products/spec"]
    result = _get_manufacturer_domain("UnknownBrandXYZ2", _search_fn=_mock_search)
    assert result == "unknownbrandxyz.com"
    assert _mod._discovered_domains.get("unknownbrandxyz2") == "unknownbrandxyz.com"
    _mod._discovered_domains.pop("unknownbrandxyz2", None)  # post-clean


def test_get_domain_discovery_failure_returns_none():
    def _empty_search(query):
        return []
    result = _get_manufacturer_domain("NoResultsBrand", _search_fn=_empty_search)
    assert result is None


from src.dimension_enrichment import _generate_queries, _generate_retailer_queries


def test_generate_queries_with_domain_produces_site_queries():
    queries = _generate_queries(
        brand="Scotsman",
        model="SCN60PA1SU",
        domain="scotsman-ice.com",
    )
    assert 'site:scotsman-ice.com "SCN60PA1SU" dimensions' in queries
    assert 'site:scotsman-ice.com "SCN60PA1SU" specifications' in queries
    assert 'site:scotsman-ice.com "SCN60PA1SU" spec sheet' in queries
    assert 'site:scotsman-ice.com "SCN60PA1SU" installation guide' in queries


def test_generate_queries_always_includes_general_queries():
    queries = _generate_queries(brand="Scotsman", model="SCN60PA1SU", domain=None)
    assert '"Scotsman" "SCN60PA1SU" "dimensions"' in queries
    assert '"Scotsman" "SCN60PA1SU" "specifications"' in queries


def test_generate_queries_without_domain_skips_site_queries():
    queries = _generate_queries(brand="Unknown", model="XYZ", domain=None)
    assert not any(q.startswith("site:") for q in queries)


def test_generate_queries_fallbacks_with_product_name():
    queries = _generate_queries(
        brand="Scotsman",
        model="SCN60PA1SU",
        domain=None,
        product_name="Icemaker Built-In",
        sku="SCN60PA1SU",
    )
    assert '"Scotsman" "Icemaker Built-In" dimensions' in queries
    assert '"SCN60PA1SU" dimensions specifications' in queries


def test_generate_queries_order_site_before_general():
    queries = _generate_queries(
        brand="Kohler",
        model="K-3999",
        domain="kohler.com",
    )
    first_site = next(i for i, q in enumerate(queries) if q.startswith("site:"))
    first_general = next(i for i, q in enumerate(queries) if '"Kohler"' in q and "site:" not in q)
    assert first_site < first_general


def test_generate_queries_deduplicates():
    # When sku == model, the fallback sku query should not duplicate the broad query
    queries = _generate_queries(
        brand="Scotsman",
        model="SCN60PA1SU",
        domain=None,
        sku="SCN60PA1SU",
    )
    assert queries == list(dict.fromkeys(queries))


def test_generate_queries_bounded():
    # With all optional args, query count stays reasonable
    queries = _generate_queries(
        brand="Kohler",
        model="K-3999",
        domain="kohler.com",
        product_name="Highline Toilet",
        sku="K-3999",
    )
    assert len(queries) <= 9


def test_generate_retailer_queries():
    queries = _generate_retailer_queries(brand="Kohler", model="K-3999")
    assert any("build.com" in q for q in queries)
    assert any("ajmadison.com" in q for q in queries)
    assert all(q.startswith("site:") for q in queries)
    assert len(queries) == len(RETAILER_DOMAINS)
