from __future__ import annotations
from typing import Dict, Any, List
from pathlib import Path
import html
import re

# match 001.html, 002.html ...
_SLIDE_RE = re.compile(r"^(\d{3})\.html$", re.IGNORECASE)

def _list_numbered(slides_dir: Path) -> List[str]:
    if not slides_dir.exists():
        return []
    names = []
    for p in sorted(slides_dir.glob("*.html")):
        if p.name.lower() == "index.html":
            continue
        m = _SLIDE_RE.match(p.name)
        if m:
            names.append(p.name)
    return names

def _build_html(project_id: str, slide_files: List[str], title: str = "العرض") -> str:
    # Inline, self-contained viewer with iframe + keyboard nav
    esc = html.escape
    items = "\n".join(
        f"<a class='item' data-idx='{i}' href='./{esc(name)}'>{esc(name)}</a>"
        for i, name in enumerate(slide_files)
    )
    first = esc(slide_files[0]) if slide_files else ""
    total = len(slide_files)
    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{esc(title)}</title>
<style>
  :root {{
    --bg:#0b0c10; --ink:#e8ecff; --dim:#a8b2d8; --panel:#0f1426; --border:#1c2340;
  }}
  * {{ box-sizing:border-box }}
  html,body {{ margin:0; height:100%; background:var(--bg); color:var(--ink); font-family:
    system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,'Noto Kufi Arabic','Noto Sans Arabic','Amiri','Cairo',sans-serif; }}
  .layout {{ display:grid; grid-template-rows:auto 1fr; height:100%; }}
  header {{ display:flex; align-items:center; gap:12px; padding:12px 16px; border-bottom:1px solid var(--border); background:var(--panel); }}
  h1 {{ font-size:18px; margin:0; color:var(--ink); }}
  .badge {{ font-size:12px; color:var(--dim); border:1px solid var(--border); border-radius:999px; padding:4px 10px; }}
  .content {{ display:grid; grid-template-columns:260px 1fr; height:100%; min-height:0; }}
  nav {{ border-inline-start:1px solid var(--border); background:linear-gradient(180deg, rgba(255,255,255,.01), transparent); padding:10px; overflow:auto; }}
  .item {{ display:block; padding:8px 10px; margin:2px 0; color:var(--ink); text-decoration:none; border-radius:8px; border:1px solid transparent; }}
  .item:hover {{ background:rgba(255,255,255,.03); border-color:var(--border); }}
  .viewer {{ height:100%; min-height:0; }}
  iframe {{ width:100%; height:100%; border:0; background:#000; }}
  .status {{ margin-inline-start:auto; color:var(--dim); font-size:12px; }}
  .keys {{ font-size:12px; color:var(--dim); }}
  @media (max-width: 900px) {{
    .content {{ grid-template-columns:1fr; }}
    nav {{ order:2; height:180px; }}
  }}
</style>
</head>
<body>
  <div class="layout">
    <header>
      <span class="badge">رابط العرض</span>
      <h1>{esc(title)}</h1>
      <span class="status" id="status">0 / {total}</span>
      <span class="keys">← السابق • التالي →</span>
    </header>
    <div class="content">
      <nav id="list">{items or "<div style='color:var(--dim);padding:8px'>لا توجد شرائح بعد</div>"}</nav>
      <div class="viewer"><iframe id="frame" src="{('./' + first) if first else 'about:blank'}" allowfullscreen></iframe></div>
    </div>
  </div>
<script>
(function() {{
  const files = {slide_files!r};
  let idx = 0;
  const frame = document.getElementById('frame');
  const status = document.getElementById('status');
  const list = document.getElementById('list');

  function go(i) {{
    if (!files.length) return;
    idx = (i + files.length) % files.length;
    frame.src = './' + files[idx];
    status.textContent = (idx+1) + ' / ' + files.length;
    for (const a of list.querySelectorAll('.item')) a.classList.remove('active');
    const active = list.querySelector('.item[data-idx="'+idx+'"]');
    if (active) active.classList.add('active');
  }}
  document.addEventListener('keydown', (e) => {{
    if (e.key === 'ArrowRight') go(idx+1);
    else if (e.key === 'ArrowLeft') go(idx-1);
  }});
  list.addEventListener('click', (e) => {{
    const a = e.target.closest('.item'); if (!a) return;
    e.preventDefault();
    go(parseInt(a.dataset.idx||'0',10));
  }});
  go(0);
}})();
</script>
</body>
</html>"""

def render_index(ctx, project_id: str, title: str | None = None) -> Dict[str, Any]:
    """
    Generate slides/index.html that links to numbered slide HTML files and
    supports ←/→ navigation. Emits artifact.ready {kind:'link'}.
    """
    base = ctx.artifacts_dir()  # artifacts/<project_id>
    slides_dir = base / "slides"
    slides = _list_numbered(slides_dir)
    # Title: use provided or derive from state.json if present
    deck_title = title or project_id
    state_path = base / "state.json"
    if state_path.exists():
      try:
        state = ctx.read_json(state_path)  # optional helper if available
        deck_title = (state or {}).get("title") or deck_title
      except Exception:
        pass

    html_text = _build_html(project_id, slides, deck_title)
    out_path = ctx.write_text("slides/index.html", html_text)
    url = ctx.url_for(out_path)
    ctx.emit("artifact.ready", {"kind": "link", "url": url})
    return {"url": url, "path": str(out_path)}

# permissions
render_index.required_permissions = {"fs_read", "fs_write"}


# from __future__ import annotations
# from typing import Any, Dict, List
# import json

# def _ctx_read_json(ctx, relpath: str) -> Dict[str, Any]:
#     try:
#         txt = ctx.read_text(relpath)
#     except FileNotFoundError:
#         return {}
#     except Exception:
#         return {}
#     if not txt:
#         return {}
#     try:
#         return json.loads(txt)
#     except Exception:
#         return {}

# def render_index(ctx, project_id: str) -> Dict[str, Any]:
#     """
#     Reads artifacts/<project_id>/state.json and generates slides/index.html
#     that links to 001.html … with simple keyboard nav. Emits artifact.ready: {kind:"link"}.
#     """
#     state = _ctx_read_json(ctx, "state.json")
#     slides: List[Dict[str, Any]] = state.get("slides") or []
#     if not slides:
#         from ..errors import ProblemDetails  # defer import
#         raise ProblemDetails(title="Not found", detail="No slides in state.json", code="E_NOT_FOUND", status=404)

#     items = []
#     for s in slides:
#         no = int(s.get("no") or 0)
#         title = (s.get("title") or f"Slide {no}").replace("<", "&lt;").replace(">", "&gt;")
#         href = f"{no:03}.html"
#         items.append(f'<li><a href="{href}" data-no="{no}">{title}</a></li>')

#     html = f"""<!doctype html>
# <html lang="{state.get('language','ar')}" dir="{'rtl' if (state.get('language','ar').lower() in ['ar','fa','ur','he']) else 'ltr'}">
# <head>
# <meta charset="utf-8"/>
# <meta name="viewport" content="width=device-width, initial-scale=1"/>
# <title>{(state.get('title') or 'Slides').replace('<','&lt;').replace('>','&gt;')}</title>
# <style>
#   body {{
#     margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
#     background:#0b0c10; color:#f5f7fb;
#   }}
#   header {{ padding:16px 20px; border-bottom:1px solid #23263a; }}
#   main {{ padding:20px; }}
#   ul {{ list-style:none; padding:0; margin:0; display:grid; gap:10px; }}
#   li a {{
#     display:block; padding:12px 16px; background:#141827; color:#e7ecff; text-decoration:none;
#     border-radius:10px; border:1px solid #1f2437;
#   }}
#   li a:hover {{ background:#1a1f31; }}
#   .navhint {{ color:#9aa3ba; font-size:14px; margin-top:8px; }}
#   iframe {{
#     width:100vw; height:calc(100vh - 64px); border:0; display:none;
#   }}
# </style>
# </head>
# <body>
#   <header>
#     <div><strong>{(state.get('title') or 'Slides')}</strong></div>
#     <div class="navhint">← / → للتنقل بين الشرائح، Esc للعودة للقائمة</div>
#   </header>
#   <main id="list">
#     <ul>
#       {''.join(items)}
#     </ul>
#   </main>
#   <iframe id="frame"></iframe>
# <script>
#   const links = Array.from(document.querySelectorAll('a[data-no]'));
#   const list = document.getElementById('list');
#   const frame = document.getElementById('frame');
#   let idx = -1;

#   function openIndex(i) {{
#     if (i < 0 || i >= links.length) return;
#     idx = i;
#     const href = links[i].getAttribute('href');
#     frame.src = href;
#     list.style.display = 'none';
#     frame.style.display = 'block';
#     history.replaceState({{}}, '', '#'+String(i+1).padStart(3,'0'));
#   }}

#   function backToList() {{
#     frame.style.display = 'none';
#     list.style.display = 'block';
#     history.replaceState({{}}, '', '#');
#     idx = -1;
#   }}

#   links.forEach((a, i) => a.addEventListener('click', (e) => {{
#     e.preventDefault();
#     openIndex(i);
#   }}));

#   window.addEventListener('keydown', (e) => {{
#     if (idx === -1) return;
#     if (e.key === 'ArrowRight') openIndex(Math.min(idx+1, links.length-1));
#     if (e.key === 'ArrowLeft')  openIndex(Math.max(idx-1, 0));
#     if (e.key === 'Escape')     backToList();
#   }});

#   // deep-link via hash (#003 etc.)
#   const h = location.hash.replace('#','').trim();
#   if (h) {{
#     const n = parseInt(h, 10);
#     if (!isNaN(n)) openIndex(n-1);
#   }}
# </script>
# </body>
# </html>"""

#     path = ctx.write_text("slides/index.html", html)
#     url = ctx.url_for(path)

#     # Emit link artifact for the FE
#     ctx.emit("artifact.ready", {"kind": "link", "url": url})

#     return {"url": url, "path": path}

# render_index.required_permissions = {"fs_read", "fs_write"}
