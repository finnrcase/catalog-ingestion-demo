from __future__ import annotations

import io
import logging
import os

import requests
from dotenv import load_dotenv
from PIL import Image, ImageOps

load_dotenv()

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_MAX_SIZES = [(1600, 1600), (1400, 1400), (1200, 1200), (1000, 1000)]
_JPEG_QUALITIES = [70, 60, 50, 40]

try:
    import cloudinary
    import cloudinary.uploader
except ImportError:  # pragma: no cover - exercised only when dependency is missing at runtime
    cloudinary = None


if cloudinary is not None:
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True,
    )


def is_public_https_image_url(url: str | None) -> bool:
    return str(url or "").strip().lower().startswith("https://")


def public_https_url_is_accessible(url: str | None) -> bool:
    """Best-effort public accessibility check for a hosted image URL."""
    if not is_public_https_image_url(url):
        return False
    try:
        response = requests.head(str(url), allow_redirects=True, timeout=10)
        if 200 <= response.status_code < 400:
            return True
        if response.status_code in {403, 405}:
            response = requests.get(str(url), stream=True, timeout=10)
            return 200 <= response.status_code < 400
    except Exception:
        return False
    return False


def _buffer_size(buffer: io.BytesIO) -> int:
    position = buffer.tell()
    buffer.seek(0, io.SEEK_END)
    size = buffer.tell()
    buffer.seek(position)
    return size


def compress_image(file) -> io.BytesIO:
    """
    Resize and compress an uploaded image so the Cloudinary/Programa URL points
    at a web-friendly JPEG under 5MB.
    """
    if hasattr(file, "seek"):
        file.seek(0)

    with Image.open(file) as opened:
        img = ImageOps.exif_transpose(opened)
        if img.mode != "RGB":
            img = img.convert("RGB")

        last_buffer: io.BytesIO | None = None
        for max_size in _MAX_SIZES:
            resized = img.copy()
            resized.thumbnail(max_size)
            for quality in _JPEG_QUALITIES:
                buffer = io.BytesIO()
                resized.save(buffer, format="JPEG", quality=quality, optimize=True)
                buffer.seek(0)
                size = _buffer_size(buffer)
                last_buffer = buffer
                logger.info(
                    "Compressed image for Cloudinary: size=%s bytes quality=%s max_size=%s",
                    size,
                    quality,
                    max_size,
                )
                if size < MAX_UPLOAD_BYTES:
                    return buffer

        if last_buffer is not None:
            last_size = _buffer_size(last_buffer)
            raise ValueError(f"Compressed image is still too large: {last_size} bytes.")
        raise ValueError("Could not compress image.")


def upload_image(file) -> str | None:
    """
    Upload a file-like object to Cloudinary and return secure_url.
    """
    if cloudinary is None:
        return None
    try:
        compressed = compress_image(file)
        result = cloudinary.uploader.upload(compressed)
        url = result.get("secure_url")
        if not is_public_https_image_url(url):
            return None
        return str(url).strip()
    except Exception:
        return None


def upload_images(files: list) -> list[str | None]:
    urls = []
    for f in files:
        url = upload_image(f)
        urls.append(url)
    return urls
