from fastapi import APIRouter, Depends, HTTPException, Header, Response, status
from pydantic import BaseModel, Field
import uuid
from sqlalchemy import select

from app.core.auth import get_tenant
from app.core.rate_limit import check_rate_limit
from app.workers.celery_app import run_agent_job
from app.services.db import tenant_session
from app.services.idempotency import put_if_absent, get_job_for_key
from app.models.job import Job
from app.packs.registry import get_registry  # ✅ validate pack/agent exist

router = APIRouter()

class JobCreate(BaseModel):
    pack: str
    agent: str
    inputs: dict = Field(default_factory=dict)
    files: list[dict] | None = None
    webhook_url: str | None = None

def _sanitize_input(req: JobCreate) -> dict:
    # Avoid persisting webhook_url or headers in the job input blob
    data = req.model_dump()
    data.pop("webhook_url", None)
    return data

@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    req: JobCreate,
    response: Response,
    tenant = Depends(get_tenant),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    await check_rate_limit(tenant.id, tenant.user_id)

    # 1) Validate pack/agent exist
    registry = get_registry()
    pack_map = registry.get(req.pack)
    if not pack_map or req.agent not in pack_map:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown pack/agent")

    job_id = str(uuid.uuid4())

    # 2) Handle idempotency
    if idempotency_key:
        existing_id = await get_job_for_key(tenant.id, idempotency_key)
        if existing_id:
            async with tenant_session(tenant.id) as session:
                res = await session.execute(select(Job).where(Job.id == existing_id))
                job = res.scalar_one_or_none()
                if job:
                    # Optional: detect mismatched payloads
                    incoming = _sanitize_input(req)
                    if job.input_json and job.input_json != incoming:
                        raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency-Key already used with different payload")
                    response.headers["Location"] = f"/v1/jobs/{job.id}"
                    return {"id": job.id, "status": job.status, "idempotent": True}
        else:
            ok = await put_if_absent(tenant.id, idempotency_key, job_id)
            if not ok:
                # someone raced us; re-fetch winner
                winner_id = await get_job_for_key(tenant.id, idempotency_key)
                if winner_id:
                    response.headers["Location"] = f"/v1/jobs/{winner_id}"
                    return {"id": winner_id, "status": "queued", "idempotent": True}

    # 3) Create job row
    sanitized_input = _sanitize_input(req)
    async with tenant_session(tenant.id) as session:
        job = Job(
            id=job_id,
            tenant_id=tenant.id,
            kind=f"{req.pack}.{req.agent}",
            status="queued",                    # ✅ explicit
            input_json=sanitized_input,         # ✅ no webhook_url in DB
        )
        session.add(job)
        await session.commit()

    # 4) Enqueue Celery task (and handle broker failures)
    try:
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
async def get_job(job_id: str, tenant = Depends(get_tenant)):
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

# from fastapi import APIRouter, Depends, HTTPException, Header
# from pydantic import BaseModel, Field
# import uuid
# from app.core.auth import get_tenant
# from app.core.rate_limit import check_rate_limit
# from app.workers.celery_app import run_agent_job
# from app.services.db import tenant_session
# from app.services.idempotency import put_if_absent, get_job_for_key
# from app.models.job import Job
# from sqlalchemy import select

# router = APIRouter()

# class JobCreate(BaseModel):
#     pack: str
#     agent: str
#     inputs: dict = Field(default_factory=dict)
#     files: list[dict] | None = None
#     webhook_url: str | None = None

# @router.post("/jobs")
# async def create_job(req: JobCreate, tenant=Depends(get_tenant), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
#     await check_rate_limit(tenant.id, tenant.user_id)
#     job_id = str(uuid.uuid4())
#     # Handle idempotency
#     if idempotency_key:
#         existing = await get_job_for_key(tenant.id, idempotency_key)
#         if existing:
#             # Return existing job
#             async with tenant_session(tenant.id) as session:
#                 res = await session.execute(select(Job).where(Job.id == existing))
#                 job = res.scalar_one_or_none()
#                 if job:
#                     return {"job_id": job.id, "status": job.status, "idempotent": True}
#         else:
#             ok = await put_if_absent(tenant.id, idempotency_key, job_id)
#             if not ok:
#                 # someone raced us; re-fetch
#                 existing = await get_job_for_key(tenant.id, idempotency_key)
#                 if existing:
#                     return {"job_id": existing, "status": "queued", "idempotent": True}

#     async with tenant_session(tenant.id) as session:
#         job = Job(id=job_id, tenant_id=tenant.id, kind=f"{req.pack}.{req.agent}", input_json=req.dict())
#         session.add(job)
#         await session.commit()
#     # Dispatch to Celery; include job_id in payload for step events
#     run_agent_job.delay(tenant.id, req.pack, req.agent, {**req.inputs, "job_id": job_id}, job_id, req.webhook_url)
#     return {"job_id": job_id, "status": "queued"}

# @router.get("/jobs/{job_id}")
# async def get_job(job_id: str, tenant=Depends(get_tenant)):
#     async with tenant_session(tenant.id) as session:
#         res = await session.execute(select(Job).where(Job.id == job_id))
#         job = res.scalar_one_or_none()
#         if not job:
#             raise HTTPException(404, "Job not found")
#         return {
#             "id": job.id,
#             "status": job.status,
#             "kind": job.kind,
#             "input": job.input_json,
#             "output": job.output_json,
#             "error": job.error,
#             "updated_at": str(job.updated_at),
#         }
