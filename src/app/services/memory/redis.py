# app/services/memory/redis.py
import redis.asyncio as redis
from app.core.config import settings

_redis = None

def get_redis():
    global _redis
    if _redis is None:
        _redis = redis.from_url(
            settings.REDIS_URL,                   # e.g. "redis://redis:6379/0"
            max_connections=int(getattr(settings, "REDIS_MAX_CONNECTIONS", 64)),
            health_check_interval=30,
            retry_on_timeout=True,
            decode_responses=False,               # keep bytes; decode in handlers if needed
        )
    return _redis
