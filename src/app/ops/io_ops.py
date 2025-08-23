# src/app/ops/io_ops.py
from __future__ import annotations

from typing import Dict

from app.services.fetch import secure_fetch


def fetch(url: str) -> Dict[str, str | int | None]:
    """
    Secure, SSRF-guarded URL fetch used by ops.

    Returns a dict:
      {
        "file": "<temp file path>",
        "bytes": <int>,            # total downloaded bytes
        "content_type": "<mime>"   # or None if unknown
      }

    Raises:
      - RuntimeError / requests.HTTPError on network errors
      - app.core.safety.FetchBlocked if blocked by policy
    """
    return secure_fetch(url)


__all__ = ["fetch"]


# import os, tempfile, requests
# from app.services.artifacts import save_file

# def fetch(url: str):
#     with requests.get(url, stream=True, timeout=60, allow_redirects=True) as r:
#         r.raise_for_status()
#         fd, p = tempfile.mkstemp()
#         with os.fdopen(fd, "wb") as f:
#             for chunk in r.iter_content(8192):
#                 f.write(chunk)
#     return {"file": p}

