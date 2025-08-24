# src/app/api/routes/slides.py
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "artifacts")).resolve()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")  # e.g. http://localhost:8081

_title_h1_re = re.compile(r"<h1[^>]*class=[\"']title[\"'][^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_title_tag_re = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _public_url(request: Request, rel_path: str) -> str:
    """
    Make a public URL for an artifacts-relative path.
    Prefer PUBLIC_BASE_URL; otherwise derive from request.
    """
    base = PUBLIC_BASE_URL.rstrip("/") if PUBLIC_BASE_URL else str(request.base_url).rstrip("/")
    rel = rel_path.lstrip("/")
    return f"{base}/{rel}"


@router.get("/v1/projects/{project_id}/slides")
def list_project_slides(project_id: str, request: Request) -> Dict[str, Any]:
    """
    Return slide HTML files (ordered) with titles and public URLs.
    {
      "project_id": "...",
      "slides": [{"no": 1, "title": "...", "path": "artifacts/<pid>/slides/001.html", "url": "..."}]
    }
    """
    slides_dir = (ARTIFACTS_DIR / project_id / "slides")
    if not slides_dir.exists():
        raise HTTPException(status_code=404, detail="slides not found for project")

    # Collect *.html sorted by name (001.html, 002.html, ...)
    files = sorted(p for p in slides_dir.iterdir() if p.suffix.lower() == ".html")
    slides: List[Dict[str, Any]] = []
    for idx, p in enumerate(files, start=1):
        # Read a tiny bit to extract a title (best-effort)
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            txt = ""
        m = _title_h1_re.search(txt) or _title_tag_re.search(txt)
        title = (m.group(1).strip() if m else p.stem) or f"Slide {idx}"

        rel_path = f"artifacts/{project_id}/slides/{p.name}"
        slides.append({
            "no": idx,
            "title": title,
            "path": rel_path,
            "url": _public_url(request, rel_path),
        })

    return {"project_id": project_id, "count": len(slides), "slides": slides}


@router.get("/v1/projects/{project_id}/artifacts")
def list_project_artifacts(project_id: str, request: Request) -> Dict[str, Any]:
    """
    List exported artifacts we know about (pdf/pptx/html5.zip), with URLs.
    {
      "project_id": "...",
      "artifacts": [{"kind": "pdf", "path": "...", "url": "..."}]
    }
    """
    export_dir = (ARTIFACTS_DIR / project_id / "export")
    if not export_dir.exists():
        return {"project_id": project_id, "artifacts": []}

    known_files = [
        ("pdf", "presentation.pdf"),
        ("pptx", "presentation.pptx"),
        ("html", "presentation.html5.zip"),
        # keep room for more kinds later
    ]

    artifacts = []
    for kind, name in known_files:
        p = export_dir / name
        if p.exists():
            rel_path = f"artifacts/{project_id}/export/{name}"
            artifacts.append({
                "kind": kind,
                "path": rel_path,
                "url": _public_url(request, rel_path),
                "bytes": p.stat().st_size,
            })

    return {"project_id": project_id, "artifacts": artifacts}
