# tests/test_dimension_enrichment.py
from unittest.mock import MagicMock, patch
from src.dimension_enrichment import (
    DimensionResult,
    BRAND_DOMAIN_TABLE,
    RETAILER_DOMAINS,
    _make_not_found_result,
    _normalize_model_variants,
    _fetch_and_parse_url,
    _generate_retailer_queries,
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


from src.dimension_enrichment import _get_manufacturer_domain


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


def test_get_domain_unknown_brand_discovered_via_injected_search(monkeypatch, tmp_path):
    import src.dimension_enrichment as _mod
    from src.enrichment_cache import ManufacturerDomainCache
    isolated_mfr = ManufacturerDomainCache()
    isolated_mfr._data = {}
    isolated_mfr._path = str(tmp_path / "mfr.json")
    monkeypatch.setattr(_mod, "_mfr_cache", isolated_mfr)
    def _mock_search(query):
        return ["https://unknownbrandxyz.com/products/spec"]
    result = _get_manufacturer_domain("UnknownBrandXYZ2", _search_fn=_mock_search)
    assert result == "unknownbrandxyz.com"
    assert _mod._mfr_cache.get("unknownbrandxyz2") is not None
    assert _mod._mfr_cache.get("unknownbrandxyz2")["domain"] == "unknownbrandxyz.com"


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


from src.dimension_enrichment import _fraction_to_decimal, _find_dimension_candidates


def test_fraction_to_decimal_mixed_number():
    assert _fraction_to_decimal("14 7/8") == "14.875"


def test_fraction_to_decimal_33_3_8():
    assert _fraction_to_decimal("33 3/8") == "33.375"


def test_fraction_to_decimal_simple_fraction():
    assert _fraction_to_decimal("3/4") == "0.75"


def test_fraction_to_decimal_half():
    assert _fraction_to_decimal("1/2") == "0.5"


def test_fraction_to_decimal_whole_number():
    assert _fraction_to_decimal("22") == "22"


def test_fraction_to_decimal_decimal_string():
    assert _fraction_to_decimal("14.875") == "14.875"


def test_fraction_to_decimal_empty():
    assert _fraction_to_decimal("") == ""


def test_fraction_to_decimal_invalid_returns_input():
    assert _fraction_to_decimal("abc") == "abc"


def test_find_dimension_candidates_product_dimensions_label():
    text = 'Product Dimensions: 14 7/8"W x 22"D x 33 3/8"H\nOther info'
    candidates = _find_dimension_candidates(text)
    assert len(candidates) >= 1
    assert any("14 7/8" in c for c in candidates)


def test_find_dimension_candidates_overall_dimensions_label():
    text = 'Overall Dimensions: 36"W x 34.5"H x 24"D'
    candidates = _find_dimension_candidates(text)
    assert any("36" in c for c in candidates)


def test_find_dimension_candidates_inline_pattern():
    text = 'The unit measures 14.875"W × 22"D × 33.375"H in the installed position.'
    candidates = _find_dimension_candidates(text)
    assert any("14.875" in c for c in candidates)


def test_find_dimension_candidates_returns_empty_for_no_match():
    candidates = _find_dimension_candidates("No dimensions here at all.")
    assert candidates == []


def test_find_dimension_candidates_product_dims_before_cutout():
    text = (
        'Product Dimensions: 14"W x 33"H x 22"D\n'
        'Cutout Dimensions: 13.5"W x 32.5"H x 21.5"D'
    )
    candidates = _find_dimension_candidates(text)
    # Product Dimensions candidate comes first; cutout excluded by default
    assert candidates[0].startswith("14")
    assert not any("13.5" in c for c in candidates)


def test_find_dimension_candidates_cutout_labeled():
    text = 'Cutout Dimensions: 13.5"W x 32.5"H x 21.5"D'
    candidates = _find_dimension_candidates(text, include_cutout=True)
    assert any("13.5" in c for c in candidates)


def test_find_dimension_candidates_excludes_shipping_by_default():
    text = 'Shipping Dimensions: 18"W x 40"H x 28"D\nNo other dimensions listed.'
    candidates = _find_dimension_candidates(text)
    assert candidates == []


def test_find_dimension_candidates_includes_shipping_when_flag_set():
    text = 'Shipping Dimensions: 18"W x 40"H x 28"D\nNo other dimensions listed.'
    candidates = _find_dimension_candidates(text, include_shipping=True)
    assert any("18" in c for c in candidates)


def test_find_dimension_candidates_shipping_not_included_when_product_found():
    text = (
        'Product Dimensions: 14"W x 33"H x 22"D\n'
        'Shipping Dimensions: 18"W x 40"H x 28"D'
    )
    # include_shipping=True but product found — shipping should NOT appear
    candidates = _find_dimension_candidates(text, include_shipping=True)
    assert not any("18" in c for c in candidates)
    assert any("14" in c for c in candidates)


def test_find_dimension_candidates_inline_inside_cutout_span_excluded():
    # A cutout label with inline W×H×D values — should not appear without include_cutout
    text = 'Cutout Dimensions: 13.5"W x 32.5"H x 21.5"D'
    candidates = _find_dimension_candidates(text)
    assert candidates == []


def test_find_dimension_candidates_bare_dimensions_excluded_when_inside_product_span():
    # "Product Dimensions: ..." contains the word "dimensions" — bare _DIM_LABEL
    # must not double-capture it as a Priority 2 candidate
    text = 'Product Dimensions: 14"W x 33"H x 22"D'
    candidates = _find_dimension_candidates(text)
    # Should have exactly 1 candidate (from Product Dimensions), not 2
    assert len(candidates) == 1
    assert "14" in candidates[0]


from src.dimension_enrichment import _parse_html_for_dimensions


def test_parse_html_json_ld_product_dimensions():
    html = """
    <html><body>
    <script type="application/ld+json">
    {"@type": "Product", "name": "Scotsman Icemaker",
     "description": "Product Dimensions: 14 7/8\\"W x 22\\"D x 33 3/8\\"H"}
    </script>
    </body></html>
    """
    product_dims, cutout_dims = _parse_html_for_dimensions(html)
    assert product_dims is not None
    assert "14" in product_dims
    assert cutout_dims is None


def test_parse_html_spec_table_dl():
    html = """
    <html><body>
    <dl>
      <dt>Width</dt><dd>14.875 in</dd>
      <dt>Height</dt><dd>33.375 in</dd>
      <dt>Depth</dt><dd>22 in</dd>
    </dl>
    </body></html>
    """
    product_dims, _ = _parse_html_for_dimensions(html)
    assert product_dims is not None
    assert "14.875" in product_dims or "Width" in product_dims


def test_parse_html_spec_table_tr():
    html = """
    <html><body>
    <table>
      <tr><th>Product Dimensions</th><td>36"W x 34.5"H x 24"D</td></tr>
    </table>
    </body></html>
    """
    product_dims, _ = _parse_html_for_dimensions(html)
    assert product_dims is not None
    assert "36" in product_dims


def test_parse_html_visible_text_inline():
    html = """
    <html><body>
    <p>The refrigerator measures 35.75"W x 69.875"H x 28.75"D.</p>
    </body></html>
    """
    product_dims, _ = _parse_html_for_dimensions(html)
    assert product_dims is not None
    assert "35.75" in product_dims


def test_parse_html_visible_text_unlabeled_triple_from_dimension_context():
    html = """
    <html><body>
    <p>Dimensions: 30 x 24 x 12</p>
    </body></html>
    """
    debug = {}
    product_dims, _ = _parse_html_for_dimensions(html, debug=debug)
    assert product_dims == '30"W x 24"D x 12"H'
    assert debug["dimension_parse_method"] == "visible_text"


def test_parse_html_next_data_metric_dimensions_converted_to_inches():
    html = """
    <html><body>
    <script id="__NEXT_DATA__" type="application/json">
    {"props":{"pageProps":{"product":{"specs":{"dimensions":"Dimensions: 762 mm x 610 mm x 305 mm"}}}}}
    </script>
    </body></html>
    """
    debug = {}
    product_dims, _ = _parse_html_for_dimensions(html, debug=debug)
    assert product_dims == '30"W x 24.016"D x 12.008"H'
    assert debug["next_data_found"] is True
    assert debug["dimension_parse_method"] == "next_data"


def test_parse_html_json_ld_width_height_depth_keys_preserve_labels():
    html = """
    <html><body>
    <script type="application/ld+json">
    {"@type":"Product","width":"762 mm","height":"305 mm","depth":"610 mm"}
    </script>
    </body></html>
    """
    debug = {}
    product_dims, _ = _parse_html_for_dimensions(html, debug=debug)
    assert product_dims == '30"W x 12.008"H x 24.016"D'
    assert debug["json_ld_found"] is True
    assert debug["dimension_parse_method"] == "json_ld"


def test_parse_html_definition_list_two_axis_partial_kept():
    html = """
    <html><body>
    <dl>
      <dt>Product Width</dt><dd>30 in</dd>
      <dt>Product Depth</dt><dd>24 in</dd>
    </dl>
    </body></html>
    """
    debug = {}
    product_dims, _ = _parse_html_for_dimensions(html, debug=debug)
    assert product_dims == '30"W x 24"D'
    assert debug["partial_dimensions_found"] == '30"W x 24"D'
    assert debug["dimension_parse_method"] == "definition_list_parts"


def test_parse_html_rejects_shipping_only_dimensions():
    html = """
    <html><body>
    <p>Shipping Dimensions: 40"W x 30"D x 20"H</p>
    </body></html>
    """
    debug = {}
    product_dims, _ = _parse_html_for_dimensions(html, debug=debug)
    assert product_dims is None
    assert "shipping" in debug["rejected_dimensions_reason"]


def test_parse_html_appliance_cutout_stored_separately():
    html = """
    <html><body>
    <p>Product Dimensions: 23.875"W x 33.375"H x 22"D</p>
    <p>Cutout Dimensions: 23"W x 33"H x 21"D</p>
    </body></html>
    """
    product_dims, cutout_dims = _parse_html_for_dimensions(html, is_appliance=True)
    assert product_dims is not None
    assert "23.875" in product_dims
    assert cutout_dims is not None
    assert "23" in cutout_dims


def test_parse_html_no_dimensions_returns_none():
    html = "<html><body><p>No specifications here.</p></body></html>"
    product_dims, cutout_dims = _parse_html_for_dimensions(html)
    assert product_dims is None
    assert cutout_dims is None


def test_parse_html_non_appliance_cutout_not_returned():
    html = """
    <html><body>
    <p>Product Dimensions: 23.875"W x 33.375"H x 22"D</p>
    <p>Cutout Dimensions: 23"W x 33"H x 21"D</p>
    </body></html>
    """
    product_dims, cutout_dims = _parse_html_for_dimensions(html, is_appliance=False)
    assert product_dims is not None
    assert cutout_dims is None


from src.dimension_enrichment import _parse_text_pages_for_dimensions, _parse_pdf_for_dimensions


def test_parse_text_pages_product_dimensions_label():
    pages = ['Product Dimensions: 14 7/8"W x 22"D x 33 3/8"H\nSome other text.']
    product_dims, cutout_dims = _parse_text_pages_for_dimensions(pages)
    assert product_dims is not None
    assert "14 7/8" in product_dims
    assert cutout_dims is None


def test_parse_text_pages_appliance_extracts_cutout():
    pages = [
        'Product Dimensions: 23.875"W x 33.375"H x 22"D\n'
        'Cutout Dimensions: 23"W x 33"H x 21"D'
    ]
    product_dims, cutout_dims = _parse_text_pages_for_dimensions(pages, is_appliance=True)
    assert product_dims is not None
    assert cutout_dims is not None
    assert "23.875" in product_dims
    assert "23" in cutout_dims


def test_parse_text_pages_ignores_shipping_when_product_found():
    pages = [
        'Product Dimensions: 14"W x 33"H x 22"D\n'
        'Shipping Dimensions: 18"W x 40"H x 28"D'
    ]
    product_dims, _ = _parse_text_pages_for_dimensions(pages)
    assert "14" in product_dims
    assert "18" not in product_dims


def test_parse_text_pages_falls_back_to_shipping_when_nothing_else():
    pages = ['Shipping Dimensions: 18"W x 40"H x 28"D']
    product_dims, _ = _parse_text_pages_for_dimensions(pages, include_shipping_fallback=True)
    assert product_dims is not None
    assert "18" in product_dims


def test_parse_text_pages_no_dimensions_returns_none():
    pages = ["Installation instructions. Plug into outlet. Done."]
    product_dims, cutout_dims = _parse_text_pages_for_dimensions(pages)
    assert product_dims is None
    assert cutout_dims is None


def test_parse_text_pages_stops_at_first_match_across_pages():
    pages = [
        "No dimensions on page 1.",
        'Product Dimensions: 36"W x 34.5"H x 24"D',
        'Another page with 50"W x 50"H x 50"D',
    ]
    product_dims, _ = _parse_text_pages_for_dimensions(pages)
    assert "36" in product_dims
    assert "50" not in product_dims


def test_parse_text_pages_shipping_not_used_when_product_found():
    # Even with include_shipping_fallback, shipping must not appear when product dims found
    pages = [
        'Product Dimensions: 14"W x 33"H x 22"D\n'
        'Shipping Dimensions: 18"W x 40"H x 28"D'
    ]
    product_dims, _ = _parse_text_pages_for_dimensions(pages, include_shipping_fallback=True)
    assert "14" in product_dims
    assert "18" not in product_dims


def test_parse_pdf_returns_none_on_empty_bytes():
    product_dims, cutout_dims = _parse_pdf_for_dimensions(b"")
    assert product_dims is None
    assert cutout_dims is None


def test_parse_pdf_returns_none_on_corrupt_bytes():
    product_dims, cutout_dims = _parse_pdf_for_dimensions(b"not a pdf at all")
    assert product_dims is None
    assert cutout_dims is None


def test_parse_text_pages_empty_list_returns_none():
    product_dims, cutout_dims = _parse_text_pages_for_dimensions([])
    assert product_dims is None
    assert cutout_dims is None


def test_fetch_and_parse_url_html_page():
    mock_resp = MagicMock()
    mock_resp.headers = {"content-type": "text/html"}
    mock_resp.text = (
        "<html><body>"
        '<p>Product Dimensions: 36"W x 34.5"H x 24"D</p>'
        "</body></html>"
    )
    mock_resp.content = mock_resp.text.encode()
    mock_resp.raise_for_status = MagicMock()

    mock_httpx = MagicMock()
    mock_httpx.get.return_value = mock_resp
    with patch("src.dimension_enrichment._httpx", mock_httpx):
        product_dims, cutout_dims, source_type_suffix = _fetch_and_parse_url(
            "https://example.com/product"
        )
    assert product_dims is not None
    assert "36" in product_dims
    assert cutout_dims is None
    assert source_type_suffix == "page"


def test_fetch_and_parse_url_pdf_content_type():
    mock_resp = MagicMock()
    mock_resp.headers = {"content-type": "application/pdf"}
    mock_resp.content = b"%PDF fake"
    mock_resp.raise_for_status = MagicMock()

    mock_httpx = MagicMock()
    mock_httpx.get.return_value = mock_resp
    with patch("src.dimension_enrichment._httpx", mock_httpx):
        with patch(
            "src.dimension_enrichment._parse_pdf_for_dimensions",
            return_value=('36"W x 34.5"H x 24"D', None),
        ):
            product_dims, cutout_dims, source_type_suffix = _fetch_and_parse_url(
                "https://example.com/spec.pdf"
            )
    assert product_dims is not None
    assert source_type_suffix == "pdf"


def test_fetch_and_parse_url_returns_none_on_http_error():
    mock_httpx = MagicMock()
    mock_httpx.get.side_effect = Exception("connection error")
    with patch("src.dimension_enrichment._httpx", mock_httpx):
        product_dims, cutout_dims, source_type_suffix = _fetch_and_parse_url(
            "https://example.com/product"
        )
    assert product_dims is None
    assert source_type_suffix == "page"


def test_fetch_and_parse_url_returns_none_when_httpx_unavailable():
    with patch("src.dimension_enrichment._httpx", None):
        product_dims, cutout_dims, source_type_suffix = _fetch_and_parse_url(
            "https://example.com/product"
        )
    assert product_dims is None
    assert cutout_dims is None
    assert source_type_suffix == "page"


def test_fetch_and_parse_url_returns_none_on_http_status_error():
    import httpx
    mock_httpx = MagicMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock()
    )
    mock_httpx.get.return_value = mock_resp
    with patch("src.dimension_enrichment._httpx", mock_httpx):
        product_dims, cutout_dims, source_type_suffix = _fetch_and_parse_url(
            "https://example.com/product"
        )
    assert product_dims is None
    assert cutout_dims is None


from src.dimension_enrichment import _assign_confidence


def test_confidence_exact_model_manufacturer_page_is_high():
    assert _assign_confidence(
        model_variant="SCN60PA1SU",
        primary_model="SCN60PA1SU",
        is_manufacturer=True,
    ) == "high"


def test_confidence_exact_model_case_insensitive():
    # .lower() normalization — mixed-case variant matches primary
    assert _assign_confidence(
        model_variant="scn60pa1su",
        primary_model="SCN60PA1SU",
        is_manufacturer=True,
    ) == "high"


def test_confidence_exact_model_retailer_is_medium():
    assert _assign_confidence(
        model_variant="SCN60PA1SU",
        primary_model="SCN60PA1SU",
        is_manufacturer=False,
    ) == "medium"


def test_confidence_variant_match_manufacturer_is_medium():
    # No-spaces variant matches — not exact primary model
    assert _assign_confidence(
        model_variant="HV48SS",    # variant (spaces removed)
        primary_model="HV 48 SS",  # primary
        is_manufacturer=True,
    ) == "medium"


def test_confidence_variant_match_retailer_is_medium():
    # Spaces-removed variant matches primary on retailer → medium
    assert _assign_confidence(
        model_variant="HV48SS",
        primary_model="HV 48 SS",
        is_manufacturer=False,
    ) == "medium"


def test_confidence_suffix_stripped_variant_is_low():
    assert _assign_confidence(
        model_variant="HV48",      # suffix stripped — partial
        primary_model="HV48SS",
        is_manufacturer=True,
    ) == "low"


def test_confidence_suffix_stripped_variant_retailer_is_low():
    assert _assign_confidence(
        model_variant="HV48",
        primary_model="HV48SS",
        is_manufacturer=False,
    ) == "low"


from src.dimension_enrichment import find_dimensions
from src.dimensions import has_complete_3d_dimensions


def _scotsman_row() -> dict:
    return {
        "Brand": "Scotsman",
        "Model/SKU": "SCN60PA1SU",
        "Product Name": "Scotsman Icemaker Built-In Pump",
        "Product Category": "Appliances",
        "Dimensions": "",
    }


def test_find_dimensions_skips_row_with_complete_dims():
    row = _scotsman_row()
    row["Dimensions"] = '14 7/8"W x 22"D x 33 3/8"H'
    result = find_dimensions(row)
    assert result.status == "not_found"
    assert result.failure_reason == "dimensions already complete"


def test_find_dimensions_skips_row_missing_brand():
    row = _scotsman_row()
    row["Brand"] = ""
    result = find_dimensions(row)
    assert result.status == "not_found"
    assert "brand" in result.failure_reason.lower()


def test_find_dimensions_skips_row_missing_sku():
    row = _scotsman_row()
    row["Model/SKU"] = ""
    result = find_dimensions(row)
    assert result.status == "not_found"
    assert "model" in result.failure_reason.lower()


def test_find_dimensions_returns_found_on_high_confidence_result():
    def _mock_search(query, **kwargs):
        return ["https://scotsman-ice.com/products/scn60pa1su"]

    def _mock_fetch(url, *, is_appliance=False):
        return ('14 7/8"W x 22"D x 33 3/8"H', None, "page")

    with patch("src.dimension_enrichment._brave_search_urls", side_effect=_mock_search):
        with patch("src.dimension_enrichment._fetch_and_parse_url", side_effect=_mock_fetch):
            result = find_dimensions(_scotsman_row())

    assert result.status == "found"
    assert result.confidence == "high"
    assert result.source_type == "manufacturer_page"
    assert has_complete_3d_dimensions(result.dimensions)
    assert result.source_url == "https://scotsman-ice.com/products/scn60pa1su"
    assert len(result.queries_tried) >= 1
    assert len(result.urls_checked) >= 1


def test_find_dimensions_returns_not_found_when_no_results():
    with patch("src.dimension_enrichment._brave_search_urls", return_value=[]):
        result = find_dimensions(_scotsman_row())
    assert result.status == "not_found"
    assert result.failure_reason != ""


def test_find_dimensions_records_low_confidence_skipped():
    def _mock_search(query, **kwargs):
        return ["https://scotsman-ice.com/other"]

    def _mock_fetch(url, *, is_appliance=False):
        return ('14 7/8"W x 22"D x 33 3/8"H', None, "page")

    row = _scotsman_row()
    row["Model/SKU"] = "SCN60SS"

    with patch("src.dimension_enrichment._brave_search_urls", side_effect=_mock_search):
        with patch("src.dimension_enrichment._fetch_and_parse_url", side_effect=_mock_fetch):
            with patch(
                "src.dimension_enrichment._assign_confidence",
                return_value="low",
            ):
                result = find_dimensions(row)
    assert result.status == "low_confidence_skipped"
    assert result.dimensions == ""


def test_find_dimensions_appliance_cutout_in_evidence():
    def _mock_search(query, **kwargs):
        return ["https://scotsman-ice.com/products/scn60pa1su"]

    def _mock_fetch(url, *, is_appliance=False):
        return ('14 7/8"W x 22"D x 33 3/8"H', '13.5"W x 21.5"D x 32"H', "page")

    with patch("src.dimension_enrichment._brave_search_urls", side_effect=_mock_search):
        with patch("src.dimension_enrichment._fetch_and_parse_url", side_effect=_mock_fetch):
            result = find_dimensions(_scotsman_row())

    assert result.status == "found"
    assert result.evidence_text != ""
    assert "13.5" not in result.dimensions


def test_find_dimensions_retailer_phase_succeeds_when_manufacturer_fails():
    def _mock_search(query, **kwargs):
        # Only retailer queries get results
        for retailer in ["build.com", "ajmadison.com", "bestbuy.com"]:
            if retailer in query:
                return ["https://build.com/kohler-k-3999-toilet"]
        return []

    def _mock_fetch(url, *, is_appliance=False):
        return ('28"W x 30"H x 17"D', None, "page")

    row = {
        "Brand": "Kohler",
        "Model/SKU": "K-3999",
        "Product Name": "Highline Toilet",
        "Product Category": "Plumbing",
        "Dimensions": "",
    }

    with patch("src.dimension_enrichment._brave_search_urls", side_effect=_mock_search):
        with patch("src.dimension_enrichment._fetch_and_parse_url", side_effect=_mock_fetch):
            result = find_dimensions(row)

    assert result.status == "found"
    assert result.confidence == "medium"
    assert "retailer" in result.source_type


def test_find_dimensions_retailer_phase_not_called_when_manufacturer_succeeds():
    def _mock_search(query, **kwargs):
        # Return a result for any non-retailer query
        if "site:" not in query or "scotsman-ice.com" in query:
            return ["https://scotsman-ice.com/products/scn60pa1su"]
        return []

    def _mock_fetch(url, *, is_appliance=False):
        return ('14 7/8"W x 22"D x 33 3/8"H', None, "page")

    retailer_called = []
    original_retailer_queries = _generate_retailer_queries

    def _mock_retailer_queries(brand, model):
        retailer_called.append((brand, model))
        return original_retailer_queries(brand, model)

    with patch("src.dimension_enrichment._brave_search_urls", side_effect=_mock_search):
        with patch("src.dimension_enrichment._fetch_and_parse_url", side_effect=_mock_fetch):
            with patch("src.dimension_enrichment._generate_retailer_queries", side_effect=_mock_retailer_queries):
                result = find_dimensions(_scotsman_row())

    assert result.status == "found"
    assert retailer_called == []  # retailer phase must not have fired


def test_find_dimensions_accepts_session_cache_and_budget():
    """find_dimensions must accept session_cache and budget kwargs without error."""
    from src.dimension_enrichment import find_dimensions
    from src.enrichment_cache import SessionCache, SearchBudget
    row = {"Brand": "Wolf", "Model/SKU": "MDD30TS", "Dimensions": "", "Product Name": "", "Product Category": ""}
    sc = SessionCache()
    budget = SearchBudget(max_searches=0, max_urls=0)  # budget exhausted immediately
    result = find_dimensions(row, session_cache=sc, budget=budget)
    # With zero budget, must return not_found without crashing
    assert result.status in ("not_found", "low_confidence_skipped")
    assert budget.urls_used == 0  # zero-budget should have fetched nothing


def test_find_dimensions_uses_existing_product_url_before_search():
    from src.enrichment_cache import SessionCache, SearchBudget
    from src.dimension_enrichment import find_dimensions

    row = _scotsman_row()
    row["Product URL"] = "https://scotsman-ice.com/products/scn60pa1su"
    sc = SessionCache()
    sc.urls[row["Product URL"]] = """
    <html><body>
      <table>
        <tr><th>Width</th><td>14 7/8 in</td></tr>
        <tr><th>Depth</th><td>22 in</td></tr>
      </table>
    </body></html>
    """
    budget = SearchBudget(max_searches=0, max_urls=0)

    with patch("src.dimension_enrichment._brave_search_urls") as mock_search:
        result = find_dimensions(row, session_cache=sc, budget=budget)

    mock_search.assert_not_called()
    assert result.status == "found"
    assert result.confidence == "medium"
    assert "14 7/8" in result.dimensions
    assert "22" in result.dimensions
    assert result.debug["page_fetch_attempted"] is True
    assert result.debug["spec_table_found"] is True
    assert budget.searches_used == 0
    assert budget.urls_used == 0


def test_find_dimensions_fetches_spec_pdf_link_from_verified_product_page():
    from src.enrichment_cache import SessionCache, SearchBudget
    from src.dimension_enrichment import find_dimensions

    row = _scotsman_row()
    row["Product URL"] = "https://scotsman-ice.com/products/scn60pa1su"
    sc = SessionCache()
    sc.urls[row["Product URL"]] = """
    <html><body>
      <a href="/docs/scn60pa1su-spec-sheet.pdf">Specification sheet</a>
    </body></html>
    """
    budget = SearchBudget(max_searches=0, max_urls=1)

    def _mock_fetch(url, *, is_appliance=False):
        assert url == "https://scotsman-ice.com/docs/scn60pa1su-spec-sheet.pdf"
        return ('14 7/8"W x 22"D x 33 3/8"H', None, "pdf")

    with patch("src.dimension_enrichment._brave_search_urls") as mock_search:
        with patch("src.dimension_enrichment._fetch_and_parse_url", side_effect=_mock_fetch):
            result = find_dimensions(row, session_cache=sc, budget=budget)

    mock_search.assert_not_called()
    assert result.status == "found"
    assert result.confidence == "high"
    assert result.source_url.endswith("scn60pa1su-spec-sheet.pdf")
    assert result.debug["spec_pdf_links_found"] == 1
    assert result.debug["spec_pdf_fetched"] is True
    assert budget.urls_used == 1


def test_brave_search_urls_respects_session_cache():
    """_brave_search_urls returns session cache hit without consuming budget."""
    from src.dimension_enrichment import _brave_search_urls
    from src.enrichment_cache import SessionCache, SearchBudget
    from src.brave_search import SearchResult
    sc = SessionCache()
    sc.queries["site:wolf.com MDD30TS dimensions"] = [
        SearchResult(title="Wolf", url="https://wolf.com/mdd30ts", description="", domain_score=90)
    ]
    budget = SearchBudget(max_searches=0, max_urls=5)  # zero searches allowed
    urls = _brave_search_urls("site:wolf.com MDD30TS dimensions", session_cache=sc, budget=budget)
    assert "https://wolf.com/mdd30ts" in urls
    assert budget.searches_used == 0  # session hit, no budget consumed


def test_brave_search_urls_respects_budget_exhaustion(monkeypatch):
    """_brave_search_urls returns [] without calling Brave when budget is exhausted."""
    from src.dimension_enrichment import _brave_search_urls
    from src.enrichment_cache import SessionCache, SearchBudget
    import src.brave_search as bs
    monkeypatch.setattr(bs, "BRAVE_API_KEY", "fake")
    sc = SessionCache()
    budget = SearchBudget(max_searches=0, max_urls=5)
    urls = _brave_search_urls("wolf MDD30TS dimensions", session_cache=sc, budget=budget)
    assert urls == []


def test_get_manufacturer_domain_uses_persistent_cache(tmp_path, monkeypatch):
    """_get_manufacturer_domain writes discovered domains to ManufacturerDomainCache."""
    from src.enrichment_cache import ManufacturerDomainCache
    import src.dimension_enrichment as de
    # Give the module-level cache a tmp path
    monkeypatch.setattr(de._mfr_cache, "_path", str(tmp_path / "mfr.json"))
    monkeypatch.setattr(de._mfr_cache, "_data", None)  # force re-load

    def fake_search(query):
        return ["https://acme-brand.com/products"]

    domain = de._get_manufacturer_domain("Acme Brand", _search_fn=fake_search)
    assert domain == "acme-brand.com"
    # Should now be in persistent cache
    de._mfr_cache._load()
    assert de._mfr_cache.get("acme brand") is not None
