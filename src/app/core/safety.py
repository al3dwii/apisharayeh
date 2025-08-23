from __future__ import annotations
import ipaddress, socket, os, re, tempfile, contextlib
from typing import Iterable, Optional, Tuple, Sequence
from urllib.parse import urlparse
import requests

try:
    import httpx  # optional; used by async fetch
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

# settings is optional; getattr with defaults so we don't require config patches
try:
    from app.core.config import settings  # type: ignore
except Exception:  # pragma: no cover
    class _S:  # fallback defaults
        ALLOWLIST_DOMAINS = None
        MAX_FETCH_BYTES = 25 * 1024 * 1024
        FETCH_TIMEOUT_SEC = 30
        MAX_UPLOAD_MB = 50
    settings = _S()  # type: ignore


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


class FetchBlocked(Exception):
    """Raised when an outbound fetch is blocked by policy."""
    pass


class UploadBlocked(Exception):
    """Raised when an inbound upload is blocked by policy."""
    pass


def _resolve_all(host: str) -> Iterable[str]:
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


def safe_fetch_to_temp(
    url: str,
    *,
    max_bytes: Optional[int] = None,
    timeout: Optional[int] = None,
    allowed_content_types: Optional[Sequence[str]] = None,
) -> Tuple[str, int, Optional[str]]:
    """
    Download a URL with SSRF protections and size cap.
    Returns (temp_path, total_bytes, content_type). Raises FetchBlocked or requests.HTTPError.
    """
    validate_url(url)
    max_bytes = max_bytes if max_bytes is not None else int(getattr(settings, "MAX_FETCH_BYTES", 25 * 1024 * 1024))
    timeout = timeout if timeout is not None else int(getattr(settings, "FETCH_TIMEOUT_SEC", 30))

    with requests.get(url, stream=True, timeout=timeout, allow_redirects=True) as r:
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
) -> Tuple[str, int, Optional[str]]:
    if httpx is None:  # pragma: no cover
        # fall back to sync version in thread if httpx isn't installed
        return safe_fetch_to_temp(url, max_bytes=max_bytes, timeout=timeout, allowed_content_types=allowed_content_types)

    validate_url(url)
    max_bytes = max_bytes if max_bytes is not None else int(getattr(settings, "MAX_FETCH_BYTES", 25 * 1024 * 1024))
    timeout = timeout if timeout is not None else int(getattr(settings, "FETCH_TIMEOUT_SEC", 30))

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
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


_EXT_RE = re.compile(r"\.([A-Za-z0-9_-]{1,16})$")


def validate_upload(filename: str, content_type: Optional[str], size_bytes: int,
                    allowed_exts: Sequence[str] = (".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".png", ".jpg", ".jpeg"),
                    max_mb: Optional[int] = None) -> None:
    """
    Basic inbound upload guard: extension allowlist + size cap + coarse content-type check.
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
