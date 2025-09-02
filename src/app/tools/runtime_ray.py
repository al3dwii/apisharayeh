from __future__ import annotations
import os
import asyncio
from typing import Any, Dict


def _try_import_ray():
    """Import Ray if available; return None otherwise."""
    try:
        import ray  # type: ignore
        return ray
    except Exception:
        return None


async def run_tool(node: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run a 'tool' node, optionally via Ray.

    Behavior:
      - If the tool is unknown, raise to fail the activity (and the plan).
      - If Ray is not installed, return a successful echo result locally.
      - If RAY_ADDRESS is set, try to connect to that cluster.
      - Otherwise, start (or reuse) a local Ray runtime.
      - Never use 'auto' discovery implicitly.

    Returns a dict shaped like:
      {"ok": True, "node_id": <id>, "tool_id": <tool>, "outputs_ref": {}}
    """
    # Fail unknown tools early so the activity errors and the plan can fail
    tool_id = node.get("tool_id")
    if tool_id != "echo":
        raise RuntimeError(f"Unknown tool '{tool_id}'")

    # If Ray isn't present, just "echo" locally
    ray = _try_import_ray()
    if not ray:
        return {
            "ok": True,
            "node_id": node["id"],
            "tool_id": node["tool_id"],
            "outputs_ref": {},
        }

    # Prefer explicit RAY_ADDRESS; ignore 'auto' to avoid false positives
    addr = os.getenv("RAY_ADDRESS")
    if addr and addr.strip().lower() == "auto":
        addr = None

    # Initialize Ray: connect to provided cluster or start local; on error, fall back to local
    try:
        if not ray.is_initialized():
            if addr:
                ray.init(address=addr, ignore_reinit_error=True, namespace="o2", log_to_driver=False)
            else:
                ray.init(ignore_reinit_error=True, namespace="o2", log_to_driver=False)
    except Exception:
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True, namespace="o2", log_to_driver=False)

    @ray.remote
    def _remote(n: Dict[str, Any]) -> Dict[str, Any]:
        # Real tools would do real work here; echo just succeeds.
        return {"ok": True, "node_id": n["id"], "tool_id": n["tool_id"], "outputs_ref": {}}

    # Run the tiny Ray task in a thread so we don't block the event loop
    return await asyncio.to_thread(ray.get, _remote.remote(node))
