# app/plugins/slides_generate/images.py
from __future__ import annotations

from typing import List, Dict, Any, Optional, Callable
import base64
import os
import re
import tempfile
import time

# Optional: artifacts saving (used for raw bytes/base64)
try:
    from app.services.artifacts import save_file
except Exception:
    def save_file(local_path: str, default_ext: str):
        return {"key": os.path.basename(local_path), "url": f"file://{local_path}"}

# ---------- simple helpers ----------

AR_STOP = set("""
من في على إلى عن أن إن كان كانت يكون يكونون تكون تكونون هو هي هم هن هذا هذه هؤلاء تلك ذلك ثم أو أم بل قد لقد ألا إلا حتى حيث كأن لكن لأن لعل لو لما لم لن ما لا إنما كما كل بعض أي إذا إذ إذن مع لدى عند خلال نحو نحوًا عبر عبرًا بين بدون دون غير سوى
""".split())

def re_split_tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[\s,؛،:.\(\)\[\]\{\}\-_/]+", s) if t]

def _keywords(title: str, bullets: list) -> str:
    text = (title or "").strip()
    if not text and bullets:
        text = bullets[0].get("text") if isinstance(bullets[0], dict) else str(bullets[0])
    toks = [t for t in re_split_tokens(text) if t and t not in AR_STOP and len(t) > 2]
    if not toks: toks = re_split_tokens(text)
    return " ".join(toks[:8])

def _prefer_landscape(images: list[dict]) -> Optional[dict]:
    if not images:
        return None
    for im in images:
        w = im.get("width") or 1200
        h = im.get("height") or 800
        if w >= h:
            return im
    return images[0]

def _save_bytes_to_artifact(data: bytes, suffix: str = ".png") -> Optional[str]:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.write(fd, data)
    os.close(fd)
    art = save_file(path, suffix)
    return art.get("url")

# ---------- ToolRouter wrappers (if available) ----------

def _tr_call(tr, tool_id: str, payload: dict) -> Optional[Any]:
    try:
        return tr.call(tool_id, payload)
    except Exception:
        return None

# ---------- Direct HTTP fallbacks (plug in your API keys) ----------

def _http_unsplash_search(query: str, per_page: int = 6) -> Optional[str]:
    """
    Requires env: UNSPLASH_ACCESS_KEY
    """
    key = os.getenv("UNSPLASH_ACCESS_KEY")
    if not key:
        return None
    import requests
    url = "https://api.unsplash.com/search/photos"
    headers = {"Authorization": f"Client-ID {key}"}
    params = {"query": query, "per_page": per_page, "orientation": "landscape"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        items = data.get("results") or []
        imgs = []
        for it in items:
            w = it.get("width"); h = it.get("height")
            # prefer 'urls' object
            uo = it.get("urls") or {}
            url = uo.get("regular") or uo.get("full") or uo.get("small")
            if url:
                imgs.append({"url": url, "width": w, "height": h})
        pick = _prefer_landscape(imgs)
        return pick.get("url") if pick else None
    except Exception:
        return None

def _http_bing_image_search(query: str, count: int = 8) -> Optional[str]:
    """
    Requires env: BING_IMAGE_SEARCH_KEY
    """
    key = os.getenv("BING_IMAGE_SEARCH_KEY")
    if not key:
        return None
    import requests
    url = "https://api.bing.microsoft.com/v7.0/images/search"
    headers = {"Ocp-Apim-Subscription-Key": key}
    params = {"q": query, "count": count, "safeSearch": "Moderate"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        items = data.get("value") or []
        imgs = [{"url": it.get("contentUrl"), "width": it.get("width"), "height": it.get("height")}
                for it in items if it.get("contentUrl")]
        pick = _prefer_landscape(imgs)
        return pick.get("url") if pick else None
    except Exception:
        return None

def _http_stability_sdxl(prompt: str, width: int = 1280, height: int = 720) -> Optional[str]:
    """
    Requires env: STABILITY_API_KEY
    Uses Stability AI SDXL text-to-image.
    """
    key = os.getenv("STABILITY_API_KEY")
    if not key:
        return None
    import requests
    url = "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image"
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    body = {
        "text_prompts": [{"text": prompt}],
        "width": width,
        "height": height,
        "cfg_scale": 7,
        "samples": 1,
        "steps": 30
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        arts = data.get("artifacts") or []
        if not arts:
            return None
        b64 = arts[0].get("base64")
        if not b64:
            return None
        img_bytes = base64.b64decode(b64)
        return _save_bytes_to_artifact(img_bytes, ".png")
    except Exception:
        return None

# ---------- Provider tries (ToolRouter first, HTTP fallback) ----------

def _try_unsplash(tr, query: str) -> Optional[str]:
    # ToolRouter path
    res = _tr_call(tr, "unsplash.search", {"query": query, "per_page": 6})
    if res:
        items = res.get("results") or res.get("data") or res.get("items") or res if isinstance(res, list) else []
        # normalize
        imgs = []
        for it in items:
            if isinstance(it, dict):
                u = it.get("url") or it.get("regular") or it.get("full") or (it.get("urls") or {}).get("regular")
                if u:
                    imgs.append({"url": u, "width": it.get("width"), "height": it.get("height")})
        pick = _prefer_landscape(imgs)
        if pick:
            return pick.get("url")
    # HTTP fallback
    return _http_unsplash_search(query)

def _try_bing_image(tr, query: str) -> Optional[str]:
    res = _tr_call(tr, "bing.image.search", {"q": query, "count": 8, "safeSearch": "Moderate"})
    if res:
        items = res.get("value") if isinstance(res, dict) else (res or [])
        imgs = [{"url": it.get("contentUrl"), "width": it.get("width"), "height": it.get("height")}
                for it in items if isinstance(it, dict) and it.get("contentUrl")]
        pick = _prefer_landscape(imgs)
        if pick:
            return pick.get("url")
    return _http_bing_image_search(query)

def _try_sdxl_generate(tr, prompt: str) -> Optional[str]:
    # ToolRouter path
    res = _tr_call(tr, "sdxl.generate", {"prompt": prompt, "width": 1280, "height": 720})
    if res:
        if isinstance(res, dict):
            if "url" in res:
                return res["url"]
            if "image_b64" in res:
                try:
                    data = base64.b64decode(res["image_b64"])
                    return _save_bytes_to_artifact(data, ".png")
                except Exception:
                    pass
            if "data" in res and isinstance(res["data"], (bytes, bytearray)):
                return _save_bytes_to_artifact(bytes(res["data"]), ".png")
        if isinstance(res, (bytes, bytearray)):
            return _save_bytes_to_artifact(bytes(res), ".png")
    # HTTP fallback (Stability AI SDXL)
    return _http_stability_sdxl(prompt, width=1280, height=720)

# ---------- Main attach function ----------

def attach_images(
    outline: List[Dict[str, Any]],
    tr,
    strategy: str = "hybrid",
    provider: Optional[str] = None,
    emit: Optional[Callable[[str, dict], None]] = None
) -> List[Dict[str, Any]]:
    """
    Fills slide['image'] where missing.
    strategy: 'search' | 'generate' | 'hybrid' | 'none'
    provider: optional hint ('unsplash'|'bing'|'sdxl'), otherwise we try a sensible order.
    emit: optional callback(event_type, payload) to mirror logs/events.
    """
    if strategy == "none":
        return outline

    def log(tool, **kw):
        if emit:
            emit("tool", {"tool": tool, **kw})

    for idx, s in enumerate(outline, 1):
        if s.get("image"):
            continue
        q = _keywords(s.get("title",""), s.get("bullets") or [])
        url: Optional[str] = None

        # Decide order
        order = []
        if strategy == "search":
            order = ["unsplash", "bing"]
        elif strategy == "generate":
            order = ["sdxl"]
        else:  # hybrid
            order = ["unsplash", "bing", "sdxl"]
        if provider in ("unsplash", "bing", "sdxl"):
            order = [provider] + [x for x in order if x != provider]

        for src in order:
            if src == "unsplash":
                log("unsplash.search", query=q)
                url = _try_unsplash(tr, q)
            elif src == "bing":
                log("bing.image.search", q=q)
                url = _try_bing_image(tr, q)
            elif src == "sdxl":
                p = f"High-quality, professional, royalty-free illustration for slide title: '{s.get('title','')}'. Clean, modern, education/corporate style, 16:9 landscape."
                log("sdxl.generate", prompt=p)
                url = _try_sdxl_generate(tr, p)

            if url:
                s["image"] = url
                break
    return outline


# # app/plugins/slides_generate/images.py
# from __future__ import annotations

# from typing import List, Dict, Any, Optional
# import base64
# import os
# import tempfile

# # Optional: artifacts saving if a tool returns raw bytes/base64 only
# try:
#     from app.services.artifacts import save_file
# except Exception:
#     def save_file(local_path: str, default_ext: str):
#         return {"key": os.path.basename(local_path), "url": f"file://{local_path}"}

# AR_STOP = set("""\
# من في على إلى عن أن إن كان كانت يكون يكونون تكون تكونون هو هي هم هن هذا هذه هؤلاء تلك ذلك ثم أو أم بل قد لقد ألا إلا حتى حيث كأن لكن لأن لعل لو لما لم لن ما لا إنما كما كل بعض أي إذا إذ إذن مع لدى عند خلال نحو نحوًا عبر عبرًا بين بدون دون غير سوى""".split())

# def _keywords(title: str, bullets: list) -> str:
#     text = (title or "").strip()
#     if not text and bullets:
#         text = bullets[0].get("text") if isinstance(bullets[0], dict) else str(bullets[0])
#     # very light keywording: drop stop-words and short tokens
#     toks = [t for t in re_split_tokens(text) if t and t not in AR_STOP and len(t) > 2]
#     # fallback: keep original if we lost everything
#     if not toks:
#         toks = re_split_tokens(text)
#     return " ".join(toks[:8])

# def re_split_tokens(s: str):
#     import re
#     return [t for t in re.split(r"[\\s,؛،:؛.()\\[\\]{}\\-_/]+", s) if t]

# def _prefer_landscape(images: list[dict]) -> Optional[dict]:
#     if not images:
#         return None
#     # pick 1st landscape-ish
#     for im in images:
#         w = im.get("width") or 1200
#         h = im.get("height") or 800
#         if w >= h:
#             return im
#     return images[0]

# def _try_unsplash(tr, query: str) -> Optional[str]:
#     # Assumes ToolRouter has tool id "unsplash.search" returning [{"url": "...", "width":..., "height":...}]
#     try:
#         res = tr.call("unsplash.search", {"query": query, "per_page": 5})
#         if isinstance(res, dict):
#             items = res.get("results") or res.get("data") or res.get("items") or []
#         else:
#             items = res or []
#         pick = _prefer_landscape(items)
#         if not pick:
#             return None
#         return pick.get("url") or pick.get("regular") or pick.get("full") or pick.get("src")
#     except Exception:
#         return None

# def _try_bing_image(tr, query: str) -> Optional[str]:
#     # Assumes tool id "bing.image.search" returning [{"contentUrl": "...", "width":..., "height":...}]
#     try:
#         res = tr.call("bing.image.search", {"q": query, "count": 8, "safeSearch": "Moderate"})
#         items = res.get("value") if isinstance(res, dict) else (res or [])
#         if not items:
#             return None
#         # map to common shape
#         imgs = [{"url": it.get("contentUrl"), "width": it.get("width"), "height": it.get("height")} for it in items if it.get("contentUrl")]
#         pick = _prefer_landscape(imgs)
#         return pick.get("url") if pick else None
#     except Exception:
#         return None

# def _save_bytes_to_artifact(data: bytes, suffix: str = ".png") -> Optional[str]:
#     fd, path = tempfile.mkstemp(suffix=suffix)
#     os.write(fd, data)
#     os.close(fd)
#     art = save_file(path, suffix)
#     return art.get("url")

# def _try_sdxl_generate(tr, prompt: str) -> Optional[str]:
#     # Assumes tool id "sdxl.generate" returning raw bytes or dict with 'image_b64' or 'url'
#     try:
#         res = tr.call("sdxl.generate", {"prompt": prompt, "width": 1280, "height": 720})
#         if isinstance(res, dict):
#             if "url" in res: 
#                 return res["url"]
#             if "image_b64" in res:
#                 data = base64.b64decode(res["image_b64"])
#                 return _save_bytes_to_artifact(data, ".png")
#             if "data" in res and isinstance(res["data"], (bytes, bytearray)):
#                 return _save_bytes_to_artifact(bytes(res["data"]), ".png")
#         if isinstance(res, (bytes, bytearray)):
#             return _save_bytes_to_artifact(bytes(res), ".png")
#     except Exception:
#         return None
#     return None

# def attach_images(outline: List[Dict[str, Any]], tr, strategy: str = "hybrid", provider: Optional[str] = None, emit=None) -> List[Dict[str, Any]]:
#     """
#     Fills slide['image'] where missing.
#     strategy: 'search' | 'generate' | 'hybrid' | 'none'
#     provider: optional hint ('unsplash'|'bing'|'sdxl'), otherwise we try a sensible order.
#     emit: optional callback(event_type, payload) to mirror logs/events.
#     """
#     if strategy == "none":
#         return outline

#     def log(tool, **kw):
#         if emit:
#             emit("tool", {"tool": tool, **kw})

#     for idx, s in enumerate(outline, 1):
#         if s.get("image"):
#             continue
#         q = _keywords(s.get("title",""), s.get("bullets") or [])
#         url: Optional[str] = None

#         # Decide order
#         order = []
#         if strategy == "search":
#             order = ["unsplash", "bing"]
#         elif strategy == "generate":
#             order = ["sdxl"]
#         else:  # hybrid
#             order = ["unsplash", "bing", "sdxl"]
#         if provider in ("unsplash", "bing", "sdxl"):
#             order = [provider] + [x for x in order if x != provider]

#         for src in order:
#             if src == "unsplash":
#                 log("unsplash.search", query=q)
#                 url = _try_unsplash(tr, q)
#             elif src == "bing":
#                 log("bing.image.search", q=q)
#                 url = _try_bing_image(tr, q)
#             elif src == "sdxl":
#                 # Construct a richer prompt for gen
#                 p = f"High-quality, professional, royalty-free illustration for slide title: '{s.get('title','')}'. Clean, modern, education/corporate style, 16:9 landscape."
#                 log("sdxl.generate", prompt=p)
#                 url = _try_sdxl_generate(tr, p)

#             if url:
#                 s["image"] = url
#                 break
#     return outline
