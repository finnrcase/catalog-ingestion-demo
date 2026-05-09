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

    # File landed on disk.
    sid = body["session_id"]
    pdf_id = body["pdf_id"]
    expected = tmp_path / ".tmp" / "uploads" / sid / "pdfs" / f"{pdf_id}.pdf"
    assert expected.exists()


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
