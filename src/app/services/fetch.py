from __future__ import annotations
from typing import Dict
from app.core.safety import safe_fetch_to_temp, FetchBlocked

def secure_fetch(url: str) -> Dict[str, str | int | None]:
    """
    Safe, SSRF-protected fetch used by ingestion/tools.
    """
    path, n, ctype = safe_fetch_to_temp(url)
    return {"file": path, "bytes": n, "content_type": ctype}
