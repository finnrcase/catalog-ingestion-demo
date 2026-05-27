from __future__ import annotations

import io
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from PIL import Image, ImageOps

load_dotenv()

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_REMOTE_IMAGE_BYTES = 25 * 1024 * 1024
_MAX_SIZES = [(1600, 1600), (1400, 1400), (1200, 1200), (1000, 1000)]
_JPEG_QUALITIES = [70, 60, 50, 40]
_REMOTE_USER_AGENT = "SCH-DesignOps/1.0 (+https://saffroncasehomes.com)"
_BAD_IMAGE_URL_RE = re.compile(
    r"(?:logo|icon|favicon|placeholder|default-meta-image|default_meta_image|"
    r"sprite|swatch|transparent|blank|noimage|no-image|loading)",
    re.IGNORECASE,
)

try:
    import cloudinary
    import cloudinary.uploader
except ImportError:  # pragma: no cover - exercised only when dependency is missing at runtime
    cloudinary = None


@dataclass
class ImageUploadResult:
    secure_url: str = ""
    public_id: str = ""
    width: int = 0
    height: int = 0
    format: str = ""
    bytes: int = 0
    status: str = "failed"
    error: str = ""
    debug: dict[str, Any] = field(default_factory=dict)


def cloudinary_configured() -> bool:
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "").strip()
    api_key = os.getenv("CLOUDINARY_API_KEY", "").strip()
    api_secret = os.getenv("CLOUDINARY_API_SECRET", "").strip()
    cloudinary_url = os.getenv("CLOUDINARY_URL", "").strip()
    return bool(
        cloudinary is not None
        and (
            _real_env_value(cloudinary_url)
            or (_real_env_value(cloud_name) and _real_env_value(api_key) and _real_env_value(api_secret))
        )
    )


def _configure_cloudinary() -> None:
    if cloudinary is None:
        return
    if not hasattr(cloudinary, "config"):
        return
    if (
        _real_env_value(os.getenv("CLOUDINARY_CLOUD_NAME", ""))
        and _real_env_value(os.getenv("CLOUDINARY_API_KEY", ""))
        and _real_env_value(os.getenv("CLOUDINARY_API_SECRET", ""))
    ):
        cloudinary.config(
            cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
            api_key=os.getenv("CLOUDINARY_API_KEY"),
            api_secret=os.getenv("CLOUDINARY_API_SECRET"),
            secure=True,
        )
    elif _real_env_value(os.getenv("CLOUDINARY_URL", "")):
        cloudinary.config(secure=True)


def _real_env_value(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    return not (
        lowered.startswith("your_")
        or lowered.startswith("your-")
        or "placeholder" in lowered
        or "api_key" in lowered
        or "api_secret" in lowered
        or lowered in {"changeme", "change_me"}
    )


_configure_cloudinary()


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


def image_url_has_bad_hint(url: str | None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return "missing_url"
    parsed = urlparse(raw)
    path = f"{parsed.path}?{parsed.query}".lower()
    match = _BAD_IMAGE_URL_RE.search(path)
    return match.group(0) if match else ""


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


def upload_image_with_metadata(file, *, source_url: str = "") -> ImageUploadResult:
    """Upload a local/file-like image to Cloudinary and return URL + metadata."""
    debug: dict[str, Any] = {
        "candidate_url": source_url,
        "cloudinary_upload_attempted": False,
        "conversion_attempted": True,
    }
    if not cloudinary_configured():
        return ImageUploadResult(status="skipped", error="cloudinary_not_configured", debug=debug)
    try:
        compressed = compress_image(file)
        debug.update({
            "conversion_result_format": "jpg",
            "converted_bytes": _buffer_size(compressed),
        })
    except Exception as exc:
        debug["failure_reason"] = f"conversion_failed:{exc}"
        return ImageUploadResult(status="failed", error=str(exc), debug=debug)

    folder = os.getenv("CLOUDINARY_UPLOAD_FOLDER", "").strip()
    upload_options: dict[str, Any] = {"resource_type": "image"}
    if folder:
        upload_options["folder"] = folder

    last_error = ""
    for attempt in range(2):
        try:
            _configure_cloudinary()
            compressed.seek(0)
            debug["cloudinary_upload_attempted"] = True
            debug["cloudinary_upload_attempt"] = attempt + 1
            try:
                result = cloudinary.uploader.upload(compressed, **upload_options)
            except TypeError:
                compressed.seek(0)
                result = cloudinary.uploader.upload(compressed)
            url = str(result.get("secure_url") or "").strip()
            debug["cloudinary_response"] = {
                key: result.get(key)
                for key in ("secure_url", "public_id", "width", "height", "format", "bytes")
                if key in result
            }
            if not is_public_https_image_url(url):
                last_error = "cloudinary_missing_secure_url"
                debug["failure_reason"] = last_error
                continue
            return ImageUploadResult(
                secure_url=url,
                public_id=str(result.get("public_id") or ""),
                width=int(result.get("width") or 0),
                height=int(result.get("height") or 0),
                format=str(result.get("format") or "jpg"),
                bytes=int(result.get("bytes") or _buffer_size(compressed)),
                status="uploaded",
                debug=debug,
            )
        except Exception as exc:
            last_error = str(exc)
            debug["cloudinary_error"] = last_error
            if attempt == 0:
                time.sleep(0.25)
    return ImageUploadResult(status="failed", error=last_error or "cloudinary_upload_failed", debug=debug)


def upload_image(file) -> str | None:
    """Upload a file-like object to Cloudinary and return secure_url."""
    result = upload_image_with_metadata(file)
    return result.secure_url or None


def fetch_convert_upload_remote_image(url: str, *, source_type: str = "") -> ImageUploadResult:
    """Fetch a remote product image, convert to JPG, and upload to Cloudinary.

    On failure the caller should keep the original candidate URL and surface the
    returned debug/error fields for review.
    """
    candidate_url = str(url or "").strip()
    debug: dict[str, Any] = {
        "candidate_url": candidate_url,
        "source_type": source_type,
        "fetch_status": "",
        "content_type": "",
        "conversion_attempted": False,
        "cloudinary_upload_attempted": False,
    }
    if not is_public_https_image_url(candidate_url):
        debug["failure_reason"] = "non_https_candidate_url"
        return ImageUploadResult(status="failed", error="non_https_candidate_url", debug=debug)
    bad_hint = image_url_has_bad_hint(candidate_url)
    if bad_hint:
        debug["failure_reason"] = f"rejected_url_hint:{bad_hint}"
        return ImageUploadResult(status="failed", error=debug["failure_reason"], debug=debug)
    if not cloudinary_configured():
        debug["failure_reason"] = "cloudinary_not_configured"
        return ImageUploadResult(status="skipped", error="cloudinary_not_configured", debug=debug)

    try:
        response = requests.get(
            candidate_url,
            headers={
                "User-Agent": _REMOTE_USER_AGENT,
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
            timeout=18,
            allow_redirects=True,
            stream=True,
        )
        debug["fetch_status"] = str(response.status_code)
        debug["final_fetch_url"] = str(response.url)
        content_type = str(response.headers.get("content-type") or "").split(";")[0].lower()
        debug["content_type"] = content_type
        if response.status_code >= 400:
            debug["failure_reason"] = f"fetch_failed:{response.status_code}"
            return ImageUploadResult(status="failed", error=debug["failure_reason"], debug=debug)
        if "svg" in content_type or candidate_url.lower().split("?", 1)[0].endswith(".svg"):
            debug["failure_reason"] = "svg_image_rejected"
            return ImageUploadResult(status="failed", error="svg_image_rejected", debug=debug)
        if content_type and not content_type.startswith("image/"):
            debug["failure_reason"] = f"unsupported_content_type:{content_type}"
            return ImageUploadResult(status="failed", error=debug["failure_reason"], debug=debug)

        raw = io.BytesIO()
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_REMOTE_IMAGE_BYTES:
                debug["failure_reason"] = "remote_image_too_large"
                return ImageUploadResult(status="failed", error="remote_image_too_large", debug=debug)
            raw.write(chunk)
        raw.seek(0)
        debug["fetched_bytes"] = total
        debug["conversion_attempted"] = True
        result = upload_image_with_metadata(raw, source_url=candidate_url)
        result.debug = {**debug, **result.debug}
        if result.secure_url:
            result.debug["final_saved_image_url"] = result.secure_url
        elif result.error:
            result.debug["failure_reason"] = result.error
        return result
    except Exception as exc:
        debug["failure_reason"] = str(exc)
        return ImageUploadResult(status="failed", error=str(exc), debug=debug)


def upload_images(files: list) -> list[str | None]:
    urls = []
    for f in files:
        url = upload_image(f)
        urls.append(url)
    return urls
