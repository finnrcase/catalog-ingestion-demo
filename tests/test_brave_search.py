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
