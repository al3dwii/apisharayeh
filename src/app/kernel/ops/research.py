# src/app/kernel/ops/research.py
from __future__ import annotations
import os, json, re, hashlib, time, html, logging, unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional
from pathlib import Path

import requests
from trafilatura import extract as trafi_extract  # readability
from trafilatura.settings import use_config

from app.core.safety import safe_fetch_to_temp

log = logging.getLogger(__name__)

# ---------- small utilities ----------

_ARTIFACTS_ROOT = Path(os.getenv("ARTIFACTS_ROOT", "artifacts")).resolve()

def _slug(s: str, maxlen: int = 64) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:maxlen] or "item"

def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _project_dir(project_id: str) -> Path:
    p = _ARTIFACTS_ROOT / project_id / "research"
    _ensure_dir(p)
    return p

def _save_json(project_id: str, name: str, data: Any) -> Path:
    p = _project_dir(project_id) / name
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p

def _readfile(path: str) -> str:
    with open(path, "rb") as f:
        b = f.read()
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1", errors="ignore")

def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]

# ---------- LLM helpers (optional, robust fallbacks) ----------

def _llm_available() -> bool:
    try:
        import openai  # noqa
        return bool(os.getenv("OPENAI_API_KEY"))
    except Exception:
        return False

def _llm_chat_json(system: str, user: str, schema_hint: str, model: Optional[str] = None) -> Dict[str, Any]:
    """
    Minimal JSON-mode chat using OpenAI SDK v1; falls back to 'gpt-4o-mini' if model unset.
    If any error occurs, returns {}.
    """
    try:
        from openai import OpenAI
        client = OpenAI()
        mdl = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        # Use responses (JSON mode) when available; otherwise regular chat and parse json
        resp = client.chat.completions.create(
            model=mdl,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"{user}\n\nJSON schema hint:\n{schema_hint}"},
            ],
        )
        txt = resp.choices[0].message.content or "{}"
        return json.loads(txt)
    except Exception as e:
        log.warning("LLM JSON call failed, falling back: %s", e)
        return {}

# ---------- Search / fetch / extract ----------

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str

def search(ctx, query: str, num: int = 8, lang: Optional[str] = None) -> Dict[str, Any]:
    """
    Uses SerpAPI if SERPAPI_KEY is set; otherwise a very light Bing fallback (no key -> 0 results).
    Returns {"results":[{title,url,snippet}], "engine":"serpapi|none"}
    """
    key = os.getenv("SERPAPI_KEY")
    results: List[SearchResult] = []
    if key:
        params = {
            "engine": "google",
            "q": query,
            "num": min(max(num, 1), 20),
            "hl": lang or "en",
            "api_key": key,
        }
        r = requests.get("https://serpapi.com/search.json", params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        for item in (data.get("organic_results") or [])[:num]:
            url = item.get("link") or ""
            if not url.startswith("http"):
                continue
            results.append(SearchResult(
                title=item.get("title") or url,
                url=url,
                snippet=item.get("snippet") or "",
            ))
        engine = "serpapi"
    else:
        engine = "none"

    out = {"results": [r.__dict__ for r in results], "engine": engine, "query": query}
    # SSE (no-op if ctx doesn't support)
    getattr(ctx, "emit_event", lambda *a, **k: None)("research.search.done", out)
    return out

def fetch_html(ctx, url: str, max_bytes: int = 5_000_000) -> Dict[str, Any]:
    """
    SSRF-safe fetch to temp, then read text.
    """
    tmp, n, ctype = safe_fetch_to_temp(url, max_bytes=max_bytes, timeout=30)
    html_text = _readfile(tmp)
    os.remove(tmp)
    out = {"url": url, "bytes": n, "content_type": ctype, "html": html_text}
    getattr(ctx, "emit_event", lambda *a, **k: None)("research.fetch.done", {"url": url, "bytes": n})
    return out

def extract_readable(ctx, html_text: str, url: str) -> Dict[str, Any]:
    """
    Clean main content using trafilatura; fall back to naive tag strip.
    """
    cfg = use_config()
    cfg.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
    text = trafi_extract(html_text, url=url, favor_recall=True, include_links=False, config=cfg)
    if not text:
        # naive strip
        text = re.sub(r"<[^>]+>", " ", html_text)
        text = html.unescape(text)
    text = re.sub(r"\s+\n", "\n", text).strip()
    out = {"url": url, "text": text[:250000]}  # cap
    getattr(ctx, "emit_event", lambda *a, **k: None)("research.extract.done", {"url": url, "chars": len(out["text"])})
    return out

# ---------- Summarize each document ----------

_SUMMARY_SYS = (
    "You are a careful researcher. Extract objective, verifiable information as crisp bullets. "
    "Prefer concrete numbers, dates, names, and definitions. Never fabricate."
)
_SUMMARY_USER_TMPL = (
    "Summarize the following article for slide bullets. "
    "Return JSON with fields: title, top_bullets (<=6), key_facts (<=4), "
    "and any warnings if the content is opinion/speculative.\n\nURL: {url}\n\nCONTENT:\n{content}"
)
_SUMMARY_SCHEMA_HINT = '{"title": "str", "top_bullets": ["str", "..."], "key_facts": ["str", "..."], "warnings": ["str", "..."]}'

def _fallback_bullets(text: str, max_bullets: int = 6) -> List[str]:
    # very simple: first sentences trimmed to length
    sents = re.split(r"(?<=[\.\!\?])\s+", text)
    out = []
    for s in sents:
        s = re.sub(r"\s+", " ", s).strip()
        if 6 <= len(out) < max_bullets and len(s) < 30:
            out.append(s)
        elif len(out) < max_bullets:
            out.append(s[:120].rstrip() + ("…" if len(s) > 120 else ""))
        if len(out) >= max_bullets:
            break
    return [b for b in out if b]

def summarize_doc(ctx, text: str, url: str, title_hint: Optional[str] = None) -> Dict[str, Any]:
    if _llm_available():
        data = _llm_chat_json(
            _SUMMARY_SYS,
            _SUMMARY_USER_TMPL.format(url=url, content=text[:12000]),
            _SUMMARY_SCHEMA_HINT,
        )
        if data:
            title = data.get("title") or (title_hint or url)
            bullets = [b.strip() for b in (data.get("top_bullets") or []) if b and isinstance(b, str)]
            facts = [b.strip() for b in (data.get("key_facts") or []) if b and isinstance(b, str)]
            return {"url": url, "title": title, "bullets": bullets, "facts": facts, "warnings": data.get("warnings") or []}
    # fallback
    return {"url": url, "title": title_hint or url, "bullets": _fallback_bullets(text), "facts": []}

# ---------- Aggregate & outline ----------

def _norm_bullet(b: str) -> str:
    b = b.strip().lower()
    b = re.sub(r"[^a-z0-9\s\-\._%:\(\)]", "", b)
    b = re.sub(r"\s+", " ", b)
    return b

def aggregate(ctx, docs: List[Dict[str, Any]], topic: str) -> Dict[str, Any]:
    """
    Deduplicate bullets across docs and rank by variety (source coverage).
    """
    seen = {}
    merged: List[Tuple[str, str, str]] = []  # (bullet, source_id, url)
    for i, d in enumerate(docs):
        sid = f"s{i+1}"
        for b in d.get("bullets", []):
            nb = _norm_bullet(b)
            if not nb:
                continue
            owner = seen.get(nb)
            if not owner:
                seen[nb] = sid
                merged.append((b.strip(), sid, d["url"]))
    # rank: keep order as discovered, cap to 60 bullets
    merged = merged[:60]
    facts = [{"text": b, "source": sid, "url": u} for (b, sid, u) in merged]
    getattr(ctx, "emit_event", lambda *a, **k: None)("research.aggregate.done", {"bullets": len(facts)})
    return {"topic": topic, "facts": facts}

_OUTLINE_SYS = (
    "You turn research bullets into a clean slide outline. "
    "Each slide has a short title and 3–5 concise bullets (<=12 words each). "
    "Keep it objective and structured. Include a slide order."
)
_OUTLINE_USER_TMPL = (
    "Topic: {topic}\n"
    "Bullets (source-tagged):\n{bullets_json}\n\n"
    "Return JSON: slides=[{title, bullets:[str], note?, image_prompt?, citation_sources:[str]}]."
)
_OUTLINE_SCHEMA_HINT = '{"slides":[{"title":"str","bullets":["str"],"note":"str","image_prompt":"str","citation_sources":["s1"]}]}'

def outline_slides(ctx, facts: List[Dict[str, Any]], topic: str, style: str = "default") -> Dict[str, Any]:
    # Prepare compact facts list for the prompt
    bullets_json = json.dumps([{"t": f["text"], "s": f["source"]} for f in facts][:60], ensure_ascii=False)
    if _llm_available():
        data = _llm_chat_json(
            _OUTLINE_SYS,
            _OUTLINE_USER_TMPL.format(topic=topic, bullets_json=bullets_json),
            _OUTLINE_SCHEMA_HINT,
        )
        slides = data.get("slides") if isinstance(data, dict) else None
        if isinstance(slides, list) and slides:
            # normalize structure
            out = []
            for s in slides:
                out.append({
                    "title": s.get("title") or "",
                    "bullets": [b for b in (s.get("bullets") or []) if isinstance(b, str)][:6],
                    "note": s.get("note") or "",
                    "image_prompt": s.get("image_prompt") or f"{topic}, {style}, presentation slide photo",
                    "citations": [c for c in (s.get("citation_sources") or []) if isinstance(c, str)],
                })
            getattr(ctx, "emit_event", lambda *a, **k: None)("research.outline.done", {"slides": len(out)})
            return {"slides": out}
    # fallback: simple chunking
    chunks = [facts[i:i+4] for i in range(0, min(len(facts), 28), 4)]
    slides = []
    if chunks:
        slides.append({"title": f"Overview: {topic}", "bullets": [c["text"] for c in chunks[0]], "note": "", "image_prompt": f"{topic}", "citations": list({c["source"] for c in chunks[0]})})
    for ch in chunks[1:]:
        slides.append({"title": f"More on {topic}", "bullets": [c["text"] for c in ch], "note": "", "image_prompt": f"{topic}", "citations": list({c["source"] for c in ch})})
    getattr(ctx, "emit_event", lambda *a, **k: None)("research.outline.done", {"slides": len(slides)})
    return {"slides": slides}

def cite_pack(ctx, docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build compact citation map: s1->{title,url}, s2->{...}
    """
    m = {}
    for i, d in enumerate(docs):
        sid = f"s{i+1}"
        title = (d.get("title") or "").strip() or d.get("url") or sid
        m[sid] = {"id": sid, "title": title, "url": d.get("url")}
    return {"citations": m}

# ---------- Master op used by slides.generate ----------

def research_pipeline(ctx, project_id: str, topic: Optional[str] = None, urls: Optional[List[str]] = None, lang: Optional[str] = None, max_docs: int = 8) -> Dict[str, Any]:
    """
    High-level: search (or use provided urls) -> fetch -> extract -> summarize -> aggregate -> outline + citations.
    Also persists artifacts under artifacts/{project}/research/.
    """
    t0 = time.time()
    urls_in: List[str] = []
    results_meta: List[Dict[str, Any]] = []

    if urls:
        urls_in = [u for u in urls if isinstance(u, str) and u.startswith("http")]
    elif topic:
        sr = search(ctx, topic, num=max_docs, lang=lang)
        for r in sr["results"]:
            urls_in.append(r["url"])
            results_meta.append(r)

    docs: List[Dict[str, Any]] = []
    for u in urls_in[:max_docs]:
        try:
            f = fetch_html(ctx, u)
            e = extract_readable(ctx, f["html"], u)
            s = summarize_doc(ctx, e["text"], u, title_hint=next((m["title"] for m in results_meta if m.get("url")==u), None))
            docs.append({"url": u, "title": s["title"], "bullets": s["bullets"], "facts": s.get("facts", [])})
        except Exception as ex:
            log.warning("Research fetch/summarize failed for %s: %s", u, ex)

    agg = aggregate(ctx, docs, topic or urls_in[0] if urls_in else "Slides")
    outline = outline_slides(ctx, agg["facts"], topic or "Slides")
    cites = cite_pack(ctx, docs)

    payload = {
        "topic": topic,
        "urls": urls_in,
        "docs": docs,
        "facts": agg["facts"],
        "outline": outline["slides"],
        "citations": cites["citations"],
        "t_ms": int((time.time() - t0) * 1000),
    }

    # Persist compact artifacts
    _save_json(project_id, "citations.json", payload["citations"])
    _save_json(project_id, "outline.json", payload["outline"])
    _save_json(project_id, "docs.json", [{"title": d["title"], "url": d["url"], "bullets": d["bullets"]} for d in docs])

    getattr(ctx, "emit_event", lambda *a, **k: None)("research.pipeline.done", {"slides": len(payload["outline"]), "docs": len(docs), "t_ms": payload["t_ms"]})
    return payload

# Mark permissions so your ToolRouter can allow FS r/w
research_pipeline.required_permissions = {"fs_read", "fs_write"}
search.required_permissions = set()
fetch_html.required_permissions = {"fs_read"}
extract_readable.required_permissions = set()
summarize_doc.required_permissions = set()
aggregate.required_permissions = set()
outline_slides.required_permissions = set()
cite_pack.required_permissions = set()

# # src/app/kernel/ops/research.py
# from __future__ import annotations
# from typing import Dict, Any, List

# from ..errors import ProblemDetails
# from . import vision as vision_ops  # offline-safe images


# def deep_thinking_plan(ctx, topic: str, slides_count: int = 5) -> Dict[str, Any]:
#     """
#     Emits 'Using Tool' + TODO checklist + a narration bubble to drive the left panel.
#     """
#     if not topic or not isinstance(topic, str):
#         raise ProblemDetails(title="Invalid input", detail="topic required", code="E_VALIDATION", status=400)

#     todos = [
#         ("t1", f"جمع المعلومات الأولية حول {topic}"),
#         ("t2", f"البحث في اتجاهات وتطورات {topic}"),
#         ("t3", f"جمع معلومات حول فوائد ومزايا {topic}"),
#         ("t4", f"دراسة التحديات والعقبات في {topic}"),
#         ("t5", f"البحث عن أدوات ومنصات {topic}"),
#         ("t6", f"إنشاء العرض التقديمي بـ {slides_count} شرائح"),
#     ]

#     ctx.emit("partial", {
#         "type": "using_tool.start",
#         "tool": "Deep Thinking",
#         "label": "تخطيط المهمة",
#         "meta": {"total_todos": len(todos)}
#     })
#     ctx.emit("partial", {
#         "type": "todos.init",
#         "items": [{"id": i, "text": t, "done": False} for i, t in todos]
#     })
#     ctx.emit("partial", {
#         "type": "narration",
#         "text": f"سأبدأ بجمع المعلومات اللازمة حول {topic} لإنشاء عرض تقديمي شامل."
#     })
#     ctx.emit("partial", {"type": "todos.progress", "done_ids": ["t1"], "remaining": len(todos) - 1})
#     ctx.emit("partial", {"type": "using_tool.end", "tool": "Deep Thinking", "meta": {"completed": True}})
#     return {"todos": [i for i, _ in todos]}


# def parallel_search(ctx, queries: List[str]) -> Dict[str, Any]:
#     """
#     Announces a parallel search step and emits empty buckets (offline-safe).
#     """
#     queries = list(queries or [])
#     ctx.emit("partial", {
#         "type": "using_tool.start",
#         "tool": "Parallel Search",
#         "label": "بحث موازي",
#         "meta": {"queries": queries}
#     })
#     for q in queries:
#         ctx.emit("partial", {"type": "search.results", "query": q, "items": []})
#     ctx.emit("partial", {"type": "using_tool.end", "tool": "Parallel Search", "meta": {"completed": True}})
#     return {"ok": True}


# def read_url(ctx, url: str) -> Dict[str, Any]:
#     """
#     Emits a 'Read' block with placeholder excerpt (can be replaced later).
#     """
#     if not url or not isinstance(url, str):
#         raise ProblemDetails(title="Invalid input", detail="url required", code="E_VALIDATION", status=400)
#     ctx.emit("partial", {"type": "using_tool.start", "tool": "Read", "label": "قراءة مصدر"})
#     ctx.emit("partial", {
#         "type": "read.snippet",
#         "url": url,
#         "title": "مصدر خارجي",
#         "excerpt": "ملخص قصير سيتم استبداله عند تفعيل القراءة الحقيقية."
#     })
#     ctx.emit("partial", {"type": "using_tool.end", "tool": "Read", "meta": {"completed": True}})
#     return {"text": ""}


# def image_search(ctx, query: str, project_id: str | None = None) -> Dict[str, Any]:
#     """
#     Offline-safe image search via local fixtures; emits image URLs for the UI.
#     """
#     if not project_id:
#         project_id = ctx.project_id
#     res = vision_ops.images_from_fixtures(ctx, queries=[query], project_id=project_id)
#     paths = res.get("images", [])[:9]
#     ctx.emit("partial", {"type": "using_tool.start", "tool": "Image Search", "label": "بحث صور"})
#     ctx.emit("partial", {"type": "images.results", "query": query, "images": [ctx.url_for(p) for p in paths]})
#     ctx.emit("partial", {"type": "using_tool.end", "tool": "Image Search", "meta": {"completed": True}})
#     return {"images": paths}


# # ---- permission annotations (used by ToolRouter) ----
# deep_thinking_plan.required_permissions = {"fs_write"}
# parallel_search.required_permissions = {"fs_write"}
# read_url.required_permissions = {"fs_write"}
# image_search.required_permissions = {"fs_write", "fs_read"}
