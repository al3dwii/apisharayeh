from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse

from app.core.auth import get_tenant
from app.memory.redis import get_redis
from app.services.db import tenant_session
from app.models.event import Event
from sqlalchemy import select

router = APIRouter()

HEARTBEAT_SEC = int(os.getenv("SSE_HEARTBEAT_SEC", "15"))
REPLAY_LIMIT = int(os.getenv("SSE_REPLAY_LIMIT", "50"))

def _sse_packet(data: dict, *, event: Optional[str] = None, id_: Optional[str] = None) -> str:
    # Proper SSE framing: optional id/event lines, then data line(s)
    payload = json.dumps(data, ensure_ascii=False)
    lines = []
    if id_:
        lines.append(f"id: {id_}")
    if event:
        lines.append(f"event: {event}")
    # Data can have newlines; split to multiple data: lines per spec
    for line in payload.splitlines() or ["{}"]:
        lines.append(f"data: {line}")
    lines.append("")  # blank line terminator
    return "\n".join(lines) + "\n"

@router.get("/jobs/{job_id}/events")
async def stream_job_events(
    job_id: str,
    tenant = Depends(get_tenant),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
):
    """
    Server-Sent Events stream of job progress.
    - Replays the most recent events (up to REPLAY_LIMIT) on connect.
    - Then streams live events from Redis pubsub channel.
    - Sends periodic heartbeats to keep the connection alive.
    """

    redis = get_redis()
    pubsub = redis.pubsub()
    channel = f"jobs:{job_id}"  # NOTE: consider namespacing with tenant: f"tenants:{tenant.id}:jobs:{job_id}"

    async def event_generator() -> AsyncIterator[str]:
        # 1) Replay recent events from DB (RLS-safe via tenant_session)
        try:
            async with tenant_session(tenant.id) as session:
                # If Last-Event-ID is supplied, we could use created_at > that event’s ts.
                # UUIDs aren't sortable, so we just replay the last N for simplicity.
                stmt = (
                    select(Event)
                    .where(Event.job_id == job_id)
                    .order_by(Event.created_at.asc())
                    .limit(REPLAY_LIMIT)
                )
                res = await session.execute(stmt)
                rows = res.scalars().all()
                for ev in rows:
                    yield _sse_packet(
                        {
                            "type": "step",
                            "job_id": ev.job_id,
                            "step": ev.step,
                            "status": ev.status,
                            "payload": ev.payload_json or {},
                            "ts": ev.created_at.isoformat() if getattr(ev, "created_at", None) else None,
                        },
                        event="replay",
                        id_=ev.id,
                    )
        except Exception:
            # Don’t fail the stream if replay has issues
            pass

        # 2) Subscribe to live channel
        await pubsub.subscribe(channel)

        # Send a retry directive so clients auto-reconnect quickly
        yield f"retry: 5000\n\n"

        try:
            last_heartbeat = 0.0
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                if msg and msg.get("type") == "message":
                    data = msg.get("data")
                    if isinstance(data, (bytes, bytearray)):
                        data = data.decode("utf-8", errors="ignore")
                    try:
                        parsed = json.loads(data)
                    except Exception:
                        # If publisher sent plain text, still forward it
                        parsed = {"raw": data}
                    # Expect payloads from emit_event with fields: id, type, job_id, step, status, payload, ts
                    yield _sse_packet(
                        parsed,
                        event=parsed.get("type") or "step",
                        id_=parsed.get("id"),
                    )

                # Heartbeat to keep proxies from closing idle conns
                last_heartbeat += 0.1
                if last_heartbeat >= HEARTBEAT_SEC:
                    last_heartbeat = 0.0
                    # Comment line is a valid heartbeat for SSE; or send an explicit ping event
                    yield ": keep-alive\n\n"
                    # Alternatively:
                    # yield _sse_packet({"ok": True}, event="ping")

                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            # Client disconnected
            raise
        finally:
            try:
                await pubsub.unsubscribe(channel)
            except Exception:
                pass

    # Important SSE headers
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # for nginx, prevents response buffering
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)
