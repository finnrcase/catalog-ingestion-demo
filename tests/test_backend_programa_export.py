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
    assert response.json()["status"] == "ok"
    assert "uptime_seconds" in response.json()


def test_programa_xlsx_with_images_endpoint_returns_xlsx():
    response = client.post("/export/programa/xlsx-with-images", json=_rows())

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.content.startswith(b"PK")


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
    assert response.json() == {"secure_url": "https://res.cloudinary.com/demo/image/upload/handmade-doll.jpg"}


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


def test_intake_enrich_endpoint_returns_photo_discovery_report(monkeypatch):
    rows = [{
        "Product Name": "Wolf Drawer",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product URL": "https://www.subzero-wolf.com/products/mdd30ts",
    }]

    def fake_enrich_dataframe(df, enrichment_mode="standard", force_refresh=False, use_web_enrichment=True):
        return df, [], []

    def fake_recover_images_for_dataframe(df, pdf_lookup=None, session_id=None, enable_screenshot=True):
        out = df.copy()
        out["confidence"] = "HIGH"
        out["local_image_path"] = "/tmp/images/wolf_mdd30ts.jpg"
        return out, [{
            "brand": "Wolf",
            "product_name": "Wolf Drawer",
            "model_sku": "MDD30TS",
            "confidence": "HIGH",
            "selected_product_page_url": "https://www.subzero-wolf.com/products/mdd30ts",
            "local_image_path": "/tmp/images/wolf_mdd30ts.jpg",
        }]

    monkeypatch.setattr("backend.main.enrich_dataframe", fake_enrich_dataframe)
    monkeypatch.setattr("backend.main.recover_images_for_dataframe", fake_recover_images_for_dataframe)

    response = client.post("/intake/enrich", json={"rows": rows, "use_web_enrichment": True})

    assert response.status_code == 200
    diagnostics = response.json()["dimension_diagnostics"]
    report = next(d for d in diagnostics if d.get("report_type") == "photo_discovery")["summary"]
    assert report["official_product_pages_found"] == 1
    assert report["images_inserted_into_excel"] == 1


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
