import pandas as pd

from src.further_enrichment import further_enrich_dataframe


def _base_row(**overrides):
    row = {
        "Include": True,
        "Product Name": "30 Inch Drawer Microwave",
        "Brand": "Wolf",
        "Model/SKU": "MD24TES",
        "Product Category": "Appliances",
        "Dimensions": "",
        "Image URL": "",
        "Product URL": "",
        "Notes": "",
        "Confidence Score": 0.7,
    }
    row.update(overrides)
    return row


def test_further_enrichment_disabled_is_noop():
    df = pd.DataFrame([_base_row()])

    result = further_enrich_dataframe(df, enabled=False, max_cost_usd=0.25)

    assert result.errors == []
    assert result.stage_timings["further_enrichment_rows_sent"] == 0
    assert result.dataframe.iloc[0]["Dimensions"] == ""


def test_further_enrichment_missing_openai_key_does_not_crash(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    df = pd.DataFrame([_base_row()])

    result = further_enrich_dataframe(df, enabled=True, max_cost_usd=0.25)

    assert result.errors
    assert "OPENAI_API_KEY" in result.errors[0]
    assert result.dataframe.iloc[0]["further_enrichment_status"] == "openai_unavailable"


def test_further_enrichment_budget_cap_blocks_call():
    df = pd.DataFrame([_base_row()])

    result = further_enrich_dataframe(df, enabled=True, max_cost_usd=0.0)

    assert result.stage_timings["further_enrichment_rows_sent"] == 0
    assert result.dataframe.iloc[0]["further_enrichment_status"] == "skipped_budget_cap"


def test_further_enrichment_writes_missing_medium_confidence_fields(monkeypatch):
    df = pd.DataFrame([_base_row()])

    def fake_openai(rows, *, max_cost_usd):
        return {
            "rows": [
                {
                    "row_id": "0",
                    "dimensions": '23 7/8"W x 15"H x 24"D',
                    "width_in": 23.875,
                    "height_in": 15,
                    "depth_in": 24,
                    "image_url": "https://example.com/wolf-md24tes.jpg",
                    "product_page_url": "https://example.com/wolf-md24tes",
                    "spec_sheet_url": "https://example.com/wolf-md24tes-spec.pdf",
                    "confidence": "medium",
                    "dimension_confidence": "medium",
                    "image_confidence": "medium",
                    "source_links": [
                        {
                            "type": "dimensions",
                            "url": "https://example.com/wolf-md24tes-spec.pdf",
                            "confidence": "medium",
                            "notes": "Spec sheet dimensions.",
                        },
                        {
                            "type": "image",
                            "url": "https://example.com/wolf-md24tes",
                            "confidence": "medium",
                            "notes": "Product page image.",
                        },
                    ],
                    "notes": "Verified from product/spec source.",
                }
            ]
        }, {"actual_cost_usd": 0.01, "estimated_cost_usd": 0.01, "model": "test-model"}

    monkeypatch.setattr("src.further_enrichment.save_successful_source_from_row", lambda row, notes="": None)
    result = further_enrich_dataframe(df, enabled=True, max_cost_usd=0.25, openai_call=fake_openai)
    row = result.dataframe.iloc[0]

    assert row["Dimensions"] == '23 7/8"W x 15"H x 24"D'
    assert row["Image URL"] == "https://example.com/wolf-md24tes.jpg"
    assert row["Product URL"] == "https://example.com/wolf-md24tes"
    assert row["spec_sheet_url"] == "https://example.com/wolf-md24tes-spec.pdf"
    assert row["further_enrichment_status"] == "updated"
    assert result.stage_timings["further_enrichment_rows_updated"] == 1


def test_further_enrichment_does_not_overwrite_high_confidence_values(monkeypatch):
    df = pd.DataFrame(
        [
            _base_row(
                Dimensions='24"W x 12"H x 20"D',
                **{
                    "Dimension Confidence": "high",
                    "Image URL": "https://cdn.example.com/good.jpg",
                    "image_confidence": "high",
                    "Product URL": "https://manufacturer.example.com/good",
                },
            )
        ]
    )

    def fake_openai(rows, *, max_cost_usd):
        return {
            "rows": [
                {
                    "row_id": "0",
                    "dimensions": '1"W x 2"H x 3"D',
                    "width_in": 1,
                    "height_in": 2,
                    "depth_in": 3,
                    "image_url": "https://bad.example.com/wrong.jpg",
                    "product_page_url": "https://bad.example.com/wrong",
                    "spec_sheet_url": "https://bad.example.com/wrong.pdf",
                    "confidence": "medium",
                    "dimension_confidence": "medium",
                    "image_confidence": "medium",
                    "source_links": [],
                    "notes": "Should not overwrite.",
                }
            ]
        }, {"actual_cost_usd": 0.01, "estimated_cost_usd": 0.01, "model": "test-model"}

    result = further_enrich_dataframe(df, enabled=True, max_cost_usd=0.25, openai_call=fake_openai)
    row = result.dataframe.iloc[0]

    assert row["Dimensions"] == '24"W x 12"H x 20"D'
    assert row["Image URL"] == "https://cdn.example.com/good.jpg"
    assert row["Product URL"] == "https://manufacturer.example.com/good"
    assert row.get("Width (in)", "") in {"", None}
    assert row.get("Height (in)", "") in {"", None}
    assert row.get("Depth (in)", "") in {"", None}
