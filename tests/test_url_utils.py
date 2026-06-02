from src.url_utils import join_url, normalize_base_url, safe_urljoin, validate_http_url


def test_normalize_base_url_strips_accidental_env_assignment_prefixes():
    url, error = normalize_base_url(
        "NEXT_PUBLIC_API_BASE_URL=NEXT_PUBLIC_API_BASE_URL=https://catalog.example.com/",
        env_var_name="NEXT_PUBLIC_API_BASE_URL",
    )

    assert error == ""
    assert url == "https://catalog.example.com"


def test_join_url_trims_base_and_path_without_ipv6_brackets():
    url, error = join_url(
        "https://catalog.example.com///",
        "///intake/enrich",
        env_var_name="NEXT_PUBLIC_API_BASE_URL",
        source="frontend_api",
    )

    assert error == ""
    assert url == "https://catalog.example.com/intake/enrich"


def test_validate_http_url_rejects_malformed_ipv6_brackets():
    reason = validate_http_url("https://[bad")

    assert "Invalid IPv6 URL" in reason


def test_validate_http_url_allows_real_ipv6_literal():
    assert validate_http_url("http://[::1]:8000/health") == ""


def test_safe_urljoin_rejects_malformed_joined_url():
    url, error = safe_urljoin("https://example.com/product", "https://[bad", source="fixture")

    assert url == ""
    assert "Invalid IPv6 URL" in error
