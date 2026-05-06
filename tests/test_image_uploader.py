import io
from types import SimpleNamespace

from PIL import Image

from src.image_uploader import MAX_UPLOAD_BYTES, compress_image, upload_image, upload_images


def _image_file(size=(800, 600), mode="RGB", image_format="PNG") -> io.BytesIO:
    buffer = io.BytesIO()
    image = Image.new(mode, size, (120, 80, 40))
    image.save(buffer, format=image_format)
    buffer.seek(0)
    return buffer


def test_upload_image_returns_cloudinary_secure_url(monkeypatch):
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
