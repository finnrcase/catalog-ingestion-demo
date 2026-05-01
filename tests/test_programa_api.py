from pathlib import Path

from src.programa_api import ProgramaAPIClient, ProgramaSession


def test_create_image_only_item_patches_only_image(monkeypatch, tmp_path):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake image bytes")
    client = ProgramaAPIClient(ProgramaSession(cookies={}, csrf_token="token"))

    calls = {}

    def fake_create_item(section_id):
        calls["section_id"] = section_id
        return "item-1"

    def fake_direct_upload(path):
        calls["upload_path"] = Path(path)
        return "signed-1"

    def fake_update_item(item_id, fields, signed_id=None):
        calls["item_id"] = item_id
        calls["fields"] = fields
        calls["signed_id"] = signed_id
        return True

    monkeypatch.setattr(client, "create_item", fake_create_item)
    monkeypatch.setattr(client, "direct_upload_image", fake_direct_upload)
    monkeypatch.setattr(client, "update_item", fake_update_item)

    result = client.create_image_only_item("section-1", str(image_path))

    assert result["ok"] is True
    assert calls["section_id"] == "section-1"
    assert calls["upload_path"] == image_path
    assert calls["item_id"] == "item-1"
    assert calls["fields"] == {}
    assert calls["signed_id"] == "signed-1"
