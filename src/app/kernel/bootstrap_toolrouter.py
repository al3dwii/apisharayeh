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
    # Research (new pipeline + atomic ops)
    # ──────────────────────────────────────────────────────────────────────────────
    tr.register("research.search",            research_ops.search,            _perms(research_ops.search,            set()))
    tr.register("research.fetch_html",        research_ops.fetch_html,        _perms(research_ops.fetch_html,        {"fs_read"}))
    tr.register("research.extract_readable",  research_ops.extract_readable,  _perms(research_ops.extract_readable,  set()))
    tr.register("research.summarize_doc",     research_ops.summarize_doc,     _perms(research_ops.summarize_doc,     set()))
    tr.register("research.aggregate",         research_ops.aggregate,         _perms(research_ops.aggregate,         set()))
    tr.register("research.outline_slides",    research_ops.outline_slides,    _perms(research_ops.outline_slides,    set()))
    tr.register("research.cite_pack",         research_ops.cite_pack,         _perms(research_ops.cite_pack,         set()))
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

    # PPTX & exports
    tr.register("slides.pptx.build",         slides_ops.build_pptx,         _perms(slides_ops.build_pptx,         {"fs_write"}))
    tr.register("slides.export.pdf_via_lo",  slides_ops.export_pdf_via_lo,  _perms(slides_ops.export_pdf_via_lo,  {"fs_write"}))
    tr.register("slides.export.html_via_lo", slides_ops.export_html_via_lo, _perms(slides_ops.export_html_via_lo, {"fs_write"}))
    tr.register("slides.export.html5_zip",   slides_ops.export_html5_zip,   _perms(slides_ops.export_html5_zip,   {"fs_write"}))

    # Docs
    tr.register("doc.detect_type", doc_ops.detect_type, _perms(doc_ops.detect_type, set()))
    tr.register("doc.parse_txt",   doc_ops.parse_txt,   _perms(doc_ops.parse_txt,   {"fs_read"}))
    tr.register("doc.parse_docx",  doc_ops.parse_docx,  _perms(doc_ops.parse_docx,  {"fs_read"}))

    # Vision fixtures (supports either function name)
    vision_fixtures_fn = _pick(vision_ops, "images_from_fixtures", "from_fixtures")
    tr.register("vision.images.from_fixtures", vision_fixtures_fn, _perms(vision_fixtures_fn, {"fs_write", "fs_read"}))

    # Utility
    tr.register("echo", echo_ops.echo, _perms(echo_ops.echo, set()))

    return tr


# from __future__ import annotations

# from .toolrouter import ToolRouter

# # Ops modules
# from .ops import io as io_ops
# from .ops import slides as slides_ops
# from .ops import doc as doc_ops
# from .ops import vision as vision_ops
# from .ops import research as research_ops
# from .ops import echo as echo_ops

# from .ops import slides_stateful as slides_stateful_ops



# def _perms(fn, default):
#     return getattr(fn, "required_permissions", default)


# def _pick(module, *names):
#     """
#     Return the first attribute that exists on module from names.
#     Raises AttributeError if none found.
#     """
#     for n in names:
#         fn = getattr(module, n, None)
#         if fn is not None:
#             return fn
#     raise AttributeError(f"{module.__name__} missing one of {names!r}")


# def build_toolrouter() -> ToolRouter:
#     tr = ToolRouter()

#     # ---------------- IO ----------------
#     tr.register("io.fetch", io_ops.fetch, _perms(io_ops.fetch, {"fs_write"}))
#     tr.register("io.save_text", io_ops.save_text, _perms(io_ops.save_text, {"fs_write"}))

#     # --------------- Research (narration / todos / search / read / images) ---------------
#     tr.register("research.plan",
#                 research_ops.deep_thinking_plan,
#                 _perms(research_ops.deep_thinking_plan, {"fs_write"}))
#     tr.register("research.parallel_search",
#                 research_ops.parallel_search,
#                 _perms(research_ops.parallel_search, {"fs_write"}))
#     tr.register("research.read_url",
#                 research_ops.read_url,
#                 _perms(research_ops.read_url, {"fs_write"}))
#     tr.register("research.image_search",
#                 research_ops.image_search,
#                 _perms(research_ops.image_search, {"fs_write", "fs_read"}))

#     # ---------------- Slides ----------------
#     # Outline
#     tr.register("slides.outline.from_prompt_stub",
#                 slides_ops.outline_from_prompt_stub,
#                 _perms(slides_ops.outline_from_prompt_stub, set()))
#     tr.register("slides.outline.from_doc",
#                 slides_ops.outline_from_doc,
#                 _perms(slides_ops.outline_from_doc, set()))

#     # HTML render (writes artifacts/.../slides/NNN.html)
#     # Pick compatible function name (repos vary between html_render vs render_html).
#     slides_html_render_fn = _pick(slides_ops, "html_render", "render_html")

#     # Replace previous slides.html.render registration with this one:
#     tr.register(
#         "slides.html.render",
#         slides_stateful_ops.html_render,
#         getattr(slides_stateful_ops.html_render, "required_permissions", {"fs_write","fs_read"}),
#     )

#     tr.register(
#     "slides.update_one",
#     slides_stateful_ops.update_one,
#     getattr(slides_stateful_ops.update_one, "required_permissions", {"fs_write","fs_read"}),
#     )



#     # PPTX + exports (real paths with LO fallbacks)
#     tr.register("slides.pptx.build",
#                 slides_ops.build_pptx,
#                 _perms(slides_ops.build_pptx, {"fs_write"}))
#     tr.register("slides.export.pdf_via_lo",
#                 slides_ops.export_pdf_via_lo,
#                 _perms(slides_ops.export_pdf_via_lo, {"fs_write"}))
#     tr.register("slides.export.html_via_lo",
#                 slides_ops.export_html_via_lo,
#                 _perms(slides_ops.export_html_via_lo, {"fs_write"}))
#     tr.register("slides.export.html5_zip",
#                 slides_ops.export_html5_zip,
#                 _perms(slides_ops.export_html5_zip, {"fs_write"}))

#     # ---------------- Docs ----------------
#     tr.register("doc.detect_type", doc_ops.detect_type, _perms(doc_ops.detect_type, set()))
#     tr.register("doc.parse_txt",   doc_ops.parse_txt,   _perms(doc_ops.parse_txt, {"fs_read"}))
#     tr.register("doc.parse_docx",  doc_ops.parse_docx,  _perms(doc_ops.parse_docx, {"fs_read"}))

#     # --------------- Vision fixtures ---------------
#     # Pick compatible fixture function name.
#     vision_fixtures_fn = _pick(vision_ops, "images_from_fixtures", "from_fixtures")
#     tr.register("vision.images.from_fixtures",
#                 vision_fixtures_fn,
#                 _perms(vision_fixtures_fn, {"fs_write", "fs_read"}))

#     # ---------------- Utility ----------------
#     tr.register("echo", echo_ops.echo, _perms(echo_ops.echo, set()))

#     # NOTE: we will register 'slides.update_one' after we add state.json + templates.
#     return tr





# # src/app/kernel/bootstrap_toolrouter.py
# from __future__ import annotations

# from .toolrouter import ToolRouter

# # --- built-in ops
# from .ops import io as io_ops
# from .ops import slides as slides_ops
# from .ops import doc as doc_ops
# from .ops import vision as vision_ops
# from .ops import research as research_ops  # ⟵ NEW: wire research stubs


# def build_toolrouter() -> ToolRouter:
#     tr = ToolRouter()

#     # ---------------- IO ----------------
#     tr.register(
#         "io.fetch",
#         io_ops.fetch,
#         getattr(io_ops.fetch, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "io.save_text",
#         io_ops.save_text,
#         getattr(io_ops.save_text, "required_permissions", {"fs_write"}),
#     )

#     # --------------- Research (left-panel narration/progress) ---------------
#     tr.register(
#         "research.plan",
#         research_ops.deep_thinking_plan,  # DSL expects research.plan
#         getattr(research_ops.deep_thinking_plan, "required_permissions", set()),
#     )
#     tr.register(
#         "research.parallel_search",
#         research_ops.parallel_search,
#         getattr(research_ops.parallel_search, "required_permissions", set()),
#     )
#     tr.register(
#         "research.read",
#         research_ops.read_url,
#         getattr(research_ops.read_url, "required_permissions", set()),
#     )
#     tr.register(
#         "image.search",
#         research_ops.image_search,  # offline-safe fixture images
#         getattr(research_ops.image_search, "required_permissions", {"fs_write", "fs_read"}),
#     )

#     # --------------- Slides ---------------
#     # Outline
#     tr.register(
#         "slides.outline.from_prompt_stub",
#         slides_ops.outline_from_prompt_stub,
#         getattr(slides_ops.outline_from_prompt_stub, "required_permissions", set()),
#     )
#     tr.register(
#         "slides.outline.from_doc",
#         slides_ops.outline_from_doc,
#         getattr(slides_ops.outline_from_doc, "required_permissions", set()),
#     )

#     # HTML render (one HTML file per slide)
#     tr.register(
#         "slides.html.render",
#         slides_ops.render_html,  # or slides_ops.html_render (alias)
#         getattr(slides_ops.render_html, "required_permissions", {"fs_write", "fs_read"}),
#     )

#     # Exports (PDF stub-or-real; PPTX real builder; HTML exports)
#     tr.register(
#         "slides.export.pdf",
#         slides_ops.export_pdf,
#         getattr(slides_ops.export_pdf, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "slides.export.pptx_stub",
#         slides_ops.export_pptx_stub,
#         getattr(slides_ops.export_pptx_stub, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "slides.pptx.build",
#         slides_ops.build_pptx,
#         getattr(slides_ops.build_pptx, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "slides.export.pdf_via_lo",
#         slides_ops.export_pdf_via_lo,
#         getattr(slides_ops.export_pdf_via_lo, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "slides.export.html_via_lo",
#         slides_ops.export_html_via_lo,
#         getattr(slides_ops.export_html_via_lo, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "slides.export.html5_zip",
#         slides_ops.export_html5_zip,
#         getattr(slides_ops.export_html5_zip, "required_permissions", {"fs_write"}),
#     )

#     # ---------------- Docs ----------------
#     tr.register("doc.detect_type", doc_ops.detect_type, set())
#     tr.register("doc.parse_txt", doc_ops.parse_txt, {"fs_read"})
#     tr.register("doc.parse_docx", doc_ops.parse_docx, {"fs_read"})

#     # --------------- Vision fixtures ---------------
#     tr.register(
#         "vision.images.from_fixtures",
#         vision_ops.from_fixtures,
#         getattr(vision_ops.from_fixtures, "required_permissions", {"fs_write"}),
#     )

#     return tr





# # src/app/kernel/bootstrap_toolrouter.py
# from __future__ import annotations

# from .toolrouter import ToolRouter

# # Core ops
# from .ops import io as io_ops
# from .ops import slides as slides_ops
# from .ops import doc as doc_ops

# # Optional modules (register only if present / attribute exists)
# try:
#     from .ops import research as research_ops  # plan, parallel_search, read_url, image_search (names may vary)
# except Exception:  # pragma: no cover
#     research_ops = None  # type: ignore

# try:
#     from .ops import vision as vision_ops  # from_fixtures (placeholder images)
# except Exception:  # pragma: no cover
#     vision_ops = None  # type: ignore


# def _register_if_present(tr: ToolRouter, op_name: str, mod, attr_names, default_perms=set()):
#     """
#     Try a list of candidate function names on a module; register the first found.
#     Used to keep compatibility with slightly different function names.
#     """
#     if not mod:
#         return
#     if isinstance(attr_names, str):
#         attr_names = [attr_names]
#     for attr in attr_names:
#         fn = getattr(mod, attr, None)
#         if callable(fn):
#             tr.register(op_name, fn, getattr(fn, "required_permissions", set(default_perms)))
#             return


# def build_toolrouter() -> ToolRouter:
#     tr = ToolRouter()

#     # ---------------- IO ----------------
#     tr.register(
#         "io.fetch",
#         io_ops.fetch,
#         getattr(io_ops.fetch, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "io.save_text",
#         io_ops.save_text,
#         getattr(io_ops.save_text, "required_permissions", {"fs_write"}),
#     )

#     # --------------- Slides (outline + render) ---------------
#     tr.register(
#         "slides.outline.from_prompt_stub",
#         slides_ops.outline_from_prompt_stub,
#         getattr(slides_ops.outline_from_prompt_stub, "required_permissions", set()),
#     )
#     tr.register(
#         "slides.outline.from_doc",
#         slides_ops.outline_from_doc,
#         getattr(slides_ops.outline_from_doc, "required_permissions", set()),
#     )

#     html_render_fn = getattr(slides_ops, "html_render", None) or slides_ops.render_html
#     tr.register(
#         "slides.html.render",
#         html_render_fn,
#         getattr(html_render_fn, "required_permissions", {"fs_write", "fs_read"}),
#     )

#     # --------------- Slides (PPTX + exports) ---------------
#     tr.register(
#         "slides.pptx.build",
#         slides_ops.build_pptx,
#         getattr(slides_ops.build_pptx, "required_permissions", {"fs_write"}),
#     )

#     # Back-compat / stub or real PDF (slides.py decides; emits artifact.ready)
#     tr.register(
#         "slides.export.pdf",
#         slides_ops.export_pdf,
#         getattr(slides_ops.export_pdf, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "slides.export.pptx_stub",
#         slides_ops.export_pptx_stub,
#         getattr(slides_ops.export_pptx_stub, "required_permissions", {"fs_write"}),
#     )

#     # Optional LO-based exporters (registered even if LO not present; fn will error gracefully)
#     tr.register(
#         "slides.export.pdf_via_lo",
#         slides_ops.export_pdf_via_lo,
#         getattr(slides_ops.export_pdf_via_lo, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "slides.export.html_via_lo",
#         slides_ops.export_html_via_lo,
#         getattr(slides_ops.export_html_via_lo, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "slides.export.html5_zip",
#         slides_ops.export_html5_zip,
#         getattr(slides_ops.export_html5_zip, "required_permissions", {"fs_write"}),
#     )

#     # ---------------- Docs ----------------
#     tr.register("doc.detect_type", doc_ops.detect_type, set())
#     tr.register("doc.parse_txt", doc_ops.parse_txt, {"fs_read"})
#     tr.register("doc.parse_docx", doc_ops.parse_docx, {"fs_read"})

#     # ---------------- Research (optional; keep name-compat) ----------------
#     # "Deep Thinking" step in UI → usually research.plan (function may be named plan or deep_thinking_plan)
#     _register_if_present(
#         tr,
#         "research.plan",
#         research_ops,
#         ["plan", "deep_thinking_plan"],
#         default_perms=set(),
#     )
#     # Parallel search aggregator
#     _register_if_present(
#         tr,
#         "research.parallel_search",
#         research_ops,
#         ["parallel_search", "multi_search"],
#         default_perms=set(),
#     )
#     # Reader (fetch + summarize); often named read_url
#     _register_if_present(
#         tr,
#         "research.read",
#         research_ops,
#         ["read_url", "read"],
#         default_perms=set(),
#     )
#     # Image search used by the flow; typically writes files
#     _register_if_present(
#         tr,
#         "image.search",
#         research_ops,
#         ["image_search", "images_search"],
#         default_perms={"fs_write", "fs_read"},
#     )

#     # --------------- Vision (fixtures → placeholder images) ---------------
#     _register_if_present(
#         tr,
#         "vision.images.from_fixtures",
#         vision_ops,
#         ["from_fixtures", "images_from_fixtures"],
#         default_perms={"fs_write"},
#     )

#     return tr


# # /Users/omair/apisharayeh/src/app/kernel/bootstrap_toolrouter.py
# from __future__ import annotations

# from .toolrouter import ToolRouter
# from .ops import io as io_ops
# from .ops import slides as slides_ops
# from .ops import doc as doc_ops
# from .ops import vision as vision_ops


# def build_toolrouter() -> ToolRouter:
#     tr = ToolRouter()

#     # ---------------- IO ----------------
#     tr.register(
#         "io.fetch",
#         io_ops.fetch,
#         getattr(io_ops.fetch, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "io.save_text",
#         io_ops.save_text,
#         getattr(io_ops.save_text, "required_permissions", {"fs_write"}),
#     )

#     # --------------- Slides ---------------
#     # Outline
#     tr.register(
#         "slides.outline.from_prompt_stub",
#         slides_ops.outline_from_prompt_stub,
#         getattr(slides_ops.outline_from_prompt_stub, "required_permissions", set()),
#     )
#     tr.register(
#         "slides.outline.from_doc",
#         slides_ops.outline_from_doc,
#         getattr(slides_ops.outline_from_doc, "required_permissions", set()),
#     )

#     # HTML render
#     tr.register(
#         "slides.html.render",
#         slides_ops.render_html,
#         getattr(slides_ops.render_html, "required_permissions", {"fs_write", "fs_read"}),
#     )

#     # Exports (back-compat PDF stub-or-real)
#     tr.register(
#         "slides.export.pdf",
#         slides_ops.export_pdf,
#         getattr(slides_ops.export_pdf, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "slides.export.pptx_stub",
#         slides_ops.export_pptx_stub,
#         getattr(slides_ops.export_pptx_stub, "required_permissions", {"fs_write"}),
#     )

#     # NEW: Real PPTX + LO-based exporters
#     tr.register(
#         "slides.pptx.build",
#         slides_ops.build_pptx,
#         getattr(slides_ops.build_pptx, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "slides.export.pdf_via_lo",
#         slides_ops.export_pdf_via_lo,
#         getattr(slides_ops.export_pdf_via_lo, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "slides.export.html_via_lo",
#         slides_ops.export_html_via_lo,
#         getattr(slides_ops.export_html_via_lo, "required_permissions", {"fs_write"}),
#     )
#     tr.register(
#         "slides.export.html5_zip",
#         slides_ops.export_html5_zip,
#         getattr(slides_ops.export_html5_zip, "required_permissions", {"fs_write"}),
#     )

#     # ---------------- Docs ----------------
#     tr.register("doc.detect_type", doc_ops.detect_type, set())
#     tr.register("doc.parse_txt", doc_ops.parse_txt, {"fs_read"})
#     tr.register("doc.parse_docx", doc_ops.parse_docx, {"fs_read"})

#     # --------------- Vision ---------------
#     tr.register(
#         "vision.images.from_fixtures",
#         vision_ops.from_fixtures,
#         getattr(vision_ops.from_fixtures, "required_permissions", {"fs_write"}),
#     )

#     return tr




# from __future__ import annotations
# from .toolrouter import ToolRouter
# from .ops import io as io_ops
# from .ops import slides as slides_ops
# from .ops import doc as doc_ops
# from .ops import vision as vision_ops

# def build_toolrouter() -> ToolRouter:
#     tr = ToolRouter()
#     # IO
#     tr.register("io.fetch", io_ops.fetch, getattr(io_ops.fetch, "required_permissions", {"fs_write"}))
#     tr.register("io.save_text", io_ops.save_text, getattr(io_ops.save_text, "required_permissions", {"fs_write"}))
#     # Slides
#     tr.register("slides.outline.from_prompt_stub", slides_ops.outline_from_prompt_stub, getattr(slides_ops.outline_from_prompt_stub, "required_permissions", set()))
#     tr.register("slides.outline.from_doc", slides_ops.outline_from_doc, getattr(slides_ops.outline_from_doc, "required_permissions", set()))
#     tr.register("slides.html.render", slides_ops.render_html, getattr(slides_ops.render_html, "required_permissions", {"fs_write", "fs_read"}))
#     tr.register("slides.export.pdf", slides_ops.export_pdf, getattr(slides_ops.export_pdf, "required_permissions", {"fs_write"}))
#     tr.register("slides.export.pptx_stub", slides_ops.export_pptx_stub, getattr(slides_ops.export_pptx_stub, "required_permissions", {"fs_write"}))
#     # Docs
#     tr.register("doc.detect_type", doc_ops.detect_type, set())
#     tr.register("doc.parse_txt", doc_ops.parse_txt, {"fs_read"})
#     tr.register("doc.parse_docx", doc_ops.parse_docx, {"fs_read"})
#     # Vision
#     tr.register("vision.images.from_fixtures", vision_ops.from_fixtures, getattr(vision_ops.from_fixtures, "required_permissions", {"fs_write"}))
#     return tr
