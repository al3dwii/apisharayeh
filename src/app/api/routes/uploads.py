# src/app/api/routes/uploads.py
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import APIRouter, File, UploadFile, Form, HTTPException, Request

router = APIRouter()

ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "artifacts")).resolve()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")  # e.g., http://localhost:8081


def _ensure_project_id(project_id: Optional[str]) -> str:
    if project_id and project_id.strip():
        return project_id.strip()
    return f"prj_{secrets.token_hex(4)}"


def _public_url(request: Request, rel_path: str) -> str:
    base = PUBLIC_BASE_URL.rstrip("/") if PUBLIC_BASE_URL else str(request.base_url).rstrip("/")
    return f"{base}/{rel_path.lstrip('/')}"


def _safe_filename(name: str) -> str:
    # very small sanitization
    name = name.replace("/", "_").replace("\\", "_").strip()
    return name or "upload.bin"


def _try_extract_text(filepath: Path) -> str:
    """
    Best-effort text extraction for DOCX/TXT without introducing hard deps.
    - TXT: read as UTF-8 (ignore errors)
    - DOCX: if python-docx is installed, use it; otherwise return empty string
    """
    suf = filepath.suffix.lower()
    if suf in {".txt", ".md"}:
        try:
            return filepath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
    if suf == ".docx":
        try:
            from docx import Document  # type: ignore
        except Exception:
            return ""
        try:
            doc = Document(str(filepath))
            out = []
            for p in doc.paragraphs:
                txt = (p.text or "").strip()
                if txt:
                    out.append(txt)
            return "\n".join(out)
        except Exception:
            return ""
    # Other formats not supported here
    return ""


@router.post("/v1/uploads/doc")
async def upload_doc(
    request: Request,
    file: UploadFile = File(..., description="DOCX or TXT"),
    project_id: Optional[str] = Form(None),
) -> Dict[str, Any]:
    """
    Save an uploaded DOCX/TXT under artifacts/<project_id>/uploads/<filename>
    and return:
      - project_id
      - path (artifacts-relative)
      - url (public)
      - doc_text (best-effort extraction; may be empty)
    """
    pid = _ensure_project_id(project_id)
    uploads_dir = (ARTIFACTS_DIR / pid / "uploads")
    uploads_dir.mkdir(parents=True, exist_ok=True)

    filename = _safe_filename(file.filename or "upload.bin")
    dst = uploads_dir / filename

    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"failed to read upload: {e}")

    try:
        dst.write_bytes(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to save upload: {e}")

    rel_path = f"artifacts/{pid}/uploads/{filename}"
    url = _public_url(request, rel_path)

    # Lightweight, optional text extraction (for immediate outline-from-doc UX)
    doc_text = _try_extract_text(dst)

    return {
        "project_id": pid,
        "path": rel_path,
        "url": url,
        "bytes": len(content),
        "doc_text": doc_text,  # can be passed directly to slides.generate later
    }
