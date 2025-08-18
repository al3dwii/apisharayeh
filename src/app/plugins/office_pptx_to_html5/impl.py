# src/app/plugins/office_pptx_to_html5/impl.py
from __future__ import annotations

import os
import re
import io
import uuid
import shutil
import zipfile
import tempfile
import subprocess
from typing import Any, Dict, List, Tuple

import httpx
from app.core.config import settings

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

# ---------------------------
# Event + small helpers
# ---------------------------

def _emit(ctx, step: str, status: str = "info", payload: Dict[str, Any] | None = None):
    """Best-effort event emission; compatible with multiple bridge shapes."""
    try:
        ctx.events.emit(ctx.tenant_id, ctx.job_id, step, status, payload or {})
    except Exception:
        try:
            ctx.events.emit(ctx.job_id, step, payload or {})
        except Exception:
            pass


def _download(url: str, path: str) -> None:
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)


def _require_soffice() -> str:
    from shutil import which
    p = which("soffice")
    if not p:
        raise RuntimeError(
            "LibreOffice 'soffice' not found on PATH. Install it in the worker image:\n"
            "  Debian/Ubuntu: apt-get update && apt-get install -y libreoffice-common libreoffice-impress\n"
            "  Alpine:        apk add --no-cache libreoffice\n"
            "  Mac (brew):    brew install --cask libreoffice\n"
            "Then restart your worker."
        )
    return p


def _pptx_to_pdf(src_pptx: str, outdir: str, timeout: int = 180) -> str:
    """Use LibreOffice to convert PPTX → PDF."""
    soffice = _require_soffice()
    cmd = [
        soffice,
        "--headless", "--nologo", "--nodefault", "--nofirststartwizard", "--nolockcheck",
        "--convert-to", "pdf:impress_pdf_Export",
        "--outdir", outdir,
        src_pptx,
    ]
    alt = [
        soffice,
        "--headless", "--nologo", "--nodefault", "--nofirststartwizard", "--nolockcheck",
        "--convert-to", "pdf",
        "--outdir", outdir,
        src_pptx,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.CalledProcessError:
        subprocess.run(alt, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    base = os.path.splitext(os.path.basename(src_pptx))[0]
    pdf_guess = os.path.join(outdir, base + ".pdf")
    if os.path.exists(pdf_guess):
        return pdf_guess
    # Fallback: pick any .pdf produced
    for name in os.listdir(outdir):
        if name.lower().endswith(".pdf"):
            return os.path.join(outdir, name)
    raise RuntimeError("LibreOffice did not produce a PDF")


def _pdf_to_images(pdf_path: str, images_dir: str, dpi: int, max_slides: int) -> List[str]:
    """Render PDF pages to PNG files using PyMuPDF."""
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is required. Add 'pymupdf' to your requirements.")
    os.makedirs(images_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    count = min(len(doc), max_slides)
    paths: List[str] = []
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    for i in range(count):
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        out = os.path.join(images_dir, f"slide-{i+1:04d}.png")
        pix.save(out)
        paths.append(out)
    doc.close()
    return paths


def _sanitize_title(title: str) -> str:
    # Minimal HTML escaping for title
    return (title or "Slides").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_reveal_index(
    title: str,
    theme: str,
    transition: str,
    auto_slide_ms: int,
    hash_urls: bool,
    controls: bool,
    progress: bool,
    center: bool,
    image_paths: List[str],
) -> str:
    """
    Build a Reveal.js HTML file that references slide images locally in ./images/.
    Uses unpkg CDN for reveal assets to keep ZIP small.
    """
    title = _sanitize_title(title)
    theme_css = f"https://unpkg.com/reveal.js/dist/theme/{theme}.css"
    reveal_css = "https://unpkg.com/reveal.js/dist/reveal.css"
    reveal_js  = "https://unpkg.com/reveal.js/dist/reveal.js"

    # Build <section> blocks
    slides_html = "\n".join(
        f'        <section><img src="images/{os.path.basename(p)}" alt="slide {i+1}" style="width: 100%; height: auto;" /></section>'
        for i, p in enumerate(image_paths)
    )

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <title>{title}</title>
    <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
    <link rel="stylesheet" href="{reveal_css}"/>
    <link rel="stylesheet" href="{theme_css}" id="theme"/>
    <style>
      .reveal {{ background: #0000; }}
      .reveal .slides {{ text-align: center; }}
      img {{ box-shadow: 0 8px 32px rgba(0,0,0,0.15); border-radius: 12px; }}
    </style>
  </head>
  <body>
    <div class="reveal">
      <div class="slides">
{slides_html}
      </div>
    </div>
    <script src="{reveal_js}"></script>
    <script>
      Reveal.initialize({{
        hash: {str(hash_urls).lower()},
        transition: "{transition}",
        autoSlide: {int(auto_slide_ms)},
        controls: {str(controls).lower()},
        progress: {str(progress).lower()},
        center: {str(center).lower()}
      }});
    </script>
  </body>
</html>
"""


def _zip_dir(src_dir: str, zip_path: str) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(src_dir):
            for f in files:
                abspath = os.path.join(root, f)
                rel = os.path.relpath(abspath, src_dir)
                zf.write(abspath, rel)


def _save_artifact_local(local_path: str, preferred_name: str) -> Tuple[str, str]:
    """
    Save under artifacts dir and build a public URL.
    Env/settings:
      - ARTIFACTS_DIR
      - PUBLIC_BASE_URL
      - ARTIFACTS_HTTP_BASE
    """
    artifacts_dir = os.getenv("ARTIFACTS_DIR", "/data/artifacts")
    http_base = os.getenv("ARTIFACTS_HTTP_BASE", "/artifacts")
    base_url = getattr(settings, "PUBLIC_BASE_URL", None) or os.getenv("PUBLIC_BASE_URL", "http://localhost:8080")

    os.makedirs(artifacts_dir, exist_ok=True)
    name = f"{os.path.splitext(preferred_name)[0]}_{uuid.uuid4().hex}.zip"
    dest = os.path.join(artifacts_dir, name)
    shutil.copy2(local_path, dest)

    sep = "" if http_base.endswith("/") else "/"
    url = f"{base_url.rstrip('/')}{http_base}{sep}{name}"
    return dest, url


# ---------------------------
# Entry point
# ---------------------------

def run(inputs: Dict[str, Any], ctx) -> Dict[str, Any]:
    """
    Inputs:
      - file_url       : PPTX URL (required)
      - title          : HTML title (opt)
      - max_slides     : limit pages rendered
      - dpi            : rasterization DPI
      - transition     : reveal transition
      - theme          : reveal theme
      - auto_slide_ms  : 0 manual; else auto-advance interval
      - hash_urls      : enable hash routing
      - controls       : next/prev arrows
      - progress       : progress bar
      - center         : vertical centering
    Returns:
      - { zip_key, zip_url }
    """
    file_url: str = inputs["file_url"]
    title: str = inputs.get("title", "Slides")
    max_slides: int = int(inputs.get("max_slides", 200))
    dpi: int = int(inputs.get("dpi", 150))
    transition: str = inputs.get("transition", "slide")
    theme: str = inputs.get("theme", "white")
    auto_slide_ms: int = int(inputs.get("auto_slide_ms", 0))
    hash_urls: bool = bool(inputs.get("hash_urls", True))
    controls: bool = bool(inputs.get("controls", True))
    progress: bool = bool(inputs.get("progress", True))
    center: bool = bool(inputs.get("center", True))

    with tempfile.TemporaryDirectory(prefix="pptx2html5_") as tmp:
        pptx_path = os.path.join(tmp, f"src_{uuid.uuid4().hex}.pptx")
        _emit(ctx, "pptx.fetch", "started", {"url": file_url})
        _download(file_url, pptx_path)
        _emit(ctx, "pptx.fetch", "succeeded", {"path": pptx_path})

        # 1) PPTX → PDF
        _emit(ctx, "pdf.convert", "started", None)
        pdf_path = _pptx_to_pdf(pptx_path, tmp)
        _emit(ctx, "pdf.convert", "succeeded", {"path": pdf_path})

        # 2) PDF → PNGs
        _emit(ctx, "images.render", "started", {"dpi": dpi})
        images_dir = os.path.join(tmp, "site", "images")
        images = _pdf_to_images(pdf_path, images_dir, dpi=dpi, max_slides=max_slides)
        _emit(ctx, "images.render", "succeeded", {"count": len(images)})

        if not images:
            raise RuntimeError("No slides produced")

        # 3) Build index.html (Reveal.js via CDN) inside site/
        site_dir = os.path.join(tmp, "site")
        os.makedirs(site_dir, exist_ok=True)
        index_html = _build_reveal_index(
            title=title,
            theme=theme,
            transition=transition,
            auto_slide_ms=auto_slide_ms,
            hash_urls=hash_urls,
            controls=controls,
            progress=progress,
            center=center,
            image_paths=images,
        )
        with open(os.path.join(site_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(index_html)
        _emit(ctx, "site.build", "succeeded", {"dir": site_dir})

        # 4) Zip the site
        zip_path = os.path.join(tmp, "html5_deck.zip")
        _zip_dir(site_dir, zip_path)
        _emit(ctx, "site.package", "succeeded", {"zip": zip_path})

        # 5) Save artifact
        zip_key, zip_url = _save_artifact_local(zip_path, "slides_html5.zip")

    return {"zip_key": zip_key, "zip_url": zip_url}
