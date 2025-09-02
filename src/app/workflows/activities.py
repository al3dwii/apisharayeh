# src/app/workflows/activities.py

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from temporalio import activity

from app.services.event_bus import EventEnvelope, emit, now_ms
from app.tools.runtime_ray import run_tool


# ---------------------------
# helpers
# ---------------------------
def _idem_key(node: Dict[str, Any]) -> str:
    """Deterministic idempotency key for a tool invocation."""
    h = hashlib.sha256()
    h.update(str(node.get("tool_id", "")).encode())
    h.update(json.dumps(node.get("inputs", {}), sort_keys=True).encode())
    return h.hexdigest()


# ---------------------------
# plan-level events
# ---------------------------
@activity.defn
async def emit_plan_created(tenant_id: str, run_id: str, plan_id: str, goal_id: str) -> None:
    """Plan created (safe side-effect emitted from activity)."""
    await emit(
        EventEnvelope(
            id=f"{plan_id}:created",
            tenant_id=tenant_id,
            run_id=run_id,
            plan_id=plan_id,
            goal_id=goal_id,
            type="PlanCreated",
            data={},
            ts_ms=now_ms(),
        )
    )


@activity.defn
async def emit_plan_completed(tenant_id: str, run_id: str, plan_id: str) -> None:
    """Plan completed (all nodes done)."""
    await emit(
        EventEnvelope(
            id=f"{plan_id}:completed",
            tenant_id=tenant_id,
            run_id=run_id,
            plan_id=plan_id,
            type="PlanCompleted",
            data={},
            ts_ms=now_ms(),
        )
    )


@activity.defn
async def emit_plan_failed(tenant_id: str, run_id: str, plan_id: str, node_id: str) -> None:
    """Plan failed because a node failed; workflow handles failure propagation."""
    await emit(
        EventEnvelope(
            id=f"{plan_id}:{node_id}:failed",
            tenant_id=tenant_id,
            run_id=run_id,
            plan_id=plan_id,
            node_id=node_id,
            type="PlanFailed",
            data={"node_id": node_id},
            ts_ms=now_ms(),
        )
    )


# ---------------------------
# node execution
# ---------------------------
@activity.defn
async def run_node_activity(
    tenant_id: str,
    run_id: str,
    plan_id: str,
    node: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute a single plan node (tool invocation).

    Contract:
      - Emits NodeStarted / NodeSucceeded / NodeFailed to the event bus.
      - Returns the tool result dict on success (must contain {"ok": True, ...}).
      - Raises on failure so the workflow can decide how to react.
    """
    # Copy & ensure idempotency key
    node = dict(node)
    node.setdefault("idempotency_key", _idem_key(node))

    # Emit "started"
    await emit(
        EventEnvelope(
            id=f"{node['id']}:start",
            tenant_id=tenant_id,
            run_id=run_id,
            plan_id=plan_id,
            node_id=node["id"],
            type="NodeStarted",
            data={"node": node},
            ts_ms=now_ms(),
        )
    )

    # Execute tool
    try:
        out = await run_tool(node)
    except Exception as e:
        # Emit "failed" then re-raise to fail the activity
        await emit(
            EventEnvelope(
                id=f"{node['id']}:failed",
                tenant_id=tenant_id,
                run_id=run_id,
                plan_id=plan_id,
                node_id=node["id"],
                type="NodeFailed",
                data={"node": node, "error": str(e)},
                ts_ms=now_ms(),
            )
        )
        raise

    # Tool returned a structured failure
    if not out.get("ok", False):
        await emit(
            EventEnvelope(
                id=f"{node['id']}:failed",
                tenant_id=tenant_id,
                run_id=run_id,
                plan_id=plan_id,
                node_id=node["id"],
                type="NodeFailed",
                data={"node": node, "error": out.get("error")},
                ts_ms=now_ms(),
            )
        )
        raise RuntimeError(out.get("error") or f"tool {node.get('tool_id')} failed")

    # Emit "succeeded"
    await emit(
        EventEnvelope(
            id=f"{node['id']}:done",
            tenant_id=tenant_id,
            run_id=run_id,
            plan_id=plan_id,
            node_id=node["id"],
            type="NodeSucceeded",
            data={"node": node, "result": out},
            ts_ms=now_ms(),
        )
    )

    return out
