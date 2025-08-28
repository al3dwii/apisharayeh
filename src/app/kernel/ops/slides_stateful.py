# src/app/kernel/ops/slides_stateful.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import time
import json
import os
from pathlib import Path
import posixpath  # POSIX-style paths in HTML

from .slides_templates import render_by_template

from typing import Dict, Any, List, Tuple, Set


# ────────────────────────────────────────────────────────────────────────────────
# small utils
# ────────────────────────────────────────────────────────────────────────────────

def _now_ts() -> int:
    return int(time.time())

def _s(maybe: Any) -> Optional[str]:
    """Return a plain string for Path/URL-ish objects; None stays None."""
    if maybe is None:
        return None
    if isinstance(maybe, Path):
        return maybe.as_posix()
    return str(maybe)


# ────────────────────────────────────────────────────────────────────────────────
# project / storage helpers
# ────────────────────────────────────────────────────────────────────────────────

def _project_base(ctx, project_id: str) -> Path:
    """
    Resolve the filesystem base for a given project.
    Prefer context-backed artifacts dir (ctx.artifacts_dir), then ctx.storage.root,
    and finally local ./artifacts/<project_id> as a dev fallback.
    """
    # Best: explicit artifacts directory provided by the context
    try:
        # ctx.artifacts_dir() should resolve to <ARTIFACTS_ROOT>/<project_id>
        p = Path(ctx.artifacts_dir()).resolve()  # type: ignore[attr-defined]
        return p
    except Exception:
        pass

    # Next best: storage root on the context (older code paths)
    storage = getattr(ctx, "storage", None)
    if storage and getattr(storage, "root", None):
        return Path(storage.root) / project_id

    # Last resort (dev)
    return Path("artifacts") / project_id

def _state_candidates(ctx, project_id: str) -> List[Path]:
    """
    All plausible locations for state.json (first hit wins).
    """
    base = _project_base(ctx, project_id)
    return [
        base / "state.json",
        base / "slides" / "state.json",
    ]

def _read_json_from(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            txt = path.read_text(encoding="utf-8")
            if not txt:
                return {}
            return json.loads(txt)
        return {}
    except Exception:
        return {}

def _write_json_to(path: Path, obj: Dict[str, Any]) -> None:
    """
    Atomic JSON write to avoid partial reads.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, ensure_ascii=False, indent=2)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    # best-effort flush before replace
    try:
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
    except Exception:
        pass
    os.replace(tmp, path)  # atomic on same fs

def _state_read(ctx, project_id: str) -> Tuple[Dict[str, Any], Optional[Path]]:
    """
    Load state.json for a project. Returns (state, path_found_or_None).
    Tries concrete filesystem candidates first (under the resolved project base),
    then falls back to context-relative read via ctx.read_text("state.json").
    """
    for p in _state_candidates(ctx, project_id):
        data = _read_json_from(p)
        if data:
            return data, p
    # absolute last fallback: context-relative "state.json"
    try:
        txt = ctx.read_text("state.json")  # type: ignore[attr-defined]
        data = json.loads(txt) if txt else {}
        return data, None
    except Exception:
        return {}, None

def _state_write(ctx, project_id: str, state: Dict[str, Any]) -> Tuple[Path, str]:
    """
    Persist state.json for project, return (fs_path, public_url).
    Always prefers context storage (ctx.write_text + ctx.url_for).
    """
    # keep timestamps sane
    now = _now_ts()
    state["updated_at"] = now
    if "created_at" not in state:
        state["created_at"] = now
    # ensure project_id inside state is set
    if not state.get("project_id"):
        state["project_id"] = project_id

    payload = json.dumps(state, ensure_ascii=False, indent=2)

    # Preferred: context-backed write under <project>/state.json
    try:
        fs_path_str = ctx.write_text("state.json", payload)  # type: ignore[attr-defined]
        fs_path = Path(fs_path_str)
        url = ctx.url_for("state.json")  # type: ignore[attr-defined]
        return fs_path, _s(url) or f"/artifacts/{project_id}/state.json"
    except Exception:
        pass

    # Fallback: direct filesystem write into the project base (atomic)
    fs_path = _project_base(ctx, project_id) / "state.json"
    _write_json_to(fs_path, state)
    try:
        url = ctx.url_for("state.json")  # type: ignore[attr-defined]
    except Exception:
        url = f"/artifacts/{project_id}/state.json"
    return fs_path, _s(url) or f"/artifacts/{project_id}/state.json"


# ────────────────────────────────────────────────────────────────────────────────
# URL & image mapping
# ────────────────────────────────────────────────────────────────────────────────

def _as_url(ctx, maybe_path: Any) -> Optional[str]:
    """
    Map a filesystem path (str/Path) or ctx-relative path to a public URL:
      - absolute http(s) URLs are returned as-is
      - '/artifacts/...' URLs are returned as-is
      - anything else is mapped via ctx.url_for (handles absolute FS & ctx-rel)
    Always returns a string URL (never a Path) or None.
    """
    if not maybe_path:
        return None
    s = _s(maybe_path) or ""
    if s.startswith("http://") or s.startswith("https://") or s.startswith("/artifacts/"):
        return s
    try:
        mapped = ctx.url_for(Path(s))  # type: ignore[attr-defined]
        return _s(mapped)
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
    s = _s(url_or_path) or ""

    # Don't normalize external URLs to project-rel
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
    External URLs are returned as-is; project-local paths become '../images/...'
    """
    url = _as_url(ctx, raw)
    if not url:
        return None
    url = _s(url) or ""
    if url.startswith("http://") or url.startswith("https://"):
        return url  # external

    rel = _to_project_rel(url, project_id)  # e.g. images/foo.jpg
    if not rel:
        return None

    # HTML files live under 'slides/'. Make the src relative to that folder.
    # Example: rel='images/foo.jpg' -> '../images/foo.jpg'
    return posixpath.relpath(rel, "slides")


# ────────────────────────────────────────────────────────────────────────────────
# slide HTML writing
# ────────────────────────────────────────────────────────────────────────────────

def _write_slide_file(ctx, project_id: str, filename: str, html: str) -> Tuple[Path, str]:
    """
    Write HTML into <project>/slides/<filename>, preferring context storage.
    Returns (fs_path, public_url)
    """
    rel = f"slides/{filename}"

    # Preferred: context-backed write (already atomic via ctx.write_text)
    try:
        fs_path_str = ctx.write_text(rel, html)  # type: ignore[attr-defined]
        fs_path = Path(fs_path_str)
        url = ctx.url_for(rel)  # type: ignore[attr-defined]
        return fs_path, _s(url) or f"/artifacts/{project_id}/{rel}"
    except Exception:
        pass

    # Fallback: direct filesystem write (atomic)
    base = _project_base(ctx, project_id) / "slides"
    base.mkdir(parents=True, exist_ok=True)
    fs_path = base / filename

    tmp = fs_path.with_suffix(fs_path.suffix + ".tmp")
    tmp.write_text(html, encoding="utf-8")
    try:
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
    except Exception:
        pass
    os.replace(tmp, fs_path)

    try:
        url = ctx.url_for(rel)  # type: ignore[attr-defined]
    except Exception:
        url = f"/artifacts/{project_id}/{rel}"
    return fs_path, _s(url) or f"/artifacts/{project_id}/{rel}"

def _render_one(ctx, project_id: str, slide: Dict[str, Any], lang: str) -> Tuple[str, str]:
    """
    Renders a single slide dict into slides/NNN.html.
    Returns (filesystem_path_str, static_url_str)
    """
    img_src = _img_src_for_template(ctx, project_id, slide.get("image"))
    html = render_by_template(slide, lang, img_src)
    filename = f"{slide['no']:03}.html"
    fs_path, url = _write_slide_file(ctx, project_id, filename, html)
    try:
        ctx.emit("debug", {"type": "slide_written", "file": f"slides/{filename}"})  # type: ignore[attr-defined]
    except Exception:
        pass
    return (_s(fs_path) or str(fs_path), _s(url) or str(url))



def apply_ops(ctx, project_id: str, ops: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Apply a sequence of slide edits atomically, then re-render affected slides.
    Supported ops:
      - {"op":"replace","no":int,"patch":{title?,subtitle?,bullets?,image?,citations?,template?,notes?}}
      - {"op":"move","frm":int,"to":int}
      - {"op":"insert","at":int,"slide":{kind?,title?,subtitle?,bullets?,image?,citations?,template?,notes?}}
      - {"op":"delete","no":int}
    Returns: {"state_url": str, "updated": [{"no": int, "url": str}]}
    """
    state, _ = _state_read(ctx, project_id)
    slides: List[Dict[str, Any]] = list(state.get("slides") or [])
    if not isinstance(slides, list):
        raise ValueError("state.slides is not a list")

    allowed_keys = {"title","subtitle","bullets","image","citations","template","notes","kind"}
    affected: Set[int] = set()

    def _coerce_no(n: Any) -> int:
        try:
            n = int(n)
        except Exception:
            raise ValueError("slide number must be int")
        if n < 1 or n > (len(slides) if slides else 1_000_000):
            # allow insert past end; others must be in-range
            pass
        return n

    for op in ops:
        kind = (op.get("op") or "").lower()
        if kind == "replace":
            no = _coerce_no(op.get("no"))
            idx = no - 1
            if idx < 0 or idx >= len(slides):
                raise ValueError(f"replace: slide {no} not found")
            patch = dict(op.get("patch") or {})
            for k in list(patch.keys()):
                if k not in allowed_keys:
                    patch.pop(k, None)
            # merge
            for k, v in patch.items():
                slides[idx][k] = v
            affected.add(no)

        elif kind == "move":
            frm = _coerce_no(op.get("frm"))
            to = _coerce_no(op.get("to"))
            fidx = frm - 1
            tidx = max(0, min(to - 1, len(slides) - 1))
            if fidx < 0 or fidx >= len(slides):
                raise ValueError(f"move: slide {frm} not found")
            slide = slides.pop(fidx)
            slides.insert(tidx, slide)
            # everything between frm/to shifts; re-render both endpoints at least
            affected.update({frm, to})

        elif kind == "insert":
            at = _coerce_no(op.get("at") if op.get("at") is not None else len(slides) + 1)
            idx = max(0, min(at - 1, len(slides)))
            new = dict(op.get("slide") or {})
            # sane defaults
            slide = {
                "kind": new.get("kind") or "content",
                "title": new.get("title") or "New slide",
                "subtitle": new.get("subtitle"),
                "bullets": list(new.get("bullets") or []),
                "image": new.get("image"),
                "citations": list(new.get("citations") or []),
                "template": new.get("template") or "default",
                "notes": new.get("notes"),
            }
            slides.insert(idx, slide)
            affected.add(idx + 1)

        elif kind == "delete":
            no = _coerce_no(op.get("no"))
            idx = no - 1
            if idx < 0 or idx >= len(slides):
                raise ValueError(f"delete: slide {no} not found")
            slides.pop(idx)
            # neighbors may change numbers; re-render neighbor too
            affected.update({no, max(1, no - 1)})

        else:
            raise ValueError(f"unsupported op: {kind}")

    # Renumber slides
    for i, s in enumerate(slides, start=1):
        s["no"] = i

    state["slides"] = slides
    fs_path, state_url = _state_write(ctx, project_id, state)  # persist state.json

    # Re-render only affected slides (after renumbering)
    lang = state.get("language") or "en"
    updated = []
    todo_nos = sorted({n for n in affected if 1 <= n <= len(slides)})
    for no in todo_nos:
        slide = slides[no - 1]
        _, url = _render_one(ctx, project_id, slide, lang)
        updated.append({"no": no, "url": _s(url) or url})

    try:
        ctx.emit("partial", {"type": "slides_edited", "updated": updated, "state_url": state_url})  # type: ignore[attr-defined]
    except Exception:
        pass

    return {"state_url": state_url, "updated": updated}

apply_ops.required_permissions = {"fs_read", "fs_write"}


# ────────────────────────────────────────────────────────────────────────────────
# public ops
# ────────────────────────────────────────────────────────────────────────────────

def html_render(
    ctx,
    project_id: str,
    outline: List[Dict[str, Any]],
    theme: str = "academic-ar",
    images: Optional[List[Any]] = None,
    language: str = "ar",
) -> Dict[str, Any]:
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
            "notes": (src.get("notes") or src.get("note") or ""),
        })

    # Full state.json
    title0 = (slides_state[0]["title"] if slides_state else "Presentation")
    state = {
        "project_id": project_id,               # << ensure populated
        "language": language or "ar",
        "theme": theme or "academic-ar",
        "title": title0,
        "slides": slides_state,
        "created_at": _now_ts(),
        "updated_at": _now_ts(),
    }

    state_fs, state_url = _state_write(ctx, project_id, state)
    try:
        ctx.emit("debug", {"type": "state_written", "path": _s(state_fs), "url": state_url})  # type: ignore[attr-defined]
    except Exception:
        pass

    # Render each slide and emit events
    slides_urls: List[str] = []
    for slide in slides_state:
        _fs_path, url = _render_one(ctx, project_id, slide, state["language"])
        slides_urls.append(_s(url) or url)
        try:
            ctx.emit("partial", {
                "type": "slide_generated",
                "no": slide["no"],
                "template": slide["template"],
                "title": slide["title"],
                "path": _s(url) or url,
                "code_path": _s(url) or url,
                "preview_url": _s(url) or url,
            })  # type: ignore[attr-defined]
        except Exception:
            pass

    return {
        "slides_html": slides_urls,
        "count": len(slides_urls),
        "state_url": state_url,
    }

# Permissions expected by ToolRouter
html_render.required_permissions = {"fs_write", "fs_read"}

def update_one(ctx, project_id: str, slide_no: int, patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Patch slide N in state.json then re-render only that slide.
    patch may include: title, subtitle, bullets, image, template, notes
    """
    state, _state_path = _state_read(ctx, project_id)
    slides = state.get("slides") or []

    # Robust coercion: sometimes slide_no comes as str (from YAML)
    try:
        target_no = int(slide_no)
    except Exception:
        target_no = int(str(slide_no).strip() or "0")

    slide = next((s for s in slides if int(s.get("no", -1)) == target_no), None)
    if not slide:
        from ..errors import ProblemDetails
        raise ProblemDetails(title="Not found", detail=f"slide {slide_no}", code="E_NOT_FOUND", status=404)

    # Apply patch; normalize image to project-relative if provided
    if isinstance(patch, dict):
        for k in ("title", "subtitle", "bullets", "template", "notes"):
            if k in patch and patch[k] is not None:
                slide[k] = patch[k]
        # Back-compat: allow 'note' in patch to set 'notes'
        if "note" in patch and patch["note"] is not None:
            slide["notes"] = patch["note"]
        if "image" in patch and patch["image"] is not None:
            rel = _to_project_rel(_as_url(ctx, patch["image"]), project_id)
            slide["image"] = rel

    # Persist state back where we found it (or canonical location)
    state_fs, state_url = _state_write(ctx, project_id, state)

    # Re-render just this slide
    lang = state.get("language", "ar")
    fs_path, url = _render_one(ctx, project_id, slide, lang)

    # Emit richer event including slide payload
    try:
        ctx.emit("partial", {
            "type": "slide_updated",
            "no": target_no,
            "path": _s(url) or url,
            "state_url": state_url,
            "slide": slide,  # ← include the updated slide snapshot
        })  # type: ignore[attr-defined]
    except Exception:
        pass

    return {
        "path": _s(fs_path) or fs_path,
        "url": _s(url) or url,
        "state_url": state_url,
        "slide": slide,  # ← also return it in the op output
    }

update_one.required_permissions = {"fs_write", "fs_read"}

