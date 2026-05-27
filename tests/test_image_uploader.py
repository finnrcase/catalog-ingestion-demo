import io
from types import SimpleNamespace

from PIL import Image

from src.image_uploader import (
    MAX_UPLOAD_BYTES,
    compress_image,
    fetch_convert_upload_remote_image,
    upload_image,
    upload_image_with_metadata,
    upload_images,
)


def _image_file(size=(800, 600), mode="RGB", image_format="PNG") -> io.BytesIO:
    buffer = io.BytesIO()
    image = Image.new(mode, size, (120, 80, 40))
    image.save(buffer, format=image_format)
    buffer.seek(0)
    return buffer


def test_upload_image_returns_cloudinary_secure_url(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "secret")
    uploaded_files = []

    def fake_upload(file):
        uploaded_files.append(file)
        return {"secure_url": "https://res.cloudinary.com/demo/image/upload/lamp.jpg"}

    fake_cloudinary = SimpleNamespace(
        uploader=SimpleNamespace(upload=fake_upload)
    )
    monkeypatch.setattr("src.image_uploader.cloudinary", fake_cloudinary)

    source = _image_file(size=(2400, 1800))

    assert upload_image(source) == "https://res.cloudinary.com/demo/image/upload/lamp.jpg"
    assert uploaded_files
    compressed = uploaded_files[0]
    assert compressed is not source
    assert compressed.getbuffer().nbytes < MAX_UPLOAD_BYTES


def test_upload_image_returns_none_without_secure_url(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "secret")
    fake_cloudinary = SimpleNamespace(uploader=SimpleNamespace(upload=lambda file: {"url": "http://example.com/a.jpg"}))
    monkeypatch.setattr("src.image_uploader.cloudinary", fake_cloudinary)

    assert upload_image(_image_file()) is None


def test_compress_image_resizes_and_stays_under_5mb():
    source = _image_file(size=(3200, 2400), mode="RGBA")

    compressed = compress_image(source)

    assert compressed.getbuffer().nbytes < MAX_UPLOAD_BYTES
    with Image.open(compressed) as image:
        assert image.mode == "RGB"
        assert max(image.size) <= 1600


def test_upload_images_preserves_order_and_failures(monkeypatch):
    urls = iter(["https://res.cloudinary.com/demo/image/upload/1.jpg", None, "https://res.cloudinary.com/demo/image/upload/3.jpg"])
    monkeypatch.setattr("src.image_uploader.upload_image", lambda file: next(urls))

    assert upload_images([object(), object(), object()]) == [
        "https://res.cloudinary.com/demo/image/upload/1.jpg",
        None,
        "https://res.cloudinary.com/demo/image/upload/3.jpg",
    ]


def test_upload_image_with_metadata_returns_cloudinary_fields(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "secret")
    monkeypatch.setenv("CLOUDINARY_UPLOAD_FOLDER", "sch-test")

    def fake_upload(file, **kwargs):
        assert kwargs["folder"] == "sch-test"
        return {
            "secure_url": "https://res.cloudinary.com/demo/image/upload/product.jpg",
            "public_id": "sch-test/product",
            "width": 800,
            "height": 600,
            "format": "jpg",
            "bytes": 12345,
        }

    monkeypatch.setattr("src.image_uploader.cloudinary", SimpleNamespace(uploader=SimpleNamespace(upload=fake_upload), config=lambda **_: None))

    result = upload_image_with_metadata(_image_file())

    assert result.status == "uploaded"
    assert result.secure_url == "https://res.cloudinary.com/demo/image/upload/product.jpg"
    assert result.public_id == "sch-test/product"
    assert result.width == 800
    assert result.height == 600
    assert result.format == "jpg"
    assert result.bytes == 12345


def test_fetch_convert_upload_remote_image_sets_user_agent_and_uploads_jpg(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "secret")
    source = _image_file(image_format="WEBP").getvalue()
    seen_headers = {}

    class FakeResponse:
        status_code = 200
        url = "https://manufacturer.example.com/product.webp"
        headers = {"content-type": "image/webp"}

        def iter_content(self, chunk_size=65536):
            yield source

    def fake_get(url, headers=None, **kwargs):
        seen_headers.update(headers or {})
        return FakeResponse()

    def fake_upload(file, **kwargs):
        with Image.open(file) as image:
            assert image.format == "JPEG"
        return {
            "secure_url": "https://res.cloudinary.com/demo/image/upload/product.jpg",
            "public_id": "product",
            "width": 800,
            "height": 600,
            "format": "jpg",
            "bytes": 1000,
        }

    monkeypatch.setattr("src.image_uploader.requests.get", fake_get)
    monkeypatch.setattr("src.image_uploader.cloudinary", SimpleNamespace(uploader=SimpleNamespace(upload=fake_upload), config=lambda **_: None))

    result = fetch_convert_upload_remote_image("https://manufacturer.example.com/product.webp", source_type="json_ld")

    assert "SCH-DesignOps" in seen_headers["User-Agent"]
    assert result.status == "uploaded"
    assert result.secure_url.startswith("https://res.cloudinary.com/")
    assert result.debug["content_type"] == "image/webp"
    assert result.debug["conversion_result_format"] == "jpg"


def test_fetch_convert_upload_remote_image_rejects_logo_before_fetch(monkeypatch):
    calls = []
    monkeypatch.setattr("src.image_uploader.requests.get", lambda *args, **kwargs: calls.append(args))

    result = fetch_convert_upload_remote_image("https://manufacturer.example.com/logo.svg", source_type="og:image")

    assert result.status == "failed"
    assert "rejected_url_hint" in result.error
    assert calls == []
