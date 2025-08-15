from fastapi import APIRouter, Depends, HTTPException, Header, Response, status
from pydantic import BaseModel, Field
import uuid
from sqlalchemy import select

from app.core.auth import get_tenant
from app.core.rate_limit import check_rate_limit
from app.services.db import tenant_session
from app.services.idempotency import put_if_absent, get_job_for_key
from app.models.job import Job

# Legacy packs registry & worker
from app.packs.registry import get_registry
from app.workers.celery_app import run_agent_job

# NEW: plugins registry & worker
from app.kernel.plugins.loader import registry as plugin_registry
from app.workers.celery_app import execute_service_job


router = APIRouter()


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
    manifest = None
    if is_plugin:
        try:
            manifest = plugin_registry.get(req.service_id)  # raises KeyError if unknown
            plugin_registry.validate_inputs(req.service_id, req.inputs or {})
        except KeyError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown service_id '{req.service_id}'")
    else:
        # Legacy path: require pack & agent
        if not req.pack or not req.agent:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "pack and agent are required (or provide service_id)")
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
            queue = (job.manifest_snapshot.get("resources") or {}).get("queue", "cpu") if job.manifest_snapshot else "cpu"
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
