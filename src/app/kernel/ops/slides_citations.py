# src/app/kernel/ops/slides_citations.py
from __future__ import annotations
from typing import Dict, Any, List, Optional
from pathlib import Path
from urllib.parse import urlparse
import html
import re


def _host(u: Optional[str]) -> str:
    if not u:
        return ""
    try:
        return (urlparse(u).hostname or "").lower()
    except Exception:
        return ""


def _inject_footer(page_html: str, footer_html: str) -> str:
    """
    Insert footer just before </body>. If not found, append.
    Also removes any previous .o2-cites block to avoid duplication.
    """
    # remove an older injected block if present
    page_html = re.sub(r"\n?<div class=\"o2-cites\"[\s\S]*?</div>\s*(?=</body>|$)", "", page_html, flags=re.IGNORECASE)
    i = page_html.lower().rfind("</body>")
    if i >= 0:
        return page_html[:i] + footer_html + page_html[i:]
    return page_html + footer_html


def _build_footer(label: str, items: List[Dict[str, str]]) -> str:
    """
    items: [{id, title, url, host}]
    """
    esc = html.escape
    pills = []
    for it in items:
        # Compact, host-forward pill: [s1] host
        pill = f"""<a href="{esc(it['url'])}" target="_blank" rel="noopener noreferrer" class="o2-pill">[{esc(it['id'])}] {esc(it['host'] or it['title'])}</a>"""
        pills.append(pill)
    pills_html = " ".join(pills) if pills else "<span class='o2-mute'>—</span>"
    # Keep styles self-contained so slides remain portable
    return f"""
<!-- injected by slides.citations.apply -->
<style>
  .o2-cites {{
    position: absolute; left: 16px; right: 16px; bottom: 10px;
    font: 12px/1.35 system-ui,-apple-system,"Segoe UI",Roboto,Arial,"Noto Sans Arabic","Amiri",sans-serif;
    color: #a8b2d8; display:flex; gap:8px; flex-wrap:wrap; align-items:center;
    background: linear-gradient(180deg, rgba(0,0,0,.00), rgba(0,0,0,.20));
    padding: 6px 8px; border-radius: 8px;
  }}
  .o2-cites .o2-label {{ color:#e8ecff; font-weight:600; margin-inline-end:4px; }}
  .o2-cites .o2-pill {{
    display:inline-block; text-decoration:none; padding:2px 8px; border:1px solid #273056;
    border-radius:999px; color:#cbd5ff;
  }}
  .o2-cites .o2-pill:hover {{ background:#0f1426; border-color:#33407a; }}
  .o2-cites .o2-mute {{ opacity:.65 }}
  @media print {{ .o2-cites {{ position: static; background:none; padding:0; }} }}
</style>
<div class="o2-cites" dir="auto"><span class="o2-label">{esc(label)}:</span>{pills_html}</div>
"""


def apply_citations(
    ctx,
    *,
    project_id: str,
    outline: List[Dict[str, Any]],
    citations: Optional[Dict[str, Any]] = None,
    language: str = "en",
) -> Dict[str, Any]:
    """
    Adds a 'Sources' footer to each slide HTML file (slides/NNN.html), based on:
      - per-slide 'citations' lists in the outline, and
      - the research citations map {id -> {title,url}}.

    Safe no-op if there are no citations.
    """
    base = ctx.artifacts_dir()  # artifacts/<project_id>
    slides_dir = base / "slides"
    if not slides_dir.exists():
        return {"updated": []}

    # If citations map isn't provided (e.g., prompt-only flow), try reading persisted one.
    if not citations:
        try:
            cit_path = base / "research" / "citations.json"
            if cit_path.exists():
                citations = ctx.read_json(cit_path)  # type: ignore
        except Exception:
            citations = {}

    citations = citations or {}
    # quick check: if the entire deck has no citing info, do nothing
    any_cites = any((isinstance(s, dict) and s.get("citations")) for s in (outline or []))
    if not any_cites:
        return {"updated": []}

    label = "Sources" if str(language).lower().startswith("en") else "المراجع"

    updated_urls: List[str] = []
    for s in (outline or []):
        try:
            no = int(s.get("no") or 0)
        except Exception:
            no = 0
        if no <= 0:
            continue

        ids = [c for c in (s.get("citations") or []) if isinstance(c, str) and c.strip()]
        if not ids:
            # skip slides without citations
            continue

        # Resolve metadata; keep order and dedupe locally
        seen = set()
        items: List[Dict[str, str]] = []
        for cid in ids:
            if cid in seen:
                continue
            seen.add(cid)
            meta = citations.get(cid) or {}
            url = meta.get("url") or ""
            title = (meta.get("title") or "").strip() or url or cid
            items.append({"id": cid, "title": title, "url": url, "host": _host(url)})

        if not items:
            continue

        # Load slide HTML
        fn = f"{no:03d}.html"
        path = slides_dir / fn
        if not path.exists():
            continue
        try:
            page_html = path.read_text(encoding="utf-8")
        except Exception:
            page_html = path.read_text(errors="ignore")

        footer = _build_footer(label, items)
        new_html = _inject_footer(page_html, footer)
        if new_html != page_html:
            path.write_text(new_html, encoding="utf-8")
            updated_urls.append(ctx.url_for(path))

    # Emit event for observability
    ctx.emit("artifact.updated", {"kind": "slides.citations", "count": len(updated_urls)})

    return {"updated": updated_urls}


apply_citations.required_permissions = {"fs_read", "fs_write"}
