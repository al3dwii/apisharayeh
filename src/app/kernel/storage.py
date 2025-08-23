# src/app/kernel/storage.py
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Union

from app.core.config import settings

# Resolve roots from settings (with sensible defaults)
_ARTIFACTS_ROOT = Path(getattr(settings, "ARTIFACTS_DIR", None) or "./artifacts").expanduser().resolve()
_PUBLIC_BASE = (getattr(settings, "PUBLIC_BASE_URL", "") or "").rstrip("/")


def _project_root(project_id: str) -> Path:
    """
    Absolute path to a project's artifacts root: <ARTIFACTS_ROOT>/<project_id>.
    Ensures the directory exists.
    """
    root = _ARTIFACTS_ROOT / project_id
    root.mkdir(parents=True, exist_ok=True)
    return root


# ------------------------- directory & file helpers --------------------------

def subdir(project_id: str, *parts: str) -> Path:
    """
    Return an absolute subdirectory path under this project's artifacts,
    creating it if necessary.
    """
    p = _project_root(project_id)
    for part in parts:
        if part:
            p = p / part
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_text(project_id: str, rel_path: str, text: str) -> Path:
    """
    Write text to <ARTIFACTS_ROOT>/<project_id>/<rel_path>.
    Returns the absolute filesystem path.
    """
    dst = _project_root(project_id) / rel_path
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    return dst.resolve()


def write_bytes(project_id: str, rel_path: str, data: bytes) -> Path:
    """
    Write bytes to <ARTIFACTS_ROOT>/<project_id>/<rel_path>.
    Returns the absolute filesystem path.
    """
    dst = _project_root(project_id) / rel_path
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return dst.resolve()


def copy_in(project_id: str, src_path: Union[str, Path], rel_dest: str) -> Path:
    """
    Copy a file or directory at src_path into this project's artifacts under rel_dest.
    Returns the absolute destination path.
    """
    src = Path(src_path).expanduser().resolve()
    dst = _project_root(project_id) / rel_dest
    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        return dst.resolve()

    shutil.copy2(src, dst)
    return dst.resolve()


# ------------------------------ URL mapping ----------------------------------

def _fs_path_to_artifacts_url(p: Union[str, Path]) -> str:
    """
    If `p` is a filesystem path inside ARTIFACTS_ROOT, map it to '/artifacts/<rel>'.
    If PUBLIC_BASE_URL is configured, return '<base>/artifacts/<rel>'.
    Otherwise, return str(p) unchanged.
    """
    try:
        path = Path(p).expanduser().resolve()
    except Exception:
        return str(p)

    try:
        rel = path.relative_to(_ARTIFACTS_ROOT)
    except Exception:
        # Not under artifacts root; return as-is
        return str(path)

    url_path = f"/artifacts/{rel.as_posix()}"
    return f"{_PUBLIC_BASE}{url_path}" if _PUBLIC_BASE else url_path


def url_for(path: Union[str, Path]) -> str:
    """
    Public URL for an artifact or path.

    Rules:
      - If given '/artifacts/<...>' → return '<PUBLIC_BASE>/artifacts/<...>' if PUBLIC_BASE_URL set, else unchanged.
      - If given 'http(s)://...' and its PATH is '/artifacts/<...>' → return unchanged.
      - If given a filesystem path inside ARTIFACTS_ROOT → map to '/artifacts/<rel>' (with PUBLIC_BASE_URL if set).
      - If given a full URL whose PATH is a filesystem path under ARTIFACTS_ROOT → rewrite to '/artifacts/<rel>'.
      - Else return str(path).
    """
    s = str(path)

    # Already a proper artifacts path?
    if s.startswith("/artifacts/"):
        return f"{_PUBLIC_BASE}{s}" if _PUBLIC_BASE else s

    # Full URL?
    if s.startswith(("http://", "https://")):
        from urllib.parse import urlparse
        parsed = urlparse(s)
        if parsed.path.startswith("/artifacts/"):
            return s  # already good
        fixed = _fs_path_to_artifacts_url(parsed.path)
        return fixed if fixed != parsed.path else s

    # Treat as filesystem path (or some other string)
    return _fs_path_to_artifacts_url(s)
