from src.enrichment_cost_history import append_cost_history, load_cost_history


def test_append_cost_history_persists_bravi_cost_summary(tmp_path):
    path = tmp_path / "cost_history.json"
    summary = {
        "mode": "fast",
        "total_rows": 3,
        "external_enrichment_rows": 1,
        "bravi_searches": 2,
        "bravi_cost_usd": 0.006,
        "estimated_cost_usd": 0.011,
        "cache_hits": 2,
        "paid_calls": 3,
        "cache_hit_rate": 0.667,
        "target_budget_usd": 0.10,
        "hard_budget_usd": 0.25,
        "skipped_calls_due_budget": 1,
    }
    rows = [{
        "Project": "1 Lily Pond Lane",
        "_source_filename": "quote.pdf",
        "_source_pdf_id": "upload-123",
    }]

    entry = append_cost_history(summary, rows, path=path)
    stored = load_cost_history(path)

    assert entry["upload_id"] == "upload-123"
    assert entry["project_name"] == "1 Lily Pond Lane"
    assert entry["file_name"] == "quote.pdf"
    assert entry["bravi_calls"] == 2
    assert entry["bravi_cost_usd"] == 0.006
    assert entry["total_enrichment_cost_usd"] == 0.011
    assert stored == [entry]
