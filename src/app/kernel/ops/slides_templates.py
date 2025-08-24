from __future__ import annotations
from typing import Dict, Any, Optional, List
import html

# ---------- utilities ----------

def _esc(s: Optional[str]) -> str:
    return html.escape(s or "", quote=True)

def _bullets_html(items: Optional[List[str]]) -> str:
    items = items or []
    li = "\n".join(f"<li>{_esc(x)}</li>" for x in items if (x or "").strip())
    return f"<ul class='bullets'>{li}</ul>"

def _dir_for(lang: str) -> str:
    l = (lang or "ar").lower()
    return "rtl" if l in ("ar", "fa", "ur", "he", "ps") else "ltr"

def _font_stack() -> str:
    # System stacks that look good in both Latin & Arabic
    return (
        "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, "
        "'Noto Kufi Arabic', 'Noto Sans Arabic', 'Amiri', 'Dubai', 'Cairo', "
        "'Segoe UI Variable', 'SF Pro', sans-serif"
    )

def _base_css() -> str:
    return f"""
    :root {{
      --bg: #0b0c10;
      --panel: #111421;
      --ink: #e8ecff;
      --ink-dim: #a8b2d8;
      --accent: #6ea8ff;
      --muted: #22263a;
      --card: #0e1220;
      --border: #1c2340;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      width: 100%; height: 100%;
      margin: 0; padding: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: {_font_stack()};
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }}
    .slide {{
      width: 100vw; height: 100vh;
      padding: 48px;
      display: flex;
      flex-direction: column;
      gap: 24px;
    }}
    .title {{
      font-size: clamp(28px, 5.5vw, 64px);
      line-height: 1.15;
      margin: 0;
      letter-spacing: .2px;
    }}
    .subtitle {{
      font-size: clamp(16px, 2.4vw, 28px);
      color: var(--ink-dim);
      margin: 4px 0 0 0;
    }}
    .meta {{
      margin-top: auto;
      color: var(--ink-dim);
      font-size: 14px;
    }}
    .card {{
      background: linear-gradient(180deg, var(--panel), var(--card));
      border: 1px solid var(--border);
      border-radius: 16px;
      overflow: hidden;
      box-shadow: 0 10px 30px rgba(0,0,0,0.35);
    }}
    .two-col {{
      display: grid;
      grid-template-columns: 1.15fr 1fr;
      gap: 24px;
      align-items: stretch;
      height: 100%;
      min-height: 0;
    }}
    .side {{
      padding: 28px 28px;
    }}
    .bullets {{
      margin: 0; padding: 0 1.2em;
      font-size: clamp(16px, 1.9vw, 24px);
      line-height: 1.45;
    }}
    .bullets > li {{
      margin: .25em 0 .45em;
    }}
    .hero {{
      position: relative;
      display: flex; align-items: center; justify-content: center;
      min-height: 42vh;
      border-bottom: 1px solid var(--border);
      background: radial-gradient(60% 80% at 70% 10%, rgba(110,168,255,.15), transparent),
                  radial-gradient(60% 80% at 20% 90%, rgba(110,168,255,.09), transparent);
    }}
    .hero img {{
      max-width: 100%;
      max-height: 60vh;
      object-fit: cover;
      display: block;
      border-radius: 12px;
      border: 1px solid var(--border);
    }}
    .badge {{
      display: inline-block;
      font-size: 12px;
      padding: 6px 10px;
      border: 1px solid var(--border);
      border-radius: 999px;
      color: var(--ink-dim);
      background: rgba(255,255,255,0.02);
    }}
    @media (max-width: 900px) {{
      .slide {{ padding: 28px; }}
      .two-col {{
        grid-template-columns: 1fr;
      }}
      .hero img {{ max-height: 40vh; }}
    }}
    """

# ---------- templates ----------

def render_cover(slide: Dict[str, Any], lang: str, img_url: Optional[str]) -> str:
    title = _esc(slide.get("title") or "")
    subtitle = _esc(slide.get("subtitle") or "")
    direction = _dir_for(lang)
    img = f"<img alt='' src='{_esc(img_url)}'/>" if img_url else ""
    return f"""<!doctype html>
<html lang="{_esc(lang or 'ar')}" dir="{direction}">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>{_base_css()}
.cover {{
  display: grid; grid-template-rows: auto 1fr auto; height: 100%;
}}
.tagline {{ display:flex; gap:10px; align-items:center; color:var(--ink-dim); }}
</style>
</head>
<body>
  <section class="slide cover">
    <header>
      <span class="badge">العرض التقديمي</span>
      <h1 class="title">{title}</h1>
      {f'<p class="subtitle">{subtitle}</p>' if subtitle else ''}
    </header>
    <main class="hero card">{img}</main>
    <footer class="meta">تم الإنشاء عبر النظام — قالب غلاف</footer>
  </section>
</body>
</html>"""

def render_text_image_right(slide: Dict[str, Any], lang: str, img_url: Optional[str]) -> str:
    title = _esc(slide.get("title") or "")
    subtitle = _esc(slide.get("subtitle") or "")
    bullets_html = _bullets_html(slide.get("bullets"))
    direction = _dir_for(lang)
    img = f"<img alt='' src='{_esc(img_url)}'/>" if img_url else ""
    return f"""<!doctype html>
<html lang="{_esc(lang or 'ar')}" dir="{direction}">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>{_base_css()}
.rightimg .side.image {{ padding: 0; display:flex; align-items:center; justify-content:center; }}
.rightimg .side.content h1 {{ margin: 0 0 6px 0; }}
.rightimg .side.content .subtitle {{ margin-bottom: 18px; }}
</style>
</head>
<body>
  <section class="slide rightimg">
    <div class="two-col">
      <div class="side content card">
        <h1 class="title">{title}</h1>
        {f'<p class="subtitle">{subtitle}</p>' if subtitle else ''}
        {bullets_html}
      </div>
      <div class="side image card">
        {img or '<div style="opacity:.7;color:var(--ink-dim);padding:18px;">(لا توجد صورة)</div>'}
      </div>
    </div>
  </section>
</body>
</html>"""

# Registry that maps template → renderer
REGISTRY = {
    "cover": render_cover,
    "text_image_right": render_text_image_right,
    # future: "text_only", "image_full", etc.
}

def render_by_template(slide: Dict[str, Any], lang: str, img_url: Optional[str]) -> str:
    key = slide.get("template") or slide.get("kind") or ("cover" if int(slide.get("no", 1)) == 1 else "text_image_right")
    fn = REGISTRY.get(key, render_text_image_right)
    return fn(slide, lang, img_url)
