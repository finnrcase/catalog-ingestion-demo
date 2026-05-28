from __future__ import annotations

import src.durable_cache as dc


class _Response:
    def __init__(self, status_code=200, payload=None, text="") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self.text = text
        self.content = b"1"

    def json(self):
        return self._payload


def test_durable_cache_disabled_without_real_supabase_env(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("ENRICHMENT_DURABLE_CACHE_ENABLED", "true")
    dc._CLIENT = None

    assert dc.durable_cache_enabled() is False


def test_durable_cache_reads_payload_map_from_supabase(monkeypatch):
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        return _Response(payload=[{
            "cache_key": "wolf_mdd30ts",
            "payload": {"dimensions": '30"W x 15"H x 17"D'},
        }])

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service_role_test_key")
    monkeypatch.setenv("ENRICHMENT_DURABLE_CACHE_ENABLED", "true")
    monkeypatch.setattr(dc.requests, "get", fake_get)
    dc._CLIENT = None

    data = dc.load_map("product_enrichment_cache", "cache_key")

    assert data["wolf_mdd30ts"]["dimensions"] == '30"W x 15"H x 17"D'
    assert calls[0]["url"].endswith("/rest/v1/product_enrichment_cache")
    assert calls[0]["params"]["select"] == "cache_key,payload"


def test_durable_cache_upserts_payload_to_supabase(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _Response(status_code=201, payload=[])

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service_role_test_key")
    monkeypatch.setenv("ENRICHMENT_DURABLE_CACHE_ENABLED", "true")
    monkeypatch.setattr(dc.requests, "post", fake_post)
    dc._CLIENT = None

    assert dc.upsert_payload(
        "image_cache",
        "cache_key",
        "wolf_mdd30ts",
        {"image_url": "https://res.cloudinary.com/demo/image/upload/wolf.jpg"},
        extra={"confidence": "HIGH"},
    ) is True

    assert calls[0]["json"]["cache_key"] == "wolf_mdd30ts"
    assert calls[0]["json"]["payload"]["image_url"].endswith("wolf.jpg")
    assert calls[0]["json"]["confidence"] == "HIGH"
    assert "resolution=merge-duplicates" in calls[0]["headers"]["Prefer"]
