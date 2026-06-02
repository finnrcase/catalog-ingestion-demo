from __future__ import annotations

import logging
import re
import urllib.parse

_log = logging.getLogger(__name__)

_HOSTNAME_RE = re.compile(r"^[a-z0-9.-]+$", re.IGNORECASE)
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _str(value: object) -> str:
    return str(value or "").strip()


def _strip_wrapping_quotes(value: str) -> str:
    text = value.strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1].strip()
    return text


def strip_accidental_env_assignment(raw_value: object, env_var_name: str = "") -> str:
    """Remove accidental "NAME=value" prefixes from an environment value.

    A common Vercel/local mistake is storing a value like
    NEXT_PUBLIC_API_BASE_URL=NEXT_PUBLIC_API_BASE_URL=https://example.com.
    This function recovers the URL value without ever adding square brackets or
    rewriting the hostname.
    """
    text = _strip_wrapping_quotes(_str(raw_value))
    prefixes = [name for name in (env_var_name, "NEXT_PUBLIC_API_BASE_URL", "API_BASE_URL", "BACKEND_URL") if name]
    changed = True
    while changed:
        changed = False
        for name in prefixes:
            prefix = f"{name}="
            if text.startswith(prefix):
                text = _strip_wrapping_quotes(text[len(prefix):])
                changed = True
    return text


def validate_http_url(url: object, *, allow_localhost: bool = True) -> str:
    """Return a validation error string for URLs that should not be fetched."""
    raw = _str(url)
    if not raw:
        return "empty URL"
    if any(ch.isspace() for ch in raw):
        return "malformed URL: contains whitespace"
    try:
        parsed = urllib.parse.urlparse(raw)
        hostname = parsed.hostname  # Raises ValueError for malformed IPv6 brackets.
    except ValueError as exc:
        return f"Invalid IPv6 URL: {exc}"
    except Exception as exc:
        return f"malformed URL: {exc}"
    if parsed.scheme.lower() not in {"http", "https"}:
        return f"malformed URL: unsupported scheme {parsed.scheme or '<missing>'}"
    if not parsed.netloc or not hostname:
        return "malformed URL: missing host"

    host = hostname.strip().lower()
    if allow_localhost and host == "localhost":
        return ""
    if ":" in host:
        return ""
    if _IPV4_RE.match(host):
        return ""
    if not _HOSTNAME_RE.match(host) or "." not in host:
        return f"malformed domain: {host}"
    return ""


def is_valid_http_url(url: object, *, allow_localhost: bool = True) -> bool:
    return not validate_http_url(url, allow_localhost=allow_localhost)


def normalize_base_url(raw_base_url: object, *, env_var_name: str = "BASE_URL") -> tuple[str, str]:
    candidate = strip_accidental_env_assignment(raw_base_url, env_var_name).rstrip("/")
    reason = validate_http_url(candidate)
    if reason:
        message = f"{env_var_name} is not a valid base URL: {reason}"
        _log.warning(
            "Invalid base URL env_var=%s raw=%r normalized=%r reason=%s",
            env_var_name,
            _str(raw_base_url),
            candidate,
            reason,
        )
        return "", message
    return candidate, ""


def join_url(base_url: object, path: object, *, env_var_name: str = "BASE_URL", source: str = "url_join") -> tuple[str, str]:
    """Safely join a base URL and path, returning (url, error)."""
    base, base_error = normalize_base_url(base_url, env_var_name=env_var_name)
    clean_path = _str(path).lstrip("/")
    if base_error:
        return "", base_error
    constructed = f"{base}/{clean_path}" if clean_path else base
    reason = validate_http_url(constructed)
    if reason:
        message = f"Constructed URL failed validation: {reason}"
        _log.warning(
            "Invalid constructed URL source=%s env_var=%s base=%r path=%r constructed=%r reason=%s",
            source,
            env_var_name,
            base,
            _str(path),
            constructed,
            reason,
        )
        return "", message
    return constructed, ""


def safe_urljoin(base_url: object, raw_url: object, *, source: str = "urljoin") -> tuple[str, str]:
    """Resolve raw_url relative to base_url and validate before fetch/use."""
    raw = _str(raw_url)
    if not raw:
        return "", "empty URL"
    if raw.startswith("//"):
        resolved = "https:" + raw
    else:
        try:
            resolved = urllib.parse.urljoin(_str(base_url), raw)
        except ValueError as exc:
            reason = f"Invalid IPv6 URL: {exc}"
            _log.warning(
                "Invalid joined URL source=%s base=%r raw=%r reason=%s",
                source,
                _str(base_url),
                raw,
                reason,
            )
            return "", reason
        except Exception as exc:
            reason = f"malformed URL: {exc}"
            _log.warning(
                "Invalid joined URL source=%s base=%r raw=%r reason=%s",
                source,
                _str(base_url),
                raw,
                reason,
            )
            return "", reason
    reason = validate_http_url(resolved)
    if reason:
        _log.warning(
            "Invalid joined URL source=%s base=%r raw=%r resolved=%r reason=%s",
            source,
            _str(base_url),
            raw,
            resolved,
            reason,
        )
        return "", reason
    return resolved, ""
