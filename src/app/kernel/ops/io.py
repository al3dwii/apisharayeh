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
      - file://relative/path (treated as relative to CWD)
      - file://plugins/<plugin-id>/fixtures/assets/...
      - /absolute/path (no scheme) or relative path (no scheme)
    """
    if "://" not in url:
        return Path(url).resolve()

    parsed = urlparse(url)
    if parsed.scheme != "file":
        raise ProblemDetails(
            title="Scheme not allowed",
            detail=f"Only file:// URLs supported in DEV. Got: {parsed.scheme}",
            code="E_SCHEME",
            status=400,
        )
    path = unquote(parsed.path)
    if os.name == "nt" and path.startswith("/"):
        # On Windows, file:///C:/path -> /C:/path ; strip leading slash
        path = path.lstrip("/")
    return Path(path).resolve()


def fetch(ctx, url: str, to_dir: str) -> Dict[str, Any]:
    """
    in:  url, to_dir
    out: { path }
    """
    # Permission check covered by ToolRouter, but double-check here for clarity
    if "fs_write" not in ctx.permissions:
        raise ProblemDetails(title="Permission denied", detail="fs_write is required", code="E_PERM", status=403)

    # DEV_OFFLINE policy: refuse http(s)
    if url.startswith("http://") or url.startswith("https://"):
        if ctx.DEV_OFFLINE:
            raise ProblemDetails(
                title="Network disabled in DEV",
                detail="DEV_OFFLINE=true blocks http(s) fetch. Use file:// instead.",
                code="E_DEV_OFFLINE",
                status=400,
            )

    src_path = _resolve_file_url(url)
    if not src_path.exists():
        raise ProblemDetails(
            title="Input not found",
            detail=f"Source path not found: {src_path}",
            code="E_NOT_FOUND",
            status=400,
        )

    # Decide destination filename
    base_name = src_path.name
    rel_dest = f"{to_dir.rstrip('/')}/{base_name}"
    dest = ctx.copy_in(src_path, rel_dest)
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


# Attach required permission annotations (used by ToolRouter if desired)
fetch.required_permissions = REQUIRED_FETCH_PERMS
save_text.required_permissions = REQUIRED_SAVE_TEXT_PERMS
