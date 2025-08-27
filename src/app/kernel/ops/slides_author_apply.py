from __future__ import annotations
from typing import List, Dict, Any

def apply_outline(ctx, outline: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Turns an outline (title, bullets, note?, image_prompt?, citations?) into your slide model list.
    This is intentionally generic—adjust field names to match your slide model.
    """
    slides = []
    for i, s in enumerate(outline, start=1):
        slides.append({
            "no": i,
            "title": s.get("title", f"Slide {i}"),
            "bullets": s.get("bullets", []),
            "notes": s.get("note", ""),
            "image_prompt": s.get("image_prompt"),
            "citations": s.get("citations", []),
        })
    return {"slides": slides}
apply_outline.required_permissions = set()
