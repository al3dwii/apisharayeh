# src/app/kernel/ops/research.py
from __future__ import annotations

import os
import json
import re
import hashlib
import time
import html
import logging
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict

import requests
from trafilatura import extract as trafi_extract  # readability
from trafilatura.settings import use_config

from app.core.safety import safe_fetch_to_temp

log = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────────
# Small utilities
# ────────────────────────────────────────────────────────────────────────────────

# Prefer ARTIFACTS_DIR if provided; fall back to ARTIFACTS_ROOT or 'artifacts'
_ARTIFACTS_ROOT = Path(os.getenv("ARTIFACTS_DIR") or os.getenv("ARTIFACTS_ROOT", "artifacts")).resolve()

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

def _host_of(u: str) -> str:
    try:
        return (urlparse(u).hostname or "").lower()
    except Exception:
        return ""

def _allow_host(host: str, allow: Optional[List[str]], block: Optional[List[str]]) -> bool:
    """
    True if host passes block/allow (block wins, allow restricts).
    Accepts bare domains like 'wikipedia.org' and matches subdomains.
    """
    h = (host or "").lower()
    if block:
        for b in block:
            b = (b or "").lower()
            if not b:
                continue
            if h == b or h.endswith("." + b):
                return False
    if allow and len(allow) > 0:
        for a in allow:
            a = (a or "").lower()
            if not a:
                continue
            if h == a or h.endswith("." + a):
                return True
        return False
    return True

def _cap_per_host(urls: List[str], cap: Optional[int]) -> List[str]:
    if cap is None or cap <= 0:
        return urls
    seen: Dict[str, int] = {}
    out: List[str] = []
    for u in urls:
        h = _host_of(u)
        n = seen.get(h, 0)
        if n < cap:
            out.append(u)
            seen[h] = n + 1
    return out

# For fuzzy dedupe (word shingles)
_WORD_RE = re.compile(r"[A-Za-z\u0600-\u06FF0-9]+")

def _word_shingles(text: str, n: int = 5) -> set[str]:
    toks = _WORD_RE.findall(text or "")
    if not toks:
        return set()
    if len(toks) <= n:
        return {" ".join(toks).lower()}
    return {" ".join(toks[i:i+n]).lower() for i in range(0, len(toks) - n + 1)}

def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / float(len(a | b))

# ────────────────────────────────────────────────────────────────────────────────
# LLM helpers (optional, robust fallbacks)
# ────────────────────────────────────────────────────────────────────────────────

def _llm_available() -> bool:
    try:
        import openai  # noqa: F401
        return bool(os.getenv("OPENAI_API_KEY"))
    except Exception:
        return False

def _llm_chat_json(system: str, user: str, schema_hint: str, model: Optional[str] = None) -> Dict[str, Any]:
    """
    Minimal JSON-mode chat using OpenAI SDK v1.
    Returns {} on any error.
    """
    try:
        from openai import OpenAI
        client = OpenAI()
        mdl = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
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

# ────────────────────────────────────────────────────────────────────────────────
# Search / fetch / extract
# ────────────────────────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str

def search(
    ctx,
    query: str,
    num: int = 8,
    lang: Optional[str] = None,
    allow_domains: Optional[List[str]] = None,
    block_domains: Optional[List[str]] = None,
    per_host_cap: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Uses SerpAPI if SERPAPI_KEY is set; otherwise returns {"results":[], "engine":"none"}.
    Domain filters and per-host cap are applied.
    """
    key = os.getenv("SERPAPI_KEY")
    results: List[SearchResult] = []
    engine = "none"
    if key:
        params = {
            "engine": "google",
            "q": query,
            "num": min(max(num, 1), 20),
            "hl": (lang or "en"),
            "api_key": key,
        }
        r = requests.get("https://serpapi.com/search.json", params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        for item in (data.get("organic_results") or [])[:num]:
            url = item.get("link") or ""
            if not url.startswith("http"):
                continue
            if not _allow_host(_host_of(url), allow_domains, block_domains):
                continue
            results.append(SearchResult(
                title=item.get("title") or url,
                url=url,
                snippet=item.get("snippet") or "",
            ))
        engine = "serpapi"

    # per-host cap post-filter
    if per_host_cap:
        allowed_urls = set(_cap_per_host([r.url for r in results], per_host_cap))
        results = [r for r in results if r.url in allowed_urls]

    out = {"results": [r.__dict__ for r in results], "engine": engine, "query": query}
    getattr(ctx, "emit_event", lambda *a, **k: None)("research.search.done", out)
    return out

def fetch_html(ctx, url: str, max_bytes: int = 5_000_000, language_hint: Optional[str] = None) -> Dict[str, Any]:
    """
    SSRF-safe fetch to temp (via safe_fetch_to_temp), then read text.
    Adds Accept-Language and optional User-Agent (HTTP_USER_AGENT env) to improve success on bot-unfriendly sites.
    """
    headers: Dict[str, str] = {}
    if language_hint:
        headers["Accept-Language"] = f"{language_hint},en;q=0.8"
    ua = os.getenv("HTTP_USER_AGENT")
    if ua:
        headers["User-Agent"] = ua

    tmp, n, ctype = safe_fetch_to_temp(
        url,
        max_bytes=max_bytes,
        timeout=30,
        allowed_content_types=None,
        headers=headers or None,  # pass None if empty
    )
    html_text = _readfile(tmp)
    try:
        os.remove(tmp)
    except Exception:
        pass
    out = {"url": url, "bytes": n, "content_type": ctype, "html": html_text}
    getattr(ctx, "emit_event", lambda *a, **k: None)("research.fetch.done", {"url": url, "bytes": n})
    return out

def extract_readable(ctx, html_text: str, url: str) -> Dict[str, Any]:
    """
    Clean main content using trafilatura; fall back to naive HTML tag strip.
    """
    cfg = use_config()
    cfg.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
    text = trafi_extract(html_text, url=url, favor_recall=True, include_links=False, config=cfg)
    if not text:
        # naive strip
        text = re.sub(r"<[^>]+>", " ", html_text)
        text = html.unescape(text)
    text = re.sub(r"\s+\n", "\n", text).strip()
    out = {"url": url, "text": text[:250000]}  # cap to 250k chars
    getattr(ctx, "emit_event", lambda *a, **k: None)("research.extract.done", {"url": url, "chars": len(out["text"])})
    return out

# ────────────────────────────────────────────────────────────────────────────────
# Summarize each document
# ────────────────────────────────────────────────────────────────────────────────

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
    # very simple: first few sentences trimmed
    sents = re.split(r"(?<=[\.\!\?])\s+", text or "")
    out: List[str] = []
    for s in sents:
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            continue
        if len(out) < max_bullets:
            out.append(s[:120].rstrip() + ("…" if len(s) > 120 else ""))
        else:
            break
    return out

def summarize_doc(ctx, text: str, url: str, title_hint: Optional[str] = None) -> Dict[str, Any]:
    if _llm_available():
        data = _llm_chat_json(
            _SUMMARY_SYS,
            _SUMMARY_USER_TMPL.format(url=url, content=text[:12000]),
            _SUMMARY_SCHEMA_HINT,
        )
        if data:
            title = data.get("title") or (title_hint or url)
            bullets = [b.strip() for b in (data.get("top_bullets") or []) if isinstance(b, str) and b.strip()]
            facts = [b.strip() for b in (data.get("key_facts") or []) if isinstance(b, str) and b.strip()]
            return {"url": url, "title": title, "bullets": bullets, "facts": facts, "warnings": data.get("warnings") or []}
    # fallback
    return {"url": url, "title": title_hint or url, "bullets": _fallback_bullets(text), "facts": []}

# ────────────────────────────────────────────────────────────────────────────────
# Aggregate & outline (filters / dedupe / ranking)
# ────────────────────────────────────────────────────────────────────────────────

def _dedupe_docs(docs: List[Dict[str, Any]], threshold: float = 0.82) -> List[Dict[str, Any]]:
    """
    Remove near-duplicate docs using Jaccard similarity over word shingles.
    Signature is computed from bullets (preferred), else facts, else title.
    """
    kept: List[Dict[str, Any]] = []
    sigs: List[set[str]] = []
    for d in docs:
        corpus = " ".join((d.get("bullets") or [])) or " ".join(d.get("facts") or []) or (d.get("title") or "")
        sig = _word_shingles(corpus, 5)
        is_dup = False
        for s2 in sigs:
            if _jaccard(sig, s2) >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(d)
            sigs.append(sig)
    return kept

def _rank_docs(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Rough scoring: bullet length + fact length + diversity bonus for new host.
    """
    seen_hosts: set[str] = set()
    ranked: List[Tuple[float, Dict[str, Any]]] = []
    for d in docs:
        host = _host_of(d.get("url", ""))
        blen = len(" ".join(d.get("bullets") or []))
        flen = len(" ".join(d.get("facts") or []))
        diversity_bonus = 200 if host and host not in seen_hosts else 0
        score = blen + flen + diversity_bonus
        ranked.append((score, d))
        seen_hosts.add(host)
    ranked.sort(key=lambda t: t[0], reverse=True)
    return [d for _, d in ranked]

def aggregate(
    ctx,
    docs: List[Dict[str, Any]],
    topic: str,
    *,
    allow_domains: Optional[List[str]] = None,
    block_domains: Optional[List[str]] = None,
    per_host_cap: int = 2,
    dedupe_threshold: float = 0.82,
    language_hint: Optional[str] = None,  # reserved
) -> Dict[str, Any]:
    """
    Merge & dedupe bullets with domain filters and ranking.
    Returns {"topic", "facts", "stats":{...}}
    """
    # 1) filter by domain
    filtered: List[Dict[str, Any]] = []
    for d in docs or []:
        if _allow_host(_host_of(d.get("url", "")), allow_domains, block_domains):
            filtered.append(d)

    # 2) cap per host (keep best by contentlen)
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for d in filtered:
        buckets[_host_of(d.get("url", ""))].append(d)

    capped: List[Dict[str, Any]] = []
    for h, lst in buckets.items():
        lst2 = sorted(
            lst,
            key=lambda x: len(" ".join(x.get("bullets") or [])) + len(" ".join(x.get("facts") or [])),
            reverse=True,
        )
        capped.extend(lst2[: max(1, per_host_cap)])

    # 3) dedupe near-duplicate docs
    docs_dedup = _dedupe_docs(capped, threshold=dedupe_threshold)

    # 4) rank for diversity/coverage
    ranked = _rank_docs(docs_dedup)

    # 5) merge bullets into facts
    seen_bullets: Dict[str, str] = {}
    facts: List[Dict[str, Any]] = []
    def _norm_bullet(b: str) -> str:
        b = (b or "").strip().lower()
        b = re.sub(r"[^a-z0-9\s\-\._%:\(\)]", "", b)
        b = re.sub(r"\s+", " ", b)
        return b

    for i, d in enumerate(ranked):
        sid = f"s{i+1}"
        for b in d.get("bullets", []):
            nb = _norm_bullet(b)
            if not nb or nb in seen_bullets:
                continue
            seen_bullets[nb] = sid
            facts.append({"text": b.strip(), "source": sid, "url": d.get("url")})
            if len(facts) >= 60:
                break
        if len(facts) >= 60:
            break

    getattr(ctx, "emit_event", lambda *a, **k: None)("research.aggregate.done", {"bullets": len(facts)})
    return {
        "topic": topic,
        "facts": facts,
        "stats": {
            "input_docs": len(docs or []),
            "filtered": len(filtered),
            "capped": len(capped),
            "deduped": len(docs_dedup),
            "ranked": len(ranked),
        },
    }

# ────────────────────────────────────────────────────────────────────────────────
# Outline
# ────────────────────────────────────────────────────────────────────────────────

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
    bullets_json = json.dumps([{"t": f["text"], "s": f["source"]} for f in facts][:60], ensure_ascii=False)
    if _llm_available() and facts:
        data = _llm_chat_json(
            _OUTLINE_SYS,
            _OUTLINE_USER_TMPL.format(topic=topic, bullets_json=bullets_json),
            _OUTLINE_SCHEMA_HINT,
        )
        slides = data.get("slides") if isinstance(data, dict) else None
        if isinstance(slides, list) and slides:
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

    # Fallback: produce a minimal, usable outline so callers don't break.
    slides: List[Dict[str, Any]] = []
    chunks = [facts[i:i+4] for i in range(0, min(len(facts), 28), 4)]
    if chunks:
        slides.append({
            "title": f"Overview: {topic}",
            "bullets": [c["text"] for c in chunks[0]],
            "note": "",
            "image_prompt": f"{topic}",
            "citations": list({c["source"] for c in chunks[0]}),
        })
        for ch in chunks[1:]:
            slides.append({
                "title": f"More on {topic}",
                "bullets": [c["text"] for c in ch],
                "note": "",
                "image_prompt": f"{topic}",
                "citations": list({c["source"] for c in ch}),
            })
    else:
        # absolutely nothing fetched? produce a tiny scaffold (matches what you observed: 3 slides)
        slides = [
            {"title": topic or "Slides", "bullets": [], "note": "", "image_prompt": f"{topic}", "citations": []},
            {"title": "Background", "bullets": [], "note": "", "image_prompt": f"{topic}", "citations": []},
            {"title": "Key points", "bullets": [], "note": "", "image_prompt": f"{topic}", "citations": []},
        ]

    getattr(ctx, "emit_event", lambda *a, **k: None)("research.outline.done", {"slides": len(slides)})
    return {"slides": slides}

def cite_pack(ctx, docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build compact citation map: s1->{title,url}, s2->{...}
    """
    m: Dict[str, Dict[str, Any]] = {}
    for i, d in enumerate(docs):
        sid = f"s{i+1}"
        title = (d.get("title") or "").strip() or d.get("url") or sid
        m[sid] = {"id": sid, "title": title, "url": d.get("url")}
    return {"citations": m}

# ────────────────────────────────────────────────────────────────────────────────
# Master op (used by /v1/research/preview and slides.generate pipelines)
# ────────────────────────────────────────────────────────────────────────────────

# --- signature: accept both lang and language
def research_pipeline(
    ctx,
    project_id: str,
    topic: Optional[str] = None,
    urls: Optional[List[str]] = None,
    lang: Optional[str] = None,           # NEW (keep for back-compat)
    language: Optional[str] = None,       # keep existing if you had it
    max_docs: int = 8,
    *,
    allow_domains: Optional[List[str]] = None,
    block_domains: Optional[List[str]] = None,
    per_host_cap: Optional[int] = None,
    dedupe_threshold: float = 0.0,
) -> Dict[str, Any]:
    t0 = time.time()

    # normalize: one effective language variable
    lang_eff = language or lang

    urls_in: List[str] = []
    results_meta: List[Dict[str, Any]] = []

    # 1) inputs (topic → search, or explicit urls)
    if urls:
        urls_in = [u for u in urls if isinstance(u, str) and u.startswith("http")]
    elif topic:
        sr = search(
            ctx,
            topic,
            num=max_docs,
            lang=lang_eff,
            allow_domains=allow_domains,
            block_domains=block_domains,
            per_host_cap=per_host_cap,
        )
        for r in sr.get("results", []):
            urls_in.append(r["url"])
            results_meta.append(r)

    # host filters & caps for explicit urls too
    urls_in = [u for u in urls_in if _allow_host(_host_of(u), allow_domains, block_domains)]
    if per_host_cap:
        urls_in = _cap_per_host(urls_in, per_host_cap)
    urls_in = urls_in[:max_docs]

    # 2) fetch → extract → summarize
    docs: List[Dict[str, Any]] = []
    for u in urls_in:
        try:
            f = fetch_html(ctx, u, language_hint=lang_eff)  # pass normalized language
            e = extract_readable(ctx, f["html"], u)
            title_hint = next((m.get("title") for m in results_meta if m.get("url") == u), None)
            s = summarize_doc(ctx, e["text"], u, title_hint=title_hint)
            docs.append({"url": u, "title": s["title"], "bullets": s["bullets"], "facts": s.get("facts", [])})
        except Exception as ex:
            log.warning("Research fetch/summarize failed for %s: %s", u, ex)

    # 3) aggregate
    agg = aggregate(ctx, docs, topic or (urls_in[0] if urls_in else "Slides"), dedupe_threshold=dedupe_threshold)

    # 4) outline + citations
    outline = outline_slides(ctx, agg["facts"], topic or "Slides")
    cites = cite_pack(ctx, docs)

    stats = {
        "urls": urls_in,
        "input_docs": len(urls_in),
        "filtered": len(urls or []) - len(urls_in) if urls else 0,
        "capped": per_host_cap or 0,
        "deduped": agg.get("deduped", 0),
        "ranked": len(agg.get("facts", [])),
        "knobs": {
            "allow_domains": allow_domains,
            "block_domains": block_domains,
            "per_host_cap": per_host_cap,
            "dedupe_threshold": dedupe_threshold,
            "language": lang_eff,
        },
        "t_ms": int((time.time() - t0) * 1000),
    }

    payload = {
        "topic": topic,
        "urls": urls_in,
        "docs": docs,
        "facts": agg["facts"],
        "outline": outline["slides"],
        "citations": cites["citations"],
        "stats": stats,
    }

    _save_json(project_id, "citations.json", payload["citations"])
    _save_json(project_id, "outline.json", payload["outline"])
    _save_json(project_id, "docs.json", [{"title": d["title"], "url": d["url"], "bullets": d["bullets"]} for d in docs])
    _save_json(project_id, "stats.json", stats)

    getattr(ctx, "emit_event", lambda *a, **k: None)("research.pipeline.done", {"slides": len(payload["outline"]), "docs": len(docs), "t_ms": stats["t_ms"]})
    return payload

# def research_pipeline(
#     ctx,
#     project_id: str,
#     topic: Optional[str] = None,
#     urls: Optional[List[str]] = None,
#     language: Optional[str] = None,
#     max_docs: int = 8,
#     *,
#     allow_domains: Optional[List[str]] = None,
#     block_domains: Optional[List[str]] = None,
#     per_host_cap: Optional[int] = None,
#     dedupe_threshold: float = 0.82,
# ) -> Dict[str, Any]:
#     """
#     Search (or use provided urls) -> fetch -> extract -> summarize -> aggregate -> outline + citations.
#     Persists artifacts under artifacts/{project}/research/.
#     """
#     t0 = time.time()
#     urls_in: List[str] = []
#     results_meta: List[Dict[str, Any]] = []

#     # 1) topic → search OR explicit urls
#     if urls:
#         urls_in = [u for u in urls if isinstance(u, str) and u.startswith("http")]
#     elif topic:
#         sr = search(
#             ctx,
#             topic,
#             num=max_docs,
#             lang=language,
#             allow_domains=allow_domains,
#             block_domains=block_domains,
#             per_host_cap=per_host_cap,
#         )
#         for r in sr.get("results", []):
#             urls_in.append(r["url"])
#             results_meta.append(r)

#     # Explicit url inputs: apply domain filters & per-host cap
#     urls_in = [u for u in urls_in if _allow_host(_host_of(u), allow_domains, block_domains)]
#     urls_in = _cap_per_host(urls_in, per_host_cap)
#     urls_in = urls_in[: max_docs or 8]

#     # 2) fetch → extract → summarize
#     docs: List[Dict[str, Any]] = []
#     for u in urls_in:
#         try:
#             f = fetch_html(ctx, u, language_hint=language)
#             e = extract_readable(ctx, f["html"], u)
#             title_hint = next((m.get("title") for m in results_meta if m.get("url") == u), None)
#             s = summarize_doc(ctx, e["text"], u, title_hint=title_hint)
#             docs.append({"url": u, "title": s["title"], "bullets": s["bullets"], "facts": s.get("facts", [])})
#         except Exception as ex:
#             log.warning("Research fetch/summarize failed for %s: %s", u, ex)

#     # 3) aggregate (filters/dedupe/ranking)
#     agg = aggregate(
#         ctx,
#         docs,
#         topic or (urls_in[0] if urls_in else "Slides"),
#         allow_domains=allow_domains,
#         block_domains=block_domains,
#         per_host_cap=int(per_host_cap or 2),
#         dedupe_threshold=dedupe_threshold,
#         language_hint=language,
#     )

#     # 4) outline + citations
#     outline = outline_slides(ctx, agg["facts"], topic or "Slides")
#     cites = cite_pack(ctx, docs)

#     stats = {
#         "urls": urls_in,
#         "input_docs": len(urls_in),
#         "filtered": len(urls or []) - len(urls_in) if urls else 0,
#         "capped": int(per_host_cap or 0),
#         "deduped": agg.get("stats", {}).get("deduped", 0),
#         "ranked": agg.get("stats", {}).get("ranked", 0),
#         "knobs": {
#             "allow_domains": allow_domains,
#             "block_domains": block_domains,
#             "per_host_cap": per_host_cap,
#             "dedupe_threshold": dedupe_threshold,
#             "language": language,
#         },
#         "t_ms": int((time.time() - t0) * 1000),
#     }

#     payload = {
#         "topic": topic,
#         "urls": urls_in,
#         "docs": docs,
#         "facts": agg["facts"],
#         "outline": outline["slides"],
#         "citations": cites["citations"],
#         "stats": stats,
#     }

#     # Persist compact artifacts
#     _save_json(project_id, "citations.json", payload["citations"])
#     _save_json(project_id, "outline.json", payload["outline"])
#     _save_json(project_id, "docs.json", [{"title": d["title"], "url": d["url"], "bullets": d["bullets"]} for d in docs])
#     _save_json(project_id, "stats.json", stats)

#     getattr(ctx, "emit_event", lambda *a, **k: None)(
#         "research.pipeline.done",
#         {"slides": len(payload["outline"]), "docs": len(docs), "t_ms": stats["t_ms"]},
#     )
#     return payload

# ────────────────────────────────────────────────────────────────────────────────
# Permissions (ToolRouter picks these up)
# ────────────────────────────────────────────────────────────────────────────────

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
# import os, json, re, hashlib, time, html, logging, unicodedata
# from dataclasses import dataclass
# from typing import Any, Dict, List, Tuple, Optional
# from pathlib import Path
# from urllib.parse import urlparse
# from collections import defaultdict

# import requests
# from trafilatura import extract as trafi_extract  # readability
# from trafilatura.settings import use_config

# from app.core.safety import safe_fetch_to_temp

# log = logging.getLogger(__name__)

# # ---------- small utilities ----------

# _ARTIFACTS_ROOT = Path(os.getenv("ARTIFACTS_ROOT", "artifacts")).resolve()

# def _slug(s: str, maxlen: int = 64) -> str:
#     s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
#     s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
#     return s[:maxlen] or "item"

# def _ensure_dir(p: Path) -> None:
#     p.mkdir(parents=True, exist_ok=True)

# def _project_dir(project_id: str) -> Path:
#     p = _ARTIFACTS_ROOT / project_id / "research"
#     _ensure_dir(p)
#     return p

# def _save_json(project_id: str, name: str, data: Any) -> Path:
#     p = _project_dir(project_id) / name
#     with p.open("w", encoding="utf-8") as f:
#         json.dump(data, f, ensure_ascii=False, indent=2)
#     return p

# def _readfile(path: str) -> str:
#     with open(path, "rb") as f:
#         b = f.read()
#     try:
#         return b.decode("utf-8")
#     except UnicodeDecodeError:
#         return b.decode("latin-1", errors="ignore")

# def _hash(s: str) -> str:
#     return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]

# def _host(u: str) -> str:
#     try:
#         return urlparse(u).hostname or ""
#     except Exception:
#         return ""

# def _domain_ok(host: str, allow: Optional[List[str]], block: Optional[List[str]]) -> bool:
#     h = (host or "").lower()
#     if block:
#         for b in block:
#             if h.endswith((b or "").lower()):
#                 return False
#     if allow:
#         return any(h.endswith((a or "").lower()) for a in allow)
#     return True

# _WORD_RE = re.compile(r"[A-Za-z\u0600-\u06FF0-9]+")

# def _shingles(text: str, n: int = 5) -> set[str]:
#     toks = _WORD_RE.findall(text or "")
#     if len(toks) < n:
#         return set([" ".join(toks).lower()]) if toks else set()
#     return set(" ".join(toks[i:i+n]).lower() for i in range(0, len(toks) - n + 1))

# def _jaccard(a: set[str], b: set[str]) -> float:
#     if not a or not b:
#         return 0.0
#     inter = len(a & b)
#     if inter == 0:
#         return 0.0
#     return inter / float(len(a | b))


# # ---------- LLM helpers (optional, robust fallbacks) ----------

# def _llm_available() -> bool:
#     try:
#         import openai  # noqa
#         return bool(os.getenv("OPENAI_API_KEY"))
#     except Exception:
#         return False

# def _llm_chat_json(system: str, user: str, schema_hint: str, model: Optional[str] = None) -> Dict[str, Any]:
#     """
#     Minimal JSON-mode chat using OpenAI SDK v1; falls back to 'gpt-4o-mini' if model unset.
#     If any error occurs, returns {}.
#     """
#     try:
#         from openai import OpenAI
#         client = OpenAI()
#         mdl = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
#         resp = client.chat.completions.create(
#             model=mdl,
#             temperature=0.2,
#             response_format={"type": "json_object"},
#             messages=[
#                 {"role": "system", "content": system},
#                 {"role": "user", "content": f"{user}\n\nJSON schema hint:\n{schema_hint}"},
#             ],
#         )
#         txt = resp.choices[0].message.content or "{}"
#         return json.loads(txt)
#     except Exception as e:
#         log.warning("LLM JSON call failed, falling back: %s", e)
#         return {}

# # ---------- Search / fetch / extract ----------

# @dataclass
# class SearchResult:
#     title: str
#     url: str
#     snippet: str

# def search(ctx, query: str, num: int = 8, lang: Optional[str] = None) -> Dict[str, Any]:
#     """
#     Uses SerpAPI if SERPAPI_KEY is set; otherwise a no-op engine.
#     Returns {"results":[{title,url,snippet}], "engine":"serpapi|none"}
#     """
#     key = os.getenv("SERPAPI_KEY")
#     results: List[SearchResult] = []
#     engine = "none"
#     if key:
#         params = {
#             "engine": "google",
#             "q": query,
#             "num": min(max(num, 1), 20),
#             "hl": lang or "en",
#             "api_key": key,
#         }
#         r = requests.get("https://serpapi.com/search.json", params=params, timeout=20)
#         r.raise_for_status()
#         data = r.json()
#         for item in (data.get("organic_results") or [])[:num]:
#             url = item.get("link") or ""
#             if not url.startswith("http"):
#                 continue
#             results.append(SearchResult(
#                 title=item.get("title") or url,
#                 url=url,
#                 snippet=item.get("snippet") or "",
#             ))
#         engine = "serpapi"

#     out = {"results": [r.__dict__ for r in results], "engine": engine, "query": query}
#     getattr(ctx, "emit_event", lambda *a, **k: None)("research.search.done", out)
#     return out

# def fetch_html(ctx, url: str, max_bytes: int = 5_000_000) -> Dict[str, Any]:
#     """
#     SSRF-safe fetch to temp, then read text.
#     """
#     ua = os.getenv("HTTP_USER_AGENT")  # optional override
#     headers = {"User-Agent": ua} if ua else None
#     tmp, n, ctype = safe_fetch_to_temp(
#         url,
#         max_bytes=max_bytes,
#         timeout=30,
#         headers=headers,     # ← forward headers
#     )
#     html_text = _readfile(tmp)
#     os.remove(tmp)
#     out = {"url": url, "bytes": n, "content_type": ctype, "html": html_text}
#     getattr(ctx, "emit_event", lambda *a, **k: None)("research.fetch.done", {"url": url, "bytes": n})
#     return out


# def extract_readable(ctx, html_text: str, url: str) -> Dict[str, Any]:
#     """
#     Clean main content using trafilatura; fall back to naive tag strip.
#     """
#     cfg = use_config()
#     cfg.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
#     text = trafi_extract(html_text, url=url, favor_recall=True, include_links=False, config=cfg)
#     if not text:
#         # naive strip
#         text = re.sub(r"<[^>]+>", " ", html_text)
#         text = html.unescape(text)
#     text = re.sub(r"\s+\n", "\n", text).strip()
#     out = {"url": url, "text": text[:250000]}  # cap
#     getattr(ctx, "emit_event", lambda *a, **k: None)("research.extract.done", {"url": url, "chars": len(out["text"])})
#     return out

# # ---------- Summarize each document ----------

# _SUMMARY_SYS = (
#     "You are a careful researcher. Extract objective, verifiable information as crisp bullets. "
#     "Prefer concrete numbers, dates, names, and definitions. Never fabricate."
# )
# _SUMMARY_USER_TMPL = (
#     "Summarize the following article for slide bullets. "
#     "Return JSON with fields: title, top_bullets (<=6), key_facts (<=4), "
#     "and any warnings if the content is opinion/speculative.\n\nURL: {url}\n\nCONTENT:\n{content}"
# )
# _SUMMARY_SCHEMA_HINT = '{"title": "str", "top_bullets": ["str", "..."], "key_facts": ["str", "..."], "warnings": ["str", "..."]}'

# def _fallback_bullets(text: str, max_bullets: int = 6) -> List[str]:
#     # crude: first few sentences trimmed
#     sents = re.split(r"(?<=[\.\!\?])\s+", text or "")
#     out: List[str] = []
#     for s in sents:
#         s = re.sub(r"\s+", " ", s).strip()
#         if not s:
#             continue
#         if len(out) < max_bullets:
#             out.append(s[:120].rstrip() + ("…" if len(s) > 120 else ""))
#         else:
#             break
#     return out

# def summarize_doc(ctx, text: str, url: str, title_hint: Optional[str] = None) -> Dict[str, Any]:
#     if _llm_available():
#         data = _llm_chat_json(
#             _SUMMARY_SYS,
#             _SUMMARY_USER_TMPL.format(url=url, content=text[:12000]),
#             _SUMMARY_SCHEMA_HINT,
#         )
#         if data:
#             title = data.get("title") or (title_hint or url)
#             bullets = [b.strip() for b in (data.get("top_bullets") or []) if b and isinstance(b, str)]
#             facts = [b.strip() for b in (data.get("key_facts") or []) if b and isinstance(b, str)]
#             return {"url": url, "title": title, "bullets": bullets, "facts": facts, "warnings": data.get("warnings") or []}
#     # fallback
#     return {"url": url, "title": title_hint or url, "bullets": _fallback_bullets(text), "facts": []}

# # ---------- Aggregate & outline (with filters / dedupe / ranking) ----------

# def _norm_bullet(b: str) -> str:
#     b = (b or "").strip().lower()
#     b = re.sub(r"[^a-z0-9\s\-\._%:\(\)]", "", b)
#     b = re.sub(r"\s+", " ", b)
#     return b

# def _cap_urls_per_host(urls: List[str], k: int, allow: Optional[List[str]], block: Optional[List[str]]) -> List[str]:
#     if k <= 0:
#         k = 2
#     buckets: Dict[str, List[str]] = defaultdict(list)
#     out: List[str] = []
#     for u in urls:
#         h = _host(u)
#         if not _domain_ok(h, allow, block):
#             continue
#         if len(buckets[h]) >= k:
#             continue
#         buckets[h].append(u)
#         out.append(u)
#     return out

# def _dedupe_docs(docs: List[Dict[str, Any]], threshold: float = 0.82) -> List[Dict[str, Any]]:
#     kept: List[Dict[str, Any]] = []
#     sigs: List[set[str]] = []
#     for d in docs:
#         # signature from bullets (preferred) then facts then title
#         corpus = " ".join((d.get("bullets") or [])) or " ".join(d.get("facts") or []) or (d.get("title") or "")
#         sig = _shingles(corpus, 5)
#         is_dup = False
#         for s2 in sigs:
#             if _jaccard(sig, s2) >= threshold:
#                 is_dup = True
#                 break
#         if not is_dup:
#             kept.append(d)
#             sigs.append(sig)
#     return kept

# def _rank_docs(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
#     seen_hosts: set[str] = set()
#     ranked: List[Tuple[float, Dict[str, Any]]] = []
#     for d in docs:
#         host = _host(d.get("url", ""))
#         blen = len(" ".join(d.get("bullets") or []))
#         flen = len(" ".join(d.get("facts") or []))
#         diversity_bonus = 200 if host and host not in seen_hosts else 0
#         score = blen + flen + diversity_bonus
#         ranked.append((score, d))
#         seen_hosts.add(host)
#     ranked.sort(key=lambda t: t[0], reverse=True)
#     return [d for _, d in ranked]

# def aggregate(
#     ctx,
#     docs: List[Dict[str, Any]],
#     topic: str,
#     *,
#     allow_domains: Optional[List[str]] = None,
#     block_domains: Optional[List[str]] = None,
#     per_host_cap: int = 2,
#     dedupe_threshold: float = 0.82,
#     language_hint: Optional[str] = None,  # reserved for future use
# ) -> Dict[str, Any]:
#     """
#     Merge & dedupe bullets with domain filters and ranking.
#     Returns {"topic", "facts", "stats":{...}}
#     """
#     # Filter by domain (in case docs provided externally)
#     filtered: List[Dict[str, Any]] = []
#     for d in docs or []:
#         if _domain_ok(_host(d.get("url","")), allow_domains, block_domains):
#             filtered.append(d)

#     # Cap per host (keep best by bullet-content length)
#     buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
#     for d in filtered:
#         buckets[_host(d.get("url",""))].append(d)
#     capped: List[Dict[str, Any]] = []
#     for h, lst in buckets.items():
#         lst2 = sorted(lst, key=lambda x: len(" ".join(x.get("bullets") or [])) + len(" ".join(x.get("facts") or [])), reverse=True)
#         capped.extend(lst2[: max(1, per_host_cap)])

#     # Deduplicate near-duplicate docs
#     docs_dedup = _dedupe_docs(capped, threshold=dedupe_threshold)

#     # Rank for coverage/diversity
#     ranked = _rank_docs(docs_dedup)

#     # Build merged bullets → facts with source ids
#     seen_bullets: Dict[str, str] = {}
#     facts: List[Dict[str, Any]] = []
#     for i, d in enumerate(ranked):
#         sid = f"s{i+1}"
#         for b in d.get("bullets", []):
#             nb = _norm_bullet(b)
#             if not nb or nb in seen_bullets:
#                 continue
#             seen_bullets[nb] = sid
#             facts.append({"text": b.strip(), "source": sid, "url": d.get("url")})
#             if len(facts) >= 60:
#                 break
#         if len(facts) >= 60:
#             break

#     getattr(ctx, "emit_event", lambda *a, **k: None)("research.aggregate.done", {"bullets": len(facts)})
#     return {
#         "topic": topic,
#         "facts": facts,
#         "stats": {
#             "input_docs": len(docs or []),
#             "filtered": len(filtered),
#             "capped": len(capped),
#             "deduped": len(docs_dedup),
#             "ranked": len(ranked),
#         },
#     }

# _OUTLINE_SYS = (
#     "You turn research bullets into a clean slide outline. "
#     "Each slide has a short title and 3–5 concise bullets (<=12 words each). "
#     "Keep it objective and structured. Include a slide order."
# )
# _OUTLINE_USER_TMPL = (
#     "Topic: {topic}\n"
#     "Bullets (source-tagged):\n{bullets_json}\n\n"
#     "Return JSON: slides=[{title, bullets:[str], note?, image_prompt?, citation_sources:[str]}]."
# )
# _OUTLINE_SCHEMA_HINT = '{"slides":[{"title":"str","bullets":["str"],"note":"str","image_prompt":"str","citation_sources":["s1"]}]}'

# def outline_slides(ctx, facts: List[Dict[str, Any]], topic: str, style: str = "default") -> Dict[str, Any]:
#     # Prepare compact facts list for the prompt
#     bullets_json = json.dumps([{"t": f["text"], "s": f["source"]} for f in facts][:60], ensure_ascii=False)
#     if _llm_available():
#         data = _llm_chat_json(
#             _OUTLINE_SYS,
#             _OUTLINE_USER_TMPL.format(topic=topic, bullets_json=bullets_json),
#             _OUTLINE_SCHEMA_HINT,
#         )
#         slides = data.get("slides") if isinstance(data, dict) else None
#         if isinstance(slides, list) and slides:
#             out = []
#             for s in slides:
#                 out.append({
#                     "title": s.get("title") or "",
#                     "bullets": [b for b in (s.get("bullets") or []) if isinstance(b, str)][:6],
#                     "note": s.get("note") or "",
#                     "image_prompt": s.get("image_prompt") or f"{topic}, {style}, presentation slide photo",
#                     "citations": [c for c in (s.get("citation_sources") or []) if isinstance(c, str)],
#                 })
#             getattr(ctx, "emit_event", lambda *a, **k: None)("research.outline.done", {"slides": len(out)})
#             return {"slides": out}
#     # fallback: simple chunking
#     chunks = [facts[i:i+4] for i in range(0, min(len(facts), 28), 4)]
#     slides = []
#     if chunks:
#         slides.append({"title": f"Overview: {topic}", "bullets": [c["text"] for c in chunks[0]], "note": "", "image_prompt": f"{topic}", "citations": list({c["source"] for c in chunks[0]})})
#     for ch in chunks[1:]:
#         slides.append({"title": f"More on {topic}", "bullets": [c["text"] for c in ch], "note": "", "image_prompt": f"{topic}", "citations": list({c["source"] for c in ch})})
#     getattr(ctx, "emit_event", lambda *a, **k: None)("research.outline.done", {"slides": len(slides)})
#     return {"slides": slides}

# def cite_pack(ctx, docs: List[Dict[str, Any]]) -> Dict[str, Any]:
#     """
#     Build compact citation map: s1->{title,url}, s2->{...}
#     """
#     m: Dict[str, Dict[str, Any]] = {}
#     for i, d in enumerate(docs):
#         sid = f"s{i+1}"
#         title = (d.get("title") or "").strip() or d.get("url") or sid
#         m[sid] = {"id": sid, "title": title, "url": d.get("url")}
#     return {"citations": m}

# # ---------- Master op used by slides.generate ----------

# def research_pipeline(
#     ctx,
#     project_id: str,
#     topic: Optional[str] = None,
#     urls: Optional[List[str]] = None,
#     lang: Optional[str] = None,
#     max_docs: int = 8,
#     *,
#     allow_domains: Optional[List[str]] = None,
#     block_domains: Optional[List[str]] = None,
#     per_host_cap: int = 2,
#     dedupe_threshold: float = 0.82,
#     language_hint: Optional[str] = None,
# ) -> Dict[str, Any]:
#     """
#     High-level: search (or use provided urls) -> fetch -> extract -> summarize -> aggregate -> outline + citations.
#     Persists artifacts under artifacts/{project}/research/.
#     """
#     t0 = time.time()
#     urls_in: List[str] = []
#     results_meta: List[Dict[str, Any]] = []

#     # 1) inputs (topic → search, or explicit urls)
#     if urls:
#         urls_in = [u for u in urls if isinstance(u, str) and u.startswith("http")]
#     elif topic:
#         sr = search(ctx, topic, num=max_docs, lang=lang or language_hint)
#         raw = [r["url"] for r in (sr.get("results") or []) if r.get("url")]
#         # keep metadata (for titles)
#         results_meta = list(sr.get("results") or [])
#         # domain filter + cap per host early (saves fetch cost)
#         urls_in = _cap_urls_per_host(raw, per_host_cap, allow_domains, block_domains)
#         # still limit to max_docs
#         urls_in = urls_in[:max_docs]

#     # 2) fetch → extract → summarize
#     docs_raw: List[Dict[str, Any]] = []
#     for u in urls_in[:max_docs]:
#         try:
#             f = fetch_html(ctx, u)
#             e = extract_readable(ctx, f["html"], u)
#             title_hint = next((m.get("title") for m in results_meta if m.get("url") == u), None)
#             s = summarize_doc(ctx, e["text"], u, title_hint=title_hint)
#             docs_raw.append({"url": u, "title": s["title"], "bullets": s["bullets"], "facts": s.get("facts", [])})
#         except Exception as ex:
#             log.warning("Research fetch/summarize failed for %s: %s", u, ex)

#     # 3) aggregate (filters/dedupe/ranking)
#     agg = aggregate(
#         ctx,
#         docs_raw,
#         topic or (urls_in[0] if urls_in else "Slides"),
#         allow_domains=allow_domains,
#         block_domains=block_domains,
#         per_host_cap=per_host_cap,
#         dedupe_threshold=dedupe_threshold,
#         language_hint=language_hint or lang,
#     )

#     # 4) outline + citations
#     outline = outline_slides(ctx, agg["facts"], topic or "Slides")
#     cites = cite_pack(ctx, docs_raw)

#     payload = {
#         "topic": topic,
#         "urls": urls_in,
#         "docs": docs_raw,
#         "facts": agg["facts"],
#         "outline": outline["slides"],
#         "citations": cites["citations"],
#         "stats": agg.get("stats", {}),
#         "t_ms": int((time.time() - t0) * 1000),
#         # echo knobs for traceability
#         "knobs": {
#             "allow_domains": allow_domains,
#             "block_domains": block_domains,
#             "per_host_cap": per_host_cap,
#             "dedupe_threshold": dedupe_threshold,
#             "language_hint": language_hint or lang,
#         },
#     }

#     # Persist compact artifacts
#     _save_json(project_id, "citations.json", payload["citations"])
#     _save_json(project_id, "outline.json", payload["outline"])
#     _save_json(project_id, "docs.json", [{"title": d["title"], "url": d["url"], "bullets": d["bullets"]} for d in docs_raw])
#     _save_json(project_id, "stats.json", {"urls": urls_in, **payload.get("stats", {}), "t_ms": payload["t_ms"], "knobs": payload["knobs"]})

#     getattr(ctx, "emit_event", lambda *a, **k: None)("research.pipeline.done", {"slides": len(payload["outline"]), "docs": len(docs_raw), "t_ms": payload["t_ms"]})
#     return payload

# # Mark permissions so your ToolRouter can allow FS r/w
# research_pipeline.required_permissions = {"fs_read", "fs_write"}
# search.required_permissions = set()
# fetch_html.required_permissions = {"fs_read"}
# extract_readable.required_permissions = set()
# summarize_doc.required_permissions = set()
# aggregate.required_permissions = set()
# outline_slides.required_permissions = set()
# cite_pack.required_permissions = set()
