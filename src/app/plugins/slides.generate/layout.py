
# app/plugins/slides_generate/layout.py
from __future__ import annotations

from typing import List, Dict, Any
from jinja2 import Environment, PackageLoader, select_autoescape

def choose_layout(slide: Dict[str, Any]) -> str:
    bullets = slide.get("bullets") or []
    if slide.get("image"):
        return "title_image"
    if len(bullets) == 0:
        return "title"
    if len(bullets) > 4:
        return "two_col"
    return "list"

def split_bullets_for_two_col(bullets: list[dict]) -> tuple[list[dict], list[dict]]:
    mid = (len(bullets) + 1) // 2
    return bullets[:mid], bullets[mid:]

def choose_layouts_and_render_html(outline: List[Dict[str, Any]], theme: str = "academic", lang: str = "ar") -> List[Dict[str, Any]]:
    env = Environment(
        loader=PackageLoader("app.plugins.slides_generate", "templates"),
        autoescape=select_autoescape(["html"]),
        enable_async=False,
    )
    tpl_map = {
        "title": env.get_template("title.html"),
        "title_image": env.get_template("title_image.html"),
        "list": env.get_template("list.html"),
        "two_col": env.get_template("two_col.html"),
    }

    out = []
    for idx, slide in enumerate(outline, 1):
        layout = slide.get("layout_hint") if slide.get("layout_hint") in tpl_map else choose_layout(slide)
        ctx = {
            "slide": slide,
            "index": idx,
            "lang": lang,
            "dir": "rtl" if slide.get("rtl") else "ltr",
            "theme": theme,
        }
        # extra helpers
        if layout == "two_col":
            left, right = split_bullets_for_two_col(slide.get("bullets") or [])
            ctx["left"] = left
            ctx["right"] = right
        html = tpl_map[layout].render(**ctx)
        out.append({"index": idx, "layout": layout, "html": html})
    return out
