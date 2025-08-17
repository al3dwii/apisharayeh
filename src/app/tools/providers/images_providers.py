# app/services/tools/providers/images_providers.py
from __future__ import annotations
import os, base64
from typing import Any, Dict, Optional

def _require_requests():
    try:
        import requests  # noqa
    except Exception as e:
        raise RuntimeError("Please `pip install requests` in your worker image.") from e

def _unsplash_search_http(query: str, per_page: int = 6):
    _require_requests()
    import requests
    key = os.getenv("UNSPLASH_ACCESS_KEY")
    if not key:
        return {"results": []}
    r = requests.get(
        "https://api.unsplash.com/search/photos",
        headers={"Authorization": f"Client-ID {key}"},
        params={"query": query, "per_page": per_page, "orientation": "landscape"},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    out = []
    for it in data.get("results", []):
        urls = it.get("urls") or {}
        url = urls.get("regular") or urls.get("full") or urls.get("small")
        if url:
            out.append({"url": url, "width": it.get("width"), "height": it.get("height")})
    return {"results": out}

def _bing_image_search_http(q: str, count: int = 8):
    _require_requests()
    import requests
    key = os.getenv("BING_IMAGE_SEARCH_KEY")
    if not key:
        return {"value": []}
    r = requests.get(
        "https://api.bing.microsoft.com/v7.0/images/search",
        headers={"Ocp-Apim-Subscription-Key": key},
        params={"q": q, "count": count, "safeSearch": "Moderate"},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    return {"value": data.get("value", [])}

def _stability_sdxl_http(prompt: str, width: int = 1280, height: int = 720):
    _require_requests()
    import requests
    key = os.getenv("STABILITY_API_KEY")
    if not key:
        # Return empty result; attach_images() will fallback elsewhere if needed
        return {"image_b64": None}
    r = requests.post(
        "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={
            "text_prompts": [{"text": prompt}],
            "width": width,
            "height": height,
            "cfg_scale": 7,
            "samples": 1,
            "steps": 30,
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    arts = data.get("artifacts") or []
    if not arts or not arts[0].get("base64"):
        return {"image_b64": None}
    return {"image_b64": arts[0]["base64"]}

def register_image_providers(tool_router) -> None:
    """
    Registers three callable tools if your ToolRouter supports .register(tool_id, fn).
    - unsplash.search:  payload -> {"results":[{"url","width","height"}]}
    - bing.image.search: payload -> {"value":[{"contentUrl","width","height"}]}
    - sdxl.generate:     payload -> {"url":...} or {"image_b64":...}
    If .register doesn't exist, this function is a no-op (HTTP fallbacks in images.py still work).
    """
    reg_fn = getattr(tool_router, "register", None) or getattr(tool_router, "add", None)
    if not callable(reg_fn):
        return

    def _unsplash_fn(payload: Dict[str, Any]) -> Dict[str, Any]:
        query = payload.get("query") or payload.get("q") or ""
        per_page = int(payload.get("per_page", 6))
        try:
            return _unsplash_search_http(query, per_page=per_page)
        except Exception:
            return {"results": []}

    def _bing_fn(payload: Dict[str, Any]) -> Dict[str, Any]:
        q = payload.get("q") or payload.get("query") or ""
        count = int(payload.get("count", 8))
        try:
            return _bing_image_search_http(q, count=count)
        except Exception:
            return {"value": []}

    def _sdxl_fn(payload: Dict[str, Any]) -> Dict[str, Any]:
        prompt = payload.get("prompt") or ""
        width  = int(payload.get("width", 1280))
        height = int(payload.get("height", 720))
        try:
            data = _stability_sdxl_http(prompt, width=width, height=height)
            if data.get("image_b64"):
                return data
            return {}  # no image
        except Exception:
            return {}

    reg_fn("unsplash.search", _unsplash_fn)
    reg_fn("bing.image.search", _bing_fn)
    reg_fn("sdxl.generate", _sdxl_fn)
