import pytest
import src.brave_search as bs


def test_score_domain_brand_in_domain():
    score = bs._score_domain("https://www.wolfappliance.com/products/MDD30TS", "Wolf")
    assert score >= 80


def test_score_domain_preferred_domain_no_brand():
    score = bs._score_domain("https://www.rh.com/catalog/product/", "Herman Miller")
    assert 60 <= score <= 80


def test_score_domain_skip_domain():
    score = bs._score_domain("https://www.amazon.com/dp/B0001234", "Wolf")
    assert score < 20


def test_score_domain_neutral():
    score = bs._score_domain("https://www.some-random-shop.com/product", "Wolf")
    assert 30 <= score <= 70


def test_score_domain_brand_and_preferred():
    score = bs._score_domain("https://www.subzero-wolf.com/products/ID-36R", "Wolf")
    assert score >= 90


def test_search_product_candidates_missing_key(monkeypatch):
    monkeypatch.setattr(bs, "BRAVE_API_KEY", "")
    results = bs.search_product_candidates("Wolf MDD30TS specifications", "Wolf")
    assert results == []


def test_search_product_candidates_exception_returns_empty(monkeypatch):
    import urllib.request
    monkeypatch.setattr(bs, "BRAVE_API_KEY", "fake_key")
    original_urlopen = urllib.request.urlopen
    def raise_exc(*args, **kwargs):
        raise OSError("network error")
    monkeypatch.setattr(urllib.request, "urlopen", raise_exc)
    results = bs.search_product_candidates("Wolf MDD30TS specifications", "Wolf")
    assert results == []


def test_search_uses_session_cache_hit_without_calling_api(monkeypatch):
    """If query is in session_cache.queries, return cached result without hitting Brave."""
    from src.enrichment_cache import SessionCache
    monkeypatch.setattr(bs, "BRAVE_API_KEY", "real_key")

    call_count = {"n": 0}
    def fake_urlopen(*args, **kwargs):
        call_count["n"] += 1
        raise AssertionError("Should not have called Brave API")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    sc = SessionCache()
    fake_result = bs.SearchResult(title="Cached", url="https://cached.com", description="", domain_score=80)
    sc.queries["Wolf MDD30TS specifications"] = [fake_result]

    results = bs.search_product_candidates("Wolf MDD30TS specifications", "Wolf", session_cache=sc)
    assert results == [fake_result]
    assert call_count["n"] == 0


def test_search_stores_result_in_session_cache(monkeypatch):
    """After a real Brave call, the result is stored in session_cache.queries."""
    from src.enrichment_cache import SessionCache
    import urllib.request, io, json as _json
    monkeypatch.setattr(bs, "BRAVE_API_KEY", "fake_key")

    fake_response_data = {"web": {"results": [{"url": "https://wolf.com/p", "title": "Wolf", "description": ""}]}}
    class FakeResp:
        def read(self): return _json.dumps(fake_response_data).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: FakeResp())

    sc = SessionCache()
    bs.search_product_candidates("Wolf MDD30TS specs", "Wolf", session_cache=sc)
    assert "Wolf MDD30TS specs" in sc.queries
