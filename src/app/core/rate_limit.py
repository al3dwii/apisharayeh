import time
from typing import Tuple, Dict, Any, Optional

from fastapi import HTTPException
from app.memory.redis import get_redis
from app.core.config import settings

def _key(prefix: str, ident: str, scope: Optional[str] = None) -> str:
    return f"rl:{scope or 'global'}:{prefix}:{ident}"

async def _allow(prefix: str, ident: str, limit: int, window_sec: int, cost: int = 1, scope: Optional[str] = None) -> Tuple[bool, int, int]:
    """
    Returns (allowed, remaining, reset_epoch).
    Fixed-window counter with TTL.
    """
    r = get_redis()
    key = _key(prefix, ident, scope)
    # atomically increment and read ttl
    pipe = r.pipeline()
    pipe.incrby(key, cost)
    pipe.ttl(key)
    current, ttl = await pipe.execute()

    # first hit in window: set TTL
    if ttl == -1:
        await r.expire(key, window_sec)
        ttl = window_sec

    allowed = int(current) <= limit
    remaining = max(0, limit - int(current))
    reset_epoch = int(time.time()) + max(int(ttl), 0)
    return allowed, remaining, reset_epoch

async def check_rate_limit(tenant_id: str, user_id: str | None = None, *, scope: str | None = None):
    """
    Enforce per-tenant and per-user fixed-window limits.
    Raises 429 with helpful headers when exceeded.
    """
    window = int(getattr(settings, "RATE_LIMIT_WINDOW_SEC", 60))
    user_limit = int(getattr(settings, "RATE_LIMIT_USER_PER_MIN", 60))
    tenant_limit = int(getattr(settings, "RATE_LIMIT_TENANT_PER_MIN", 600))

    # tenant first
    ok_tenant, rem_tenant, reset_tenant = await _allow("tenant", tenant_id, tenant_limit, window, scope=scope)
    if not ok_tenant:
        raise HTTPException(
            status_code=429,
            detail="Tenant rate limit exceeded",
            headers={
                "Retry-After": str(max(1, reset_tenant - int(time.time()))),
                "X-RateLimit-Limit": str(tenant_limit),
                "X-RateLimit-Remaining": str(rem_tenant),
                "X-RateLimit-Reset": str(reset_tenant),
                "X-RateLimit-Scope": scope or "global",
                "X-RateLimit-Subject": "tenant",
            },
        )

    if user_id:
        ok_user, rem_user, reset_user = await _allow("user", user_id, user_limit, window, scope=scope)
        if not ok_user:
            raise HTTPException(
                status_code=429,
                detail="User rate limit exceeded",
                headers={
                    "Retry-After": str(max(1, reset_user - int(time.time()))),
                    "X-RateLimit-Limit": str(user_limit),
                    "X-RateLimit-Remaining": str(rem_user),
                    "X-RateLimit-Reset": str(reset_user),
                    "X-RateLimit-Scope": scope or "global",
                    "X-RateLimit-Subject": "user",
                },
            )

    # could return info if callers want to set headers on success in the future
    return {
        "window": window,
        "tenant": {"limit": tenant_limit, "remaining": rem_tenant, "reset": reset_tenant},
        **({"user": {"limit": user_limit, "remaining": rem_user, "reset": reset_user}} if user_id else {}),
    }
