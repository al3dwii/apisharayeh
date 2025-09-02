from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict

import httpx
from temporalio import activity

from app.services.event_bus import EventEnvelope, emit, now_ms
from app.tools.runtime_ray import run_tool


# ---------- helpers ----------
def _idem_key(node: Dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(node["tool_id"].encode())
    h.update(json.dumps(node.get("inputs", {}), sort_keys=True).encode())
    return h.hexdigest()


async def _opa_allow(payload: Dict[str, Any]) -> bool:
    """
    Call OPA with POST and an `input` object.
    If OPA_URL is unset, allow. If set and any error occurs, fail closed (deny).
    """
    opa_url = os.getenv("OPA_URL")
    if not opa_url:
        return True

    body = {"input": payload}
    try:
        async with httpx.AsyncClient(timeout=5) as x:
            resp = await x.post(opa_url, json=body)
        if resp.status_code != 200:
            # Log body for debugging but deny
            print(f"[activities] OPA non-200 ({resp.status_code}) body={resp.text}")
            return False
        data = resp.json()
        allowed = bool(data.get("result", False))
        if not allowed:
            print(f"[activities] OPA deny result=false payload={body} resp={data}")
        return allowed
    except Exception as e:
        print(f"[activities] OPA error calling {opa_url}: {e!r} payload={body}")
        return False


# ---------- event emit activities ----------
@activity.defn
async def emit_plan_created(tenant_id: str, run_id: str, plan_id: str, goal_id: str) -> None:
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


# ---------- (optional) plan validation against registry ----------
@activity.defn
async def validate_plan_activity(tenant_id: str, plan_spec: Dict[str, Any]) -> None:
    # Minimal slice: no-op. (Registry/schema checks can be added here.)
    return None


# ---------- node run activity ----------
@activity.defn
async def run_node_activity(
    tenant_id: str, run_id: str, plan_id: str, node: Dict[str, Any]
) -> Dict[str, Any]:
    node = dict(node)
    node.setdefault("idempotency_key", _idem_key(node))

    # NodeStarted
    await emit(
        EventEnvelope(
            id=node["id"] + ":start",
            tenant_id=tenant_id,
            run_id=run_id,
            type="NodeStarted",
            data={"node": node},
            ts_ms=now_ms(),
            node_id=node["id"],
            plan_id=plan_id,
        )
    )

    # --- OPA policy gate (deny tenant/tool combos) ---
    allowed = await _opa_allow({"tenant": tenant_id, "tool_id": node["tool_id"]})
    if not allowed:
        msg = f"policy denied: tenant={tenant_id} tool={node['tool_id']}"
        await emit(
            EventEnvelope(
                id=node["id"] + ":denied",
                tenant_id=tenant_id,
                run_id=run_id,
                type="NodeFailed",
                data={"node": node, "error": msg},
                ts_ms=now_ms(),
                node_id=node["id"],
                plan_id=plan_id,
            )
        )
        raise RuntimeError(msg)

    # Execute the tool
    out = await run_tool(node)
    if not out.get("ok"):
        await emit(
            EventEnvelope(
                tenant_id=tenant_id,
                run_id=run_id,
                plan_id=plan_id,
                node_id=node["id"],
                type="NodeFailed",
                data={"node": node, "error": out.get("error")},
            )
        )
        raise RuntimeError(out.get("error") or (f"tool {node.get('tool_id')} failed"))

    # NodeSucceeded
    await emit(
        EventEnvelope(
            id=node["id"] + ":done",
            tenant_id=tenant_id,
            run_id=run_id,
            type="NodeSucceeded",
            data={"node": node, "result": out},
            ts_ms=now_ms(),
            node_id=node["id"],
            plan_id=plan_id,
        )
    )
    return out
