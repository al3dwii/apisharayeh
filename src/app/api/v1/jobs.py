# /Users/omair/apisharayeh/src/app/api/v1/jobs.py
# ---------------------------------------------------------------------------
# Jobs API (plugins-only)
# - service_id is REQUIRED (legacy packs/agents removed)
# - per-tenant manifest resolution + JSON-Schema validation
# - idempotency via Idempotency-Key header
# - queues to Celery based on manifest.resources.queue (default "cpu")
# - multi-tenant safe lookups (always filter by tenant_id)
# ---------------------------------------------------------------------------

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from jsonschema import Draft202012Validator as DraftValidator
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError

from app.core.auth import get_tenant
from app.core.rate_limit import check_rate_limit
from app.services.db import tenant_session
from app.services.idempotency import put_if_absent, get_job_for_key
from app.models.job import Job
from app.kernel.plugins.spec import ServiceManifest

# Worker for plugins (native/in-proc or DSL executed inside the worker)
from app.workers.celery_app import execute_service_job


router = APIRouter(tags=["jobs"])


# ----------------------------
# Request / Response models
# ----------------------------

class JobCreate(BaseModel):
    """
    Create a job for a plugin service.

    - service_id: REQUIRED. Must be enabled for the tenant.
    - inputs:     Validated against the service manifest JSON-Schema.
    - webhook_url: Optional (not persisted); if you need webhooks, wire this
                   through your worker via a job metadata store instead.
    """
    service_id: str = Field(..., description="Plugin service identifier (e.g., 'office.word_to_pptx')")
    inputs: Dict[str, Any] = Field(default_factory=dict)
    webhook_url: Optional[str] = Field(default=None)


# ----------------------------
# Helpers
# ----------------------------

async def resolve_manifest_for_tenant(session, tenant_id: str, service_id: str) -> ServiceManifest:
    """
    Fetch the enabled manifest for this tenant + service_id.
    Raises 403 if not allowed.
    """
    sql = text(
        """
        SELECT pr.spec
        FROM tenant_plugins tp
        JOIN plugin_registry pr
          ON pr.service_id = tp.service_id AND pr.version = tp.version
        WHERE tp.tenant_id = :tid
          AND tp.service_id = :sid
          AND tp.enabled = TRUE
          AND pr.enabled = TRUE
        LIMIT 1
        """
    )
    row = (await session.execute(sql, {"tid": tenant_id, "sid": service_id})).mappings().first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Service '{service_id}' is not enabled for this tenant",
        )
    return ServiceManifest.model_validate(row["spec"])


def _validate_inputs_against_manifest(manifest: ServiceManifest, inputs: Dict[str, Any]) -> None:
    """
    Validate inputs dict against the manifest's JSON-Schema.
    """
    schema = manifest.inputs or {"type": "object"}
    try:
        DraftValidator(schema).validate(inputs or {})
    except JSONSchemaValidationError as e:
        path = ".".join(str(p) for p in e.path) if e.path else ""
        loc = f" at '{path}'" if path else ""
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Input validation error{loc}: {e.message}",
        ) from e


# ----------------------------
# Routes
# ----------------------------

@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    req: JobCreate,
    response: Response,
    tenant=Depends(get_tenant),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """
    Create a new job for a plugin service.
    - Requires service_id.
    - Validates inputs via manifest JSON-Schema.
    - Enqueues a worker task that will execute the service.
    - Returns 202 + Location header pointing to /v1/jobs/{id}.
    """
    # 0) Rate limit
    await check_rate_limit(tenant.id, tenant.user_id)

    # 1) Resolve & validate manifest (governance)
    async with tenant_session(tenant.id) as session:
        manifest = await resolve_manifest_for_tenant(session, tenant.id, req.service_id)
    _validate_inputs_against_manifest(manifest, req.inputs)

    # 2) Idempotency check (by tenant + header key)
    payload_for_hash = req.inputs or {}
    job_id = str(uuid.uuid4())

    if idempotency_key:
        existing_id = await get_job_for_key(tenant.id, idempotency_key)
        if existing_id:
            # Return the existing job if payload matches, else 409
            async with tenant_session(tenant.id) as session:
                res = await session.execute(
                    select(Job).where(Job.id == existing_id, Job.tenant_id == tenant.id)
                )
                existing = res.scalar_one_or_none()
                if existing:
                    if existing.input_json and existing.input_json != payload_for_hash:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="Idempotency-Key already used with a different payload",
                        )
                    response.headers["Location"] = f"/v1/jobs/{existing.id}"
                    return {"id": existing.id, "status": existing.status, "idempotent": True}
        else:
            # Win the race to claim the key → job_id
            ok = await put_if_absent(tenant.id, idempotency_key, job_id)
            if not ok:
                # Another request won; return the winner
                winner_id = await get_job_for_key(tenant.id, idempotency_key)
                if winner_id:
                    response.headers["Location"] = f"/v1/jobs/{winner_id}"
                    return {"id": winner_id, "status": "queued", "idempotent": True}

    # 3) Persist job row
    async with tenant_session(tenant.id) as session:
        job = Job(
            id=job_id,
            tenant_id=tenant.id,
            kind=manifest.id,  # for quick filtering
            status="queued",
            input_json=req.inputs or {},
            service_id=manifest.id,
            service_version=manifest.version,
            service_runtime=manifest.runtime,
            manifest_snapshot=manifest.model_dump(),  # freeze for audit
        )
        session.add(job)
        await session.commit()

    # 4) Enqueue worker task
    try:
        queue = (manifest.resources or {}).get("queue", "cpu")
        # Enqueue by job_id; worker re-loads the row and executes based on snapshot/runtime
        execute_service_job.apply_async(args=[job_id], queue=queue)
    except Exception as e:
        # Mark job failed if enqueue fails
        async with tenant_session(tenant.id) as session:
            res = await session.execute(
                select(Job).where(Job.id == job_id, Job.tenant_id == tenant.id)
            )
            failed_job = res.scalar_one_or_none()
            if failed_job:
                failed_job.status = "failed"
                failed_job.error = f"enqueue_error: {type(e).__name__}: {e}"
                await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Queue unavailable, please try again later",
        )

    response.headers["Location"] = f"/v1/jobs/{job_id}"
    return {"id": job_id, "status": "queued"}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, tenant=Depends(get_tenant)):
    """
    Fetch a job status/result (tenant-scoped).
    """
    async with tenant_session(tenant.id) as session:
        res = await session.execute(
            select(Job).where(Job.id == job_id, Job.tenant_id == tenant.id)
        )
        job = res.scalar_one_or_none()
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        return {
            "id": job.id,
            "status": job.status,
            "kind": job.kind,
            "input": job.input_json,
            "output": job.output_json,
            "error": job.error,
            "updated_at": job.updated_at.isoformat() if getattr(job, "updated_at", None) else None,
        }
