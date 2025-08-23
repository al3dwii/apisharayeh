# src/app/services/artifacts.py
from __future__ import annotations

import uuid
from typing import Dict, Optional

from app.kernel.artifact_store import get_store

# Optional settings; fall back to safe defaults if the config module isn't ready
try:
    from app.core.config import settings  # type: ignore
except Exception:  # pragma: no cover
    class _S:
        ARTIFACT_TTL_DAYS = 7
    settings = _S()  # type: ignore


def save_file(src_path: str, suffix: str) -> Dict[str, str]:
    """
    Save an artifact to the configured store (S3 if configured, else local).

    Args:
        src_path: Path to a local source file to upload.
        suffix:   File suffix to append to a generated UUID (e.g., ".pdf", ".pptx").

    Returns:
        {
          "url":  "<public or presigned GET URL>",
          "path": "<store URI: s3://bucket/key or file://...>",
          "key":  "artifacts/<uuid><suffix>"
        }
    """
    store = get_store()
    fid = f"{uuid.uuid4()}{suffix}"
    key = f"artifacts/{fid}"
    url, uri = store.put_file(src_path, key)
    return {"url": url, "path": uri, "key": key}


def presign_put(
    suffix: str,
    *,
    content_type: Optional[str] = None,
    expires: int = 3600,
) -> Optional[Dict[str, str]]:
    """
    Create a presigned PUT URL for direct client uploads (S3-backed stores only).

    Returns:
        {
          "key": "artifacts/<uuid><suffix>",
          "url": "<presigned PUT URL>",
          "expires": "<seconds>"
        }
        or None if the active store doesn't support presigned PUT.
    """
    store = get_store()
    fid = f"{uuid.uuid4()}{suffix}"
    key = f"artifacts/{fid}"
    url = store.presign_put(key, expires=expires, content_type=content_type)
    if not url:
        return None
    return {"key": key, "url": url, "expires": str(expires)}


def presign_get(key: str, *, expires: int = 3600) -> Optional[str]:
    """
    Create a presigned GET URL for an existing artifact key (S3-backed stores only).
    Returns None if the active store doesn't support presigned GET.
    """
    store = get_store()
    return store.presign_get(key, expires=expires)


def gc_expired() -> None:
    """
    Garbage-collect artifacts older than the configured TTL (both local and S3).
    """
    days = int(getattr(settings, "ARTIFACT_TTL_DAYS", 7))
    get_store().gc_older_than(days)


__all__ = ["save_file", "presign_put", "presign_get", "gc_expired"]


# import os, uuid, shutil
# from app.core.config import settings

# def save_file(src_path: str, suffix: str) -> dict:
#     os.makedirs(settings.ARTIFACTS_DIR, exist_ok=True)
#     fid = f"{uuid.uuid4()}{suffix}"
#     dst = os.path.join(settings.ARTIFACTS_DIR, fid)
#     shutil.copyfile(src_path, dst)
#     base = settings.PUBLIC_BASE_URL.rstrip("/")
#     return {"url": f"{base}/artifacts/{fid}", "path": dst}


