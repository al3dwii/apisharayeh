# src/app/api/routes/uploads_media.py
from fastapi import APIRouter, File, UploadFile, Form, Request, HTTPException
from pathlib import Path
import os, secrets, mimetypes

router = APIRouter()
ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "artifacts")).resolve()

def _is_media(content_type: str | None, filename: str) -> bool:
    if not content_type or content_type == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(filename)
        if guessed and guessed.split("/")[0] in {"audio", "video"}:
            return True
    return bool(content_type and content_type.split("/")[0] in {"audio", "video"})

@router.post("/uploads/media")
async def upload_media(request: Request, file: UploadFile = File(...), project_id: str = Form(None)):
    if not _is_media(file.content_type, file.filename):
        raise HTTPException(status_code=415, detail="audio/* or video/* only")

    pid = project_id or f"prj_{secrets.token_hex(4)}"
    dest_dir = ARTIFACTS_DIR / pid / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / file.filename
    with open(dest, "wb") as f:
        f.write(await file.read())

    base = str(request.base_url).rstrip("/")
    return {
        "project_id": pid,
        "path": f"/artifacts/{pid}/uploads/{file.filename}",
        "content_type": file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream",
        "public_url": f"{base}/artifacts/{pid}/uploads/{file.filename}",
    }
