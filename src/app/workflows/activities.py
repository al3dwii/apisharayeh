from __future__ import annotations
from temporalio import activity
from typing import Any, Dict
import hashlib, json

from app.services.event_bus import EventEnvelope, emit, now_ms

# ---------- helpers ----------
def _idem_key(node: Dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(node["tool_id"].encode())
    h.update(json.dumps(node.get("inputs", {}), sort_keys=True).encode())
    return h.hexdigest()

# ---------- event emit activities (safe; side-effects belong in activities) ----------
@activity.defn
async def emit_plan_created(tenant_id: str, run_id: str, plan_id: str, goal_id: str) -> None:
    await emit(EventEnvelope(
        id=f"{plan_id}:created", tenant_id=tenant_id, run_id=run_id,
        plan_id=plan_id, goal_id=goal_id, type="PlanCreated", data={}, ts_ms=now_ms(),
    ))

@activity.defn
async def emit_plan_completed(tenant_id: str, run_id: str, plan_id: str) -> None:
    await emit(EventEnvelope(
        id=f"{plan_id}:completed", tenant_id=tenant_id, run_id=run_id,
        plan_id=plan_id, type="PlanCompleted", data={}, ts_ms=now_ms(),
    ))

@activity.defn
async def emit_plan_failed(tenant_id: str, run_id: str, plan_id: str, node_id: str) -> None:
    await emit(EventEnvelope(
        id=f"{plan_id}:{node_id}:failed", tenant_id=tenant_id, run_id=run_id,
        plan_id=plan_id, node_id=node_id, type="PlanFailed", data={"node_id": node_id}, ts_ms=now_ms(),
    ))

# ---------- node run activity ----------
from app.tools.runtime_ray import run_tool

@activity.defn
async def run_node_activity(tenant_id: str, run_id: str, plan_id: str, node: Dict[str, Any]) -> Dict[str, Any]:
    node = dict(node)
    node.setdefault("idempotency_key", _idem_key(node))

    await emit(EventEnvelope(
        id=node["id"] + ":start",
        tenant_id=tenant_id, run_id=run_id, type="NodeStarted",
        data={"node": node}, ts_ms=now_ms(), node_id=node["id"], plan_id=plan_id
    ))

    out = await run_tool(node)
    if not out.get("ok"):
        await emit(EventEnvelope(tenant_id=tenant_id, run_id=run_id, plan_id=plan_id, node_id=node["id"], type="NodeFailed", data={"node": node, "error": out.get("error")}))
        raise RuntimeError(out.get("error") or ("tool %s failed" % node.get("tool_id")))


    await emit(EventEnvelope(
        id=node["id"] + ":done",
        tenant_id=tenant_id, run_id=run_id, type="NodeSucceeded",
        data={"node": node, "result": out}, ts_ms=now_ms(), node_id=node["id"], plan_id=plan_id
    ))
    return out
