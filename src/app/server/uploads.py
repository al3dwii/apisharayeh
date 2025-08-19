from __future__ import annotations
import os
import uuid
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import UploadFile

ALLOWED_EXTS = {
    "docx", "pptx", "pdf", "txt", "md", "jpg", "jpeg", "png", "webp"
}

def _safe_name(name: str) -> str:
    # strip directory parts and disallow weird chars
    base = os.path.basename(name)
    # collapse spaces
    base = " ".join(base.split())
    # extremely defensive: remove path separators just in case
    return base.replace("/", "_").replace("\\", "_")

def _ext_ok(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ALLOWED_EXTS

async def save_upload(file: UploadFile, artifacts_root: Path, project_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Saves an UploadFile into artifacts/{project_id}/input and returns a dict:
    {
      "project_id": ...,
      "filename": ...,
      "path": "<absolute path>",
      "url": "/artifacts/<project_id>/input/<filename>",
      "kind": "<ext>"
    }
    Raises ValueError on invalid file type.
    """
    if not file or not file.filename:
        raise ValueError("No file uploaded.")

    filename = _safe_name(file.filename)
    if not _ext_ok(filename):
        raise ValueError(f"Unsupported file type for '{filename}'. Allowed: {sorted(ALLOWED_EXTS)}")

    if not project_id:
        project_id = f"prj_{uuid.uuid4().hex[:8]}"

    input_dir = artifacts_root / project_id / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    dest_path = input_dir / filename

    # Stream to disk in chunks
    with dest_path.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)  # 1 MB
            if not chunk:
                break
            out.write(chunk)

    url = f"/artifacts/{project_id}/input/{filename}"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "project_id": project_id,
        "filename": filename,
        "path": str(dest_path),
        "url": url,
        "kind": ext,
    }
