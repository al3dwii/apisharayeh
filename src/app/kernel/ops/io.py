from __future__ import annotations
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse, unquote
from typing import Dict, Any
from ..errors import ProblemDetails

REQUIRED_FETCH_PERMS = {"fs_write"}
REQUIRED_SAVE_TEXT_PERMS = {"fs_write"}


def _resolve_file_url(url: str) -> Path:
    """
    Accepts:
      - file:///absolute/path
      - /absolute/path (no scheme)
      - relative/path (no scheme)  -> resolved against CWD
    In DEV we do not allow http(s) — that check happens in fetch() below.
    """
    if "://" not in url:
        # No scheme -> treat as filesystem path
        return Path(url).resolve()

    parsed = urlparse(url)
    if parsed.scheme != "file":
        raise ProblemDetails(
            title="Scheme not allowed",
            detail=f"Only file:// URLs supported in DEV. Got: {parsed.scheme}",
            code="E_SCHEME",
            status=400,
        )

    path = unquote(parsed.path or "")
    if os.name == "nt" and path.startswith("/"):
        # On Windows, file:///C:/path -> /C:/path ; strip leading slash
        path = path.lstrip("/")
    return Path(path).resolve()


def fetch(ctx, url: str, to_dir: str) -> Dict[str, Any]:
    """
    in:  url, to_dir
    out: { path }

    Copies a local file (path or file:// URL) into the project's artifacts/<to_dir>/.
    If the source is already the exact same destination file, it returns the path
    without copying (avoids SameFileError).
    """
    # Permission check (router should also enforce)
    if "fs_write" not in ctx.permissions:
        raise ProblemDetails(title="Permission denied", detail="fs_write is required", code="E_PERM", status=403)

    # DEV_OFFLINE policy: refuse http(s)
    if isinstance(url, str) and (url.startswith("http://") or url.startswith("https://")):
        if getattr(ctx, "DEV_OFFLINE", True):
            raise ProblemDetails(
                title="Network disabled in DEV",
                detail="DEV_OFFLINE=true blocks http(s) fetch. Use a file path or file:// URL.",
                code="E_DEV_OFFLINE",
                status=400,
            )

    # Resolve the source file
    src_path = _resolve_file_url(url)
    if not src_path.exists():
        raise ProblemDetails(
            title="Input not found",
            detail=f"Source path not found: {src_path}",
            code="E_NOT_FOUND",
            status=400,
        )

    # Build the destination path inside the current project's artifacts
    base_name = src_path.name
    # ctx.artifacts_dir(to_dir) ensures the directory exists and returns the absolute dir
    dest_dir: Path = ctx.artifacts_dir(str(to_dir).rstrip("/"))
    dest: Path = (dest_dir / base_name).resolve()

    # NEW: If the source is already the same file as destination, just return it
    try:
        if dest.exists() and os.path.samefile(src_path, dest):
            return {"path": str(dest)}
    except FileNotFoundError:
        # dest may not exist yet; continue to copy
        pass
    except OSError:
        # some FS may not support samefile; fall back to path equality
        if str(src_path) == str(dest):
            return {"path": str(dest)}

    # Otherwise copy into the project
    shutil.copy2(src_path, dest)
    return {"path": str(dest)}


def save_text(ctx, text: str, to_dir: str, filename: str) -> Dict[str, Any]:
    """
    in:  text, to_dir, filename
    out: { path }
    """
    if "fs_write" not in ctx.permissions:
        raise ProblemDetails(title="Permission denied", detail="fs_write is required", code="E_PERM", status=403)

    rel_path = f"{to_dir.rstrip('/')}/{filename}"
    path = ctx.write_text(rel_path, text)
    return {"path": str(path)}


# Permission annotations (optional use by ToolRouter)
fetch.required_permissions = REQUIRED_FETCH_PERMS
save_text.required_permissions = REQUIRED_SAVE_TEXT_PERMS
