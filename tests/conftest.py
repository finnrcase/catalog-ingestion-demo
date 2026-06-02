from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_source_memory_storage(monkeypatch, tmp_path):
    """Keep durable source memory from leaking between tests.

    Production can use Supabase or runtime JSON fallback. Tests should not read
    a developer's /tmp source-memory cache because it changes enrichment control
    flow before the mocked search/dimension functions run.
    """
    try:
        import src.source_memory as sm
    except Exception:
        return

    monkeypatch.setattr(sm, "_SOURCE_MEMORY_ENABLED", True)
    monkeypatch.setattr(sm, "_supabase_configured", lambda: False)
    monkeypatch.setattr(sm, "_PRODUCT_SOURCE_PATH", Path(tmp_path / "stored_product_sources.json"))
    monkeypatch.setattr(sm, "_PREFERRED_DOMAIN_PATH", Path(tmp_path / "preferred_source_domains.json"))
