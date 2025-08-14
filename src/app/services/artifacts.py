import os, uuid, shutil
from app.core.config import settings

def save_file(src_path: str, suffix: str) -> dict:
    os.makedirs(settings.ARTIFACTS_DIR, exist_ok=True)
    fid = f"{uuid.uuid4()}{suffix}"
    dst = os.path.join(settings.ARTIFACTS_DIR, fid)
    shutil.copyfile(src_path, dst)
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return {"url": f"{base}/artifacts/{fid}", "path": dst}
