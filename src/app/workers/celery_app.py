import asyncio
import inspect
import traceback
from typing import Any, Optional

from celery import Celery

from app.core.config import settings
from app.packs.registry import resolve_agent  # ✅ direct resolver

# Plugins runtime (NEW)
from sqlalchemy import select, text
from app.kernel.plugins.loader import registry as plugin_registry
from app.kernel.runtime import KernelContext, run_service
from app.services.models import ModelRouter
from app.services.tools import ToolRouter
from app.kernel.plugins.spec import ServiceManifest

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
# run_agent_job (legacy packs/agents)
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
    Celery entrypoint to execute a legacy agent job:
      1) mark job running, emit started event
      2) resolve/execute agent runner (sync/async)
      3) persist success/failure, emit event, enqueue webhook
    """
    from app.services.db import SessionLocal
    from app.models.job import Job
    from app.services.webhooks import enqueue_delivery
    from app.services.events import emit_event

    try:
        from app.memory.redis import get_redis  # optional
        r = get_redis()
    except Exception:
        r = None  # non-fatal

    async def _run() -> None:
        # 0) Resolve agent builder -> runner
        runner = None
        error: Optional[str] = None
        try:
            builder = resolve_agent(pack, agent)  # raises KeyError if unknown
            runner = builder(r, tenant_id)        # builder(redis, tenant_id) -> runner
        except KeyError as e:
            error = str(e)
        except Exception as e:
            error = f"Failed to build agent runner: {e}"

        # 1) Mark job running (idempotent: skip if already final)
        async with SessionLocal() as session:
            await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
            res = await session.execute(select(Job).where(Job.id == job_id))
            job = res.scalar_one_or_none()
            if not job or job.status in ("succeeded", "failed"):
                return
            job.status = "running"
            await session.commit()

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
            await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
            res = await session.execute(select(Job).where(Job.id == job_id))
            job = res.scalar_one_or_none()
            if not job:
                return

            if error:
                job.status = "failed"
                job.error = (error or "")[:4000]
                await session.commit()
                await emit_event(tenant_id, job_id, step="run", status="failed", payload={"error": job.error})
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
                await emit_event(tenant_id, job_id, step="run", status="succeeded", payload={"result": result})
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
# execute_service_job (NEW: plugins runtime) — backward compatible
# ---------------------------------------------------------------------------

@celery.task(
    name="execute_service_job",
    autoretry_for=(),  # let caller decide retries
)
def execute_service_job(*args, **kwargs) -> None:
    """
    Backward-compatible task entrypoint for plugin services.

    Supports BOTH invocation shapes:
      - New:   execute_service_job(job_id)
      - Legacy:execute_service_job(tenant_id, job_id, inputs, webhook_url=None)

    The job row (manifest_snapshot, input_json, tenant_id) is the source of truth.
    """
    # --- normalize job_id from args/kwargs ---
    job_id: Optional[str] = None
    if isinstance(kwargs.get("job_id"), str):
        job_id = kwargs["job_id"]
    elif len(args) == 1 and isinstance(args[0], str):
        job_id = args[0]
    elif len(args) >= 2 and isinstance(args[1], str):
        job_id = args[1]

    if not job_id:
        raise ValueError(f"execute_service_job: couldn't extract job_id from args={args} kwargs={kwargs}")

    from app.services.db import SessionLocal
    from app.models.job import Job
    from app.services.events import emit_event

    class _EventsBridge:
        """Synchronous .emit called by DSL; forwards to async emit_event."""
        def __init__(self, tenant_id: str):
            self.tenant_id = tenant_id
        def emit(self, job_id: str, step: str, payload: dict | None = None):
            try:
                asyncio.get_event_loop().create_task(
                    emit_event(self.tenant_id, job_id, step=step, status="info", payload=payload or {})
                )
            except RuntimeError:
                asyncio.run(emit_event(self.tenant_id, job_id, step=step, status="info", payload=payload or {}))

    async def _run() -> None:
        # 1) fetch job + manifest snapshot & mark running
        async with SessionLocal() as session:
            res = await session.execute(select(Job).where(Job.id == job_id))
            job = res.scalar_one_or_none()
            if not job:
                return
            tenant_id = job.tenant_id

            await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
            if job.status in ("succeeded", "failed"):
                return
            job.status = "running"
            await session.commit()

        await emit_event(tenant_id, job_id, step="service.start", status="started", payload={"kind": job.kind})

        # 2) reconstruct manifest + run
        manifest = ServiceManifest.model_validate(job.manifest_snapshot or {}) if job.manifest_snapshot else None
        if manifest is None and job.service_id:
            # Fallback to current registry (less ideal for determinism, but ok)
            manifest = plugin_registry.get(job.service_id)

        models = ModelRouter()
        tools  = ToolRouter(models=models)
        ctx    = KernelContext(job_id=job_id, events=_EventsBridge(tenant_id), artifacts=None, models=models, tools=tools)

        outputs = None
        err_txt = None
        try:
            # run_service is sync; offload to thread so we don't block the loop
            outputs = await asyncio.to_thread(run_service, manifest, job.input_json or {}, ctx)
        except Exception:
            err_txt = traceback.format_exc(limit=8)

        # 3) persist final state
        async with SessionLocal() as session:
            await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
            res = await session.execute(select(Job).where(Job.id == job_id))
            job = res.scalar_one_or_none()
            if not job:
                return
            if err_txt:
                job.status = "failed"
                job.error = (err_txt or "")[:4000]
                await session.commit()
                await emit_event(tenant_id, job_id, step="service.end", status="failed", payload={"error": job.error})
            else:
                job.status = "succeeded"
                job.output_json = outputs or {}
                await session.commit()
                await emit_event(tenant_id, job_id, step="service.end", status="succeeded", payload=outputs or {})

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
    from app.services.db import SessionLocal
    from app.models.webhook_delivery import WebhookDelivery
    from app.services.webhooks import sign_payload

    async def _send() -> None:
        async with SessionLocal() as session:
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


# import os
# import json
# import asyncio
# import inspect
# import traceback
# from typing import Any, Optional

# from celery import Celery

# from app.core.config import settings
# from app.packs.registry import resolve_agent  # ✅ direct resolver

# celery = Celery("agentic")
# celery.conf.broker_url = settings.REDIS_URL_QUEUE
# celery.conf.result_backend = settings.REDIS_URL_QUEUE
# celery.conf.update(
#     task_serializer="json",
#     accept_content=["json"],
#     result_serializer="json",
#     timezone="UTC",
#     enable_utc=True,
#     task_acks_late=True,            # safer with idempotent tasks
#     worker_prefetch_multiplier=1,   # fair dispatch
#     task_time_limit=60 * 30,        # hard limit: 30 min
#     task_soft_time_limit=60 * 28,   # soft limit: 28 min
# )

# # ---------------------------------------------------------------------------
# # run_agent_job
# # ---------------------------------------------------------------------------

# @celery.task(
#     name="run_agent_job",
#     autoretry_for=(Exception,),
#     retry_backoff=True,
#     retry_kwargs={"max_retries": 3},
# )
# def run_agent_job(
#     tenant_id: str,
#     pack: str,
#     agent: str,
#     payload: dict,
#     job_id: str,
#     webhook_url: Optional[str] = None,
# ) -> None:
#     """
#     Celery entrypoint to execute an agent job:
#       1) mark job running, emit started event
#       2) resolve/execute agent runner (sync/async)
#       3) persist success/failure, emit event, enqueue webhook
#     """
#     from sqlalchemy import select, text
#     from app.services.db import SessionLocal
#     from app.models.job import Job
#     from app.services.webhooks import enqueue_delivery
#     from app.services.events import emit_event
#     from app.memory.redis import get_redis  # if your builders want redis

#     async def _run() -> None:
#         # 0) Resolve agent builder -> runner
#         runner = None
#         error: Optional[str] = None

#         # Provide redis if your builders expect it
#         try:
#             r = get_redis()
#         except Exception:
#             r = None  # non-fatal

#         try:
#             builder = resolve_agent(pack, agent)  # ✅ raises KeyError if unknown
#             runner = builder(r, tenant_id)        # builder(redis, tenant_id) -> runner
#         except KeyError as e:
#             error = str(e)
#         except Exception as e:
#             error = f"Failed to build agent runner: {e}"

#         # 1) Mark job running (idempotent: skip if already final)
#         async with SessionLocal() as session:
#             # Set tenant GUC for RLS
#             await session.execute(
#                 text("SELECT set_config('app.tenant_id', :tid, true)"),
#                 {"tid": tenant_id},
#             )
#             res = await session.execute(select(Job).where(Job.id == job_id))
#             job = res.scalar_one_or_none()
#             if not job:
#                 # Nothing to do
#                 return
#             if job.status in ("succeeded", "failed"):
#                 # Avoid double-processing due to retries
#                 return

#             job.status = "running"
#             await session.commit()

#         # Emit started event (persisted + pubsub)
#         await emit_event(tenant_id, job_id, step="run", status="started", payload=None)

#         # 2) Execute runner
#         result: Any = None
#         if runner and error is None:
#             enriched = {**(payload or {}), "tenant_id": tenant_id, "job_id": job_id}
#             try:
#                 if hasattr(runner, "arun"):
#                     result = await runner.arun(enriched)
#                 elif hasattr(runner, "run"):
#                     maybe = runner.run(enriched)
#                     result = await maybe if inspect.isawaitable(maybe) else maybe
#                 elif callable(runner):
#                     maybe = runner(enriched)
#                     result = await maybe if inspect.isawaitable(maybe) else maybe
#                 else:
#                     error = "Agent runner type is not supported"
#             except Exception:
#                 error = traceback.format_exc(limit=8)

#         # 3) Persist final state + events + optional webhook
#         async with SessionLocal() as session:
#             await session.execute(
#                 text("SELECT set_config('app.tenant_id', :tid, true)"),
#                 {"tid": tenant_id},
#             )
#             res = await session.execute(select(Job).where(Job.id == job_id))
#             job = res.scalar_one_or_none()
#             if not job:
#                 return

#             if error:
#                 job.status = "failed"
#                 job.error = (error or "")[:4000]
#                 await session.commit()
#                 await emit_event(
#                     tenant_id, job_id, step="run", status="failed", payload={"error": job.error}
#                 )
#                 if webhook_url:
#                     await enqueue_delivery(
#                         tenant_id,
#                         job_id,
#                         webhook_url,
#                         "job.failed",
#                         {"job_id": job_id, "error": job.error},
#                     )
#             else:
#                 job.status = "succeeded"
#                 job.output_json = {"result": result}
#                 await session.commit()
#                 await emit_event(
#                     tenant_id, job_id, step="run", status="succeeded", payload={"result": result}
#                 )
#                 if webhook_url:
#                     await enqueue_delivery(
#                         tenant_id,
#                         job_id,
#                         webhook_url,
#                         "job.succeeded",
#                         {"job_id": job_id, "result": result},
#                     )

#     asyncio.run(_run())

# # ---------------------------------------------------------------------------
# # deliver_webhook  (NOTE: requires enqueue to pass tenant_id)
# # ---------------------------------------------------------------------------

# @celery.task(
#     name="deliver_webhook",
#     bind=True,
#     max_retries=6,
#     default_retry_delay=30,  # seconds; exponential backoff below
# )
# def deliver_webhook(self, tenant_id: str, delivery_id: str) -> None:
#     """
#     Deliver a queued webhook using tenant-scoped RLS.
#     IMPORTANT: enqueue must schedule with (tenant_id, delivery_id).
#     """
#     import httpx
#     from sqlalchemy import select, text
#     from app.services.db import SessionLocal
#     from app.models.webhook_delivery import WebhookDelivery
#     from app.services.webhooks import sign_payload

#     async def _send() -> None:
#         async with SessionLocal() as session:
#             # Set tenant BEFORE first SELECT (RLS)
#             await session.execute(
#                 text("SELECT set_config('app.tenant_id', :tid, true)"),
#                 {"tid": tenant_id},
#             )

#             res = await session.execute(
#                 select(WebhookDelivery).where(WebhookDelivery.id == delivery_id)
#             )
#             d = res.scalar_one_or_none()
#             if not d:
#                 return

#             payload = d.payload_json or {}
#             sig = sign_payload(payload)

#             try:
#                 async with httpx.AsyncClient(timeout=20) as c:
#                     r = await c.post(
#                         d.url,
#                         json=payload,
#                         headers={
#                             "X-Agentic-Event": d.event_type,
#                             "X-Agentic-Signature": sig,
#                             "Content-Type": "application/json",
#                         },
#                     )
#                 if 200 <= r.status_code < 300:
#                     d.status = "sent"
#                     d.attempts += 1
#                     d.last_error = None
#                     await session.commit()
#                     return
#                 raise Exception(f"HTTP {r.status_code}: {r.text[:200]}")
#             except Exception as e:
#                 d.status = "retrying"
#                 d.attempts += 1
#                 d.last_error = str(e)[:1000]
#                 await session.commit()
#                 raise

#     try:
#         asyncio.run(_send())
#     except Exception as exc:
#         # exponential backoff with cap
#         delay = min(600, (2 ** max(0, self.request.retries)) * 10)
#         raise self.retry(exc=exc, countdown=delay)

# ---------------------------------------------------------------------------
# execute_service_job  — runs a plugin service (DSL or inproc)
# ---------------------------------------------------------------------------
@celery.task(
    name="execute_service_job",
    autoretry_for=(),  # don't auto-retry; let upstream decide
)
def execute_service_job(job_id: str) -> None:
    import asyncio
    from types import SimpleNamespace
    from sqlalchemy import select, text
    from app.services.db import SessionLocal
    from app.models.job import Job
    from app.services.events import emit_event
    from app.kernel.plugins.spec import ServiceManifest
    from app.kernel.runtime import KernelContext, run_service
    from app.services.models import ModelRouter
    from app.services.tools import ToolRouter

    class _EventsBridge:
        """Synchronous .emit called by DSL; forwards to async emit_event."""
        def __init__(self, tenant_id: str):
            self.tenant_id = tenant_id
        def emit(self, job_id: str, step: str, payload: dict | None = None):
            try:
                asyncio.get_event_loop().create_task(
                    emit_event(self.tenant_id, job_id, step=step, status="info", payload=payload or {})
                )
            except RuntimeError:
                # no running loop (unlikely in Celery), run inline
                asyncio.run(emit_event(self.tenant_id, job_id, step=step, status="info", payload=payload or {}))

    async def _run() -> None:
        # 1) fetch job + manifest snapshot
        async with SessionLocal() as session:
            res = await session.execute(select(Job).where(Job.id == job_id))
            job = res.scalar_one_or_none()
            if not job:
                return
            tenant_id = job.tenant_id

            # mark running
            await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
            if job.status in ("succeeded", "failed"):
                return
            job.status = "running"
            await session.commit()

        await emit_event(tenant_id, job_id, step="service.start", status="started", payload={"kind": job.kind})

        # 2) reconstruct manifest + run
        manifest = ServiceManifest.model_validate(job.manifest_snapshot or {})
        models = ModelRouter()
        tools  = ToolRouter(models=models)
        ctx    = KernelContext(job_id=job_id, events=_EventsBridge(tenant_id), artifacts=None, models=models, tools=tools)

        outputs = None
        err_txt = None
        try:
            # run_service is sync; offload to thread so we don't block the loop
            outputs = await asyncio.to_thread(run_service, manifest, job.input_json or {}, ctx)
        except Exception as e:
            import traceback
            err_txt = traceback.format_exc(limit=8)

        # 3) persist final state
        async with SessionLocal() as session:
            await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
            res = await session.execute(select(Job).where(Job.id == job_id))
            job = res.scalar_one_or_none()
            if not job:
                return
            if err_txt:
                job.status = "failed"
                job.error = (err_txt or "")[:4000]
                await session.commit()
                await emit_event(tenant_id, job_id, step="service.end", status="failed", payload={"error": job.error})
            else:
                job.status = "succeeded"
                job.output_json = outputs or {}
                await session.commit()
                await emit_event(tenant_id, job_id, step="service.end", status="succeeded", payload=outputs or {})

    asyncio.run(_run())
