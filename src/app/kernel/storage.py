from __future__ import annotations
import os
import shutil
from pathlib import Path
from typing import Optional


ARTIFACTS_BASE = Path(os.environ.get("ARTIFACTS_DIR", "./artifacts")).resolve()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")

def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p

def project_root(project_id: str) -> Path:
    return ensure_dir(ARTIFACTS_BASE / project_id)

def subdir(project_id: str, *parts: str) -> Path:
    return ensure_dir(project_root(project_id) / Path(*parts))

def write_bytes(project_id: str, rel_path: str, data: bytes) -> Path:
    target = project_root(project_id) / rel_path
    ensure_dir(target.parent)
    target.write_bytes(data)
    return target.resolve()

def write_text(project_id: str, rel_path: str, text: str, encoding: str = "utf-8") -> Path:
    target = project_root(project_id) / rel_path
    ensure_dir(target.parent)
    target.write_text(text, encoding=encoding)
    return target.resolve()

def copy_in(project_id: str, src: Path, rel_dest: str) -> Path:
    dest = project_root(project_id) / rel_dest
    ensure_dir(dest.parent)
    shutil.copy2(src, dest)
    return dest.resolve()

def url_for(p: Path) -> str:
    # If you serve ./artifacts as /artifacts, this will produce a stable URL
    try:
        rel = p.relative_to(ARTIFACTS_BASE)
    except ValueError:
        # Not under ARTIFACTS_BASE; return file:// URL as fallback
        return f"file://{p}"
    prefix = "/artifacts"
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL.rstrip('/')}{prefix}/{rel.as_posix()}"
    return f"{prefix}/{rel.as_posix()}"
