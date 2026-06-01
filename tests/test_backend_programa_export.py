from fastapi.testclient import TestClient
import datetime

from backend.main import app


client = TestClient(app)


def _rows():
    return {
        "rows": [
            {
                "Include": True,
                "Product Name": "Lamp",
                "Product Category": "Decor",
                "Image URL": "https://cdn.example.com/lamp.jpg",
                "Quantity": 1,
            },
            {
                "Include": True,
                "Product Name": "",
                "Product Category": "Decor",
                "Image URL": "https://cdn.example.com/skip.jpg",
                "Quantity": 1,
            },
        ]
    }


def test_programa_export_validate_endpoint():
    response = client.post("/export/programa/validate", json=_rows())

    assert response.status_code == 200
    data = response.json()
    assert data["export_count"] == 1
    assert len(data["skipped"]) == 1
    assert data["section_counts"] == {"Decor": 1}
    assert "canonical_sections" in data


def test_schema_exposes_canonical_sections():
    response = client.get("/schema")

    assert response.status_code == 200
    assert response.json()["sections"] == [
        "Appliances",
        "Lighting",
        "Plumbing",
        "Cabinetry",
        "Flooring",
        "Furniture",
        "Decor",
        "Hardware",
        "Exterior",
        "General",
    ]


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_image_endpoint_returns_secure_url(monkeypatch):
    monkeypatch.setattr(
        "backend.main.upload_image",
        lambda file: "https://res.cloudinary.com/demo/image/upload/handmade-doll.jpg",
    )

    response = client.post(
        "/api/upload-image",
        files={"file": ("doll.jpg", b"fake image", "image/jpeg")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["secure_url"] == "https://res.cloudinary.com/demo/image/upload/handmade-doll.jpg"
    assert "cloudinary_ms" in data["stage_timings"]


def test_intake_generate_ai_fallback_keeps_deterministic_rows_when_key_missing(monkeypatch):
    def fail_enrich(*args, **kwargs):
        raise AssertionError("enrichment must not run during PDF parsing")

    def fake_parse_pdf_rows(pdf, project="", room="", supplier="", notes="", stage_timings=None):
        if stage_timings is not None:
            stage_timings.update(
                {
                    "pdf_text_extraction_ms": 1.0,
                    "table_row_parsing_ms": 2.0,
                    "normalization_ms": 0.5,
                    "page_count": 1,
                    "rows_returned": 1,
                }
            )
        return [
            {
                "Product Name": "Panel Ready Icemaker",
                "Brand": "Scotsman",
                "Model/SKU": "SCN60PA1SU",
                "Quantity": 1,
                "Source Type": "PDF",
            }
        ]

    monkeypatch.setattr("src.ai_extraction.ANTHROPIC_API_KEY", "")
    monkeypatch.setattr("backend.main.enrich_dataframe", fail_enrich)
    monkeypatch.setattr("backend.main.recover_images_for_dataframe", fail_enrich)
    monkeypatch.setattr("backend.main.parse_pdf_rows", fake_parse_pdf_rows)

    response = client.post(
        "/intake/generate",
        data={"project": "1 Lily Pond", "room": "Kitchen", "use_ai_pdf": "true"},
        files={"files": ("quote.pdf", b"%PDF fake", "application/pdf")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["rows"][0]["Model/SKU"] == "SCN60PA1SU"
    assert data["rows"][0]["ai_used"] is False
    assert "ANTHROPIC_API_KEY" in data["rows"][0]["ai_skipped_reason"]
    assert data["stage_timings"]["parse_mode"] == "deterministic_plus_ai"
    assert data["stage_timings"]["enrichment_ms"] == 0.0
    assert data["stage_timings"]["image_recovery_ms"] == 0.0
    assert any("ANTHROPIC_API_KEY" in err for err in data["errors"])


def test_upload_image_endpoint_rejects_missing_secure_url(monkeypatch):
    monkeypatch.setattr("backend.main.upload_image", lambda file: None)

    response = client.post(
        "/api/upload-image",
        files={"file": ("doll.jpg", b"fake image", "image/jpeg")},
    )

    assert response.status_code == 502


def test_upload_image_endpoint_rejects_non_image(monkeypatch):
    monkeypatch.setattr(
        "backend.main.upload_image",
        lambda file: "https://res.cloudinary.com/demo/image/upload/not-used.jpg",
    )

    response = client.post(
        "/api/upload-image",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 400


def test_manufacturer_override_endpoint_saves_mapping(monkeypatch, tmp_path):
    path = tmp_path / "manufacturer_domain_cache.json"
    monkeypatch.setattr("src.manufacturer_domains.CACHE_PATH", path)

    response = client.post(
        "/manufacturer-override",
        json={"brand": "Scotsman", "website": "https://scotsman-ice.com/products"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["override"]["brand"] == "scotsman"
    assert data["override"]["domain"] == "scotsman-ice.com"
    assert data["override"]["source"] == "user"


def test_intake_enrich_endpoint_passes_web_enrichment_flag(monkeypatch):
    captured = {}

    def fake_enrich_dataframe(df, enrichment_mode="standard", force_refresh=False, use_web_enrichment=True):
        captured["use_web_enrichment"] = use_web_enrichment
        return df, [], []

    monkeypatch.setattr("backend.main.enrich_dataframe", fake_enrich_dataframe)

    response = client.post(
        "/intake/enrich",
        json={"rows": [{"Product Name": "Lamp"}], "use_web_enrichment": False},
    )

    assert response.status_code == 200
    assert captured["use_web_enrichment"] is False


def test_intake_enrich_endpoint_reports_pre_completion_failure(monkeypatch):
    def fail_enrich_dataframe(*args, **kwargs):
        raise OSError(30, "Read-only file system", "data")

    monkeypatch.setattr("backend.main.enrich_dataframe", fail_enrich_dataframe)

    response = client.post(
        "/intake/enrich",
        json={"rows": [{"Product Name": "Lamp", "Brand": "Wolf", "Model/SKU": "WWD30"}]},
    )

    assert response.status_code == 200
    data = response.json()
    assert "Enrichment failed before completion" in data["errors"][0]
    assert data["rows"][0]["enrichment_status"] == "failed"
    assert "Read-only file system" in data["rows"][0]["enrichment_error"]
    assert "OSError" in data["rows"][0]["debug_traceback"]


def test_programa_export_csv_endpoint_uses_programa_columns():
    response = client.post("/export/programa/csv", json=_rows())
    today = datetime.date.today().isoformat()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert f'filename="programa_import_{today}.csv"' in response.headers["content-disposition"]
    text = response.content.decode("utf-8")
    assert "Section,Product Name,Brand,SKU,Model" in text
    assert "Decor,Lamp" in text
    assert "https://cdn.example.com/lamp.jpg" in text
    assert "skip.jpg" not in text


def test_programa_export_xlsx_endpoint_returns_workbook():
    response = client.post("/export/programa/xlsx", json=_rows())
    today = datetime.date.today().isoformat()

    assert response.status_code == 200
    assert f'filename="programa_import_{today}.xlsx"' in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.content.startswith(b"PK")


def test_programa_export_debug_csv_endpoint_includes_debug_columns():
    response = client.post("/export/programa/debug-csv", json=_rows())

    assert response.status_code == 200
    text = response.content.decode("utf-8")
    assert "Confidence Score" in text
    assert "Local Image Path" in text
    assert "enrichment_status" in text
    assert "enrichment_error" in text
