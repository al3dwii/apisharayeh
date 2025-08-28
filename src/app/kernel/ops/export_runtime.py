# src/app/kernel/ops/export_runtime.py
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

# ────────────────────────────────────────────────────────────────────────────────
# Errors
# ────────────────────────────────────────────────────────────────────────────────

class ExportRuntimeError(RuntimeError):
    pass


# ────────────────────────────────────────────────────────────────────────────────
# Discovery / probing
# ────────────────────────────────────────────────────────────────────────────────

def _which(bin_name: str) -> Optional[str]:
    return shutil.which(bin_name)

def _run(cmd: Iterable[str], timeout: int = 60) -> Tuple[int, str, str]:
    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return 124, out, err or "timeout"
    return proc.returncode, out, err

def _env_path(env_key: str) -> Optional[str]:
    val = os.getenv(env_key)
    if val and val.strip():
        return val.strip()
    return None

def require_soffice() -> str:
    """
    Return an absolute path to soffice (LibreOffice headless) or raise with a helpful message.
    Resolution order:
      1) $SOFFICE_BIN
      2) PATH lookup for 'soffice'
      3) macOS default install
    Also probes the binary with `--headless --version` to ensure it runs.
    """
    candidates = []
    if (p := _env_path("SOFFICE_BIN")):
        candidates.append(p)
    # PATH
    if (p := _which("soffice")):
        candidates.append(p)
    # macOS default
    darwin_default = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if os.path.exists(darwin_default):
        candidates.append(darwin_default)

    for cand in candidates:
        try:
            rc, out, err = _run([cand, "--headless", "--version"], timeout=20)
            if rc == 0 and ("LibreOffice" in out or "LibreOffice" in err):
                return cand
        except Exception:
            pass

    msg = (
        "LibreOffice (soffice) not found or not runnable.\n"
        "Set SOFFICE_BIN to the absolute path of 'soffice'.\n"
        "Examples:\n"
        "  macOS:  /Applications/LibreOffice.app/Contents/MacOS/soffice\n"
        "  Debian: /usr/bin/soffice (install: apt-get install libreoffice)\n"
        f"Current SOFFICE_BIN={os.getenv('SOFFICE_BIN')!r} PATH={os.getenv('PATH')!r}"
    )
    raise ExportRuntimeError(msg)

def require_ffmpeg() -> str:
    """
    Ensure ffmpeg exists and can run. Return absolute path.
    """
    candidates = []
    if (p := _env_path("FFMPEG_BIN")):
        candidates.append(p)
    if (p := _which("ffmpeg")):
        candidates.append(p)

    for cand in candidates:
        try:
            rc, out, err = _run([cand, "-version"], timeout=15)
            if rc == 0 and "ffmpeg version" in (out or err):
                return cand
        except Exception:
            pass

    msg = (
        "FFmpeg not found or not runnable.\n"
        "Install ffmpeg in your environment or set FFMPEG_BIN to the absolute path.\n"
        "Debian/Ubuntu: apt-get install ffmpeg"
    )
    raise ExportRuntimeError(msg)


# ────────────────────────────────────────────────────────────────────────────────
# Retry helpers
# ────────────────────────────────────────────────────────────────────────────────

@dataclass
class Retry:
    attempts: int = 3
    base_delay: float = 0.8  # seconds
    factor: float = 1.7

    def sleep(self, i: int) -> None:
        time.sleep(self.base_delay * (self.factor ** i))


# ────────────────────────────────────────────────────────────────────────────────
# LibreOffice conversions
# ────────────────────────────────────────────────────────────────────────────────

def _lo_filter_for(ext: str, target: str) -> Optional[str]:
    """
    Return a LibreOffice export filter for (input_ext -> target).
    Only a few we care about for slides.
    """
    ext = ext.lower().lstrip(".")
    target = target.lower()

    # PPTX → PDF or HTML
    if ext in {"pptx", "ppt"} and target == "pdf":
        return "impress_pdf_Export"
    if ext in {"pptx", "ppt"} and target in {"html", "xhtml"}:
        return "impress_html_Export"

    # DOCX → PDF (sometimes used pre-slides)
    if ext in {"docx", "doc"} and target == "pdf":
        return "writer_pdf_Export"

    # Fallback: let LO decide
    return None

def soffice_convert(input_path: str | Path, out_dir: str | Path, target: str, retry: Retry | None = None) -> Path:
    """
    Convert an office file using LibreOffice in headless mode.
    Returns the output file path.
    """
    src = Path(input_path)
    outd = Path(out_dir)
    outd.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        raise ExportRuntimeError(f"Input file not found: {src}")

    soffice = require_soffice()
    filter_name = _lo_filter_for(src.suffix, target)
    convert_arg = target if not filter_name else f"{target}:{filter_name}"

    cmd = [
        soffice,
        "--headless",
        "--convert-to", convert_arg,
        "--outdir", str(outd),
        str(src),
    ]

    r = retry or Retry()
    last_rc, last_out, last_err = 1, "", ""
    for i in range(r.attempts):
        rc, out, err = _run(cmd, timeout=180)
        last_rc, last_out, last_err = rc, out, err
        if rc == 0:
            # guess output filename
            produced = next(outd.glob(src.stem + f".{target}"), None)
            if produced and produced.exists():
                return produced
            # some LO versions print the path; try to extract
            for cand in outd.glob(f"*{src.stem}*.{target}"):
                if cand.exists():
                    return cand
        # transient hiccup → sleep and retry
        r.sleep(i)

    raise ExportRuntimeError(
        f"LibreOffice convert failed after {r.attempts} attempts.\n"
        f"cmd={' '.join(cmd)}\nrc={last_rc}\nstdout={last_out}\nstderr={last_err}"
    )


# ────────────────────────────────────────────────────────────────────────────────
# FFmpeg thumbnails (optional, for media cards)
# ────────────────────────────────────────────────────────────────────────────────

def ffmpeg_thumbnail(input_media: str | Path, out_png: str | Path, ss: float = 1.0, size: str = "640x360") -> Path:
    """
    Extract a thumbnail PNG from a local media file or a remote URL (if ffmpeg supports it).
    """
    ffmpeg = require_ffmpeg()
    out_path = Path(out_png)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg,
        "-ss", str(ss),
        "-i", str(input_media),
        "-vframes", "1",
        "-vf", f"scale={size}",
        "-y",
        str(out_path),
    ]
    rc, out, err = _run(cmd, timeout=60)
    if rc != 0 or not out_path.exists():
        raise ExportRuntimeError(f"ffmpeg thumbnail failed (rc={rc}). stderr={err}")
    return out_path
