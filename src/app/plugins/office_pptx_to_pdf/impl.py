# src/app/plugins/office_pptx_to_pdf/impl.py
from __future__ import annotations

import os
import io
import uuid
import shutil
import tempfile
import subprocess
from typing import Any, Dict, Tuple

import httpx
from app.core.config import settings


def _emit(ctx, step: str, status: str = "info", payload: Dict[str, Any] | None = None):
    """Best-effort event emission compatible with your bridge."""
    try:
        ctx.events.emit(ctx.tenant_id, ctx.job_id, step, status, payload or {})
    except Exception:
        try:
            ctx.events.emit(ctx.job_id, step, payload or {})
        except Exception:
            pass


def _download_pptx(url: str, tmpdir: str) -> str:
    name = f"src_{uuid.uuid4().hex}.pptx"
    dest = os.path.join(tmpdir, name)
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return dest


def _require_soffice() -> str:
    """Return soffice binary path or raise with a helpful message."""
    from shutil import which
    path = which("soffice")
    if not path:
        raise RuntimeError(
            "LibreOffice 'soffice' not found on PATH. Install it in the worker image:\n"
            "  Debian/Ubuntu: apt-get update && apt-get install -y libreoffice-common libreoffice-impress\n"
            "  Alpine:        apk add --no-cache libreoffice\n"
            "  Mac (brew):    brew install --cask libreoffice\n"
            "Then restart your worker."
        )
    return path


def _convert_with_soffice(src_pptx: str, outdir: str, export_profile: str, timeout: int) -> str:
    """
    Use headless LibreOffice to export PPTX -> PDF.
    We try the explicit filter then the generic 'pdf' target for broader compatibility.
    """
    soffice = _require_soffice()

    # Primary command: explicit filter
    # On many builds, 'impress_pdf_Export' is the correct filter name for PPT/PPTX.
    cmd_primary = [
        soffice,
        "--headless",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
        "--nolockcheck",
        "--convert-to",
        "pdf:impress_pdf_Export",
        "--outdir",
        outdir,
        src_pptx,
    ]

    # Some builds are happier with a plain 'pdf' target (auto-detects filter)
    cmd_fallback = [
        soffice,
        "--headless",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
        "--nolockcheck",
        "--convert-to",
        "pdf",
        "--outdir",
        outdir,
        src_pptx,
    ]

    # PDF/A (best-effort): certain LO versions support a filter option 'SelectPdfVersion=1' -> PDF/A-1
    # That syntax is platform/LO-version dependent; we try it if export_profile == 'pdfa'.
    cmd_pdfa = [
        soffice,
        "--headless",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
        "--nolockcheck",
        "--convert-to",
        "pdf:impress_pdf_Export:SelectPdfVersion=1",
        "--outdir",
        outdir,
        src_pptx,
    ]

    def run_cmd(cmd):
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
            return True
        except subprocess.CalledProcessError as e:
            return False
        except subprocess.TimeoutExpired:
            raise RuntimeError("LibreOffice conversion timed out")

    if export_profile == "pdfa":
        if run_cmd(cmd_pdfa):
            pdf_path = _resolve_output_path(src_pptx, outdir, ".pdf")
            if os.path.exists(pdf_path):
                return pdf_path
        # fall through to standard if PDF/A export isn't supported

    if run_cmd(cmd_primary):
        pdf_path = _resolve_output_path(src_pptx, outdir, ".pdf")
        if os.path.exists(pdf_path):
            return pdf_path

    if run_cmd(cmd_fallback):
        pdf_path = _resolve_output_path(src_pptx, outdir, ".pdf")
        if os.path.exists(pdf_path):
            return pdf_path

    raise RuntimeError("LibreOffice could not convert PPTX to PDF (filter not available or file unsupported)")


def _resolve_output_path(src_path: str, outdir: str, new_ext: str) -> str:
    base = os.path.splitext(os.path.basename(src_path))[0]
    candidate = os.path.join(outdir, f"{base}{new_ext}")
    if os.path.exists(candidate):
        return candidate
    # LO sometimes normalizes names; search for any .pdf produced in outdir
    for name in os.listdir(outdir):
        if name.lower().endswith(new_ext):
            return os.path.join(outdir, name)
    return candidate


def _save_artifact_local(local_path: str, preferred_name: str) -> Tuple[str, str]:
    """
    Save under artifacts dir and build a public URL.
    Uses env or settings:
      - ARTIFACTS_DIR
      - PUBLIC_BASE_URL
      - ARTIFACTS_HTTP_BASE
    """
    artifacts_dir = os.getenv("ARTIFACTS_DIR", "/data/artifacts")
    http_base = os.getenv("ARTIFACTS_HTTP_BASE", "/artifacts")
    base_url = getattr(settings, "PUBLIC_BASE_URL", None) or os.getenv("PUBLIC_BASE_URL", "http://localhost:8080")

    os.makedirs(artifacts_dir, exist_ok=True)
    name = f"{os.path.splitext(preferred_name)[0]}_{uuid.uuid4().hex}.pdf"
    dest = os.path.join(artifacts_dir, name)
    shutil.copy2(local_path, dest)

    sep = "" if http_base.endswith("/") else "/"
    url = f"{base_url.rstrip('/')}{http_base}{sep}{name}"
    return dest, url


def run(inputs: Dict[str, Any], ctx) -> Dict[str, Any]:
    """
    Inputs:
      - file_url (str)   : PPTX URL
      - export_profile   : 'standard' | 'pdfa'
      - timeout_sec (int): default 120
    Returns:
      - { pdf_key, pdf_url }
    """
    file_url: str = inputs["file_url"]
    export_profile: str = inputs.get("export_profile", "standard")
    timeout_sec: int = int(inputs.get("timeout_sec", 120))

    _emit(ctx, "pptx.fetch", "started", {"url": file_url})

    with tempfile.TemporaryDirectory(prefix="pptx2pdf_") as tmp:
        # 1) Download PPTX
        src_pptx = _download_pptx(file_url, tmp)
        _emit(ctx, "pptx.fetch", "succeeded", {"path": src_pptx})

        # 2) Convert with soffice
        _emit(ctx, "pdf.convert", "started", {"profile": export_profile})
        pdf_path = _convert_with_soffice(src_pptx, tmp, export_profile=export_profile, timeout=timeout_sec)
        _emit(ctx, "pdf.convert", "succeeded", {"path": pdf_path})

        # 3) Save artifact
        pdf_key, pdf_url = _save_artifact_local(pdf_path, "slides.pdf")

    return {"pdf_key": pdf_key, "pdf_url": pdf_url}
