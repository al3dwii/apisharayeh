# /Users/omair/apisharayeh/src/app/workers/celery_app.py
# ------------------------------------------------------------------------------
# Celery worker (plugins-only, DSL)
# - Executes services via ServiceRunner (modern DSL)
# - Emits start/end events; respects tenant RLS via GUC
# - Normalizes outputs to '/artifacts/...'
# - Validates produced PDFs (size > 2KB) to catch silent LibreOffice failures
# - Includes deliver_webhook with exponential backoff
# ------------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import os
import traceback
from pathlib import Path
from typing import Any, Optional, Union
from urllib.parse import urlparse

from celery import Celery
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, text

from app.core.config import settings
from app.kernel.plugins.spec import ServiceManifest
from app.kernel.service_runner import ServiceRunner
from app.services.events import emit_event


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


# Helpers: artifacts URL normalization ----------------------------------------

def _artifacts_root() -> Path:
    return Path(settings.ARTIFACTS_DIR or "./artifacts").expanduser().resolve()

def _fs_to_artifacts_url(p: Union[str, Path]) -> str:
    try:
        path = Path(p).expanduser().resolve()
    except Exception:
        return str(p)
    try:
        rel = path.relative_to(_artifacts_root())
    except Exception:
        return str(path)
    return f"/artifacts/{rel.as_posix()}"

def _string_to_artifacts_url_if_needed(s: str) -> str:
    if s.startswith(("http://", "https://")):
        parsed = urlparse(s)
        fixed_path = _fs_to_artifacts_url(parsed.path)
        return fixed_path
    return _fs_to_artifacts_url(s)

def _normalize_artifact_urls(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {k: _normalize_artifact_urls(v) for k, v in obj.items()}

        for base in ("pdf", "pptx"):
            p_key, u_key = f"{base}_path", f"{base}_url"
            if p_key in out and u_key not in out:
                out[u_key] = _string_to_artifacts_url_if_needed(out[p_key])

        for k, v in list(out.items()):
            if isinstance(v, str) and (k.endswith("_url") or k in ("artifact", "url")):
                out[k] = _string_to_artifacts_url_if_needed(v)

        return out

    if isinstance(obj, list):
        return [_normalize_artifact_urls(v) for v in obj]

    if isinstance(obj, (str, Path)):
        return _string_to_artifacts_url_if_needed(str(obj))

    return obj

def _json_safe(data: Any) -> Any:
    return jsonable_encoder(data, custom_encoder={Path: lambda p: str(p)})

def _artifacts_path_from_url(u: str) -> Optional[Path]:
    """
    Map '/artifacts/<rel>' back to local filesystem path.
    Returns None if it's not an artifacts URL.
    """
    if not isinstance(u, str):
        return None
    path = urlparse(u).path  # handles full URLs too
    if not path.startswith("/artifacts/"):
        return None
    rel = path[len("/artifacts/"):]
    return _artifacts_root() / rel


# Tasks ------------------------------------------------------------------------

@celery.task(name="execute_service_job", autoretry_for=())
def execute_service_job(job_id: str, tenant_id: str) -> None:
    """
    IMPORTANT: this task now requires the tenant_id to be passed from the API.
    That lets us set the RLS GUC *before* any SELECTs so the row is visible.
    """
    from app.services.db import SessionLocal
    from app.models.job import Job

    async def _run() -> None:
        # 1) Load job with tenant RLS set, mark running
        async with SessionLocal() as session:
            # Set the GUC first so RLS allows visibility
            await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})

            job = await session.get(Job, job_id)
            if not job:
                # Not visible (wrong tenant) or missing
                return

            # If already terminal, skip
            if job.status in ("succeeded", "failed"):
                return

            job.status = "running"
            await session.commit()

            # Keep the service id handy
            service_id: Optional[str] = job.service_id
            if job.manifest_snapshot:
                try:
                    snap = ServiceManifest.model_validate(job.manifest_snapshot)
                    if snap and snap.id:
                        service_id = snap.id
                except Exception:
                    pass

        await emit_event(
            tenant_id, job_id,
            step="service.start", status="started",
            payload={
                "service_id": service_id or "",
                "env": {
                    "SOFFICE_BIN": os.environ.get("SOFFICE_BIN", ""),
                },
            },
        )

        if not service_id:
            err = "Job missing service_id"
            async with SessionLocal() as session:
                await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
                j = await session.get(Job, job_id)
                if j:
                    j.status = "failed"
                    j.error = err
                    await session.commit()
            await emit_event(tenant_id, job_id, step="service.end", status="failed", payload={"error": err})
            return

        # 2) Run via ServiceRunner in a thread
        err_txt: Optional[str] = None
        outputs: Optional[dict[str, Any]] = None
        try:
            loop = asyncio.get_running_loop()
            runner = ServiceRunner(Path(settings.PLUGINS_DIR or "./plugins").resolve())

            async def _on_event(event_type: str, payload: dict[str, Any]) -> None:
                norm = _normalize_artifact_urls(payload or {})
                await emit_event(tenant_id, job_id, step=event_type, status="info", payload=_json_safe(norm))

            inputs = dict(job.input_json or {})
            inputs.setdefault("_job_id", str(job_id))

            if getattr(job, "project_id", None):
                inputs.setdefault("project_id", job.project_id)

            raw_outputs = await asyncio.to_thread(runner.run, service_id, inputs, _on_event, loop)
            outputs = _normalize_artifact_urls(raw_outputs)
        except Exception:
            err_txt = traceback.format_exc(limit=12)

        # 3) Persist (+ validate artifacts) + events
        async with SessionLocal() as session:
            await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
            j = await session.get(Job, job_id)
            if not j:
                return

            if err_txt:
                j.status = "failed"
                j.error = (err_txt or "")[:4000]
                await session.commit()
                await emit_event(tenant_id, job_id, step="service.end", status="failed", payload={"error": j.error})
                return

            # Validate PDF/PPTX size if present (catch silent LibreOffice failures)
            safe_out = _json_safe(outputs or {})
            artifact_url = None
            if isinstance(safe_out, dict):
                artifact_url = (
                    safe_out.get("pdf_url")
                    or safe_out.get("pptx_url")
                    or safe_out.get("artifact")
                    or safe_out.get("url")
                )
            if isinstance(artifact_url, str):
                p = _artifacts_path_from_url(artifact_url)
                if p and p.exists():
                    try:
                        size = p.stat().st_size
                    except Exception:
                        size = 0
                    if size < 2048:  # smaller than 2KB is almost certainly a failed export
                        err_msg = (
                            f"artifact too small ({size} bytes) — check LibreOffice. "
                            f"SOFFICE_BIN={os.environ.get('SOFFICE_BIN','')}"
                        )
                        j.status = "failed"
                        j.error = err_msg
                        await session.commit()
                        await emit_event(
                            tenant_id, job_id, step="service.end", status="failed", payload={"error": err_msg}
                        )
                        return

                await emit_event(
                    tenant_id, job_id, step="artifact.ready", status="info", payload={"artifact": artifact_url}
                )

            # Success
            j.status = "succeeded"
            j.output_json = safe_out
            await session.commit()
            await emit_event(tenant_id, job_id, step="service.end", status="succeeded", payload=safe_out)

    asyncio.run(_run())


@celery.task(
    name="deliver_webhook",
    bind=True,
    max_retries=6,
    default_retry_delay=30,  # seconds; exponential backoff below
)
def deliver_webhook(self, tenant_id: str, delivery_id: str) -> None:
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
        delay = min(600, (2 ** max(0, self.request.retries)) * 10)
        raise self.retry(exc=exc, countdown=delay)
