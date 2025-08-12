from __future__ import annotations

import os
import inspect
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Body, status

from app.core.auth import get_tenant
from app.core.rate_limit import check_rate_limit
from app.memory.redis import get_redis

# Prefer the direct resolver if available
try:
    from app.packs.registry import resolve_agent
except Exception:
    resolve_agent = None  # fallback to get_registry below
from app.packs.registry import get_registry

router = APIRouter()

SYNC_AGENT_TIMEOUT_S = int(os.getenv("SYNC_AGENT_TIMEOUT_S", "60"))

async def _exec_runner(runner, enriched: dict):
    """
    Execute a runner that may implement:
      - async .arun(dict)
      - .run(dict) -> maybe awaitable
      - __call__(dict) -> maybe awaitable
    """
    if hasattr(runner, "arun") and inspect.iscoroutinefunction(runner.arun):
        return await runner.arun(enriched)
    if hasattr(runner, "run"):
        maybe = runner.run(enriched)
        return await maybe if inspect.isawaitable(maybe) else maybe
    if callable(runner):
        maybe = runner(enriched)
        return await maybe if inspect.isawaitable(maybe) else maybe
    raise TypeError("Runner is not callable")

@router.post("/agents/{pack}/{agent}")
async def run_agent(
    pack: str,
    agent: str,
    payload: dict = Body(default={}),
    tenant = Depends(get_tenant),
):
    # Rate limit per-tenant/per-user
    await check_rate_limit(tenant.id, tenant.user_id)

    # Resolve agent builder
    try:
        if resolve_agent:
            builder = resolve_agent(pack, agent)  # raises KeyError if unknown
        else:
            reg = get_registry()  # { pack: { agent: builder } }
            builder = reg.get(pack, {}).get(agent)
            if not callable(builder):
                raise KeyError(f"Unknown agent '{pack}.{agent}'")
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e).strip("'"))

    # Build runner
    try:
        runner = builder(get_redis(), tenant.id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent init error: {e}",
        )

    # Execute with timeout to avoid hangs
    enriched = {**(payload or {}), "tenant_id": tenant.id, "job_id": payload.get("job_id", "ad-hoc")}
    try:
        result = await asyncio.wait_for(_exec_runner(runner, enriched), timeout=SYNC_AGENT_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Agent timed out after {SYNC_AGENT_TIMEOUT_S}s",
        )
    except HTTPException:
        raise
    except Exception as e:
        # Keep it generic to avoid leaking internals
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent error: {e}",
        )

    # Return whatever the agent produced (usually a dict)
    return result
