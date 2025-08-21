# src/app/services/events.py
# Loop-safe Redis event bus with backward compatibility.
# Publishes JSON: {"event": "<type>", "data": {...}} to channel jobs:{job_id}

from __future__ import annotations

import os
import json
import asyncio
import contextlib
from typing import Any, AsyncIterator, Dict, Optional

from redis import Redis as SyncRedis
from redis.asyncio import Redis as AsyncRedis


# Prefer queue URL (Celery), fall back to general/localhost.
REDIS_URL: str = (
    os.getenv("REDIS_URL_QUEUE")
    or os.getenv("REDIS_URL")
    or "redis://localhost:6379/1"
)


def _channel(job_id: str) -> str:
    return f"jobs:{job_id}"


# --------------------------- low-level publish helpers ---------------------------

def _sync_client() -> SyncRedis:
    return SyncRedis.from_url(REDIS_URL, decode_responses=True)


def _async_client() -> AsyncRedis:
    return AsyncRedis.from_url(REDIS_URL, decode_responses=True)


async def _publish(job_id: str, payload: Dict[str, Any]) -> None:
    """
    Publish a payload (already shaped as {"event": ..., "data": ...})
    Prefer async client; if the current loop is closed/mismatched (common in
    Celery threads), fall back to a blocking publish on a worker thread.
    """
    msg = json.dumps(payload, ensure_ascii=False)

    try:
        cli = _async_client()
        try:
            await cli.publish(_channel(job_id), msg)
        finally:
            with contextlib.suppress(Exception):
                await cli.aclose()
        return
    except Exception:
        # Fallback to sync client on a thread
        def _sync_pub() -> None:
            c = _sync_client()
            try:
                c.publish(_channel(job_id), msg)
            finally:
                with contextlib.suppress(Exception):
                    c.close()

        await asyncio.to_thread(_sync_pub)


# ------------------------------- public API (new) -------------------------------

async def emit_status(job_id: str, state: str, error: Optional[Any] = None) -> None:
    await _publish(job_id, {"event": "status", "data": {"state": state, "error": error}})


async def emit_step(
    job_id: str,
    name: str,
    status: str = "running",
    detail: Optional[str] = None,
    **extra: Any,
) -> None:
    data: Dict[str, Any] = {"name": name, "status": status}
    if detail:
        data["detail"] = detail
    if extra:
        data.update(extra)
    await _publish(job_id, {"event": "step", "data": data})


async def emit_artifact(job_id: str, kind: str, **fields: Any) -> None:
    data: Dict[str, Any] = {"kind": kind}
    data.update(fields)
    await _publish(job_id, {"event": "artifact", "data": data})


# --------------------------- backward-compat wrapper ----------------------------

async def emit_event(*args: Any, **kwargs: Any) -> None:
    """
    Supports BOTH:
      1) New style: emit_event(job_id, event, data?: dict)
      2) Legacy style (as used by celery_app.py):
         emit_event(tenant_id, job_id, step="...", status="...", payload={...})
         emit_event(tenant_id, job_id, status="queued"/"running"/"succeeded"/"failed", error=?)
         emit_event(tenant_id, job_id, kind="pdf"/..., url/html/..., payload=?)

    We ignore tenant_id for channel naming (we publish to jobs:{job_id}).
    """
    # -------- new style --------
    if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], str) and not kwargs:
        job_id = args[0]
        event = args[1]
        data = args[2] if len(args) >= 3 and isinstance(args[2], dict) else {}
        await _publish(job_id, {"event": event, "data": data})
        return

    # -------- legacy style --------
    if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], str):
        # tenant_id = args[0]  # kept for compatibility, not used in channel
        job_id = args[1]

        # step update
        if "step" in kwargs:
            name = kwargs.get("step")
            status = kwargs.get("status") or kwargs.get("state") or "running"
            detail = kwargs.get("detail") or kwargs.get("message")
            payload = kwargs.get("payload")
            extra = payload if isinstance(payload, dict) else {}
            await emit_step(job_id, name, status=status, detail=detail, **extra)
            return

        # status update
        if "status" in kwargs and "step" not in kwargs:
            state = kwargs.get("status") or kwargs.get("state")
            error = kwargs.get("error")
            await emit_status(job_id, state, error=error)
            return

        # artifact (kind/html/url/etc.) or generic payload
        kind = kwargs.get("kind") or kwargs.get("artifact")
        payload = kwargs.get("payload")
        fields = {}
        if isinstance(payload, dict):
            fields.update(payload)
        # pass through common fields if provided directly
        for k in ("url", "html", "path", "filename", "page", "size"):
            if k in kwargs:
                fields[k] = kwargs[k]
        if kind:
            await emit_artifact(job_id, kind, **fields)
            return

        # fallback: generic message
        await _publish(job_id, {"event": kwargs.get("event", "message"), "data": fields})
        return

    raise TypeError(
        "emit_event() expected (job_id, event, data?) or "
        "(tenant_id, job_id, step?/status?/kind?/payload=...)."
    )


# ----------------------------------- listen -----------------------------------

async def subscribe_job_events(job_id: str) -> AsyncIterator[str]:
    """
    Async generator yielding raw JSON payloads for jobs:{job_id}.
    Used by the SSE endpoint.
    """
    client = _async_client()
    pubsub = client.pubsub()
    await pubsub.subscribe(_channel(job_id))
    try:
        async for message in pubsub.listen():
            if message.get("type") == "message":
                data = message.get("data")
                if isinstance(data, (str, bytes)):
                    yield data if isinstance(data, str) else data.decode("utf-8")
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(_channel(job_id))
        with contextlib.suppress(Exception):
            await client.aclose()


__all__ = [
    "emit_event",
    "emit_status",
    "emit_step",
    "emit_artifact",
    "subscribe_job_events",
]
