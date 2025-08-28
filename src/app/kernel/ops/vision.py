# src/app/kernel/ops/vision.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any, List, Iterable, Optional

from ..errors import ProblemDetails

# Optional network fetch for nicer placeholders (gracefully falls back if offline)
try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore

# Minimal valid 1x1 transparent PNG (binary)
_ONE_BY_ONE_PNG = bytes([
    0x89,0x50,0x4E,0x47,0x0D,0x0A,0x1A,0x0A,0x00,0x00,0x00,0x0D,0x49,0x48,0x44,0x52,
    0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x01,0x08,0x06,0x00,0x00,0x00,0x1F,0x15,0xC4,
    0x89,0x00,0x00,0x00,0x0A,0x49,0x44,0x41,0x54,0x78,0x9C,0x63,0xF8,0xCF,0x00,0x00,
    0x02,0x0B,0x01,0x02,0xA7,0x69,0x1D,0xD7,0x00,0x00,0x00,0x00,0x49,0x45,0x4E,0x44,
    0xAE,0x42,0x60,0x82
])

REQUIRED_PERMS = {"fs_write", "fs_read"}


# ────────────────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────────────────

def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    # best-effort flush before replace
    try:
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
    except Exception:
        pass
    os.replace(tmp, path)  # atomic on same fs


def _ensure_placeholders(ctx, count: int, project_id: str) -> List[str]:
    """
    Create N placeholder images under this project's artifacts/images.
    - Tries to fetch real 1200x800 JPEGs from picsum.photos (no API key).
    - If network unavailable, falls back to embedded 1x1 PNGs.
    Returns absolute paths as strings.
    """
    if "fs_write" not in ctx.permissions:
        raise ProblemDetails(title="Permission denied", detail="fs_write is required", code="E_PERM", status=403)

    # e.g., /.../artifacts/prj_xxx/images (Path since Step 7)
    out_dir: Path = ctx.artifacts_dir("images")

    paths: List[str] = []
    for i in range(1, count + 1):
        # Prefer a real-looking placeholder if requests is available
        jpg_name = f"placeholder_{i}.jpg"
        jpg_path = out_dir / jpg_name
        wrote = False

        if requests is not None and not jpg_path.exists():
            try:
                # deterministic seed keeps results stable per project/run
                url = f"https://picsum.photos/seed/{project_id}-{i}/1200/800"
                r = requests.get(url, timeout=5)  # small timeout; we will gracefully fallback
                r.raise_for_status()
                ctype = (r.headers.get("content-type") or "").lower()
                if ctype.startswith("image/") and r.content:
                    _atomic_write_bytes(jpg_path, r.content)
                    wrote = True
            except Exception:
                wrote = False

        if wrote or jpg_path.exists():
            paths.append(jpg_path.as_posix())
            continue

        # Fallback: embedded 1x1 PNG, guarantees a valid image even offline
        png_name = f"placeholder_{i}.png"
        png_path = out_dir / png_name
        if not png_path.exists():
            _atomic_write_bytes(png_path, _ONE_BY_ONE_PNG)
        paths.append(png_path.as_posix())

    return paths


# ────────────────────────────────────────────────────────────────────────────────
# public op
# ────────────────────────────────────────────────────────────────────────────────

def images_from_fixtures(
    ctx,
    queries: Optional[Iterable[str]] = None,
    project_id: Optional[str] = None,
    total: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Produce a batch of placeholder images suitable for slide backgrounds.

    Inputs:
      - queries: list of thematic categories (unused here; keeps signature stable)
      - project_id: used to place images under the correct artifacts folder
      - total: optional explicit count (overrides computed size)

    Output:
      - images: List[str] (absolute filesystem paths under the project's artifacts)
    """
    qlist = list(queries or ["موضوع", "أدوات", "فوائد", "تحديات"])
    project_id = project_id or getattr(ctx, "project_id", None)
    if not project_id:
        raise ProblemDetails(title="Invalid input", detail="project_id is required", code="E_INPUT", status=400)

    # Heuristic: enough images to cover most decks (e.g., 4 queries -> 37 images)
    computed = (len(qlist) * 9) + 1
    n = int(total) if total else max(3, min(6, computed))

    paths = _ensure_placeholders(ctx, n, project_id)

    # Emit a tool_used event for tracing
    try:
        ctx.emit("tool_used", {
            "name": "vision.images.from_fixtures",
            "args": {"queries": qlist, "project_id": project_id, "count": len(paths)}
        })
    except Exception:
        pass

    return {"images": paths}


# ---- Compatibility exports ---------------------------------------------------
# Some routers try: getattr(module, "from_fixtures")
def from_fixtures(ctx, queries=None, project_id: Optional[str] = None, total: Optional[int] = None) -> Dict[str, Any]:
    return images_from_fixtures(ctx, queries=queries, project_id=project_id, total=total)

# Some routers try chained: getattr(module, "images").from_fixtures
class images:  # noqa: N801 (exposed as 'vision.images')
    @staticmethod
    def from_fixtures(ctx, queries=None, project_id: Optional[str] = None, total: Optional[int] = None) -> Dict[str, Any]:
        return images_from_fixtures(ctx, queries=queries, project_id=project_id, total=total)


# Permission annotations (used by ToolRouter, if present)
images_from_fixtures.required_permissions = REQUIRED_PERMS
from_fixtures.required_permissions = REQUIRED_PERMS
images.from_fixtures.required_permissions = REQUIRED_PERMS
