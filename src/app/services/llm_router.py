# app/services/llm_router.py
import os, json

def _openai_chat(model: str, prompt: str) -> str:
    base = os.getenv("LOCAL_OPENAI_BASE_URL", "https://api.openai.com/v1")
    key  = os.getenv("OPENAI_API_KEY", "")
    if not key:
        return "[]"
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
    if task == "structure" and tokens > 12000:
        return os.getenv("MODEL_STRUCTURE_LONG", "gpt-4o")
    return {
        "structure": os.getenv("MODEL_STRUCTURE", "gpt-4o-mini"),
        "rewrite":   os.getenv("MODEL_REWRITE",   "claude-3-haiku"),
        "vision":    os.getenv("MODEL_VISION",    "gemini-1.5-pro"),
    }.get(task, os.getenv("MODEL_FALLBACK", "gpt-4o-mini"))

def run_structurer(outline, inputs):
    # Allow bypass for debugging
    if os.getenv("STRUCTURE_BYPASS") == "1":
        return outline
    model = route_model("structure", tokens=len(json.dumps(outline)))
    prompt = f"""Tighten and de-duplicate this slide outline.
Keep sections <= {inputs.get('max_slides',12)}.
Return JSON: [{{"title":"","bullets":["",""]}}]"""
    try:
        txt = _openai_chat(model, prompt + "\n\n" + json.dumps(outline, ensure_ascii=False))
        return json.loads(txt)
    except Exception:
        return outline
