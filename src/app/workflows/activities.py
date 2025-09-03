# src/app/workflows/activities.py
from __future__ import annotations

from typing import Any, Dict, Optional
from temporalio import activity
import hashlib
import json
import os
import time
import base64

import httpx

from app.core.ctx import set_ctx
from app.core.logging import get_logger
from app.services.event_bus import EventEnvelope, emit, now_ms
from app.tools.runtime_ray import run_tool

# -------- soft imports (cache/artifacts) --------
# These fall back to no-ops if the optional modules are not present,
# so you can enable them later without changing this file.
try:
    from app.services.cache import cache_get, cache_put  # type: ignore
except Exception:  # pragma: no cover
    async def cache_get(_: str) -> Optional[Dict[str, Any]]:  # type: ignore
        return None

    async def cache_put(_: str, __: Dict[str, Any], **___: Any) -> None:  # type: ignore
        return None

try:
    from app.services.artifacts import put_json  # type: ignore
except Exception:  # pragma: no cover
    async def put_json(payload: Dict[str, Any]) -> tuple[str, str]:  # type: ignore
        # Fallback: inline "uri" as data:... with digest of content
        raw = json.dumps(payload, sort_keys=True).encode()
        h = hashlib.sha256(raw).hexdigest()
        b64 = base64.b64encode(raw).decode()
        return (f"data:application/json;base64,{b64}", h)

log = get_logger(__name__)

# -------- constants / env --------
OPA_URL = os.getenv("OPA_URL")  # if unset -> allow (fail-open)
TOOL_REGISTRY_URL = os.getenv("TOOL_REGISTRY_URL")  # optional, for validator
EVENT_SCHEMA_VERSION = os.getenv("EVENT_SCHEMA_VERSION", "v1")


# ---------- helpers ----------
def _idem_key(node: Dict[str, Any]) -> str:
    """Deterministic idempotency key for a node based on tool_id + sorted inputs."""
    h = hashlib.sha256()
    h.update(str(node.get("tool_id", "")).encode())
    h.update(json.dumps(node.get("inputs", {}), sort_keys=True).encode())
    return h.hexdigest()


async def _opa_allow(payload: Dict[str, Any]) -> bool:
    """
    Call OPA if OPA_URL is set; fail-closed on any error.
    If OPA_URL is not set, allow (fail-open).
    """
    if not OPA_URL:
        return True

    data = {"input": payload}
    try:
        async with httpx.AsyncClient(timeout=3) as x:
            r = await x.post(OPA_URL, json=data)
            # OPA returns {"result": <bool>} for decision endpoints
            resp = r.json() if r.content else {}
            allowed = bool(resp.get("result", False))
            if not allowed:
                log.info("[OPA] deny result=false payload=%s resp=%s", data, resp)
            return allowed
    except Exception as e:
        # Fail-closed if OPA configured but unreachable/errored
        log.info("[OPA] error calling %s: %r payload=%s", OPA_URL, e, data)
        return False


async def _registry_get(tool_id: str) -> Optional[Dict[str, Any]]:
    """Fetch tool metadata from registry if configured."""
    if not TOOL_REGISTRY_URL:
        return None
    url = f"{TOOL_REGISTRY_URL.rstrip('/')}/tools/{tool_id}"
    try:
        async with httpx.AsyncClient(timeout=3) as x:
            r = await x.get(url)
            if r.status_code != 200:
                raise RuntimeError(f"registry returned {r.status_code} for {tool_id}")
            return r.json()
    except Exception as e:
        raise RuntimeError(f"registry error for {tool_id}: {e!r}") from e


# ---------- event helpers ----------
def _mk_event(**kwargs: Any) -> EventEnvelope:
    """
    Build an EventEnvelope, adding schema_version if the dataclass supports it.
    (If not, we also stuff it into data as a fallback.)
    """
    data = dict(kwargs.get("data") or {})
    if "schema_version" not in data:
        data["schema_version"] = EVENT_SCHEMA_VERSION
    kwargs["data"] = data
    # Try to pass schema_version as top-level too (if supported by the model)
    try:
        return EventEnvelope(schema_version=EVENT_SCHEMA_VERSION, **kwargs)  # type: ignore[arg-type]
    except TypeError:
        return EventEnvelope(**kwargs)  # type: ignore[arg-type]


# ---------- event emit activities ----------
@activity.defn
async def emit_plan_created(
    tenant_id: str, run_id: str, plan_id: str, goal_id: str
) -> None:
    set_ctx(tenant_id=tenant_id, run_id=run_id, plan_id=plan_id)
    log.info("emit PlanCreated goal_id=%s", goal_id)
    await emit(
        _mk_event(
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
    set_ctx(tenant_id=tenant_id, run_id=run_id, plan_id=plan_id)
    log.info("emit PlanCompleted")
    await emit(
        _mk_event(
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
async def emit_plan_failed(
    tenant_id: str, run_id: str, plan_id: str, node_id: str
) -> None:
    set_ctx(tenant_id=tenant_id, run_id=run_id, plan_id=plan_id, node_id=node_id)
    log.info("emit PlanFailed node_id=%s", node_id)
    await emit(
        _mk_event(
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


@activity.defn
async def emit_plan_replanned(
    tenant_id: str,
    run_id: str,
    plan_id: str,
    reason_node_id: str,
    delta: Dict[str, Any],
) -> None:
    set_ctx(tenant_id=tenant_id, run_id=run_id, plan_id=plan_id, node_id=reason_node_id)
    log.info("emit PlanReplanned reason_node_id=%s delta=%s", reason_node_id, delta)
    await emit(
        _mk_event(
            id=f"{plan_id}:{reason_node_id}:replanned",
            tenant_id=tenant_id,
            run_id=run_id,
            plan_id=plan_id,
            node_id=reason_node_id,
            type="PlanReplanned",
            data={"reason_node_id": reason_node_id, "delta": delta},
            ts_ms=now_ms(),
        )
    )


# ---------- (registry) plan validation ----------
@activity.defn
async def validate_plan_activity(tenant_id: str, plan_spec: Dict[str, Any]) -> None:
    set_ctx(tenant_id=tenant_id, plan_id=str(plan_spec.get("id")))
    # Optional: if registry is configured, validate every tool id
    if not TOOL_REGISTRY_URL:
        log.info("validate_plan_activity (noop; no TOOL_REGISTRY_URL)")
        return None

    nodes = plan_spec.get("nodes") or []
    for n in nodes:
        tool_id = n.get("tool_id")
        if not tool_id:
            raise RuntimeError("node missing tool_id")
        meta = await _registry_get(tool_id)
        # optional input shape check if registry exposes JSON Schema
        req_schema = (meta or {}).get("input_schema")
        if req_schema and isinstance(n.get("inputs"), dict) is False:
            raise RuntimeError(f"inputs must be object for tool={tool_id}")
    log.info("validate_plan_activity OK via registry")
    return None


# ---------- node run activity ----------
@activity.defn
async def run_node_activity(
    tenant_id: str, run_id: str, plan_id: str, node: Dict[str, Any]
) -> Dict[str, Any]:
    node = dict(node)
    node.setdefault("idempotency_key", _idem_key(node))

    set_ctx(tenant_id=tenant_id, run_id=run_id, plan_id=plan_id, node_id=node.get("id"))
    log.info("NodeStarted tool=%s", node.get("tool_id"))

    # NodeStarted
    await emit(
        _mk_event(
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

    # --- OPA policy gate ---
    allowed = await _opa_allow({"tenant": tenant_id, "tool_id": node["tool_id"]})
    if not allowed:
        msg = f"policy denied: tenant={tenant_id} tool={node['tool_id']}"
        await emit(
            _mk_event(
                id=f"{node['id']}:denied",
                tenant_id=tenant_id,
                run_id=run_id,
                plan_id=plan_id,
                node_id=node["id"],
                type="NodeFailed",
                data={"node": node, "error": msg},
                ts_ms=now_ms(),
            )
        )
        log.info("NodeDenied %s", msg)
        raise RuntimeError(msg)

    # --- Idempotency cache (optional) ---
    try:
        hit = await cache_get(node["idempotency_key"])
    except Exception:
        hit = None

    if hit:
        await emit(
            _mk_event(
                id=f"{node['id']}:cached",
                tenant_id=tenant_id,
                run_id=run_id,
                plan_id=plan_id,
                node_id=node["id"],
                type="NodeSkippedCached",
                data={"node": node, "result_ref": hit},
                ts_ms=now_ms(),
            )
        )
        log.info("NodeSkippedCached")
        return {"ok": True, "result_ref": hit, "cached": True}

    # Execute the tool via Ray runtime
    t0 = time.time()
    out = await run_tool(node)

    # Fail path
    if not out.get("ok"):
        err = out.get("error") or f"tool {node.get('tool_id')} failed"
        await emit(
            _mk_event(
                id=f"{node['id']}:failed",
                tenant_id=tenant_id,
                run_id=run_id,
                plan_id=plan_id,
                node_id=node["id"],
                type="NodeFailed",
                data={"node": node, "error": err},
                ts_ms=now_ms(),
            )
        )
        log.info("NodeFailed error=%s", err)
        raise RuntimeError(err)

    # Persist outputs to artifact store, then cache by idempotency key
    uri, digest = await put_json(out)
    result_ref = {"uri": uri, "digest": digest}

    try:
        # pass tool_id as kwarg if the implementation supports it
        await cache_put(node["idempotency_key"], result_ref, tool_id=node.get("tool_id"))
    except Exception:
        pass

    # Success path
    await emit(
        _mk_event(
            id=f"{node['id']}:done",
            tenant_id=tenant_id,
            run_id=run_id,
            plan_id=plan_id,
            node_id=node["id"],
            type="NodeSucceeded",
            data={
                "node": node,
                "result_ref": result_ref,
                "duration_ms": int((time.time() - t0) * 1000),
            },
            ts_ms=now_ms(),
        )
    )
    log.info("NodeSucceeded")
    return {"ok": True, "result_ref": result_ref}
