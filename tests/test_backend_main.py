"""Tests for backend/main.py endpoints — image recovery integration."""

from __future__ import annotations


def test_upload_pdf_writes_to_session_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    import backend.main as bm

    # Build a tiny valid PDF (one page, no images).
    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "Hello")
    pdf_bytes = doc.tobytes()
    doc.close()

    with _patch.object(bm, "_TMP_UPLOADS", tmp_path / ".tmp" / "uploads"):
        client = TestClient(bm.app)
        resp = client.post(
            "/intake/upload-pdf",
            files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body
    assert "pdf_id" in body
    assert body["parse_job_id"]
    assert body["status"] in {"queued", "parsing", "complete"}

    # File landed on disk.
    sid = body["session_id"]
    pdf_id = body["pdf_id"]
    expected = tmp_path / ".tmp" / "uploads" / sid / "pdfs" / f"{pdf_id}.pdf"
    assert expected.exists()


def test_upload_pdf_status_eventually_returns_rows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    import backend.main as bm

    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "Model #: MDD30TS Wolf Warming Drawer $999")
    pdf_bytes = doc.tobytes()
    doc.close()

    with _patch.object(bm, "_TMP_UPLOADS", tmp_path / ".tmp" / "uploads"):
        client = TestClient(bm.app)
        upload = client.post(
            "/intake/upload-pdf",
            data={"project": "SCH", "room": "Kitchen"},
            files={"file": ("spec.pdf", pdf_bytes, "application/pdf")},
        )
        assert upload.status_code == 200
        job_id = upload.json()["parse_job_id"]
        body = {}
        for _ in range(50):
            status = client.get(f"/intake/pdf-jobs/{job_id}")
            assert status.status_code == 200
            body = status.json()
            if body["status"] in {"complete", "failed"}:
                break
            import time
            time.sleep(0.05)

    assert body["status"] == "complete"
    assert body["rows"][0]["Model/SKU"] == "MDD30TS"
    assert body["rows"][0]["Project"] == "SCH"
    assert body["rows"][0]["Room"] == "Kitchen"
    assert body["telemetry"]["parser_used"] in {"pdf-parse", "pdfjs"}
    assert body["telemetry"]["page_count"] == 1


def test_pdf_parse_job_cancel_and_logs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    import backend.main as bm

    pdfs_dir = tmp_path / ".tmp" / "uploads" / "sid" / "pdfs"
    pdfs_dir.mkdir(parents=True)
    path = pdfs_dir / "abc.pdf"
    path.write_bytes(b"%PDF-1.4 mock")
    job = bm.PdfParseJob(
        job_id="jobcancel",
        session_id="sid",
        pdf_id="abc",
        filename="abc.pdf",
        pdf_path=str(path),
    )
    bm._store_pdf_job(job)
    with _patch.object(bm, "_TMP_UPLOADS", tmp_path / ".tmp" / "uploads"):
        client = TestClient(bm.app)
        cancelled = client.post("/intake/pdf-jobs/jobcancel/cancel")
        logs = client.get("/intake/pdf-jobs/jobcancel/logs")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert logs.status_code == 200
    assert logs.json()["logs"]


def test_recover_images_endpoint_uses_pdf_lookup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    import backend.main as bm

    # Pre-populate a session and pdf file under tmp_path.
    sid = "abcdef123456"
    pdfs_dir = tmp_path / ".tmp" / "uploads" / sid / "pdfs"
    pdfs_dir.mkdir(parents=True)
    (pdfs_dir / "deadbeef0001.pdf").write_bytes(b"%PDF-1.4 mock")

    rows = [{
        "Product Name": "X", "Brand": "Y", "Model/SKU": "Z",
        "Image URL": "", "Product URL": "https://example.com/p",
        "_source_pdf_id": "deadbeef0001",
    }]

    with _patch.object(bm, "_TMP_UPLOADS", tmp_path / ".tmp" / "uploads"), \
         _patch("backend.main.recover_images_for_dataframe") as m:
        import pandas as pd
        m.return_value = (pd.DataFrame(rows), [])
        client = TestClient(bm.app)
        resp = client.post(
            "/intake/recover-images",
            json={"session_id": sid, "rows": rows},
        )
    assert resp.status_code == 200
    kwargs = m.call_args.kwargs
    assert kwargs["pdf_lookup"] == {"deadbeef0001": str(pdfs_dir / "deadbeef0001.pdf")}
    assert kwargs["session_id"] == sid
    assert kwargs["enable_screenshot"] is True


def test_generate_intake_ai_error_falls_back_to_local_pdf_parser(monkeypatch):
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    import backend.main as bm

    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Model #: MDD30TS Wolf Warming Drawer $999")
    pdf_bytes = doc.tobytes()
    doc.close()

    with _patch("backend.main.extract_products_from_pdf_with_ai", return_value=(None, "missing api key")), \
         _patch("backend.main.enrich_pdf_rows_with_official_product_urls", side_effect=lambda rows: (rows, [])):
        client = TestClient(bm.app)
        resp = client.post(
            "/intake/generate",
            data={"project": "SCH", "room": "Kitchen", "use_ai_pdf": "true"},
            files={"files": ("spec.pdf", pdf_bytes, "application/pdf")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"][0]["Model/SKU"] == "MDD30TS"
    assert body["rows"][0]["_source_filename"] == "spec.pdf"
    assert body["rows"][0]["_source_page_number"] == 1
    assert body["rows"][0]["_extracted_model_sku"] == "MDD30TS"


def test_generate_intake_routes_image_uploads_to_photo_rows(monkeypatch):
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    import backend.main as bm

    image_bytes = b"\x89PNG\r\n\x1a\nfake"

    with _patch("backend.main.parse_pdf_rows", side_effect=AssertionError("image should not use PDF parser")), \
         _patch("backend.main.extract_products_from_pdf_with_ai", side_effect=AssertionError("image should not use AI PDF parser")), \
         _patch("backend.main.enrich_pdf_rows_with_official_product_urls", side_effect=lambda rows: (rows, [])):
        client = TestClient(bm.app)
        resp = client.post(
            "/intake/generate",
            data={"project": "SCH", "room": "Living", "use_ai_pdf": "true"},
            files={"files": ("chair.png", image_bytes, "image/png")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"][0]["Source Type"] == "Photo"
    assert body["rows"][0]["Import Type"] == "Photo Upload"
    assert body["rows"][0]["Image Filename"] == "chair.png"
    assert body["rows"][0]["Image Upload Status"] == "Ready"
