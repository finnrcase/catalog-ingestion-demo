from fastapi.testclient import TestClient
import datetime
import io

import openpyxl

from backend.main import app
from src.image_uploader import ImageUploadResult


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
        "backend.main.upload_image_with_metadata",
        lambda file: ImageUploadResult(
            secure_url="https://res.cloudinary.com/demo/image/upload/handmade-doll.jpg",
            public_id="handmade-doll",
            width=800,
            height=600,
            format="jpg",
            bytes=1234,
            status="uploaded",
        ),
    )

    response = client.post(
        "/api/upload-image",
        files={"file": ("doll.jpg", b"fake image", "image/jpeg")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["secure_url"] == "https://res.cloudinary.com/demo/image/upload/handmade-doll.jpg"
    assert body["public_id"] == "handmade-doll"
    assert body["width"] == 800
    assert body["image_upload_status"] == "uploaded"


def test_upload_image_endpoint_rejects_missing_secure_url(monkeypatch):
    monkeypatch.setattr("backend.main.upload_image_with_metadata", lambda file: ImageUploadResult(status="failed"))

    response = client.post(
        "/api/upload-image",
        files={"file": ("doll.jpg", b"fake image", "image/jpeg")},
    )

    assert response.status_code == 502


def test_upload_image_endpoint_reports_missing_cloudinary_config(monkeypatch):
    monkeypatch.setattr(
        "backend.main.upload_image_with_metadata",
        lambda file: ImageUploadResult(status="skipped", error="cloudinary_not_configured"),
    )

    response = client.post(
        "/api/upload-image",
        files={"file": ("doll.jpg", b"fake image", "image/jpeg")},
    )

    assert response.status_code == 503
    assert "CLOUDINARY_CLOUD_NAME" in response.json()["detail"]


def test_upload_image_endpoint_rejects_non_image(monkeypatch):
    monkeypatch.setattr(
        "backend.main.upload_image_with_metadata",
        lambda file: ImageUploadResult(secure_url="https://res.cloudinary.com/demo/image/upload/not-used.jpg"),
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


def test_preferred_websites_endpoints_crud():
    create = client.post(
        "/settings/preferred-websites",
        json={"keyword": "Wolf drawer", "url": "https://subzero-wolf.com/products/mdd30ts", "notes": "official"},
    )
    assert create.status_code == 200
    created = create.json()["entry"]
    assert created["keyword"] == "Wolf drawer"
    assert created["domain"] == "subzero-wolf.com"

    listed = client.get("/settings/preferred-websites")
    assert listed.status_code == 200
    assert listed.json()["entries"][0]["id"] == created["id"]

    update = client.put(
        f"/settings/preferred-websites/{created['id']}",
        json={"keyword": "Wolf microwave drawer", "url": created["url"], "notes": "updated"},
    )
    assert update.status_code == 200
    assert update.json()["entry"]["notes"] == "updated"

    delete = client.delete(f"/settings/preferred-websites/{created['id']}")
    assert delete.status_code == 200
    assert delete.json()["entries"] == []


def test_intake_enrich_endpoint_passes_web_enrichment_flag(monkeypatch):
    captured = {}

    def fake_enrich_dataframe(df, enrichment_mode="standard", force_refresh=False, use_web_enrichment=True, **kwargs):
        captured["use_web_enrichment"] = use_web_enrichment
        return df, [], []

    monkeypatch.setattr("backend.main.enrich_dataframe", fake_enrich_dataframe)

    response = client.post(
        "/intake/enrich",
        json={"rows": [{"Product Name": "Lamp"}], "use_web_enrichment": False},
    )

    assert response.status_code == 200
    assert captured["use_web_enrichment"] is False


def test_intake_enrich_blocks_non_fast_modes_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOW_ADMIN_ENRICHMENT_MODES", "false")
    monkeypatch.setattr("backend.main.enrich_dataframe", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not run")))

    response = client.post(
        "/intake/enrich",
        json={"rows": [{"Product Name": "Lamp"}], "enrichment_mode": "deep"},
    )

    assert response.status_code == 403


def test_intake_enrich_endpoint_returns_photo_discovery_report(monkeypatch):
    rows = [{
        "Product Name": "Wolf Drawer",
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product URL": "https://www.subzero-wolf.com/products/mdd30ts",
    }]

    def fake_enrich_dataframe(df, enrichment_mode="standard", force_refresh=False, use_web_enrichment=True, **kwargs):
        return df, [], []

    def fake_recover_images_for_dataframe(df, pdf_lookup=None, session_id=None, enable_screenshot=True, enable_web_lookup=True):
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
    assert f'filename="Untitled_Project_Decor_Programa_Import_{today}.csv"' in response.headers["content-disposition"]
    text = response.content.decode("utf-8")
    assert "Section,Product Name,Brand,SKU,Model" in text
    assert "Decor,Lamp" in text
    assert "https://cdn.example.com/lamp.jpg" in text
    assert "skip.jpg" not in text


def test_programa_export_xlsx_endpoint_returns_workbook():
    response = client.post("/export/programa/xlsx", json=_rows())
    today = datetime.date.today().isoformat()

    assert response.status_code == 200
    assert f'filename="Untitled_Project_Decor_Programa_Import_{today}.xlsx"' in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.content.startswith(b"PK")
    assert not response.content.startswith(b"Section,")
    workbook = openpyxl.load_workbook(io.BytesIO(response.content))
    worksheet = workbook.active
    assert worksheet.freeze_panes == "A2"
    assert worksheet["A1"].font.bold is True
    assert worksheet["O2"].value == "https://cdn.example.com/lamp.jpg"


def test_programa_export_exposes_download_headers_for_browser():
    response = client.post(
        "/export/programa/xlsx",
        json=_rows(),
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.status_code == 200
    exposed = response.headers.get("access-control-expose-headers", "")
    assert "Content-Disposition" in exposed
    assert "Content-Type" in exposed


def test_programa_export_debug_csv_endpoint_includes_debug_columns():
    response = client.post("/export/programa/debug-csv", json=_rows())

    assert response.status_code == 200
    text = response.content.decode("utf-8")
    assert "Confidence Score" in text
    assert "Local Image Path" in text
