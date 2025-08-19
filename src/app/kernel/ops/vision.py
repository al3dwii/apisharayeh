from __future__ import annotations
import base64
from pathlib import Path
from typing import Dict, Any, List
from ..errors import ProblemDetails

REQUIRED_PERMS = {"fs_write"}

# A tiny 1x1 PNG (transparent) to use as placeholder if no fixture images are found.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAA"
    "AAC0lEQVR42mP8/58HAAMBAQAY4mT7AAAAAElFTkSuQmCC"
)

def from_fixtures(ctx, queries: List[str], project_id: str) -> Dict[str, Any]:
    """
    Copy a couple of images per query from local fixtures (if available) into
    /artifacts/{project}/images and return a flat list of file paths.
    """
    if "fs_write" not in ctx.permissions:
        raise ProblemDetails(title="Permission denied", detail="fs_write is required", code="E_PERM", status=403)

    # Try to find plugin fixtures by convention (optional)
    candidates = [
        Path("./plugins/slides.generate/fixtures/assets"),
        Path("./src/app/plugins/slides.generate/fixtures/assets"),
        Path("./plugins/slides.generate/fixtures"),
    ]
    fixtures_dir = next((p for p in candidates if p.exists()), None)

    out_dir = ctx.artifacts_dir("images")
    results: List[str] = []

    if fixtures_dir and fixtures_dir.exists():
        imgs = [p for p in fixtures_dir.glob("*.png")] + [p for p in fixtures_dir.glob("*.jpg")] + [p for p in fixtures_dir.glob("*.jpeg")]
        # If nothing in fixtures, fall back to placeholders
        if not imgs:
            for i in range(max(3, len(queries))):
                ph = out_dir / f"placeholder_{i+1}.png"
                if not ph.exists():
                    ph.write_bytes(_PNG_1x1)
                results.append(str(ph))
        else:
            # Copy up to N images
            take = max(3, min(10, len(imgs)))
            for i, img in enumerate(imgs[:take], start=1):
                dest = out_dir / img.name
                if not dest.exists():
                    dest.write_bytes(img.read_bytes())
                results.append(str(dest))
    else:
        # No fixtures dir—produce placeholders
        for i in range(max(3, len(queries))):
            ph = out_dir / f"placeholder_{i+1}.png"
            if not ph.exists():
                ph.write_bytes(_PNG_1x1)
            results.append(str(ph))

    ctx.emit("tool_used", {"name": "vision.images.from_fixtures", "args": {"count": len(results)}})
    return {"images": results}

from_fixtures.required_permissions = REQUIRED_PERMS
