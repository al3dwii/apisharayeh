from __future__ import annotations
import os
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

    # Ensure reasonable bound
    count = max(3, min(30, int(count or 12)))

    # Standard sections
    base_sections = [
        ("cover", topic, None),
        ("index", "فهرس المحتوى" if language == "ar" else "Table of Contents", None),
        ("content", "ما هو " + topic if language == "ar" else f"What is {topic}", None),
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

    # Fit to requested count
    slides = []
    no = 1
    for kind, title, bullets in base_sections:
        slides.append({
            "no": no, "kind": kind, "title": title,
            "subtitle": None,
            "bullets": bullets or [],
            "image": None,
        })
        no += 1
        if len(slides) >= count:
            break

    # Ensure cover + index exist
    if slides[0]["kind"] != "cover":
        slides.insert(0, {"no": 1, "kind": "cover", "title": topic, "subtitle": None, "bullets": [], "image": None})
    for i, s in enumerate(slides, start=1):
        s["no"] = i

    ctx.emit("tool_used", {"name": "slides.outline.from_prompt_stub", "args": {"topic": topic, "language": language, "count": count}})
    return {"outline": slides}


def outline_from_doc(ctx, text: str, language: str = "ar", count: int = 12) -> Dict[str, Any]:
    """
    Turn raw extracted text (from DOCX) into a simple outline.
    Heuristic: first non-empty line -> cover title; headings (lines with length<70) -> section titles.
    """
    if not text or not isinstance(text, str):
        raise ProblemDetails(title="Invalid input", detail="text is required", code="E_VALIDATION", status=400)

    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]  # drop empties

    cover_title = lines[0] if lines else ("عرض تقديمي" if language == "ar" else "Presentation")
    sections: List[str] = []
    for ln in lines[1:]:
        if len(ln) <= 70 and ln[0].isalpha():
            sections.append(ln)
        if len(sections) >= (count - 2):
            break

    slides = [{"no": 1, "kind": "cover", "title": cover_title, "subtitle": None, "bullets": [], "image": None}]
    slides.append({"no": 2, "kind": "index", "title": "فهرس المحتوى" if language == "ar" else "Table of Contents",
                   "subtitle": None, "bullets": sections[:8], "image": None})
    no = 3
    for sec in sections:
        slides.append({"no": no, "kind": "content", "title": sec, "subtitle": None, "bullets": [], "image": None})
        no += 1
        if len(slides) >= count:
            break
    for i, s in enumerate(slides, start=1):
        s["no"] = i

    ctx.emit("tool_used", {"name": "slides.outline.from_doc", "args": {"language": language, "count": count}})
    return {"outline": slides}


# ... keep imports and other functions ...

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
  html,body {{ margin:0; padding:0; height:100%; background:var(--bg); color:var(--fg); font-family: system-ui, -apple-system, Segoe UI, Roboto, "Tajawal", sans-serif; }}
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
  /* Use logical property so it works for RTL and LTR without template vars */
  .bullets {{
    margin: 12px 0 0 0;
    padding-inline-start: 1.2em;
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

def _render_slide_html(slide: Dict[str, Any], lang: str, img_path: Optional[str]) -> str:
    dir_attr = "rtl" if lang == "ar" else "ltr"
    subtitle_html = f'<h2 class="subtitle">{slide.get("subtitle") or ""}</h2>' if slide.get("subtitle") else ""
    bullets = slide.get("bullets") or []
    if bullets:
        items = "".join(f"<li>{b}</li>" for b in bullets[:10])
        bullets_html = f'<ul class="bullets">{items}</ul>'
    else:
        bullets_html = ""
    image_html = f'<div class="imgwrap card"><img src="{img_path}" alt=""></div>' if img_path else ""
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

    slides_dir = ctx.artifacts_dir("slides")
    html_paths: List[str] = []
    images = images or []

    # language heuristics from first slide title script
    lang = "ar" if outline and any("\u0600" <= ch <= "\u06FF" for ch in outline[0]["title"]) else "en"

    for idx, slide in enumerate(outline, start=1):
        img = images[(idx - 1) % len(images)] if images else None
        # Make image path relative to artifacts web root if we stored it inside project
        img_rel = None
        if img:
            img_rel = Path(img)
            try:
                from .. import storage
                rel = img_rel.resolve().relative_to(storage.ARTIFACTS_BASE)
                img_rel = f"/artifacts/{rel.as_posix()}"
            except Exception:
                img_rel = img_rel.as_posix()

        html = _render_slide_html(slide, lang, img_rel)
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


def export_pdf(ctx, project_id: str, slides_html: List[str]) -> Dict[str, Any]:
    """
    Stub PDF exporter (valid PDF header + note). Writes /export/presentation.pdf
    """
    if "fs_write" not in ctx.permissions:
        raise ProblemDetails(title="Permission denied", detail="fs_write is required", code="E_PERM", status=403)

    # Minimal valid-ish PDF structure (not a real rendering)
    pdf_bytes = b"%PDF-1.4\n%DEV-STUB\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>endobj\n4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 72 720 Td (Slides exported in DEV stub) Tj ET\nendstream endobj\nxref\n0 5\n0000000000 65535 f \n0000000015 00000 n \n0000000068 00000 n \n0000000129 00000 n \n0000000235 00000 n \ntrailer<</Size 5/Root 1 0 R>>\nstartxref\n330\n%%EOF\n"
    path = ctx.write_bytes("export/presentation.pdf", pdf_bytes)
    url = ctx.url_for(path)
    ctx.emit("tool_used", {"name": "slides.export.pdf", "args": {"engine": "stub"}})
    ctx.emit("artifact_ready", {"kind": "pdf", "url": url})
    return {"pdf_url": url}


def export_pptx_stub(ctx, project_id: str) -> Dict[str, Any]:
    """
    Tiny placeholder PPTX file for DEV.
    """
    if "fs_write" not in ctx.permissions:
        raise ProblemDetails(title="Permission denied", detail="fs_write is required", code="E_PERM", status=403)
    data = b"PK\x03\x04PPTX-STUB"
    path = ctx.write_bytes("export/out.pptx", data)
    url = ctx.url_for(path)
    ctx.emit("tool_used", {"name": "slides.export.pptx_stub", "args": {}})
    ctx.emit("artifact_ready", {"kind": "pptx", "url": url})
    return {"pptx_url": url}


# Permission annotations for ToolRouter
outline_from_prompt_stub.required_permissions = set()          # no FS writes
outline_from_doc.required_permissions = set()
render_html.required_permissions = RENDER_PERMS
export_pdf.required_permissions = EXPORT_PERMS
export_pptx_stub.required_permissions = EXPORT_PERMS
