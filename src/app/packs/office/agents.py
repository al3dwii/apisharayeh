from __future__ import annotations
from typing import Any, Dict, List, Tuple, Optional

from app.tools.office_io import (
    fetch_to_tmp,
    pdf_to_outline,
    docx_to_outline,
    pptx_to_outline,
    outline_to_pptx,
    build_pptx_from_pdf,
    build_pptx_from_docx,
    pptx_to_pdf_file,
    pptx_to_docx_file,
    pptx_to_html5_zip,
    save_artifact,
)


class _Runner:
    """Tiny async-compatible wrapper around a sync function."""
    def __init__(self, fn):
        self._fn = fn

    async def arun(self, inputs: Dict[str, Any]):
        return self._fn(inputs)


def _save_once(path: str, name: str) -> Tuple[str, str]:
    key, url = save_artifact(path, name)
    return key, url


# ----------------- Builders -----------------

def pdf_to_pptx_builder(_redis, _tenant_id):
    def run(inputs: Dict[str, Any]):
        local = fetch_to_tmp(inputs)
        title = inputs.get("title")
        max_slides = int(inputs.get("max_slides", 12))
        outline = pdf_to_outline(local, max_slides=max_slides)
        pptx_path = outline_to_pptx(outline, title=title)
        key, url = _save_once(pptx_path, "slides.pptx")
        return {"pptx_key": key, "pptx_url": url, "outline": outline}
    return _Runner(run)


def word_to_pptx_builder(_redis, _tenant_id):
    def run(inputs: Dict[str, Any]):
        local = fetch_to_tmp(inputs)
        title = inputs.get("title")
        max_slides = int(inputs.get("max_slides", 12))
        outline = docx_to_outline(local, max_slides=max_slides)
        pptx_path = outline_to_pptx(outline, title=title)
        key, url = _save_once(pptx_path, "slides.pptx")
        return {"pptx_key": key, "pptx_url": url, "outline": outline}
    return _Runner(run)


def pptx_to_pdf_builder(_redis, _tenant_id):
    def run(inputs: Dict[str, Any]):
        local = fetch_to_tmp(inputs)
        pdf_path = pptx_to_pdf_file(local)
        key, url = _save_once(pdf_path, "slides.pdf")
        return {"pdf_key": key, "pdf_url": url}
    return _Runner(run)


def pptx_to_docx_builder(_redis, _tenant_id):
    def run(inputs: Dict[str, Any]):
        local = fetch_to_tmp(inputs)
        docx_path = pptx_to_docx_file(local)
        key, url = _save_once(docx_path, "slides.docx")
        return {"docx_key": key, "docx_url": url}
    return _Runner(run)


def pptx_to_html5_builder(_redis, _tenant_id):
    def run(inputs: Dict[str, Any]):
        local = fetch_to_tmp(inputs)
        zip_path = pptx_to_html5_zip(local)
        key, url = _save_once(zip_path, "slides_html.zip")
        return {"zip_key": key, "zip_url": url}
    return _Runner(run)

# ----------------- Registry export -----------------

def register():
    """
    Expose Office pack agents as { "office": { agent_name: builder } }.
    Each builder takes (redis, tenant_id) and returns a runner with .arun(inputs).
    """
    return {
        "office": {
            "pdf_to_pptx": pdf_to_pptx_builder,
            "word_to_pptx": word_to_pptx_builder,
            "docx_to_pptx": word_to_pptx_builder,  # alias for clarity
            "pptx_to_pdf": pptx_to_pdf_builder,
            "pptx_to_docx": pptx_to_docx_builder,
            "pptx_to_html5": pptx_to_html5_builder,
        }
    }
