# /Users/omair/apisharayeh/src/app/kernel/bootstrap_toolrouter.py
from __future__ import annotations

from .toolrouter import ToolRouter
from .ops import io as io_ops
from .ops import slides as slides_ops
from .ops import doc as doc_ops
from .ops import vision as vision_ops


def build_toolrouter() -> ToolRouter:
    tr = ToolRouter()

    # ---------------- IO ----------------
    tr.register(
        "io.fetch",
        io_ops.fetch,
        getattr(io_ops.fetch, "required_permissions", {"fs_write"}),
    )
    tr.register(
        "io.save_text",
        io_ops.save_text,
        getattr(io_ops.save_text, "required_permissions", {"fs_write"}),
    )

    # --------------- Slides ---------------
    # Outline
    tr.register(
        "slides.outline.from_prompt_stub",
        slides_ops.outline_from_prompt_stub,
        getattr(slides_ops.outline_from_prompt_stub, "required_permissions", set()),
    )
    tr.register(
        "slides.outline.from_doc",
        slides_ops.outline_from_doc,
        getattr(slides_ops.outline_from_doc, "required_permissions", set()),
    )

    # HTML render
    tr.register(
        "slides.html.render",
        slides_ops.render_html,
        getattr(slides_ops.render_html, "required_permissions", {"fs_write", "fs_read"}),
    )

    # Exports (back-compat PDF stub-or-real)
    tr.register(
        "slides.export.pdf",
        slides_ops.export_pdf,
        getattr(slides_ops.export_pdf, "required_permissions", {"fs_write"}),
    )
    tr.register(
        "slides.export.pptx_stub",
        slides_ops.export_pptx_stub,
        getattr(slides_ops.export_pptx_stub, "required_permissions", {"fs_write"}),
    )

    # NEW: Real PPTX + LO-based exporters
    tr.register(
        "slides.pptx.build",
        slides_ops.build_pptx,
        getattr(slides_ops.build_pptx, "required_permissions", {"fs_write"}),
    )
    tr.register(
        "slides.export.pdf_via_lo",
        slides_ops.export_pdf_via_lo,
        getattr(slides_ops.export_pdf_via_lo, "required_permissions", {"fs_write"}),
    )
    tr.register(
        "slides.export.html_via_lo",
        slides_ops.export_html_via_lo,
        getattr(slides_ops.export_html_via_lo, "required_permissions", {"fs_write"}),
    )
    tr.register(
        "slides.export.html5_zip",
        slides_ops.export_html5_zip,
        getattr(slides_ops.export_html5_zip, "required_permissions", {"fs_write"}),
    )

    # ---------------- Docs ----------------
    tr.register("doc.detect_type", doc_ops.detect_type, set())
    tr.register("doc.parse_txt", doc_ops.parse_txt, {"fs_read"})
    tr.register("doc.parse_docx", doc_ops.parse_docx, {"fs_read"})

    # --------------- Vision ---------------
    tr.register(
        "vision.images.from_fixtures",
        vision_ops.from_fixtures,
        getattr(vision_ops.from_fixtures, "required_permissions", {"fs_write"}),
    )

    return tr




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
