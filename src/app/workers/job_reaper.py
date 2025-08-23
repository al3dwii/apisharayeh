from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone

try:
    from app.core.config import settings  # type: ignore
except Exception:  # pragma: no cover
    class _S:
        JOB_STUCK_AFTER_SEC = 900
        ARTIFACT_TTL_DAYS = 7
    settings = _S()  # type: ignore

from app.services.db import SessionLocal  # async session
from sqlalchemy import select, func
from app.models.job import Job
from app.models.event import Event  # latest event times
from app.services.events import emit_event
from app.services.artifacts import gc_expired

# Use the main Celery app for task registration
from app.workers.celery_app import celery


@celery.task(name="maintenance.sync_heartbeats_from_events")
def sync_heartbeats_from_events() -> None:
    """
    Derive Job.last_heartbeat_at for running jobs from their latest Event timestamp.
    This avoids patching the worker to emit explicit heartbeats.
    """
    async def _run():
        async with SessionLocal() as session:
            # max(event.created_at) per job where job.status='running'
            res = await session.execute(
                select(Job.id, func.max(Event.created_at))
                .join(Event, Event.job_id == Job.id)
                .where(Job.status == "running")
                .group_by(Job.id)
            )
            updates = res.all()
            for job_id, last_evt in updates:
                if last_evt:
                    j = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
                    if j and (not j.last_heartbeat_at or j.last_heartbeat_at < last_evt):
                        j.last_heartbeat_at = last_evt.astimezone(timezone.utc)
            await session.commit()
    asyncio.run(_run())


@celery.task(name="maintenance.reap_stuck_jobs")
def reap_stuck_jobs() -> None:
    """
    Mark jobs as failed if they are running but haven't heartbeated within JOB_STUCK_AFTER_SEC.
    """
    threshold = datetime.now(timezone.utc) - timedelta(seconds=int(getattr(settings, "JOB_STUCK_AFTER_SEC", 900)))

    async def _run():
        async with SessionLocal() as session:
            res = await session.execute(select(Job).where(Job.status == "running"))
            jobs = res.scalars().all()
            for j in jobs:
                hb = j.last_heartbeat_at
                if not hb or hb < threshold:
                    j.status = "failed"
                    j.error = "reaped: heartbeat timeout"
                    await session.commit()
                    try:
                        await emit_event(j.tenant_id, j.id, step="service.end", status="failed", payload={"error": j.error})
                    except Exception:
                        pass
    asyncio.run(_run())


@celery.task(name="maintenance.gc_artifacts")
def gc_artifacts() -> None:
    """
    Daily artifact TTL cleanup (local & S3).
    """
    gc_expired()