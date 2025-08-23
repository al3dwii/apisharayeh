from __future__ import annotations

from pathlib import Path
from typing import Any, Union
from urllib.parse import urlparse

from app.core.config import settings

_ART_ROOT = Path(getattr(settings, "ARTIFACTS_DIR", None) or "./artifacts").expanduser().resolve()
_BASE = (getattr(settings, "PUBLIC_BASE_URL", "") or "").rstrip("/")


def _fs_to_art_url(p: Union[str, Path]) -> str:
    """Map a filesystem path under ARTIFACTS_DIR → '/artifacts/<rel>' (or full with PUBLIC_BASE_URL)."""
    try:
        path = Path(p).expanduser().resolve()
    except Exception:
        return str(p)
    try:
        rel = path.relative_to(_ART_ROOT)
    except Exception:
        return str(path)
    url_path = f"/artifacts/{rel.as_posix()}"
    return f"{_BASE}{url_path}" if _BASE else url_path


def _string_to_art_url_if_needed(s: str) -> str:
    """Fix bad 'http://.../Users/.../artifacts/...' or bare fs paths → artifacts URL."""
    if s.startswith(("http://", "https://")):
        parsed = urlparse(s)
        fixed = _fs_to_art_url(parsed.path)
        return fixed if fixed != parsed.path else s
    return _fs_to_art_url(s)


def normalize_artifact_urls(obj: Any) -> Any:
    """
    Recursively:
      - add sibling *_url from *_path
      - rewrite any *_url, 'artifact', 'url' string values that are fs paths
      - rewrite any bare string that is an artifacts fs path
    """
    if isinstance(obj, dict):
        out = {k: normalize_artifact_urls(v) for k, v in obj.items()}

        # add sibling urls from *_path
        for base in ("pdf", "pptx"):
            p, u = f"{base}_path", f"{base}_url"
            if p in out and u not in out:
                out[u] = _string_to_art_url_if_needed(out[p])

        # normalize any URL-like keys or generic 'artifact'
        for k, v in list(out.items()):
            if isinstance(v, str) and (k.endswith("_url") or k in ("artifact", "url")):
                out[k] = _string_to_art_url_if_needed(v)

        return out
    if isinstance(obj, list):
        return [normalize_artifact_urls(v) for v in obj]
    if isinstance(obj, (str, Path)):
        return _string_to_art_url_if_needed(str(obj))
    return obj
