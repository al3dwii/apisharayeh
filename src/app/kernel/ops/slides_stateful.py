from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import time
import json
from pathlib import Path
import posixpath  # POSIX-style paths in HTML

from .slides_templates import render_by_template

# ---- helpers ----

def _now_ts() -> int:
    return int(time.time())

def _as_url(ctx, maybe_path: Any) -> Optional[str]:
    """
    Map a filesystem path (str/Path) or ctx-relative path to a public URL:
      - absolute http(s) URLs are returned as-is
      - '/artifacts/...' URLs are returned as-is
      - anything else is mapped via ctx.url_for (handles absolute FS & ctx-rel)
    """
    if not maybe_path:
        return None
    s = str(maybe_path)
    if s.startswith("http://") or s.startswith("https://") or s.startswith("/artifacts/"):
        return s
    try:
        return ctx.url_for(Path(s))
    except Exception:
        return None

def _to_project_rel(url_or_path: Optional[str], project_id: str) -> Optional[str]:
    """
    Convert one of:
      - /artifacts/<project_id>/images/foo.jpg  ->  images/foo.jpg
      - absolute FS path containing .../artifacts/<project_id>/... -> strip prefix
      - already-relative (no scheme, no leading slash) -> return as-is
      - http(s) URLs -> None (we keep them absolute)
    """
    if not url_or_path:
        return None
    s = str(url_or_path)

    # Don't normalize absolute external URLs to project-rel
    if "://" in s and not s.startswith("file://"):
        return None

    prefix = f"/artifacts/{project_id}/"
    if s.startswith(prefix):
        return s[len(prefix):]

    idx = s.find(prefix)
    if idx != -1:
        return s[idx + len(prefix):]

    if not s.startswith("/"):
        return s  # already relative (e.g. images/foo.jpg)

    # Unknown absolute path we can't relate safely
    return None

def _img_src_for_template(ctx, project_id: str, raw: Any) -> Optional[str]:
    """
    Value for <img src="..."> inside slides/NNN.html.

    If external (http/https): return as-is.
    If inside the project: compute a path RELATIVE TO 'slides/' (../images/xxx).
    """
    url = _as_url(ctx, raw)
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url  # external image

    rel = _to_project_rel(url, project_id)  # e.g. images/foo.jpg
    if not rel:
        return None

    # HTML files live under 'slides/'. Make the src relative to that folder.
    # Example: rel='images/foo.jpg' -> '../images/foo.jpg'
    return posixpath.relpath(rel, "slides")

# --- JSON I/O shims (ExecutionContext doesn't have write_json/read_json) ---

def _ctx_read_json(ctx, relpath: str) -> Dict[str, Any]:
    try:
        txt = ctx.read_text(relpath)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    if not txt:
        return {}
    try:
        return json.loads(txt)
    except Exception:
        return {}

def _ctx_write_json(ctx, relpath: str, obj: Dict[str, Any]) -> None:
    payload = json.dumps(obj, ensure_ascii=False, indent=2)
    ctx.write_text(relpath, payload)

def _read_state(ctx) -> Dict[str, Any]:
    return _ctx_read_json(ctx, "state.json") or {}

def _write_state(ctx, state: Dict[str, Any]) -> None:
    state["updated_at"] = _now_ts()
    if "created_at" not in state:
        state["created_at"] = state["updated_at"]
    _ctx_write_json(ctx, "state.json", state)

# ---- robust slide writing ----

def _write_slide_file(ctx, project_id: str, filename: str, html: str) -> Tuple[Path, str]:
    """
    Write HTML into artifacts/<project_id>/slides/<filename>, ensuring the folder exists.
    Returns (fs_path, public_url)
    """
    # storage.root points at the artifacts root
    storage = getattr(ctx, "storage", None)
    if not storage or not hasattr(storage, "root"):
        # fallback: try context-relative write (may fail if parents absent)
        fs_path = ctx.write_text(f"slides/{filename}", html)
        return Path(fs_path), ctx.url_for(fs_path)

    base = Path(storage.root) / project_id / "slides"
    base.mkdir(parents=True, exist_ok=True)
    fs_path = base / filename
    fs_path.write_text(html, encoding="utf-8")
    # url_for accepts either a rel path under the project or an absolute path
    try:
        url = ctx.url_for(fs_path)
    except Exception:
        # fallback to a project-relative path to be mapped by url_for
        url = ctx.url_for(Path(project_id) / "slides" / filename)
    return fs_path, url

def _render_one(ctx, project_id: str, slide: Dict[str, Any], lang: str) -> Tuple[str, str]:
    """
    Renders a single slide dict into slides/NNN.html.
    Returns (filesystem_path_str, static_url_str)
    """
    img_src = _img_src_for_template(ctx, project_id, slide.get("image"))
    html = render_by_template(slide, lang, img_src)
    filename = f"{slide['no']:03}.html"
    fs_path, url = _write_slide_file(ctx, project_id, filename, html)
    # Optional: emit a debug event so you can see writes in the SSE stream
    try:
        ctx.emit("debug", {"type":"slide_written", "file": f"slides/{filename}"})
    except Exception:
        pass
    return (str(fs_path), url)

# ---- public ops ----

def html_render(ctx, project_id: str, outline: List[Dict[str, Any]],
                theme: str = "academic-ar",
                images: Optional[List[Any]] = None,
                language: str = "ar") -> Dict[str, Any]:
    """
    Builds slides HTML, writes state.json as the canonical deck state,
    and emits slide_generated events with template & URLs.
    """
    # Map any input images to project-relative paths and store those in state
    img_rel_list: List[str] = []
    for raw in (images or []):
        url = _as_url(ctx, raw)                   # -> /artifacts/<prj>/images/...
        rel = _to_project_rel(url, project_id)    # -> images/...
        if rel:
            img_rel_list.append(rel)

    # Compose state from outline
    slides_state: List[Dict[str, Any]] = []
    for idx, src in enumerate(outline or [], start=1):
        template = (src.get("template") or src.get("kind") or ("cover" if idx == 1 else "text_image_right"))
        title = src.get("title") or f"Slide {idx}"
        subtitle = src.get("subtitle") or ""
        bullets = src.get("bullets") or []
        img_rel = img_rel_list[(idx - 1) % len(img_rel_list)] if img_rel_list else None

        slides_state.append({
            "no": idx,
            "template": template,
            "title": title,
            "subtitle": subtitle,
            "bullets": bullets,
            "image": img_rel,   # store project-relative (templates will make it ../images/..)
            "notes": src.get("notes", "")
        })

    # Full state.json
    title0 = (slides_state[0]["title"] if slides_state else "Presentation")
    state = {
        "project_id": project_id,
        "language": language or "ar",
        "title": title0,
        "slides": slides_state,
        "created_at": _now_ts(),
        "updated_at": _now_ts(),
    }
    _write_state(ctx, state)

    # Render each slide and emit events
    slides_urls: List[str] = []
    for slide in slides_state:
        fs_path, url = _render_one(ctx, project_id, slide, state["language"])
        slides_urls.append(url)
        try:
            ctx.emit("partial", {
                "type": "slide_generated",
                "no": slide["no"],
                "template": slide["template"],
                "title": slide["title"],
                "path": url,
                "code_path": url,
                "preview_url": url
            })
        except Exception:
            pass

    return {
        "slides_html": slides_urls,
        "count": len(slides_urls),
        "state_url": ctx.url_for("state.json"),
    }

# Permissions expected by ToolRouter
html_render.required_permissions = {"fs_write", "fs_read"}

def update_one(ctx, project_id: str, slide_no: int, patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Patch slide N in state.json then re-render only that slide.
    patch may include: title, subtitle, bullets, image, template, notes
    """
    state = _read_state(ctx)
    slides = state.get("slides") or []
    slide = next((s for s in slides if int(s.get("no", -1)) == int(slide_no)), None)
    if not slide:
        from ..errors import ProblemDetails
        raise ProblemDetails(title="Not found", detail=f"slide {slide_no}", code="E_NOT_FOUND", status=404)

    # Apply patch; normalize image to project-relative if provided
    for k in ("title", "subtitle", "bullets", "template", "notes"):
        if k in patch and patch[k] is not None:
            slide[k] = patch[k]
    if "image" in patch and patch["image"] is not None:
        rel = _to_project_rel(_as_url(ctx, patch["image"]), project_id)
        slide["image"] = rel

    _write_state(ctx, state)

    # Re-render one
    lang = state.get("language", "ar")
    fs_path, url = _render_one(ctx, project_id, slide, lang)

    try:
        ctx.emit("partial", {"type": "slide_updated", "no": int(slide_no), "path": url})
    except Exception:
        pass
    return {"path": fs_path, "url": url, "state_url": ctx.url_for("state.json")}

update_one.required_permissions = {"fs_write", "fs_read"}

# from __future__ import annotations
# from typing import Any, Dict, List, Optional, Tuple
# from pathlib import Path
# import time
# import json

# from .slides_templates import render_by_template

# # ---- helpers ----

# def _now_ts() -> int:
#     return int(time.time())

# def _as_url(ctx, maybe_path: Any) -> Optional[str]:
#     """
#     Accepts a path returned by other ops (str/Path) and returns a static URL
#     via ctx.url_for. Returns None if input is falsy.
#     """
#     if not maybe_path:
#         return None
#     try:
#         return ctx.url_for(maybe_path)
#     except Exception:
#         s = str(maybe_path)
#         return s if s.startswith("/") or s.startswith("http") else None

# # --- JSON I/O shims (ExecutionContext doesn't have write_json/read_json) ---

# def _ctx_read_json(ctx, relpath: str) -> Dict[str, Any]:
#     try:
#         txt = ctx.read_text(relpath)
#     except FileNotFoundError:
#         return {}
#     except Exception:
#         # If the file exists but can't be read, treat as empty state (defensive)
#         return {}
#     if not txt:
#         return {}
#     try:
#         return json.loads(txt)
#     except Exception:
#         return {}

# def _ctx_write_json(ctx, relpath: str, obj: Dict[str, Any]) -> None:
#     payload = json.dumps(obj, ensure_ascii=False, indent=2)
#     ctx.write_text(relpath, payload)

# def _read_state(ctx) -> Dict[str, Any]:
#     return _ctx_read_json(ctx, "state.json") or {}

# def _write_state(ctx, state: Dict[str, Any]) -> None:
#     state["updated_at"] = _now_ts()
#     if "created_at" not in state:
#         state["created_at"] = state["updated_at"]
#     _ctx_write_json(ctx, "state.json", state)

# def _render_one(ctx, slide: Dict[str, Any], lang: str, image_url: Optional[str]) -> Tuple[str, str]:
#     """
#     Renders a single slide dict into slides/NNN.html.
#     Returns (filesystem_path_str, static_url_str)
#     """
#     html = render_by_template(slide, lang, image_url)
#     filename = f"{slide['no']:03}.html"
#     fs_path = ctx.write_text(f"slides/{filename}", html)  # returns path-like
#     url = ctx.url_for(fs_path)
#     return (str(fs_path), url)

# # ---- public ops ----

# def html_render(ctx, project_id: str, outline: List[Dict[str, Any]],
#                 theme: str = "academic-ar",
#                 images: Optional[List[Any]] = None,
#                 language: str = "ar") -> Dict[str, Any]:
#     """
#     Builds slides HTML, writes state.json as the canonical deck state,
#     and emits slide_generated events with template & URLs.
#     Signature mirrors existing 'slides.html.render' callsites.
#     """
#     # Prepare images: turn any paths into URLs; we'll store URL in state for FE.
#     img_urls = [u for u in (_as_url(ctx, p) for p in (images or [])) if u] or []

#     # Compose state from outline
#     slides_state: List[Dict[str, Any]] = []
#     for idx, src in enumerate(outline or [], start=1):
#         template = (src.get("template") or src.get("kind") or ("cover" if idx == 1 else "text_image_right"))
#         title = src.get("title") or f"Slide {idx}"
#         subtitle = src.get("subtitle") or ""
#         bullets = src.get("bullets") or []
#         img_url = img_urls[(idx - 1) % len(img_urls)] if img_urls else None

#         slides_state.append({
#             "no": idx,
#             "template": template,
#             "title": title,
#             "subtitle": subtitle,
#             "bullets": bullets,
#             "image": img_url,   # store URL (served statically)
#             "notes": src.get("notes", "")
#         })

#     # Full state.json
#     title0 = (slides_state[0]["title"] if slides_state else "Presentation")
#     state = {
#         "project_id": project_id,
#         "language": language or "ar",
#         "title": title0,
#         "slides": slides_state,
#         "created_at": _now_ts(),
#         "updated_at": _now_ts(),
#     }
#     _write_state(ctx, state)

#     # Render each slide and emit events
#     slides_urls: List[str] = []
#     for slide in slides_state:
#         fs_path, url = _render_one(ctx, slide, state["language"], slide.get("image"))
#         slides_urls.append(url)
#         ctx.emit("partial", {
#             "type": "slide_generated",
#             "no": slide["no"],
#             "template": slide["template"],
#             "title": slide["title"],
#             "path": url,
#             "code_path": url,
#             "preview_url": url
#         })

#     # Return mirror of previous renderer contract
#     return {
#         "slides_html": slides_urls,
#         "count": len(slides_urls),
#         "state_url": ctx.url_for("state.json"),
#     }

# # Permissions expected by ToolRouter
# html_render.required_permissions = {"fs_write", "fs_read"}

# def update_one(ctx, project_id: str, slide_no: int, patch: Dict[str, Any]) -> Dict[str, Any]:
#     """
#     Patch slide N in state.json then re-render only that slide.
#     patch may include: title, subtitle, bullets, image, template, notes
#     """
#     state = _read_state(ctx)
#     slides = state.get("slides") or []
#     slide = next((s for s in slides if int(s.get("no", -1)) == int(slide_no)), None)
#     if not slide:
#         from ..errors import ProblemDetails  # lazy import to avoid hard dep at import time
#         raise ProblemDetails(title="Not found", detail=f"slide {slide_no}", code="E_NOT_FOUND", status=404)

#     # Apply patch
#     for k in ("title", "subtitle", "bullets", "image", "template", "notes"):
#         if k in patch and patch[k] is not None:
#             slide[k] = patch[k]

#     _write_state(ctx, state)

#     # Re-render one
#     lang = state.get("language", "ar")
#     img_url = slide.get("image")
#     fs_path, url = _render_one(ctx, slide, lang, img_url)

#     # Emit update event
#     ctx.emit("partial", {"type": "slide_updated", "no": int(slide_no), "path": url})

#     return {"path": fs_path, "url": url, "state_url": ctx.url_for("state.json")}

# update_one.required_permissions = {"fs_write", "fs_read"}
