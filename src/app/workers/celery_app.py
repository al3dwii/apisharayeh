# /Users/omair/apisharayeh/src/app/workers/celery_app.py
# ------------------------------------------------------------------------------
# Celery worker (plugins-only)
# - No legacy packs/agents imports or tasks
# - Executes services using manifest snapshot stored on the Job row
# - Emits start/end events; respects tenant RLS via GUC
# - Optional fallback to in-memory registry if snapshot missing
# - Includes deliver_webhook with exponential backoff
# ------------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import traceback
from typing import Any, Optional

from celery import Celery
from sqlalchemy import select, text

from app.core.config import settings
from app.kernel.plugins.loader import registry as plugin_registry
from app.kernel.plugins.spec import ServiceManifest
from app.kernel.runtime import KernelContext, run_service
from app.services.models import ModelRouter
from app.services.tools import ToolRouter

# Celery configuration ---------------------------------------------------------

celery = Celery("agentic")
celery.conf.broker_url = settings.REDIS_URL_QUEUE
celery.conf.result_backend = settings.REDIS_URL_QUEUE
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,            # safer with idempotent tasks
    worker_prefetch_multiplier=1,   # fair dispatch
    task_time_limit=60 * 30,        # hard limit: 30 min
    task_soft_time_limit=60 * 28,   # soft limit: 28 min
)

# Utilities --------------------------------------------------------------------

class _EventsBridge:
    """
    Bridge used by Kernel/DSL to emit events.
    Normalizes various emit call shapes and forwards to async emit_event().
    """
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id

    def emit(self, *args, **kwargs) -> None:
        """
        Accepts:
          emit(tenant_id, job_id, step, status, payload)
          emit(job_id, step, payload)  # status 'info'
          emit(job_id=..., step=..., status=..., payload=...)
        """
        from app.services.events import emit_event  # local import to avoid worker import cycles

        # Normalize arguments
        if len(args) >= 5:
            tenant_id, job_id, step, status, payload = args[:5]
        elif len(args) == 4:
            tenant_id, job_id, step, payload = args
            status = kwargs.get("status", "info")
        elif len(args) == 3:
            job_id, step, payload = args
            tenant_id = self.tenant_id
            status = kwargs.get("status", "info")
        else:
            tenant_id = kwargs.get("tenant_id", self.tenant_id)
            job_id = kwargs.get("job_id")
            step = kwargs.get("step") or kwargs.get("event") or "event"
            status = kwargs.get("status", "info")
            payload = kwargs.get("payload", {})

        # Fire-and-forget (run in loop if available, else run inline)
        try:
            asyncio.get_event_loop().create_task(
                emit_event(tenant_id, job_id, step=step, status=status, payload=payload or {})
            )
        except RuntimeError:
            asyncio.run(emit_event(tenant_id, job_id, step=step, status=status, payload=payload or {}))


# Tasks ------------------------------------------------------------------------

@celery.task(
    name="execute_service_job",
    autoretry_for=(),  # don't auto-retry; caller decides
)
def execute_service_job(job_id: str) -> None:
    """
    Execute a plugin service job by job_id.
    Reads the Job row (tenant_id, manifest_snapshot, input_json), marks it running,
    builds a KernelContext, runs the service, then persists result/error and emits events.
    """
    from app.services.db import SessionLocal
    from app.models.job import Job
    from app.services.events import emit_event

    async def _run() -> None:
        # 1) Load job row (unscoped), get tenant_id, then set RLS GUC before further queries.
        async with SessionLocal() as session:
            res = await session.execute(select(Job).where(Job.id == job_id))
            job = res.scalar_one_or_none()
            if not job:
                return

            tenant_id = job.tenant_id
            await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})

            # If already terminal, skip
            if job.status in ("succeeded", "failed"):
                return

            job.status = "running"
            await session.commit()

        await emit_event(tenant_id, job_id, step="service.start", status="started", payload={"kind": job.kind})

        # 2) Reconstruct manifest (prefer snapshot; fallback to registry for resilience)
        manifest: Optional[ServiceManifest]
        if job.manifest_snapshot:
            try:
                manifest = ServiceManifest.model_validate(job.manifest_snapshot)
            except Exception:
                manifest = None
        else:
            manifest = None

        if manifest is None and job.service_id:
            # Fallback isn't strictly deterministic, but better than failing if snapshot is absent
            manifest = plugin_registry.get(job.service_id)

        if manifest is None:
            err_txt = "Missing service manifest (no snapshot and not in registry)"
            # Persist failure
            async with SessionLocal() as session:
                await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
                res = await session.execute(select(Job).where(Job.id == job_id))
                j = res.scalar_one_or_none()
                if j:
                    j.status = "failed"
                    j.error = err_txt
                    await session.commit()
            await emit_event(tenant_id, job_id, step="service.end", status="failed", payload={"error": err_txt})
            return

        # 3) Build routers and context
        models = ModelRouter()
        tools = ToolRouter(models=models)
        ctx = KernelContext(tenant_id, job_id, _EventsBridge(tenant_id), None, models, tools)

        # 4) Run service (offload sync runner to thread)
        outputs: Optional[dict[str, Any]] = None
        err_txt: Optional[str] = None
        try:
            outputs = await asyncio.to_thread(run_service, manifest, job.input_json or {}, ctx)
        except Exception:
            err_txt = traceback.format_exc(limit=8)

        # 5) Persist final state + emit end event
        async with SessionLocal() as session:
            await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
            res = await session.execute(select(Job).where(Job.id == job_id))
            j = res.scalar_one_or_none()
            if not j:
                return

            if err_txt:
                j.status = "failed"
                j.error = (err_txt or "")[:4000]
                await session.commit()
                await emit_event(tenant_id, job_id, step="service.end", status="failed", payload={"error": j.error})
            else:
                j.status = "succeeded"
                j.output_json = outputs or {}
                await session.commit()
                await emit_event(tenant_id, job_id, step="service.end", status="succeeded", payload=outputs or {})

    asyncio.run(_run())


@celery.task(
    name="deliver_webhook",
    bind=True,
    max_retries=6,
    default_retry_delay=30,  # seconds; exponential backoff below
)
def deliver_webhook(self, tenant_id: str, delivery_id: str) -> None:
    """
    Deliver a queued webhook using tenant-scoped RLS.
    IMPORTANT: enqueue must schedule with (tenant_id, delivery_id).
    """
    import httpx
    from app.services.db import SessionLocal
    from app.models.webhook_delivery import WebhookDelivery
    from app.services.webhooks import sign_payload

    async def _send() -> None:
        async with SessionLocal() as session:
            # Set tenant BEFORE first SELECT (RLS)
            await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})

            res = await session.execute(select(WebhookDelivery).where(WebhookDelivery.id == delivery_id))
            d = res.scalar_one_or_none()
            if not d:
                return

            payload = d.payload_json or {}
            sig = sign_payload(payload)

            try:
                async with httpx.AsyncClient(timeout=20) as c:
                    r = await c.post(
                        d.url,
                        json=payload,
                        headers={
                            "X-Agentic-Event": d.event_type,
                            "X-Agentic-Signature": sig,
                            "Content-Type": "application/json",
                        },
                    )
                if 200 <= r.status_code < 300:
                    d.status = "sent"
                    d.attempts += 1
                    d.last_error = None
                    await session.commit()
                    return
                raise Exception(f"HTTP {r.status_code}: {r.text[:200]}")
            except Exception as e:
                d.status = "retrying"
                d.attempts += 1
                d.last_error = str(e)[:1000]
                await session.commit()
                raise

    try:
        asyncio.run(_send())
    except Exception as exc:
        # exponential backoff with cap
        delay = min(600, (2 ** max(0, self.request.retries)) * 10)
        raise self.retry(exc=exc, countdown=delay)
