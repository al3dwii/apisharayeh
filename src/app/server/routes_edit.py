from __future__ import annotations

from typing import List, Optional, Any
from pathlib import Path
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Use the stateful renderer we added
from app.kernel.ops import slides_stateful as slides_ops


router = APIRouter()


class SlidePatch(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    bullets: Optional[List[str]] = None
    image: Optional[str] = None     # URL (or relative /artifacts path)
    template: Optional[str] = None
    notes: Optional[str] = None


class _CtxShim:
    """
    Minimal ExecutionContext shim that satisfies the methods used by slides_stateful:
      - read_text / write_text
      - url_for
      - emit (no-op for HTTP path)
    It operates under ARTIFACTS_DIR/<project_id>/.
    """
    def __init__(self, project_id: str):
        artifacts_root = os.getenv("ARTIFACTS_DIR", "./artifacts")
        self.project_id = project_id
        self.base = Path(artifacts_root) / project_id
        self.base.mkdir(parents=True, exist_ok=True)
        (self.base / "slides").mkdir(parents=True, exist_ok=True)
        (self.base / "images").mkdir(parents=True, exist_ok=True)

    # -------- file I/O expected by slides_stateful --------
    def write_text(self, relpath: str, text: str) -> str:
        p = (self.base / relpath).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return str(p)

    def read_text(self, relpath: str) -> str:
        p = (self.base / relpath).resolve()
        return p.read_text(encoding="utf-8")

    # -------- event emitter hook (not streamed over HTTP; keep no-op) --------
    def emit(self, *_args: Any, **_kwargs: Any) -> None:
        # In job/SSE flows this would push to the stream; HTTP PATCH returns immediately.
        return None

    # -------- URL builder compatible with StaticFiles("/artifacts") --------
    def url_for(self, maybe_path: Any) -> str:
        """
        Accept absolute FS paths or project-relative paths and produce a static URL
        served by the /artifacts mount.
        """
        if not maybe_path:
            return ""
        s = str(maybe_path)
        # Already a URL
        if s.startswith("/"):
            return s
        p = Path(s)
        # If absolute path, make it relative to project base
        try:
            if p.is_absolute():
                rel = p.relative_to(self.base)
            else:
                rel = p
        except Exception:
            rel = p
        # Normalize to POSIX for URLs
        rel_url = str(rel).replace("\\", "/")
        return f"/artifacts/{self.project_id}/{rel_url}"


@router.patch("/v1/projects/{project_id}/slides/{no}")
def patch_slide(project_id: str, no: int, patch: SlidePatch):
    """
    PATCH a single slide in state.json and re-render just that slide.
    Returns static URLs ready for the frontend to iframe or fetch.
    """
    ctx = _CtxShim(project_id)
    try:
        out = slides_ops.update_one(
            ctx,
            project_id=project_id,
            slide_no=int(no),
            patch=patch.model_dump(exclude_none=True),
        )
    except FileNotFoundError:
        # No state.json yet (user didn't render slides) → 404
        raise HTTPException(status_code=404, detail="Deck not found (state.json missing)")
    except Exception as ex:
        # Map kernel ProblemDetails or any other error to HTTP 400
        raise HTTPException(status_code=400, detail=str(ex))

    return {
        "url": out.get("url"),
        "state_url": out.get("state_url"),
    }
