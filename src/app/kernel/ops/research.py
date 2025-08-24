# src/app/kernel/ops/research.py
from __future__ import annotations
from typing import Dict, Any, List

from ..errors import ProblemDetails
from . import vision as vision_ops  # offline-safe images


def deep_thinking_plan(ctx, topic: str, slides_count: int = 5) -> Dict[str, Any]:
    """
    Emits 'Using Tool' + TODO checklist + a narration bubble to drive the left panel.
    """
    if not topic or not isinstance(topic, str):
        raise ProblemDetails(title="Invalid input", detail="topic required", code="E_VALIDATION", status=400)

    todos = [
        ("t1", f"جمع المعلومات الأولية حول {topic}"),
        ("t2", f"البحث في اتجاهات وتطورات {topic}"),
        ("t3", f"جمع معلومات حول فوائد ومزايا {topic}"),
        ("t4", f"دراسة التحديات والعقبات في {topic}"),
        ("t5", f"البحث عن أدوات ومنصات {topic}"),
        ("t6", f"إنشاء العرض التقديمي بـ {slides_count} شرائح"),
    ]

    ctx.emit("partial", {
        "type": "using_tool.start",
        "tool": "Deep Thinking",
        "label": "تخطيط المهمة",
        "meta": {"total_todos": len(todos)}
    })
    ctx.emit("partial", {
        "type": "todos.init",
        "items": [{"id": i, "text": t, "done": False} for i, t in todos]
    })
    ctx.emit("partial", {
        "type": "narration",
        "text": f"سأبدأ بجمع المعلومات اللازمة حول {topic} لإنشاء عرض تقديمي شامل."
    })
    ctx.emit("partial", {"type": "todos.progress", "done_ids": ["t1"], "remaining": len(todos) - 1})
    ctx.emit("partial", {"type": "using_tool.end", "tool": "Deep Thinking", "meta": {"completed": True}})
    return {"todos": [i for i, _ in todos]}


def parallel_search(ctx, queries: List[str]) -> Dict[str, Any]:
    """
    Announces a parallel search step and emits empty buckets (offline-safe).
    """
    queries = list(queries or [])
    ctx.emit("partial", {
        "type": "using_tool.start",
        "tool": "Parallel Search",
        "label": "بحث موازي",
        "meta": {"queries": queries}
    })
    for q in queries:
        ctx.emit("partial", {"type": "search.results", "query": q, "items": []})
    ctx.emit("partial", {"type": "using_tool.end", "tool": "Parallel Search", "meta": {"completed": True}})
    return {"ok": True}


def read_url(ctx, url: str) -> Dict[str, Any]:
    """
    Emits a 'Read' block with placeholder excerpt (can be replaced later).
    """
    if not url or not isinstance(url, str):
        raise ProblemDetails(title="Invalid input", detail="url required", code="E_VALIDATION", status=400)
    ctx.emit("partial", {"type": "using_tool.start", "tool": "Read", "label": "قراءة مصدر"})
    ctx.emit("partial", {
        "type": "read.snippet",
        "url": url,
        "title": "مصدر خارجي",
        "excerpt": "ملخص قصير سيتم استبداله عند تفعيل القراءة الحقيقية."
    })
    ctx.emit("partial", {"type": "using_tool.end", "tool": "Read", "meta": {"completed": True}})
    return {"text": ""}


def image_search(ctx, query: str, project_id: str | None = None) -> Dict[str, Any]:
    """
    Offline-safe image search via local fixtures; emits image URLs for the UI.
    """
    if not project_id:
        project_id = ctx.project_id
    res = vision_ops.images_from_fixtures(ctx, queries=[query], project_id=project_id)
    paths = res.get("images", [])[:9]
    ctx.emit("partial", {"type": "using_tool.start", "tool": "Image Search", "label": "بحث صور"})
    ctx.emit("partial", {"type": "images.results", "query": query, "images": [ctx.url_for(p) for p in paths]})
    ctx.emit("partial", {"type": "using_tool.end", "tool": "Image Search", "meta": {"completed": True}})
    return {"images": paths}


# ---- permission annotations (used by ToolRouter) ----
deep_thinking_plan.required_permissions = {"fs_write"}
parallel_search.required_permissions = {"fs_write"}
read_url.required_permissions = {"fs_write"}
image_search.required_permissions = {"fs_write", "fs_read"}
