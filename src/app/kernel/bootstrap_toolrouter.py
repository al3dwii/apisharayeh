# src/app/kernel/bootstrap_toolrouter.py
from __future__ import annotations

from .toolrouter import ToolRouter

# Ops modules
from .ops import io as io_ops
from .ops import slides as slides_ops
from .ops import slides_stateful as slides_stateful_ops
from .ops import slides_index as slides_index_ops  # ← index page renderer
from .ops import doc as doc_ops
from .ops import vision as vision_ops
from .ops import research as research_ops
from .ops import echo as echo_ops

# M8 exports (LibreOffice/FFmpeg helpers)
from app.kernel.ops import slides_export  # new module providing export_pdf/html_zip/media_thumbnail

# Optional helper module (may not exist in some trees)
try:
    from .ops import slides_author_apply as slides_author_apply_ops  # provides apply_outline()
except Exception:  # pragma: no cover
    slides_author_apply_ops = None  # type: ignore


def _perms(fn, default):
    return getattr(fn, "required_permissions", default)


def _pick(module, *names):
    for n in names:
        fn = getattr(module, n, None)
        if fn is not None:
            return fn
    raise AttributeError(f"{module.__name__} missing one of {names!r}")


def _maybe_register(tr: ToolRouter, name: str, module, attr: str, default_perms: set) -> None:
    fn = getattr(module, attr, None)
    if fn is not None:
        tr.register(name, fn, _perms(fn, default_perms))


def build_toolrouter() -> ToolRouter:
    tr = ToolRouter()

    # IO
    tr.register("io.fetch", io_ops.fetch, _perms(io_ops.fetch, {"fs_write"}))
    tr.register("io.save_text", io_ops.save_text, _perms(io_ops.save_text, {"fs_write"}))

    # ──────────────────────────────────────────────────────────────────────────────
    # Research (pipeline + atomic ops)
    # ──────────────────────────────────────────────────────────────────────────────
    tr.register("research.search",            research_ops.search,            _perms(research_ops.search,            set()))
    tr.register("research.fetch_html",        research_ops.fetch_html,        _perms(research_ops.fetch_html,        {"fs_read"}))
    tr.register("research.extract_readable",  research_ops.extract_readable,  _perms(research_ops.extract_readable,  set()))
    tr.register("research.summarize_doc",     research_ops.summarize_doc,     _perms(research_ops.summarize_doc,     set()))
    tr.register("research.aggregate",         research_ops.aggregate,         _perms(research_ops.aggregate,         set()))
    tr.register("research.outline_slides",    research_ops.outline_slides,    _perms(research_ops.outline_slides,    set()))
    tr.register("research.cite_pack",         research_ops.cite_pack,         _perms(research_ops.cite_pack,         set()))
    # preferred public name:
    tr.register("research.pipeline",          research_ops.research_pipeline, _perms(research_ops.research_pipeline, {"fs_read", "fs_write"}))
    # back-compat name (some flows may still call this):
    tr.register("research.research_pipeline", research_ops.research_pipeline, _perms(research_ops.research_pipeline, {"fs_read", "fs_write"}))

    # (Back-compat) old placeholder research ops—register only if present
    _maybe_register(tr, "research.plan",            research_ops, "deep_thinking_plan", {"fs_write"})
    _maybe_register(tr, "research.parallel_search", research_ops, "parallel_search",    {"fs_write"})
    _maybe_register(tr, "research.read_url",        research_ops, "read_url",           {"fs_write"})
    _maybe_register(tr, "research.image_search",    research_ops, "image_search",       {"fs_write", "fs_read"})

    # ──────────────────────────────────────────────────────────────────────────────
    # Slides (outline)
    # ──────────────────────────────────────────────────────────────────────────────
    tr.register("slides.outline.from_prompt_stub", slides_ops.outline_from_prompt_stub, _perms(slides_ops.outline_from_prompt_stub, set()))
    tr.register("slides.outline.from_doc",         slides_ops.outline_from_doc,         _perms(slides_ops.outline_from_doc,         set()))
    # Optional helper to apply an outline object directly (used by research flow)
    if slides_author_apply_ops and hasattr(slides_author_apply_ops, "apply_outline"):
        tr.register("slides.author.apply_outline", slides_author_apply_ops.apply_outline, _perms(slides_author_apply_ops.apply_outline, set()))

    # Slides (stateful renderer + partial update)
    tr.register("slides.html.render", slides_stateful_ops.html_render, _perms(slides_stateful_ops.html_render, {"fs_write", "fs_read"}))
    tr.register("slides.update_one",  slides_stateful_ops.update_one,  _perms(slides_stateful_ops.update_one,  {"fs_write", "fs_read"}))

    # Slides (index/link)
    tr.register("slides.index.render", slides_index_ops.render_index, _perms(slides_index_ops.render_index, {"fs_write", "fs_read"}))

    # PPTX (builder in legacy slides.ops)
    tr.register("slides.pptx.build", slides_ops.build_pptx, _perms(slides_ops.build_pptx, {"fs_write"}))

    # ──────────────────────────────────────────────────────────────────────────────
    # Exports (old names for back-compat in some flows)
    # ──────────────────────────────────────────────────────────────────────────────
    _maybe_register(tr, "slides.export.pdf_via_lo",  slides_ops, "export_pdf_via_lo",  {"fs_write"})
    _maybe_register(tr, "slides.export.html_via_lo", slides_ops, "export_html_via_lo", {"fs_write"})
    _maybe_register(tr, "slides.export.html5_zip",   slides_ops, "export_html5_zip",   {"fs_write"})

    # ──────────────────────────────────────────────────────────────────────────────
    # Exports (new M8 hardened ops backed by export_runtime.py)
    # ──────────────────────────────────────────────────────────────────────────────
    tr.register("slides.export.pdf",      slides_export.export_pdf,      _perms(slides_export.export_pdf,      {"fs_read", "fs_write"}))
    tr.register("slides.export.html_zip", slides_export.export_html_zip, _perms(slides_export.export_html_zip, {"fs_read", "fs_write"}))
    tr.register("media.thumb",            slides_export.media_thumbnail, _perms(slides_export.media_thumbnail, {"fs_read", "fs_write"}))

    # Docs
    tr.register("doc.detect_type", doc_ops.detect_type, _perms(doc_ops.detect_type, set()))
    tr.register("doc.parse_txt",   doc_ops.parse_txt,   _perms(doc_ops.parse_txt,   {"fs_read"}))
    tr.register("doc.parse_docx",  doc_ops.parse_docx,  _perms(doc_ops.parse_docx,  {"fs_read"}))

    # Vision fixtures (supports either function name)
    vision_fixtures_fn = _pick(vision_ops, "images_from_fixtures", "from_fixtures")
    tr.register("vision.images.from_fixtures", vision_fixtures_fn, _perms(vision_fixtures_fn, {"fs_write", "fs_read"}))
    # (Optional future use) Image search with cache/fallback if implemented
    _maybe_register(tr, "vision.images.search", vision_ops, "images_search", {"fs_write", "fs_read"})

    # Utility
    tr.register("echo", echo_ops.echo, _perms(echo_ops.echo, set()))

    return tr
