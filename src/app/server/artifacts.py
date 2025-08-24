# /Users/omair/apisharayeh/src/app/server/artifacts.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
import re

_TITLE_RE = re.compile(
    r'<h1[^>]*class="[^"]*\btitle\b[^"]*"[^>]*>(.*?)</h1>',
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class SlideItem:
    no: int
    title: str
    path: str       # absolute FS path
    url: str        # public URL (under /artifacts)
    mtime: float


def _read_title(html_path: Path) -> str:
    """
    Extracts the slide title from <h1 class="title">...</h1> if present,
    otherwise falls back to the filename stem.
    """
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


def _url_for(project_id: str, relpath: str) -> str:
    rel = relpath.replace("\\", "/").lstrip("/")
    return f"/artifacts/{project_id}/{rel}"


def _exists(p: Path) -> bool:
    try:
        return p.exists()
    except Exception:
        return False


def _detect_exports(export_dir: Path, project_id: str) -> Dict[str, str]:
    """
    Prefer canonical names; fall back to any reasonable match.
    Returns url/path pairs (empty strings if missing).
    """
    pdf_url = ""
    pdf_path = ""
    html_zip_url = ""
    html_zip_path = ""
    pptx_url = ""
    pptx_path = ""

    if not _exists(export_dir):
        return {
            "pdf_url": pdf_url,
            "pdf_path": pdf_path,
            "html_zip_url": html_zip_url,
            "html_zip_path": html_zip_path,
            "pptx_url": pptx_url,
            "pptx_path": pptx_path,
        }

    # Canonical first
    pdf = export_dir / "presentation.pdf"
    if _exists(pdf):
        pdf_url = _url_for(project_id, f"export/{pdf.name}")
        pdf_path = str(pdf)
    else:
        # Any *.pdf as fallback
        for p in sorted(export_dir.glob("*.pdf")):
            pdf_url = _url_for(project_id, f"export/{p.name}")
            pdf_path = str(p)
            break

    html_zip = export_dir / "presentation.html5.zip"
    if _exists(html_zip):
        html_zip_url = _url_for(project_id, f"export/{html_zip.name}")
        html_zip_path = str(html_zip)
    else:
        for p in sorted(export_dir.glob("*.zip")):
            n = p.name.lower()
            if "html" in n or "web" in n:
                html_zip_url = _url_for(project_id, f"export/{p.name}")
                html_zip_path = str(p)
                break

    pptx = export_dir / "presentation.pptx"
    if _exists(pptx):
        pptx_url = _url_for(project_id, f"export/{pptx.name}")
        pptx_path = str(pptx)
    else:
        for p in sorted(export_dir.glob("*.pptx")):
            pptx_url = _url_for(project_id, f"export/{p.name}")
            pptx_path = str(p)
            break

    return {
        "pdf_url": pdf_url,
        "pdf_path": pdf_path,
        "html_zip_url": html_zip_url,
        "html_zip_path": html_zip_path,
        "pptx_url": pptx_url,
        "pptx_path": pptx_path,
    }


def _list_slides(slides_dir: Path, project_id: str) -> List[SlideItem]:
    """
    Lists NNN.html slides (skips index.html). Extracts titles and sorts by slide number.
    """
    items: List[SlideItem] = []
    if not _exists(slides_dir):
        return items

    for p in sorted(slides_dir.glob("*.html")):
        if p.name.lower() == "index.html":
            continue  # don't include the index page as a slide
        # extract numeric if like 001.html
        try:
            no = int(p.stem)
        except ValueError:
            # fallback: try leading digits
            m = re.match(r"^(\d+)", p.stem)
            if not m:
                # skip non-numbered HTML pages
                continue
            no = int(m.group(1))

        title = _read_title(p)
        rel = _url_for(project_id, f"slides/{p.name}")
        try:
            mtime = p.stat().st_mtime
        except Exception:
            mtime = 0.0

        items.append(SlideItem(no=no, title=title, path=str(p), url=rel, mtime=mtime))

    # stable sort by slide number
    items.sort(key=lambda s: s.no or 0)
    return items


def list_project_artifacts(artifacts_root: Union[Path, str], project_id: str) -> Dict[str, Any]:
    """
    Returns a JSON-serializable dict with slides, export info, and helpful URLs.
    Looks for:
      - state.json
      - slides/index.html
      - slides/*.html (numbered only)
      - export/presentation.(pdf|pptx|html5.zip) with graceful fallbacks

    Shape:
    {
      "project_id": "...",
      "state_url": "/artifacts/<project_id>/state.json" | "",
      "state_path": "<abs path>" | "",
      "index_url": "/artifacts/<project_id>/slides/index.html" | "",
      "index_path": "<abs path>" | "",
      "slides": [ { "no", "title", "url", "path", "mtime" }, ... ],
      "exports": {
        "pdf_url", "pdf_path",
        "html_zip_url", "html_zip_path",
        "pptx_url", "pptx_path"
      },
      "counts": { "slides": N, "has_pdf": bool, "has_html": bool, "has_pptx": bool }
    }
    """
    root = Path(artifacts_root)
    project_dir = root / project_id
    slides_dir = project_dir / "slides"
    export_dir = project_dir / "export"

    # --------- State / Index ----------
    state_path = project_dir / "state.json"
    index_path = slides_dir / "index.html"

    state_url = _url_for(project_id, "state.json") if _exists(state_path) else ""
    index_url = _url_for(project_id, "slides/index.html") if _exists(index_path) else ""

    # --------- Slides ----------
    slides = _list_slides(slides_dir, project_id)

    # --------- Exports ----------
    exports = _detect_exports(export_dir, project_id)

    # --------- Counts ----------
    counts = {
        "slides": len(slides),
        "has_pdf": bool(exports.get("pdf_url")),
        "has_html": bool(exports.get("html_zip_url")),
        "has_pptx": bool(exports.get("pptx_url")),
    }

    return {
        "project_id": project_id,
        "state_url": state_url,
        "state_path": str(state_path) if state_url else "",
        "index_url": index_url,
        "index_path": str(index_path) if index_url else "",
        "slides": [
            {
                "no": s.no,
                "title": s.title,
                "url": s.url,
                "path": s.path,
                "mtime": s.mtime,
            }
            for s in slides
        ],
        "exports": exports,
        "counts": counts,
    }
