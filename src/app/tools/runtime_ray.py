from __future__ import annotations

import os
import asyncio
from typing import Any, Dict


def _try_import_ray():
    try:
        import ray  # type: ignore
        return ray
    except Exception:
        return None


async def run_tool(node: Dict[str, Any]) -> Dict[str, Any]:
    """
    If RAY_ADDRESS is set, try to connect to that cluster.
    Otherwise start a local Ray runtime. Never default to 'auto'.
    """
    # Fail unknown tools early so the activity errors and the plan can fail
    tool_id = node.get("tool_id")
    if tool_id != "echo":
        raise RuntimeError(f"Unknown tool '{tool_id}'")

    ray = _try_import_ray()
    if not ray:
        return {"ok": True, "node_id": node["id"], "tool_id": node["tool_id"], "outputs_ref": {}}

    addr = os.getenv("RAY_ADDRESS")  # no default; 'auto' causes false positives
    try:
        if not ray.is_initialized():
            if addr:
                ray.init(address=addr, ignore_reinit_error=True, namespace="o2", log_to_driver=False)
            else:
                ray.init(ignore_reinit_error=True, namespace="o2", log_to_driver=False)
    except Exception:
        # last resort: start local
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True, namespace="o2", log_to_driver=False)

    @ray.remote
    def _remote(n: Dict[str, Any]) -> Dict[str, Any]:
        # echo tool just acknowledges inputs
        return {"ok": True, "node_id": n["id"], "tool_id": n["tool_id"], "outputs_ref": {}}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, ray.get, _remote.remote(node))
