from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Tuple

from app.core.logging import get_logger

log = get_logger(__name__)

# ------------------ config ------------------
ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", str(Path.cwd() / "var" / "artifacts")))
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

S3_BUCKET = os.getenv("ARTIFACTS_S3_BUCKET")
S3_PREFIX = os.getenv("ARTIFACTS_S3_PREFIX", "nodes")
S3_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")

# optional boto3
try:
    import boto3  # type: ignore
except Exception:  # pragma: no cover
    boto3 = None  # type: ignore


# ------------------ helpers ------------------
def _canon_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")

def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _shard_path(root: Path, digest: str, suffix: str) -> Path:
    # file layout: /root/ab/cd/abcdef... .suffix
    return root / digest[:2] / digest[2:4] / f"{digest}{suffix}"


# ------------------ local FS backend ------------------
async def _fs_put_json(obj: Any) -> Tuple[str, str]:
    data = await asyncio.to_thread(_canon_bytes, obj)
    dig = _digest(data)
    path = _shard_path(ARTIFACTS_DIR, dig, ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    await asyncio.to_thread(tmp.write_bytes, data)
    await asyncio.to_thread(os.replace, tmp, path)
    uri = f"file://{path.resolve()}"
    return uri, dig


# ------------------ S3 backend ------------------
def _s3_available() -> bool:
    return bool(S3_BUCKET and boto3 is not None)

def _s3_key_for(digest: str) -> str:
    return f"{S3_PREFIX}/{digest[:2]}/{digest[2:4]}/{digest}.json"

def _s3_put_sync(bucket: str, key: str, data: bytes) -> None:
    assert boto3 is not None
    client = boto3.client("s3", region_name=S3_REGION) if S3_REGION else boto3.client("s3")
    client.put_object(Bucket=bucket, Key=key, Body=data, ContentType="application/json")


async def _s3_put_json(obj: Any) -> Tuple[str, str]:
    data = await asyncio.to_thread(_canon_bytes, obj)
    dig = _digest(data)
    key = _s3_key_for(dig)
    try:
        await asyncio.to_thread(_s3_put_sync, S3_BUCKET, key, data)  # type: ignore[arg-type]
        uri = f"s3://{S3_BUCKET}/{key}"
        return uri, dig
    except Exception as e:
        log.warning("artifacts(s3) put failed bucket=%s key=%s err=%r; falling back to FS", S3_BUCKET, key, e)
        return await _fs_put_json(obj)


# ------------------ public API ------------------
async def put_json(obj: Any) -> Tuple[str, str]:
    """
    Persist an artifact (JSON-serializable) and return (uri, digest).
    Tries S3 if configured, else local filesystem.
    """
    if _s3_available():
        return await _s3_put_json(obj)
    return await _fs_put_json(obj)


# # src/app/services/artifacts.py
# from __future__ import annotations

# import uuid
# from typing import Dict, Optional

# from app.kernel.artifact_store import get_store

# # Optional settings; fall back to safe defaults if the config module isn't ready
# try:
#     from app.core.config import settings  # type: ignore
# except Exception:  # pragma: no cover
#     class _S:
#         ARTIFACT_TTL_DAYS = 7
#     settings = _S()  # type: ignore


# def save_file(src_path: str, suffix: str) -> Dict[str, str]:
#     """
#     Save an artifact to the configured store (S3 if configured, else local).

#     Args:
#         src_path: Path to a local source file to upload.
#         suffix:   File suffix to append to a generated UUID (e.g., ".pdf", ".pptx").

#     Returns:
#         {
#           "url":  "<public or presigned GET URL>",
#           "path": "<store URI: s3://bucket/key or file://...>",
#           "key":  "artifacts/<uuid><suffix>"
#         }
#     """
#     store = get_store()
#     fid = f"{uuid.uuid4()}{suffix}"
#     key = f"artifacts/{fid}"
#     url, uri = store.put_file(src_path, key)
#     return {"url": url, "path": uri, "key": key}


# def presign_put(
#     suffix: str,
#     *,
#     content_type: Optional[str] = None,
#     expires: int = 3600,
# ) -> Optional[Dict[str, str]]:
#     """
#     Create a presigned PUT URL for direct client uploads (S3-backed stores only).

#     Returns:
#         {
#           "key": "artifacts/<uuid><suffix>",
#           "url": "<presigned PUT URL>",
#           "expires": "<seconds>"
#         }
#         or None if the active store doesn't support presigned PUT.
#     """
#     store = get_store()
#     fid = f"{uuid.uuid4()}{suffix}"
#     key = f"artifacts/{fid}"
#     url = store.presign_put(key, expires=expires, content_type=content_type)
#     if not url:
#         return None
#     return {"key": key, "url": url, "expires": str(expires)}


# def presign_get(key: str, *, expires: int = 3600) -> Optional[str]:
#     """
#     Create a presigned GET URL for an existing artifact key (S3-backed stores only).
#     Returns None if the active store doesn't support presigned GET.
#     """
#     store = get_store()
#     return store.presign_get(key, expires=expires)


# def gc_expired() -> None:
#     """
#     Garbage-collect artifacts older than the configured TTL (both local and S3).
#     """
#     days = int(getattr(settings, "ARTIFACT_TTL_DAYS", 7))
#     get_store().gc_older_than(days)


# __all__ = ["save_file", "presign_put", "presign_get", "gc_expired"]



