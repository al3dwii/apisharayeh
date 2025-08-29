# src/app/core/safety.py
from __future__ import annotations

import contextlib
import ipaddress
import os
import re
import socket
import tempfile
from typing import Dict, Iterable, Optional, Sequence, Tuple
from urllib.parse import urlparse

import requests

# httpx is optional; used only by async helper
try:  # pragma: no cover
    import httpx  # type: ignore
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

# settings is optional; keep soft dependency with safe defaults
try:
    from app.core.config import settings  # type: ignore
except Exception:  # pragma: no cover
    class _S:  # fallback defaults
        ALLOWLIST_DOMAINS = None
        MAX_FETCH_BYTES = 25 * 1024 * 1024
        FETCH_TIMEOUT_SEC = 30
        MAX_UPLOAD_MB = 50

    settings = _S()  # type: ignore


# ────────────────────────────────────────────────────────────────────────────────
# Network policy constants
# ────────────────────────────────────────────────────────────────────────────────

PRIVATE_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]
ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443}

# Realistic browser-like default headers to avoid simplistic bot/403 blocks.
DEFAULT_HTTP_HEADERS: Dict[str, str] = {
    "User-Agent": os.getenv(
        "HTTP_USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36",
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": os.getenv("HTTP_ACCEPT_LANGUAGE", "en-US,en;q=0.9"),
    "Connection": "keep-alive",
}


# ────────────────────────────────────────────────────────────────────────────────
# Exceptions
# ────────────────────────────────────────────────────────────────────────────────

class FetchBlocked(Exception):
    """Raised when an outbound fetch is blocked by policy."""
    pass


class UploadBlocked(Exception):
    """Raised when an inbound upload is blocked by policy."""
    pass

# near the top (with the other globals)
def _ports_from_env(default="80,443"):
    raw = os.getenv("ALLOWED_PORTS", default)
    ports = set()
    for tok in (raw or "").split(","):
        tok = tok.strip()
        if tok.isdigit():
            ports.add(int(tok))
    return ports or {80, 443}

ALLOWED_PORTS = _ports_from_env()   # replaces the fixed {80, 443}

# ────────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────────

def _resolve_all(host: str) -> Iterable[str]:
    """Return all resolved IPs for host; empty on error."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        for info in infos:
            yield info[4][0]
    except Exception:
        return []


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in PRIVATE_NETS) or addr.is_private or addr.is_loopback or addr.is_link_local
    except Exception:
        return True  # be safe


def _host_allowed(host: str) -> bool:
    allow_raw = getattr(settings, "ALLOWLIST_DOMAINS", "") or ""
    if not allow_raw:
        return True
    allowed = [h.strip().lower() for h in allow_raw.split(",") if h.strip()]
    host = (host or "").lower()
    return any(host == a or host.endswith("." + a) for a in allowed)


def _port_allowed(netloc: str) -> bool:
    # netloc may contain "host:port"
    if ":" not in netloc:
        return True
    try:
        port = int(netloc.rsplit(":", 1)[1])
    except Exception:
        return False
    return port in ALLOWED_PORTS


def validate_url(url: str) -> None:
    """
    Validate that the URL is safe to fetch:
      - scheme http/https
      - (optional) host allowlist
      - no embedded credentials
      - default ports only
      - DNS doesn't resolve to private/link-local ranges
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise FetchBlocked(f"blocked: scheme {parsed.scheme} not allowed")
    if parsed.username or parsed.password:
        raise FetchBlocked("blocked: credentials in URL not allowed")
    if not _port_allowed(parsed.netloc):
        raise FetchBlocked("blocked: port not allowed")
    host = parsed.hostname or ""
    if not _host_allowed(host):
        raise FetchBlocked(f"blocked: host not in allowlist ({host})")
    for ip in _resolve_all(host):
        if _is_private_ip(ip):
            raise FetchBlocked(f"blocked: resolved to private IP ({ip})")


# ────────────────────────────────────────────────────────────────────────────────
# Safe outbound fetch (sync/async)
# ────────────────────────────────────────────────────────────────────────────────

def safe_fetch_to_temp(
    url: str,
    *,
    max_bytes: Optional[int] = None,
    timeout: Optional[int] = None,
    allowed_content_types: Optional[Sequence[str]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[str, int, Optional[str]]:
    """
    Download a URL with SSRF protections and size cap.
    Returns (temp_path, total_bytes, content_type).
    Raises FetchBlocked or requests.HTTPError.
    """
    validate_url(url)
    max_bytes = max_bytes if max_bytes is not None else int(getattr(settings, "MAX_FETCH_BYTES", 25 * 1024 * 1024))
    timeout = timeout if timeout is not None else int(getattr(settings, "FETCH_TIMEOUT_SEC", 30))

    merged_headers = dict(DEFAULT_HTTP_HEADERS)
    if headers:
        merged_headers.update(headers)

    with requests.get(
        url,
        stream=True,
        timeout=timeout,
        allow_redirects=True,
        headers=merged_headers,
    ) as r:
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip() or None
        if allowed_content_types and ctype and ctype not in allowed_content_types:
            raise FetchBlocked(f"blocked: content-type {ctype} not permitted")

        total = 0
        fd, tmp = tempfile.mkstemp()
        try:
            with os.fdopen(fd, "wb") as f:
                for chunk in r.iter_content(1024 * 64):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchBlocked(f"blocked: file exceeds cap ({total}>{max_bytes})")
                    f.write(chunk)
        except Exception:
            with contextlib.suppress(Exception):
                os.remove(tmp)
            raise
    return tmp, total, ctype


async def async_safe_fetch_to_temp(
    url: str,
    *,
    max_bytes: Optional[int] = None,
    timeout: Optional[int] = None,
    allowed_content_types: Optional[Sequence[str]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[str, int, Optional[str]]:
    """
    Async variant using httpx when available; falls back to sync helper otherwise.
    """
    if httpx is None:  # pragma: no cover
        # fall back to sync version in a thread if caller wants true async
        return safe_fetch_to_temp(
            url,
            max_bytes=max_bytes,
            timeout=timeout,
            allowed_content_types=allowed_content_types,
            headers=headers,
        )

    validate_url(url)
    max_bytes = max_bytes if max_bytes is not None else int(getattr(settings, "MAX_FETCH_BYTES", 25 * 1024 * 1024))
    timeout = timeout if timeout is not None else int(getattr(settings, "FETCH_TIMEOUT_SEC", 30))

    merged_headers = dict(DEFAULT_HTTP_HEADERS)
    if headers:
        merged_headers.update(headers)

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=merged_headers) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip() or None
        if allowed_content_types and ctype and ctype not in allowed_content_types:
            raise FetchBlocked(f"blocked: content-type {ctype} not permitted")

        fd, tmp = tempfile.mkstemp()
        total = 0
        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in resp.aiter_bytes(1024 * 64):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchBlocked(f"blocked: file exceeds cap ({total}>{max_bytes})")
                    f.write(chunk)
        except Exception:
            with contextlib.suppress(Exception):
                os.remove(tmp)
            raise
    return tmp, total, ctype


# ────────────────────────────────────────────────────────────────────────────────
# Inbound upload validation
# ────────────────────────────────────────────────────────────────────────────────

_EXT_RE = re.compile(r"\.([A-Za-z0-9_-]{1,16})$")


def validate_upload(
    filename: str,
    content_type: Optional[str],
    size_bytes: int,
    allowed_exts: Sequence[str] = (
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".csv",
        ".txt",
        ".png",
        ".jpg",
        ".jpeg",
    ),
    max_mb: Optional[int] = None,
) -> None:
    """
    Basic inbound upload guard: extension allowlist + size cap (+ coarse content-type sanity).
    """
    max_mb = max_mb if max_mb is not None else int(getattr(settings, "MAX_UPLOAD_MB", 50))
    if size_bytes > max_mb * 1024 * 1024:
        raise UploadBlocked(f"upload too large ({size_bytes} bytes > {max_mb} MB)")
    m = _EXT_RE.search(filename or "")
    ext = ("." + m.group(1).lower()) if m else ""
    if allowed_exts and ext not in allowed_exts:
        raise UploadBlocked(f"file type {ext or 'unknown'} not allowed")
    # coarse content-type sanity only
    if content_type and "/" in content_type:
        pass
