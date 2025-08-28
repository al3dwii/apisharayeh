# src/app/kernel/ops/slides_export.py
from __future__ import annotations
import os, zipfile
from pathlib import Path
from typing import Dict, Any, Optional

from .export_runtime import soffice_convert, ffmpeg_thumbnail, ExportRuntimeError

# ───────────── helpers ─────────────

def _project_base(ctx, project_id: str) -> Path:
    try:
        return Path(ctx.artifacts_dir()).resolve()  # worker context usually provides this
    except Exception:
        pass
    storage = getattr(ctx, "storage", None)
    if storage and getattr(storage, "root", None):
        return Path(storage.root) / project_id
    return Path("artifacts") / project_id

def _url_for(ctx, project_id: str, rel: str) -> str:
    try:
        return str(ctx.url_for(rel))  # context mapping to /artifacts/…
    except Exception:
        return f"/artifacts/{project_id}/{rel}"

def _find_pptx(base: Path) -> Optional[Path]:
    # prefer deck.pptx, else newest *.pptx
    p = base / "deck.pptx"
    if p.exists():
        return p
    cands = sorted(base.glob("*.pptx"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None

def _zip_dir(folder: Path, zip_path: Path) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                abspath = Path(root) / f
                arcname = abspath.relative_to(folder)
                zf.write(abspath, arcname)
    return zip_path

# ───────────── ops ─────────────

def export_pdf(ctx, project_id: str, pptx_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Convert <project>/deck.pptx (or given pptx_path) → <project>/deck.pdf
    """
    base = _project_base(ctx, project_id)
    base.mkdir(parents=True, exist_ok=True)

    src = Path(pptx_path).resolve() if pptx_path else _find_pptx(base)
    if not src or not src.exists():
        raise ExportRuntimeError(f"PPTX not found for project {project_id}")

    out = soffice_convert(src, base, target="pdf")
    rel = out.relative_to(base).as_posix()
    return {"pdf_url": _url_for(ctx, project_id, rel), "pdf_path": str(out)}

export_pdf.required_permissions = {"fs_read", "fs_write"}


def export_html_zip(ctx, project_id: str, pptx_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Convert PPTX → HTML (LO export) into <project>/html_export/, then zip → <project>/html.zip
    """
    base = _project_base(ctx, project_id)
    export_dir = base / "html_export"
    export_dir.mkdir(parents=True, exist_ok=True)

    src = Path(pptx_path).resolve() if pptx_path else _find_pptx(base)
    if not src or not src.exists():
        raise ExportRuntimeError(f"PPTX not found for project {project_id}")

    # LO writes .html files into export_dir
    _ = soffice_convert(src, export_dir, target="html")

    zip_path = base / "html.zip"
    _zip_dir(export_dir, zip_path)

    return {
        "html_zip_url": _url_for(ctx, project_id, "html.zip"),
        "html_zip_path": str(zip_path),
        "html_dir": str(export_dir),
    }

export_html_zip.required_permissions = {"fs_read", "fs_write"}


def media_thumbnail(ctx, project_id: str, input_rel: str, out_rel: str = "thumbs/auto.png",
                    ss: float = 1.0, size: str = "640x360") -> Dict[str, Any]:
    """
    Extract thumbnail PNG for a media file living under the project's artifacts.
    """
    base = _project_base(ctx, project_id)
    src = base / input_rel
    if not src.exists():
        raise ExportRuntimeError(f"Media not found: {src}")
    out = base / out_rel
    out.parent.mkdir(parents=True, exist_ok=True)

    ff_out = ffmpeg_thumbnail(src, out, ss=ss, size=size)
    rel = ff_out.relative_to(base).as_posix()
    return {"thumb_url": _url_for(ctx, project_id, rel), "thumb_path": str(ff_out)}

media_thumbnail.required_permissions = {"fs_read", "fs_write"}
