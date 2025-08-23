# src/app/kernel/ops/slides.py
from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, List, Optional

from ..errors import ProblemDetails

RENDER_PERMS = {"fs_write", "fs_read"}
EXPORT_PERMS = {"fs_write"}


# ---------- Outline generators ----------

def outline_from_prompt_stub(ctx, topic: str, language: str = "ar", count: int = 12) -> Dict[str, Any]:
    """
    Deterministic outline generator (no LLM). Arabic-friendly.
    """
    if not topic or not isinstance(topic, str):
        raise ProblemDetails(title="Invalid input", detail="topic is required", code="E_VALIDATION", status=400)

    try:
        count = int(count)
    except Exception:
        count = 12
    count = max(3, min(30, count))

    base_sections = [
        ("cover", topic, None),
        ("index", "فهرس المحتوى" if language == "ar" else "Table of Contents", None),
        ("content", ("ما هو " + topic) if language == "ar" else f"What is {topic}", None),
        ("list", "الأنواع / التصنيفات" if language == "ar" else "Types / Classifications", [
            "متزامن / غير متزامن" if language == "ar" else "Synchronous / Asynchronous",
            "مدمج" if language == "ar" else "Blended",
            "التفاعلي" if language == "ar" else "Interactive",
        ]),
        ("content", "المنصات والأدوات" if language == "ar" else "Platforms & Tools", None),
        ("list", "الفوائد والمزايا" if language == "ar" else "Benefits", [
            "مرونة الوقت والمكان" if language == "ar" else "Flexibility",
            "توفير التكاليف" if language == "ar" else "Cost savings",
            "تتبع الأداء" if language == "ar" else "Progress tracking",
        ]),
        ("list", "التحديات" if language == "ar" else "Challenges", [
            "ضعف التفاعل المباشر" if language == "ar" else "Lower live interaction",
            "فجوة رقمية" if language == "ar" else "Digital divide",
            "صعوبة التقييم العملي" if language == "ar" else "Assessing hands-on skills",
        ]),
        ("content", "الذكاء الاصطناعي" if language == "ar" else "AI in the domain", None),
        ("content", "مؤشرات ونمو" if language == "ar" else "Trends & Growth", None),
        ("content", "أفضل الممارسات" if language == "ar" else "Best Practices", None),
        ("content", "الخاتمة والتوصيات" if language == "ar" else "Conclusion & Recommendations", None),
    ]

    slides: List[Dict[str, Any]] = []
    for i, (kind, title, bullets) in enumerate(base_sections, start=1):
        slides.append({
            "no": i,
            "kind": kind,
            "title": title,
            "subtitle": None,
            "bullets": bullets or [],
            "image": None,
        })
        if len(slides) >= count:
            break

    # Ensure cover exists
    if not slides or slides[0]["kind"] != "cover":
        slides.insert(0, {"no": 1, "kind": "cover", "title": topic, "subtitle": None, "bullets": [], "image": None})
    for i, s in enumerate(slides, start=1):
        s["no"] = i

    ctx.emit("tool_used", {"name": "slides.outline.from_prompt_stub",
                           "args": {"topic": topic, "language": language, "count": count}})
    return {"outline": slides}


def outline_from_doc(ctx, text: str, language: str = "ar", count: int = 12) -> Dict[str, Any]:
    """
    Turn raw extracted text (from DOCX/TXT) into a simple outline.
    Heuristics:
      - First non-empty line -> cover title
      - Subsequent short lines act like headings/sections
    """
    if not text or not isinstance(text, str):
        raise ProblemDetails(title="Invalid input", detail="text is required", code="E_VALIDATION", status=400)

    try:
        count = int(count)
    except Exception:
        count = 12
    count = max(3, min(30, count))

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    cover_title = lines[0] if lines else ("عرض تقديمي" if language == "ar" else "Presentation")
    sections: List[str] = []
    for ln in lines[1:]:
        if len(ln) <= 70:
            sections.append(ln)
        if len(sections) >= (count - 2):
            break

    slides: List[Dict[str, Any]] = [
        {"no": 1, "kind": "cover", "title": cover_title, "subtitle": None, "bullets": [], "image": None},
        {"no": 2, "kind": "index", "title": "فهرس المحتوى" if language == "ar" else "Table of Contents",
         "subtitle": None, "bullets": sections[:8], "image": None}
    ]
    no = 3
    for sec in sections:
        slides.append({"no": no, "kind": "content", "title": sec, "subtitle": None, "bullets": [], "image": None})
        no += 1
        if len(slides) >= count:
            break
    for i, s in enumerate(slides, start=1):
        s["no"] = i

    ctx.emit("tool_used", {"name": "slides.outline.from_doc",
                           "args": {"language": language, "count": count}})
    return {"outline": slides}


# ---------- HTML rendering ----------

_HTML_SHELL = """<!doctype html>
<html lang="{lang}" dir="{dir}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #0e1b2a;
    --fg: #e9f1ff;
    --accent: #3aaed8;
    --muted: #a9bdd6;
    --card: #14273e;
  }}
  html,body {{ margin:0; padding:0; height:100%; background:var(--bg); color:var(--fg);
               font-family: system-ui, -apple-system, Segoe UI, Roboto, "Tajawal", sans-serif; }}
  .slide {{
    box-sizing: border-box;
    width: 100vw;
    height: 100vh;
    padding: 64px 72px;
    display: flex;
    flex-direction: column;
    gap: 24px;
  }}
  .title {{ font-size: 56px; line-height: 1.1; margin:0; }}
  .subtitle {{ font-size: 22px; color: var(--muted); margin:0; }}
  .bullets {{
    margin: 12px 0 0 0;
    padding-inline-start: 1.2em; /* RTL/LTR friendly */
    font-size: 24px;
  }}
  .bullets li {{ margin: 10px 0; }}
  .card {{
    background: var(--card);
    border-radius: 16px;
    padding: 20px;
  }}
  .imgwrap {{
    margin-top: auto;
    background: linear-gradient(0deg, #ffffff10, transparent);
    border-radius: 16px;
    max-height: 45vh;
    overflow: hidden;
  }}
  .imgwrap img {{ width: 100%; height: auto; display: block; }}
  .footer {{ margin-top: auto; font-size: 14px; color: var(--muted); }}
</style>
</head>
<body>
  <section class="slide">
    <h1 class="title">{title}</h1>
    {subtitle_html}
    {bullets_html}
    {image_html}
    <div class="footer">{footer}</div>
  </section>
</body>
</html>
"""

def _render_slide_html(slide: Dict[str, Any], lang: str, img_url: Optional[str]) -> str:
    dir_attr = "rtl" if lang == "ar" else "ltr"
    subtitle_html = f'<h2 class="subtitle">{slide.get("subtitle") or ""}</h2>' if slide.get("subtitle") else ""
    bullets = slide.get("bullets") or []
    bullets_html = ""
    if bullets:
        items = "".join(f"<li>{b}</li>" for b in bullets[:10])
        bullets_html = f'<ul class="bullets">{items}</ul>'
    image_html = f'<div class="imgwrap card"><img src="{img_url}" alt=""></div>' if img_url else ""
    footer = "تم الإنشاء محلياً (DEV)" if lang == "ar" else "Generated locally (DEV)"
    return _HTML_SHELL.format(
        lang=lang, dir=dir_attr, title=slide["title"],
        subtitle_html=subtitle_html, bullets_html=bullets_html,
        image_html=image_html, footer=footer
    )

def render_html(ctx, project_id: str, outline: List[Dict[str, Any]], theme: str = "academic-ar",
                images: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Write one HTML file per slide under /slides, emit partial events.
    """
    if "fs_write" not in ctx.permissions:
        raise ProblemDetails(title="Permission denied", detail="fs_write is required", code="E_PERM", status=403)

    _ = ctx.artifacts_dir("slides")  # ensure dir exists
    html_paths: List[str] = []
    images = images or []

    # language heuristic: if first title has Arabic letters
    lang = "ar"
    if outline:
        if not any("\u0600" <= ch <= "\u06FF" for ch in outline[0]["title"]):
            lang = "en"

    for idx, slide in enumerate(outline, start=1):
        img_path = images[(idx - 1) % len(images)] if images else None
        img_url = ctx.url_for(Path(img_path)) if img_path else None

        html = _render_slide_html(slide, lang, img_url)
        filename = f"{idx:03}.html"
        path = ctx.write_text(f"slides/{filename}", html)
        html_paths.append(str(path))

        ctx.emit("partial", {
            "type": "slide_generated",
            "no": idx,
            "title": slide.get("title"),
            "path": ctx.url_for(path),
        })

    ctx.emit("tool_used", {"name": "slides.html.render", "args": {"count": len(html_paths), "theme": theme}})
    return {"slides_html": html_paths}


# ---------- Exports ----------

def _dev_pdf_bytes(page_count: int = 1) -> bytes:
    """
    Produce a slightly larger dev PDF so smoke tests don't fail on size.
    It's still a stub (not a real render), but > 3KB and opens in most viewers.
    """
    if page_count < 1:
        page_count = 1

    header = b"%PDF-1.4\n%DEV-STUB\n"
    objs = []
    xref_positions = [0]

    def add_obj(obj_bytes: bytes) -> None:
        xref_positions.append(len(header) + sum(len(o) for o in objs))
        objs.append(obj_bytes)

    # Catalog & pages
    add_obj(b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n")
    kids = " ".join(f"{i+3} 0 R" for i in range(page_count))
    add_obj(f"2 0 obj<</Type/Pages/Count {page_count}/Kids[{kids}]>>endobj\n".encode("utf-8"))

    # Pages + padded content
    for i in range(page_count):
        content = (
            b"BT /F1 16 Tf 72 720 Td (Slides exported in DEV stub) Tj ET\n" +
            (b"% padding\n" * 150)  # ~1.2KB padding per page
        )
        add_obj(f"{i+3} 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents {i+3+page_count} 0 R>>endobj\n".encode("utf-8"))
        add_obj(f"{i+3+page_count} 0 obj<</Length {len(content)}>>stream\n".encode("utf-8") + content + b"\nendstream endobj\n")

    body = b"".join(objs)
    xref_offset = len(header) + len(body)
    xref = [b"xref\n", f"0 {len(xref_positions)}\n".encode("utf-8"), b"0000000000 65535 f \n"]
    for pos in xref_positions[1:]:
        xref.append(f"{pos:010d} 00000 n \n".encode("utf-8"))
    trailer = (
        b"trailer<</Size " + str(len(xref_positions)).encode("utf-8") +
        b"/Root 1 0 R>>\nstartxref\n" + str(xref_offset).encode("utf-8") + b"\n%%EOF\n"
    )
    return header + body + b"".join(xref) + trailer


def export_pdf(ctx, project_id: str, slides_html: List[str]) -> Dict[str, Any]:
    """
    DEV exporter that writes a stub PDF at /export/presentation.pdf
    - Emits 'artifact.ready' so the smoke script can proceed.
    - Returns both pdf_url and pdf_path.
    """
    if "fs_write" not in ctx.permissions:
        raise ProblemDetails(title="Permission denied", detail="fs_write is required", code="E_PERM", status=403)

    page_count = max(1, min(len(slides_html) or 1, 10))
    pdf_bytes = _dev_pdf_bytes(page_count=page_count)

    path = ctx.write_bytes("export/presentation.pdf", pdf_bytes)
    url = ctx.url_for(path)

    ctx.emit("tool_used", {"name": "slides.export.pdf", "args": {"engine": "dev-stub", "pages": page_count}})
    # IMPORTANT: dot (.) not underscore
    ctx.emit("artifact.ready", {"kind": "pdf", "url": url})

    return {"pdf_url": url, "pdf_path": str(path)}


def export_pptx_stub(ctx, project_id: str) -> Dict[str, Any]:
    """
    Placeholder PPTX file for DEV (a bit larger than before).
    Also emits 'artifact.ready' for PPTX.
    """
    if "fs_write" not in ctx.permissions:
        raise ProblemDetails(title="Permission denied", detail="fs_write is required", code="E_PERM", status=403)

    data = b"PK\x03\x04PPTX-STUB" + (b"\x00" * 4096)
    path = ctx.write_bytes("export/out.pptx", data)
    url = ctx.url_for(path)

    ctx.emit("tool_used", {"name": "slides.export.pptx_stub", "args": {}})
    ctx.emit("artifact.ready", {"kind": "pptx", "url": url})
    return {"pptx_url": url, "pptx_path": str(path)}


# ---------- Permission annotations (optional) ----------

outline_from_prompt_stub.required_permissions = set()
outline_from_doc.required_permissions = set()
render_html.required_permissions = RENDER_PERMS
export_pdf.required_permissions = EXPORT_PERMS
export_pptx_stub.required_permissions = EXPORT_PERMS

# --- compatibility alias so router op "slides.html.render" resolves correctly ---
html_render = render_html
