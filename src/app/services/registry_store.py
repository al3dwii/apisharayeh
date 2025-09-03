from __future__ import annotations

"""
Lightweight client for the Tool Registry.

Env:
- TOOL_REGISTRY_URL (e.g. http://localhost:8088)
- REGISTRY_TIMEOUT (seconds, default: 3)
- REGISTRY_CACHE_TTL (seconds, default: 30)

Public:
- async def get_tool(tool_id: str, *, require_enabled: bool = True) -> dict
- async def list_tools() -> list[dict]
"""

from typing import Any, Dict, List, Tuple
import asyncio
import os
import time

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

BASE_URL = os.getenv("TOOL_REGISTRY_URL", "").rstrip("/")
TIMEOUT = float(os.getenv("REGISTRY_TIMEOUT", "3"))
CACHE_TTL = float(os.getenv("REGISTRY_CACHE_TTL", "30"))

_cache: dict[str, Tuple[float, Dict[str, Any]]] = {}
_cache_all: Tuple[float, List[Dict[str, Any]]] | None = None
_lock = asyncio.Lock()


def _assert_base() -> None:
    if not BASE_URL:
        raise RuntimeError("TOOL_REGISTRY_URL not set")


async def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT)


async def get_tool(tool_id: str, *, require_enabled: bool = True) -> Dict[str, Any]:
    """Fetch a single tool record (with small in-process TTL cache)."""
    _assert_base()
    now = time.time()

    async with _lock:
        entry = _cache.get(tool_id)
        if entry and entry[0] > now:
            return entry[1]

    url = f"{BASE_URL}/tools/{tool_id}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as x:
        r = await x.get(url)
    if r.status_code == 404:
        raise RuntimeError(f"tool not found: {tool_id}")
    r.raise_for_status()
    data: Dict[str, Any] = r.json()

    if require_enabled and not data.get("enabled", True):
        raise RuntimeError(f"tool disabled: {tool_id}")

    async with _lock:
        _cache[tool_id] = (now + CACHE_TTL, data)
    return data


async def list_tools() -> List[Dict[str, Any]]:
    """List tools (cached)."""
    _assert_base()
    global _cache_all
    now = time.time()
    if _cache_all and _cache_all[0] > now:
        return _cache_all[1]

    url = f"{BASE_URL}/tools"
    async with httpx.AsyncClient(timeout=TIMEOUT) as x:
        r = await x.get(url)
    r.raise_for_status()
    all_tools: List[Dict[str, Any]] = r.json()

    # backfill single-item cache entries too
    async with _lock:
        _cache_all = (now + CACHE_TTL, all_tools)
        for t in all_tools:
            tid = t.get("id") or t.get("tool_id")
            if tid:
                _cache[tid] = (now + CACHE_TTL, t)
    return all_tools
