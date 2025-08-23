from __future__ import annotations
import os, shlex, subprocess, signal, time
from pathlib import Path
from typing import Optional

class LOExportError(RuntimeError):
    pass

def find_soffice() -> str:
    env = os.environ.get("SOFFICE_BIN")
    if env and Path(env).exists():
        return env
    # macOS default path
    mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if Path(mac).exists():
        return mac
    raise LOExportError("LibreOffice 'soffice' not found. Set SOFFICE_BIN in .env")

def convert_to_pdf(pptx: str | Path, outdir: str | Path, timeout_s: int = 90) -> Path:
    soffice = find_soffice()
    pptx = Path(pptx).resolve()
    outdir = Path(outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    cmd = [
        soffice,
        "--headless", "--invisible",
        "--nologo", "--nolockcheck",
        "--nodefault", "--norestore",
        "--convert-to", "pdf",
        "--outdir", str(outdir),
        str(pptx),
    ]

    # Run with hard timeout. Kill process group on timeout.
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            raise LOExportError(f"LibreOffice timed out after {timeout_s}s")
    except FileNotFoundError:
        raise LOExportError("soffice not found on PATH and SOFFICE_BIN not set")
    except Exception as e:
        raise LOExportError(f"LibreOffice failed to start: {e}")

    if proc.returncode != 0:
        raise LOExportError(
            f"LibreOffice returned {proc.returncode}: "
            f"{(stderr or b'').decode(errors='ignore')[:500]}"
        )

    # Determine expected output filename (same base name, .pdf)
    pdf = outdir / (pptx.stem + ".pdf")
    if not pdf.exists():
        raise LOExportError("LibreOffice reported success but PDF not found")
    size = pdf.stat().st_size
    if size < 2048:
        raise LOExportError(f"PDF too small ({size} bytes) — conversion failed")

    return pdf
