import os
import json
import asyncio
import inspect
import traceback
from typing import Any, Optional

from celery import Celery

from app.core.config import settings
from app.packs.registry import resolve_agent  # ✅ direct resolver

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

# ---------------------------------------------------------------------------
# run_agent_job
# ---------------------------------------------------------------------------

@celery.task(
    name="run_agent_job",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def run_agent_job(
    tenant_id: str,
    pack: str,
    agent: str,
    payload: dict,
    job_id: str,
    webhook_url: Optional[str] = None,
) -> None:
    """
    Celery entrypoint to execute an agent job:
      1) mark job running, emit started event
      2) resolve/execute agent runner (sync/async)
      3) persist success/failure, emit event, enqueue webhook
    """
    from sqlalchemy import select, text
    from app.services.db import SessionLocal
    from app.models.job import Job
    from app.services.webhooks import enqueue_delivery
    from app.services.events import emit_event
    from app.memory.redis import get_redis  # if your builders want redis

    async def _run() -> None:
        # 0) Resolve agent builder -> runner
        runner = None
        error: Optional[str] = None

        # Provide redis if your builders expect it
        try:
            r = get_redis()
        except Exception:
            r = None  # non-fatal

        try:
            builder = resolve_agent(pack, agent)  # ✅ raises KeyError if unknown
            runner = builder(r, tenant_id)        # builder(redis, tenant_id) -> runner
        except KeyError as e:
            error = str(e)
        except Exception as e:
            error = f"Failed to build agent runner: {e}"

        # 1) Mark job running (idempotent: skip if already final)
        async with SessionLocal() as session:
            # Set tenant GUC for RLS
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": tenant_id},
            )
            res = await session.execute(select(Job).where(Job.id == job_id))
            job = res.scalar_one_or_none()
            if not job:
                # Nothing to do
                return
            if job.status in ("succeeded", "failed"):
                # Avoid double-processing due to retries
                return

            job.status = "running"
            await session.commit()

        # Emit started event (persisted + pubsub)
        await emit_event(tenant_id, job_id, step="run", status="started", payload=None)

        # 2) Execute runner
        result: Any = None
        if runner and error is None:
            enriched = {**(payload or {}), "tenant_id": tenant_id, "job_id": job_id}
            try:
                if hasattr(runner, "arun"):
                    result = await runner.arun(enriched)
                elif hasattr(runner, "run"):
                    maybe = runner.run(enriched)
                    result = await maybe if inspect.isawaitable(maybe) else maybe
                elif callable(runner):
                    maybe = runner(enriched)
                    result = await maybe if inspect.isawaitable(maybe) else maybe
                else:
                    error = "Agent runner type is not supported"
            except Exception:
                error = traceback.format_exc(limit=8)

        # 3) Persist final state + events + optional webhook
        async with SessionLocal() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": tenant_id},
            )
            res = await session.execute(select(Job).where(Job.id == job_id))
            job = res.scalar_one_or_none()
            if not job:
                return

            if error:
                job.status = "failed"
                job.error = (error or "")[:4000]
                await session.commit()
                await emit_event(
                    tenant_id, job_id, step="run", status="failed", payload={"error": job.error}
                )
                if webhook_url:
                    await enqueue_delivery(
                        tenant_id,
                        job_id,
                        webhook_url,
                        "job.failed",
                        {"job_id": job_id, "error": job.error},
                    )
            else:
                job.status = "succeeded"
                job.output_json = {"result": result}
                await session.commit()
                await emit_event(
                    tenant_id, job_id, step="run", status="succeeded", payload={"result": result}
                )
                if webhook_url:
                    await enqueue_delivery(
                        tenant_id,
                        job_id,
                        webhook_url,
                        "job.succeeded",
                        {"job_id": job_id, "result": result},
                    )

    asyncio.run(_run())

# ---------------------------------------------------------------------------
# deliver_webhook  (NOTE: requires enqueue to pass tenant_id)
# ---------------------------------------------------------------------------

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
    from sqlalchemy import select, text
    from app.services.db import SessionLocal
    from app.models.webhook_delivery import WebhookDelivery
    from app.services.webhooks import sign_payload

    async def _send() -> None:
        async with SessionLocal() as session:
            # Set tenant BEFORE first SELECT (RLS)
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": tenant_id},
            )

            res = await session.execute(
                select(WebhookDelivery).where(WebhookDelivery.id == delivery_id)
            )
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
