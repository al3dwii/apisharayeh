
# app/plugins/slides_generate/quality.py
from __future__ import annotations
from typing import List, Dict, Any

def _trim(s: str) -> str:
    return " ".join((s or "").strip().split())

def _split_long_bullet(text: str, max_words: int = 12) -> list[str]:
    words = text.split()
    if len(words) <= max_words:
        return [text]
    # try to break by punctuation first
    for sep in ["؛", ";", "—", "–", "،", ",", ".", " - "]:
        if sep in text and len(text.split(sep)[0].split()) >= 4:
            parts = [p.strip() for p in text.split(sep) if p.strip()]
            if 1 < len(parts) <= 3:
                return parts
    # otherwise chunk by words
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words:
            out.append(" ".join(cur))
            cur = []
    if cur: out.append(" ".join(cur))
    return out[:2]  # cap sub-bullets

def normalize_outline(outline: List[Dict[str, Any]], max_slides: int = 12, language: str = "ar") -> List[Dict[str, Any]]:
    out = []
    for i, s in enumerate(outline, 1):
        title = _trim(s.get("title",""))
        bullets = s.get("bullets") or []
        notes = s.get("notes","")
        norm_bullets: list[Any] = []
        for b in bullets[:10]:  # hard cap raw inputs
            if isinstance(b, dict):
                text = _trim(b.get("text",""))
                subs = [_trim(x) for x in (b.get("subs") or []) if _trim(x)]
            else:
                text = _trim(str(b))
                subs = []
            if not text: 
                continue
            # colon heuristic → split into title/detail
            if ":" in text and len(text.split(":")[0]) < 40 and len(subs) == 0:
                head, tail = text.split(":", 1)
                text = head.strip()
                firsts = _split_long_bullet(tail.strip(), 10)
                subs = firsts[:2]
            # length heuristic
            if len(text.split()) > 14:
                parts = _split_long_bullet(text, 12)
                text = parts[0]
                subs = (subs or []) + parts[1:2]
            norm_bullets.append({"text": text, "subs": subs})
            if len(norm_bullets) >= 6:  # <= 6 bullets
                break

        slide = {
            "title": title or f"شريحة {i}",
            "bullets": norm_bullets,
            "notes": notes,
            "layout_hint": s.get("layout_hint","auto"),
            "rtl": True if language.startswith(("ar","fa","ur","he")) else False,
        }
        out.append(slide)
        if len(out) >= max_slides:
            break

    # Ensure a cover if first slide has many bullets
    if out and out[0].get("bullets"):
        cover = {"title": out[0]["title"], "bullets": [], "notes": "", "layout_hint":"title", "rtl": out[0]["rtl"]}
        out.insert(0, cover)
        if len(out) > max_slides:
            out = out[:max_slides]
    return out
