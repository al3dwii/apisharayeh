from fastapi import APIRouter, Depends, HTTPException, Header, Response, status
from pydantic import BaseModel, Field
import uuid
from sqlalchemy import select, text
from jsonschema import Draft202012Validator as DraftValidator

from app.core.auth import get_tenant
from app.core.rate_limit import check_rate_limit
from app.services.db import tenant_session
from app.services.idempotency import put_if_absent, get_job_for_key
from app.models.job import Job
from app.kernel.plugins.spec import ServiceManifest

# Legacy packs registry & worker
from app.packs.registry import get_registry
from app.workers.celery_app import run_agent_job

# Worker for plugins (native/in-proc or DSL executed inside the worker)
from app.workers.celery_app import execute_service_job


router = APIRouter(tags=["jobs"])


class JobCreate(BaseModel):
    # NEW (plugins)
    service_id: str | None = None
    inputs: dict = Field(default_factory=dict)

    # LEGACY (packs/agents)
    pack: str | None = None
    agent: str | None = None
    files: list[dict] | None = None

    # Optional delivery
    webhook_url: str | None = None


def _sanitize_input(req: JobCreate) -> dict:
    """Avoid persisting webhook_url or headers in the job input blob."""
    data = req.model_dump()
    data.pop("webhook_url", None)
    return data


async def resolve_manifest_for_tenant(session, tenant_id: str, service_id: str) -> ServiceManifest:
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
        # not enabled / unknown for this tenant
        raise HTTPException(status_code=403, detail=f"Service '{service_id}' not enabled for this tenant")
    return ServiceManifest.model_validate(row["spec"])


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    req: JobCreate,
    response: Response,
    tenant=Depends(get_tenant),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    await check_rate_limit(tenant.id, tenant.user_id)

    is_plugin = bool(req.service_id)

    # 1) Validate the requested target (plugin OR legacy pack/agent)
    manifest: ServiceManifest | None = None
    if is_plugin:
        # Governance: resolve manifest from DB per-tenant and validate inputs
        async with tenant_session(tenant.id) as session:
            manifest = await resolve_manifest_for_tenant(session, tenant.id, req.service_id)  # raises 403 if not allowed
        DraftValidator(manifest.inputs or {"type": "object"}).validate(req.inputs or {})
    else:
        # Legacy path: require pack & agent
        if not req.pack or not req.agent:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "pack and agent are required (or provide service_id)",
            )
        registry = get_registry()
        pack_map = registry.get(req.pack)
        if not pack_map or req.agent not in pack_map:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown pack/agent")

    job_id = str(uuid.uuid4())

    # 2) Idempotency
    incoming = (req.inputs or {}) if is_plugin else _sanitize_input(req)
    if idempotency_key:
        existing_id = await get_job_for_key(tenant.id, idempotency_key)
        if existing_id:
            async with tenant_session(tenant.id) as session:
                res = await session.execute(select(Job).where(Job.id == existing_id))
                job = res.scalar_one_or_none()
                if job:
                    # Detect mismatched payloads
                    if job.input_json and job.input_json != incoming:
                        raise HTTPException(
                            status.HTTP_409_CONFLICT,
                            "Idempotency-Key already used with different payload",
                        )
                    response.headers["Location"] = f"/v1/jobs/{job.id}"
                    return {"id": job.id, "status": job.status, "idempotent": True}
        else:
            ok = await put_if_absent(tenant.id, idempotency_key, job_id)
            if not ok:
                winner_id = await get_job_for_key(tenant.id, idempotency_key)
                if winner_id:
                    response.headers["Location"] = f"/v1/jobs/{winner_id}"
                    return {"id": winner_id, "status": "queued", "idempotent": True}

    # 3) Create job row
    async with tenant_session(tenant.id) as session:
        if is_plugin:
            assert manifest is not None  # for type-checkers
            job = Job(
                id=job_id,
                tenant_id=tenant.id,
                kind=manifest.id,
                status="queued",
                input_json=req.inputs or {},
                service_id=manifest.id,
                service_version=manifest.version,
                service_runtime=manifest.runtime,
                manifest_snapshot=manifest.model_dump(),
            )
        else:
            job = Job(
                id=job_id,
                tenant_id=tenant.id,
                kind=f"{req.pack}.{req.agent}",
                status="queued",
                input_json=incoming,  # sanitized legacy payload
            )
        session.add(job)
        await session.commit()

    # 4) Enqueue task
    try:
        if is_plugin:
            queue = (
                (job.manifest_snapshot.get("resources") or {}).get("queue", "cpu")
                if getattr(job, "manifest_snapshot", None)
                else "cpu"
            )
            # enqueue with ONLY job_id to match worker signature
            execute_service_job.apply_async(args=[job_id], queue=queue)
        else:
            payload = {**(req.inputs or {}), "job_id": job_id, "files": req.files}
            run_agent_job.delay(tenant.id, req.pack, req.agent, payload, job_id, req.webhook_url)
    except Exception as e:
        # mark job failed if enqueue dies
        async with tenant_session(tenant.id) as session:
            res = await session.execute(select(Job).where(Job.id == job_id))
            job = res.scalar_one()
            job.status = "failed"
            job.error = f"enqueue_error: {type(e).__name__}: {e}"
            await session.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Queue unavailable, try again")

    response.headers["Location"] = f"/v1/jobs/{job_id}"
    return {"id": job_id, "status": "queued"}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, tenant=Depends(get_tenant)):
    async with tenant_session(tenant.id) as session:
        res = await session.execute(select(Job).where(Job.id == job_id))
        job = res.scalar_one_or_none()
        if not job:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
        return {
            "id": job.id,
            "status": job.status,
            "kind": job.kind,
            "input": job.input_json,
            "output": job.output_json,
            "error": job.error,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }
