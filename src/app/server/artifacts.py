# /Users/omair/apisharayeh/src/app/server/artifacts.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any
import re
import time

_TITLE_RE = re.compile(r'<h1[^>]*class="[^"]*\btitle\b[^"]*"[^>]*>(.*?)</h1>', re.IGNORECASE | re.DOTALL)


@dataclass
class SlideItem:
    no: int
    title: str
    path: str       # absolute FS path
    url: str        # public URL (under /artifacts)
    mtime: float


def _read_title(html_path: Path) -> str:
    try:
        txt = html_path.read_text(encoding="utf-8", errors="ignore")
        m = _TITLE_RE.search(txt)
        if not m:
            return html_path.stem
        # collapse whitespace inside title
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        return title or html_path.stem
    except Exception:
        return html_path.stem


def list_project_artifacts(artifacts_root: Path, project_id: str) -> Dict[str, Any]:
    """
    Returns a JSON-serializable dict with slides and export info.
    Looks for:
      - slides/*.html
      - export/presentation.pdf
      - export/presentation.html5.zip
      - export/presentation.pptx
    """
    project_dir = artifacts_root / project_id
    slides_dir = project_dir / "slides"
    export_dir = project_dir / "export"

    # --------- Slides ----------
    slides: List[SlideItem] = []
    if slides_dir.exists():
        for p in sorted(slides_dir.glob("*.html")):
            # extract numeric if like 001.html
            try:
                no = int(p.stem)
            except ValueError:
                # fallback: try leading digits
                m = re.match(r"^(\d+)", p.stem)
                no = int(m.group(1)) if m else 0
            title = _read_title(p)
            rel = f"/artifacts/{project_id}/slides/{p.name}"
            slides.append(SlideItem(no=no, title=title, path=str(p), url=rel, mtime=p.stat().st_mtime))
        # stable sort by "no"
        slides.sort(key=lambda s: s.no or 0)

    # --------- Exports ----------
    pdf_url = ""
    pdf_path = ""
    html_zip_url = ""
    html_zip_path = ""
    pptx_url = ""
    pptx_path = ""

    if export_dir.exists():
        pdf = export_dir / "presentation.pdf"
        if pdf.exists():
            pdf_url = f"/artifacts/{project_id}/export/{pdf.name}"
            pdf_path = str(pdf)

        html_zip = export_dir / "presentation.html5.zip"
        if html_zip.exists():
            html_zip_url = f"/artifacts/{project_id}/export/{html_zip.name}"
            html_zip_path = str(html_zip)

        pptx = export_dir / "presentation.pptx"
        if pptx.exists():
            pptx_url = f"/artifacts/{project_id}/export/{pptx.name}"
            pptx_path = str(pptx)

    return {
        "project_id": project_id,
        "slides": [
            {
                "no": s.no,
                "title": s.title,
                "url": s.url,
                "path": s.path,
                "mtime": s.mtime,
            } for s in slides
        ],
        "exports": {
            "pdf_url": pdf_url,
            "pdf_path": pdf_path,
            "html_zip_url": html_zip_url,
            "html_zip_path": html_zip_path,
            "pptx_url": pptx_url,
            "pptx_path": pptx_path,
        },
        "counts": {
            "slides": len(slides),
            "has_pdf": bool(pdf_url),
            "has_html": bool(html_zip_url),
            "has_pptx": bool(pptx_url),
        }
    }
