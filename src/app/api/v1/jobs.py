# Jobs API (plugins-only)
# - service_id is REQUIRED
# - per-tenant manifest resolution + JSON-Schema validation
# - idempotency via Idempotency-Key header
# - queues to Celery based on manifest.resources.queue (default "cpu")
# - multi-tenant safe lookups (always filter by tenant_id)
# - normalizes artifact URLs in responses (maps fs paths → /artifacts/…)
from __future__ import annotations

import uuid
from pathlib import Path
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
from app.kernel.storage import url_for as storage_url_for
from app.workers.celery_app import execute_service_job


import os

from app.core.config import settings
from fastapi import HTTPException, status
from sqlalchemy import text
from app.kernel.plugins.spec import ServiceManifest

async def resolve_manifest_for_tenant(session, tenant_id: str, service_id: str) -> ServiceManifest:
    """
    In dev, skip tenant gating and read from plugin_registry directly.
    In prod, enforce tenant_plugins -> plugin_registry join.
    """
    dev_bypass = (settings.ENV != "prod") or os.getenv("DISABLE_TENANT_GATING", "") or os.getenv("DISABLE_TENANT", "")
    if dev_bypass:
        row = (
            await session.execute(
                text("""
                    SELECT spec
                    FROM plugin_registry
                    WHERE service_id = :sid AND enabled = TRUE
                    ORDER BY updated_at DESC NULLS LAST, created_at DESC
                    LIMIT 1
                """),
                {"sid": service_id},
            )
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Service '{service_id}' not found")
        return ServiceManifest.model_validate(row["spec"])

    # --- strict path for prod ---
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



router = APIRouter(tags=["jobs"])


class JobCreate(BaseModel):
    service_id: str = Field(..., description="Plugin service identifier (e.g., 'slides.generate')")
    project_id: str
    inputs: Dict[str, Any] = Field(default_factory=dict)
    webhook_url: Optional[str] = Field(default=None)


async def _resolve_manifest_no_tenant(session, service_id: str) -> ServiceManifest:
    """
    Dev-mode resolver: pull the latest enabled spec for a service directly from plugin_registry,
    ignoring tenant gating.
    """
    sql = text(
        """
        SELECT spec
        FROM plugin_registry
        WHERE service_id = :sid
          AND (enabled IS TRUE OR enabled IS NULL)   -- tolerate missing column defaults
        ORDER BY updated_at DESC NULLS LAST
        LIMIT 1
        """
    )
    row = (
    await session.execute(
        text("""
            SELECT spec
            FROM plugin_registry
            WHERE service_id = :sid
              AND (enabled IS TRUE OR enabled IS NULL)
            ORDER BY updated_at DESC NULLS LAST
            LIMIT 1
        """),
        {"sid": service_id},
    )
).mappings().first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{service_id}' is not registered",
        )
    return ServiceManifest.model_validate(row["spec"])


async def resolve_manifest_for_tenant(session, tenant_id: str, service_id: str) -> ServiceManifest:
    if settings.DISABLE_TENANT:
        # bypass tenant gating entirely in dev
        return await _resolve_manifest_no_tenant(session, service_id)

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

async def resolve_manifest_for_tenant(session, tenant_id: str, service_id: str) -> ServiceManifest:
    """
    In dev, skip tenant gating and read from plugin_registry directly.
    In prod, enforce tenant_plugins -> plugin_registry join.
    """
    dev_bypass = (
        settings.ENV != "prod"
        or os.getenv("DISABLE_TENANT_GATING", "")
        or os.getenv("DISABLE_TENANT", "")
    )
    if dev_bypass:
        # use the helper that doesn't reference created_at
        return await _resolve_manifest_no_tenant(session, service_id)

    # --- strict path for prod ---
    sql = text("""
        SELECT pr.spec
        FROM tenant_plugins tp
        JOIN plugin_registry pr
          ON pr.service_id = tp.service_id AND pr.version = tp.version
        WHERE tp.tenant_id = :tid
          AND tp.service_id = :sid
          AND tp.enabled = TRUE
          AND pr.enabled = TRUE
        LIMIT 1
    """)
    row = (await session.execute(sql, {"tid": tenant_id, "sid": service_id})).mappings().first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Service '{service_id}' is not enabled for this tenant",
        )
    return ServiceManifest.model_validate(row["spec"])


def _validate_inputs_against_manifest(manifest: ServiceManifest, inputs: Dict[str, Any]) -> None:
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


def _normalize_output(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {k: _normalize_output(v) for k, v in obj.items()}

        for base in ("pdf", "pptx"):
            p, u = f"{base}_path", f"{base}_url"
            if p in out and u not in out:
                out[u] = storage_url_for(out[p])

        for k, v in list(out.items()):
            if isinstance(v, (str, Path)) and (k.endswith("_url") or k in ("artifact", "url")):
                out[k] = storage_url_for(v)

        return out
    if isinstance(obj, list):
        return [_normalize_output(v) for v in obj]
    if isinstance(obj, (str, Path)):
        return storage_url_for(obj)
    return obj


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    req: JobCreate,
    response: Response,
    tenant=Depends(get_tenant),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    # Rate limit as before (you can use DISABLE_RATE_LIMIT elsewhere if you prefer)
    await check_rate_limit(tenant.id, tenant.user_id)

    # Resolve manifest (tenant-gated in prod, bypassed in dev)
    async with tenant_session(tenant.id) as session:
        manifest = await resolve_manifest_for_tenant(session, tenant.id, req.service_id)
    _validate_inputs_against_manifest(manifest, req.inputs)

    payload_for_hash = req.inputs or {}
    job_id = str(uuid.uuid4())

    # Idempotency
    if idempotency_key:
        existing_id = await get_job_for_key(tenant.id, idempotency_key)
        if existing_id:
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
            ok = await put_if_absent(tenant.id, idempotency_key, job_id)
            if not ok:
                winner_id = await get_job_for_key(tenant.id, idempotency_key)
                if winner_id:
                    response.headers["Location"] = f"/v1/jobs/{winner_id}"
                    return {"id": winner_id, "status": "queued", "idempotent": True}

    # Persist job
    async with tenant_session(tenant.id) as session:
        job = Job(
            id=job_id,
            tenant_id=tenant.id,
            project_id=req.project_id,
            kind=manifest.id,
            status="queued",
            input_json=req.inputs or {},
            service_id=manifest.id,
            service_version=manifest.version,
            service_runtime=manifest.runtime,
            manifest_snapshot=manifest.model_dump(),
        )
        session.add(job)
        await session.commit()

    # Enqueue
    try:
        queue = ((manifest.resources or {}).get("queue")) or "media"
        execute_service_job.apply_async(args=[job_id, tenant.id], queue=queue)
    except Exception as e:
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
    async with tenant_session(tenant.id) as session:
        res = await session.execute(
            select(Job).where(Job.id == job_id, Job.tenant_id == tenant.id)
        )
        job = res.scalar_one_or_none()
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

        output_norm = _normalize_output(job.output_json) if job.output_json else None

        return {
            "id": job.id,
            "status": job.status,
            "kind": job.kind,
            "input": job.input_json,
            "output": output_norm,
            "error": job.error,
            "updated_at": job.updated_at.isoformat() if getattr(job, "updated_at", None) else None,
        }
