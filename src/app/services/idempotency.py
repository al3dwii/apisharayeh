from app.memory.redis import get_redis
from app.core.config import settings

def _key(tenant_id: str, idem_key: str) -> str:
    return f"idemp:{tenant_id}:{idem_key}"

async def put_if_absent(tenant_id: str, idem_key: str, job_id: str) -> bool:
    ttl = int(getattr(settings, "IDEMPOTENCY_TTL_SECONDS", 86400))
    r = get_redis()
    ok = await r.set(_key(tenant_id, idem_key), job_id, nx=True, ex=ttl)
    return bool(ok)

async def get_job_for_key(tenant_id: str, idem_key: str) -> str | None:
    r = get_redis()
    return await r.get(_key(tenant_id, idem_key))
