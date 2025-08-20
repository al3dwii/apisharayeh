# src/app/kernel/toolrouter.py
from __future__ import annotations
from typing import Callable, Dict, Any, Optional, Set

from .errors import ProblemDetails


class ToolRouter:
    """
    Minimal op registry + invoker used by the DSLRunner.

    - register(name, func, required_permissions?)
    - call(name, ctx, **kwargs) -> Dict[str, Any]
    - list() -> [names]
    - has(name) -> bool
    """

    def __init__(self) -> None:
        self._tools: Dict[str, Callable[..., Dict[str, Any]]] = {}
        self._perms: Dict[str, Set[str]] = {}

    def register(
        self,
        name: str,
        func: Callable[..., Dict[str, Any]],
        required_permissions: Optional[Set[str]] = None,
    ) -> "ToolRouter":
        if not callable(func):
            raise ValueError(f"Tool '{name}' must be callable")
        self._tools[name] = func

        # Pull required permissions from arg or from function annotation if present
        perms = set(required_permissions or getattr(func, "required_permissions", set()) or set())
        self._perms[name] = perms
        return self

    # Convenience API
    def has(self, name: str) -> bool:
        return name in self._tools

    def list(self) -> list[str]:
        return sorted(self._tools.keys())

    def call(self, name: str, ctx, **kwargs) -> Dict[str, Any]:
        """
        Invoke a registered op:
        - Emits step started/succeeded/failed events
        - Enforces required permissions (if any)
        - Returns a dict (empty dict if tool returns None)
        """
        if name not in self._tools:
            raise ProblemDetails(
                title="Unknown op",
                detail=f"'{name}' is not registered",
                code="E_OP_UNKNOWN",
                status=400,
            )

        # Permission check (ops can double-check internally too)
        required = self._perms.get(name, set())
        if required and not required.issubset(ctx.permissions):
            missing = sorted(required - set(ctx.permissions))
            raise ProblemDetails(
                title="Permission denied",
                detail=f"'{name}' requires: {', '.join(missing)}",
                code="E_PERM",
                status=403,
            )

        fn = self._tools[name]

        # Emit step lifecycle
        ctx.emit("step", {"name": name, "status": "started"})
        try:
            out = fn(ctx, **kwargs)
            if out is None:
                out = {}
            if not isinstance(out, dict):
                # Normalize unexpected returns
                out = {"result": out}
            ctx.emit("step", {"name": name, "status": "succeeded"})
            return out
        except ProblemDetails:
            ctx.emit("step", {"name": name, "status": "failed"})
            raise
        except Exception as e:
            ctx.emit("step", {"name": name, "status": "failed"})
            raise


# from __future__ import annotations
# from typing import Any, Dict, Callable
# from app.kernel.errors import ProblemDetails

# # --- slides ops (from your updated slides.py) ---
# from app.kernel.ops.slides import (
#     outline_from_prompt_stub,
#     outline_from_doc,
#     html_render,     # maps to op: "slides.html.render"
#     export_pdf,      # maps to op: "slides.export.pdf"
#     # export_pptx_stub  # (optional) if you want to register it too
# )

# # --- vision (placeholders/images for DEV) ---
# from app.kernel.ops.vision import images_from_fixtures

# # --- IO ops (you already have these) ---
# from app.kernel.ops.io import fetch as io_fetch, save_text as io_save_text

# # --- Document ops (you already have these) ---
# from app.kernel.ops.doc import (
#     detect_type as doc_detect_type,
#     parse_docx as doc_parse_docx,
#     parse_txt as doc_parse_txt,
# )

# class ToolRouter:
#     """
#     String-op → function registry.
#     Each function signature: fn(ctx, **kwargs) -> dict
#     """
#     def __init__(self):
#         self._ops: Dict[str, Callable[..., Dict[str, Any]]] = {
#             # slides
#             "slides.outline.from_prompt_stub": outline_from_prompt_stub,
#             "slides.outline.from_doc":        outline_from_doc,
#             "slides.html.render":             html_render,
#             "slides.export.pdf":              export_pdf,
#             # "slides.export.pptx_stub":      export_pptx_stub,  # (optional)

#             # vision
#             "vision.images.from_fixtures":    images_from_fixtures,

#             # io
#             "io.fetch":                       io_fetch,
#             "io.save_text":                   io_save_text,

#             # doc
#             "doc.detect_type":                doc_detect_type,
#             "doc.parse_docx":                 doc_parse_docx,
#             "doc.parse_txt":                  doc_parse_txt,
#         }

#     def call(self, op: str, ctx, **kwargs) -> Dict[str, Any]:
#         fn = self._ops.get(op)
#         if not fn:
#             raise ProblemDetails(
#                 title="Unknown operation",
#                 detail=f"op='{op}' not registered",
#                 code="E_OP_UNKNOWN",
#                 status=400,
#             )
#         return fn(ctx, **kwargs)
