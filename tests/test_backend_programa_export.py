from fastapi.testclient import TestClient

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


def test_programa_export_csv_endpoint_uses_programa_columns():
    response = client.post("/export/programa/csv", json=_rows())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    text = response.content.decode("utf-8")
    assert "Section,Product Name,Brand,SKU,Model" in text
    assert "Decor,Lamp" in text
    assert "https://cdn.example.com/lamp.jpg" in text
    assert "skip.jpg" not in text


def test_programa_export_xlsx_endpoint_returns_workbook():
    response = client.post("/export/programa/xlsx", json=_rows())

    assert response.status_code == 200
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
