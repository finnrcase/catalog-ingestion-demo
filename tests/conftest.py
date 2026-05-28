from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_source_success_registry(monkeypatch, tmp_path):
    """Keep resolver source-learning tests from writing to real project data."""
    monkeypatch.setenv("SOURCE_SUCCESS_REGISTRY_PATH", str(tmp_path / "source_success_registry.json"))
    monkeypatch.setenv("ENRICHMENT_COST_HISTORY_PATH", str(tmp_path / "enrichment_cost_history.json"))
    monkeypatch.setenv("PREFERRED_WEBSITES_PATH", str(tmp_path / "preferred_product_websites.json"))
    monkeypatch.setenv("IMAGE_CACHE_PATH", str(tmp_path / "image_cache.json"))
