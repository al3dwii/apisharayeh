import uuid, json, logging, datetime
from typing import Any, Dict, Optional
from sqlalchemy import select, text
from app.services.db import tenant_session
from app.memory.redis import get_redis
from app.models.event import Event

log = logging.getLogger(__name__)

ALLOWED_STEPS = {"plan", "act", "run"}        # expand as you like
ALLOWED_STATUS = {"started", "progress", "finished", "succeeded", "failed"}

def _safe_payload(p: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if p is None:
        return {}
    try:
        # ensure it’s JSON serializable
        json.dumps(p, ensure_ascii=False)
        return p
    except TypeError:
        return {"_non_json": str(p)}

async def emit_event(
    tenant_id: str,
    job_id: str,
    step: str,
    status: str,
    payload: Optional[Dict[str, Any]] = None,
):
    if step not in ALLOWED_STEPS:
        log.debug("emit_event: unknown step %s (allowed: %s)", step, sorted(ALLOWED_STEPS))
    if status not in ALLOWED_STATUS:
        log.debug("emit_event: unknown status %s (allowed: %s)", status, sorted(ALLOWED_STATUS))

    eid = str(uuid.uuid4())
    payload = _safe_payload(payload)
    created_at_iso = None

    # 1) Persist to DB (RLS-scoped)
    try:
        async with tenant_session(tenant_id) as session:
            ev = Event(
                id=eid,
                tenant_id=tenant_id,
                job_id=job_id,
                step=step,
                status=status,
                payload_json=payload,
            )
            session.add(ev)
            await session.commit()
            # try to get DB timestamp if the model sets it on insert
            try:
                await session.refresh(ev)
                if getattr(ev, "created_at", None):
                    created_at_iso = ev.created_at.isoformat()
            except Exception:
                pass
    except Exception as e:
        # Don’t crash the job because an event failed to persist
        log.warning("emit_event DB error: %s", e, exc_info=True)
        # still attempt to notify live listeners via Redis below

    # 2) Publish to Redis for SSE
    try:
        r = get_redis()
        msg = {
            "type": "step",
            "id": eid,
            "job_id": job_id,
            "step": step,
            "status": status,
            "payload": payload,
            "ts": created_at_iso or datetime.datetime.utcnow().isoformat() + "Z",
        }
        await r.publish(f"jobs:{job_id}", json.dumps(msg, ensure_ascii=False))
    except Exception as e:
        log.warning("emit_event Redis publish error: %s", e, exc_info=True)
