from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, Optional

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

# Controls whether to use Ray or run locally.
DISABLE_RAY = os.getenv("O2_DISABLE_RAY", "").lower() in {"1", "true", "yes"}
RAY_ADDRESS = os.getenv("RAY_ADDRESS")  # if unspecified, Ray starts local
TOOL_REGISTRY_URL = os.getenv("TOOL_REGISTRY_URL")

# Lazy import Ray
_ray_ready = False
try:
    import ray  # type: ignore
except Exception:  # pragma: no cover
    ray = None  # type: ignore


async def _ensure_ray() -> bool:
    """Start Ray if available and not disabled; return True if Ray is usable."""
    global _ray_ready
    if DISABLE_RAY or ray is None:
        return False
    if _ray_ready:
        return True
    try:
        # Ray is safe to call sync even from async contexts
        if ray.is_initialized():  # type: ignore[attr-defined]
            _ray_ready = True
            return True
        addr = RAY_ADDRESS or "local"
        ray.init(address=addr, ignore_reinit_error=True, log_to_driver=False)  # type: ignore[attr-defined]
        _ray_ready = True
        log.info("Ray initialized address=%s", addr)
        return True
    except Exception as e:
        log.warning("Ray init failed: %r; falling back to local execution", e)
        return False


# -------------------- tool registry (optional) --------------------
async def _registry_call(tool_id: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    If a registry exposes tool endpoints, call them here.
    Expected contract (example):
      POST {TOOL_REGISTRY_URL}/run/{tool_id}  -> {ok: bool, ...}
    """
    if not TOOL_REGISTRY_URL:
        return {"ok": False, "error": "registry not configured"}
    url = f"{TOOL_REGISTRY_URL.rstrip('/')}/run/{tool_id}"
    try:
        async with httpx.AsyncClient(timeout=30) as x:
            r = await x.post(url, json={"inputs": inputs})
            if r.status_code != 200:
                return {"ok": False, "error": f"registry HTTP {r.status_code}"}
            out = r.json()
            if not isinstance(out, dict):
                return {"ok": False, "error": "registry returned non-object"}
            return out
    except Exception as e:
        return {"ok": False, "error": f"registry error: {e!r}"}


# -------------------- built-in tools (minimal) --------------------
async def _tool_echo(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "tool": "echo", "inputs": inputs, "ts": time.time()}

async def _tool_sleep(inputs: Dict[str, Any]) -> Dict[str, Any]:
    secs = float(inputs.get("seconds", 0.1))
    await asyncio.sleep(secs)
    return {"ok": True, "tool": "sleep", "slept": secs}

async def _tool_http_get(inputs: Dict[str, Any]) -> Dict[str, Any]:
    url = inputs.get("url")
    if not url:
        return {"ok": False, "error": "missing 'url'"}
    try:
        async with httpx.AsyncClient(timeout=20) as x:
            r = await x.get(url)
            return {
                "ok": True,
                "tool": "http_get",
                "status": r.status_code,
                "headers": dict(r.headers),
                "text": r.text[:4096],
            }
    except Exception as e:
        return {"ok": False, "error": f"http_get error: {e!r}"}

_BUILTINS: dict[str, Any] = {
    "echo": _tool_echo,
    "sleep": _tool_sleep,
    "http_get": _tool_http_get,
}


async def _run_locally(node: Dict[str, Any]) -> Dict[str, Any]:
    tool_id = node.get("tool_id") or "__missing_tool__"
    inputs = node.get("inputs") or {}

    # Prefer built-ins, otherwise try registry
    if tool_id in _BUILTINS:
        return await _BUILTINS[tool_id](inputs)

    # Try registry endpoint if configured
    if TOOL_REGISTRY_URL:
        out = await _registry_call(tool_id, inputs)
        if out.get("ok"):
            return out

    return {"ok": False, "error": f"unknown tool '{tool_id}'"}


# Ray remote shim that calls the same local function (keeps determinism separate)
if ray is not None:  # pragma: no cover
    @ray.remote  # type: ignore[misc]
    def _run_remote(node: Dict[str, Any]) -> Dict[str, Any]:
        # Run the same code path locally inside a Ray worker
        import asyncio as _asyncio
        return _asyncio.get_event_loop().run_until_complete(_run_locally(node))  # type: ignore[attr-defined]
else:  # pragma: no cover
    _run_remote = None  # type: ignore


# -------------------- public entrypoint --------------------
async def run_tool(node: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a tool, preferably via Ray for resource isolation. Falls back to local.
    Contract: returns dict with at least {"ok": bool, ...}
    """
    use_ray = await _ensure_ray()
    if use_ray and _run_remote is not None:
        try:
            # Ray submit is sync; wrap in thread to avoid blocking event loop
            def _submit() -> Any:
                obj_ref = _run_remote.remote(node)  # type: ignore[operator]
                return ray.get(obj_ref)  # type: ignore[attr-defined]

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _submit)
        except Exception as e:
            log.warning("Ray execution failed: %r; falling back to local", e)
            return await _run_locally(node)

    # Local fallback
    return await _run_locally(node)
