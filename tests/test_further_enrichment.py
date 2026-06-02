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


def test_further_enrichment_disabled_does_not_fetch_evidence(monkeypatch):
    df = pd.DataFrame([_base_row(**{"Product URL": "https://example.com/product"})])
    monkeypatch.setattr(
        "src.further_enrichment._fetch_source_evidence",
        lambda row: (_ for _ in ()).throw(AssertionError("should not fetch evidence when disabled")),
    )

    result = further_enrich_dataframe(df, enabled=False, max_cost_usd=0.25)

    assert result.stage_timings["further_enrichment_rows_sent"] == 0


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


def test_further_enrichment_sends_source_evidence_to_openai(monkeypatch):
    df = pd.DataFrame([
        _base_row(
            **{
                "Product URL": "https://manufacturer.example.com/wolf-md24tes",
                "Dimension Source URL": "https://manufacturer.example.com/wolf-md24tes-spec.pdf",
            }
        )
    ])

    def fake_evidence(row):
        return (
            [
                {
                    "source_url": "https://manufacturer.example.com/wolf-md24tes-spec.pdf",
                    "source_type": "pdf",
                    "text_snippet": 'Overall Dimensions 23 7/8"W x 15"H x 24"D',
                    "candidate_images": [],
                }
            ],
            [],
            ["https://manufacturer.example.com/images/md24tes.jpg"],
        )

    def fake_openai(rows, *, max_cost_usd):
        assert rows[0]["source_evidence"][0]["text_snippet"].startswith("Overall Dimensions")
        assert rows[0]["candidate_image_urls"] == ["https://manufacturer.example.com/images/md24tes.jpg"]
        return {
            "rows": [
                {
                    "row_id": "0",
                    "normalized_title": "Wolf MD24TES Drawer Microwave",
                    "dimensions": None,
                    "width_in": 23.875,
                    "height_in": 15,
                    "depth_in": 24,
                    "dimension_raw_text": 'Overall Dimensions 23 7/8"W x 15"H x 24"D',
                    "dimension_source_url": "https://manufacturer.example.com/wolf-md24tes-spec.pdf",
                    "dimension_type": "overall",
                    "image_url": "https://manufacturer.example.com/images/md24tes.jpg",
                    "image_source_url": "https://manufacturer.example.com/wolf-md24tes",
                    "product_page_url": "https://manufacturer.example.com/wolf-md24tes",
                    "spec_sheet_url": "https://manufacturer.example.com/wolf-md24tes-spec.pdf",
                    "confidence": "high",
                    "dimension_confidence": "high",
                    "image_confidence": "medium",
                    "safe_to_write": True,
                    "reason": "Fetched spec evidence contains all three axes.",
                    "source_links": [],
                    "notes": "",
                }
            ]
        }, {"actual_cost_usd": 0.01, "estimated_cost_usd": 0.01, "model": "test-model"}

    monkeypatch.setattr("src.further_enrichment._fetch_source_evidence", fake_evidence)
    monkeypatch.setattr("src.further_enrichment.save_successful_source_from_row", lambda row, notes="": None)
    result = further_enrich_dataframe(df, enabled=True, max_cost_usd=0.25, openai_call=fake_openai)
    row = result.dataframe.iloc[0]

    assert row["Dimensions"] == '23.875"W x 15"H x 24"D'
    assert row["Dimension Source URL"] == "https://manufacturer.example.com/wolf-md24tes-spec.pdf"
    assert row["dimension_raw_text"].startswith("Overall Dimensions")
    assert row["Image URL"] == "https://manufacturer.example.com/images/md24tes.jpg"


def test_further_enrichment_safe_to_write_false_blocks_fields(monkeypatch):
    df = pd.DataFrame([_base_row()])

    def fake_openai(rows, *, max_cost_usd):
        return {
            "rows": [
                {
                    "row_id": "0",
                    "normalized_title": "Wolf MD24TES",
                    "dimensions": '1"W x 2"H x 3"D',
                    "width_in": 1,
                    "height_in": 2,
                    "depth_in": 3,
                    "dimension_raw_text": "Unsupported guess",
                    "dimension_source_url": "https://example.com",
                    "dimension_type": "unknown",
                    "image_url": "https://example.com/guess.jpg",
                    "image_source_url": "https://example.com",
                    "product_page_url": "https://example.com",
                    "spec_sheet_url": None,
                    "confidence": "medium",
                    "dimension_confidence": "medium",
                    "image_confidence": "medium",
                    "safe_to_write": False,
                    "reason": "No fetched evidence supports the values.",
                    "source_links": [],
                    "notes": "",
                }
            ]
        }, {"actual_cost_usd": 0.01, "estimated_cost_usd": 0.01, "model": "test-model"}

    result = further_enrich_dataframe(df, enabled=True, max_cost_usd=0.25, openai_call=fake_openai)
    row = result.dataframe.iloc[0]

    assert row["Dimensions"] == ""
    assert row["Image URL"] == ""
    assert row["further_enrichment_status"] == "no_verified_fields"
    assert "No fetched evidence" in row["further_enrichment_error"]


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
