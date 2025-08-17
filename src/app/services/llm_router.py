# app/services/llm_router.py
import os, json, re

def _extract_topic(prompt: str) -> str:
    """Heuristic topic extractor from the last non-empty line of the prompt."""
    lines = [l.strip() for l in prompt.splitlines() if l.strip()]
    if not lines:
        return "عرض تقديمي"
    # Drop obvious instruction lines that mention JSON
    for line in reversed(lines):
        if "JSON" in line or "Return JSON" in line:
            continue
        # Trim very long lines
        return line[:120]
    return lines[-1][:120]

def _mock_json_outline(prompt: str) -> list[dict]:
    """Deterministic, sensible outline for local dev (no API key)."""
    topic = _extract_topic(prompt) or "عرض تقديمي"
    slides: list[dict] = [
        {"title": topic, "subtitle": "عرض تقديمي شامل"}
    ]
    sections = [
        ("مقدمة",            ["نظرة عامة سريعة", "أهداف العرض"]),
        ("المفاهيم الأساسية", ["تعريفات مختصرة", "أمثلة تطبيقية"]),
        ("الفوائد",           ["تحسين الكفاءة", "خفض التكاليف"]),
        ("التحديات",          ["عوائق التنفيذ", "مخاطر شائعة"]),
        ("الأدوات",           ["منصات رئيسية", "خصائص مهمة"]),
        ("أفضل الممارسات",    ["منهجية العمل", "نصائح عملية"]),
        ("خريطة الطريق",      ["خطوات التنفيذ", "مؤشرات قياس"]),
        ("الخلاصة والتوصيات", ["ملخص النقاط", "التوصيات المقبلة"]),
    ]
    for title, bullets in sections:
        slides.append({"title": f"{title} — {topic}", "bullets": bullets})
    return slides

def _openai_chat(model: str, prompt: str) -> str:
    """
    Thin HTTP client for OpenAI-compatible /chat/completions endpoint.
    If no API key, return a valid JSON slide outline when the prompt asks for JSON.
    """
    base = os.getenv("LOCAL_OPENAI_BASE_URL", "https://api.openai.com/v1")
    key  = os.getenv("OPENAI_API_KEY", "")

    # Offline/mock mode
    if not key:
        wants_json = (
            "Return JSON" in prompt
            or "JSON:" in prompt
            or re.search(r"\[\s*{\s*\"title\"", prompt) is not None
        )
        if wants_json:
            return json.dumps(_mock_json_outline(prompt), ensure_ascii=False)
        # Fallback plain text (not used by slides.generate path)
        topic = _extract_topic(prompt)
        return (
            f"عنوان: {topic}\n"
            "• نظرة عامة\n"
            "• أهداف العرض\n"
            "• النقاط الرئيسية\n"
            "• التحديات والحلول\n"
            "• الخلاصة والتوصيات"
        )

    # Real call
    import requests
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages":[{"role":"user","content":prompt}]},
        timeout=60
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def route_model(task: str, tokens: int = 0) -> str:
    """Simple task→model routing with env overrides."""
    if task == "structure" and tokens > 12000:
        return os.getenv("MODEL_STRUCTURE_LONG", "gpt-4o")
    table = {
        "structure":  os.getenv("MODEL_STRUCTURE",  "gpt-4o-mini"),
        "rewrite":    os.getenv("MODEL_REWRITE",    "claude-3-haiku"),
        "vision":     os.getenv("MODEL_VISION",     "gemini-1.5-pro"),
        "chat":       os.getenv("MODEL_CHAT",       os.getenv("MODEL_FALLBACK", "gpt-4o-mini")),
        "translate":  os.getenv("MODEL_TRANSLATE",  os.getenv("MODEL_FALLBACK", "gpt-4o-mini")),
    }
    return table.get(task, os.getenv("MODEL_FALLBACK", "gpt-4o-mini"))

def run_structurer(outline, inputs):
    """
    Optional post-LLM tightening pass (can be bypassed via STRUCTURE_BYPASS=1).
    """
    if os.getenv("STRUCTURE_BYPASS") == "1":
        return outline
    model = route_model("structure", tokens=len(json.dumps(outline, ensure_ascii=False)))
    prompt = f"""Tighten and de-duplicate this slide outline.
Keep sections <= {inputs.get('max_slides',12)}.
Return JSON: [{{"title":"","bullets":["",""]}}]"""
    try:
        txt = _openai_chat(model, prompt + "\n\n" + json.dumps(outline, ensure_ascii=False))
        return json.loads(txt)
    except Exception:
        return outline

# --- Helpers used by app.services.models.ModelRouter -------------------------

def run_chat(content: str, task: str = "chat") -> str:
    """
    Generic chat entry used by ModelRouter.chat().
    In dev/no-key mode this returns a JSON slide array if the prompt demands JSON.
    """
    model = route_model(task)
    return _openai_chat(model, content)

def run_translate(text: str, source: str | None, target: str | None) -> str:
    """
    Simple translate helper used by ModelRouter.translate().
    If no key is set, returns the input text unchanged.
    """
    model = route_model("translate")
    preface = "Translate the following text"
    if source:
        preface += f" from {source}"
    if target:
        preface += f" to {target}"
    preface += ":\n\n"
    out = _openai_chat(model, preface + text)
    return out if out else text
