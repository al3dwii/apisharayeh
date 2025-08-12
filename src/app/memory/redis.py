# app/memory/redis.py

from __future__ import annotations

import os
from typing import Optional
import redis.asyncio as redis
from app.core.config import settings

_client: Optional[redis.Redis] = None
_queue: Optional[redis.Redis] = None

def _make(url: str) -> redis.Redis:
    # Use safe defaults; all args below are supported by redis-py asyncio client
    return redis.from_url(
        url,
        decode_responses=True,                     # we send/expect JSON strings
        health_check_interval=int(os.getenv("REDIS_HEALTHCHECK_SEC", "30")),
        socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT", "5")),
        socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT", "5")),
        retry_on_timeout=True,
        max_connections=int(os.getenv("REDIS_MAX_CONNS", "50")),
    )

def get_redis() -> redis.Redis:
    """General-purpose Redis (rate limiting, events pubsub, etc.)."""
    global _client
    if _client is None:
        _client = _make(settings.REDIS_URL)
    return _client

def get_queue() -> redis.Redis:
    """Queue Redis (Celery broker/result)."""
    global _queue
    if _queue is None:
        _queue = _make(settings.REDIS_URL_QUEUE)
    return _queue

async def ping_all() -> bool:
    """Lightweight readiness check for /readyz."""
    ok = True
    try:
        await get_redis().ping()
    except Exception:
        ok = False
    try:
        await get_queue().ping()
    except Exception:
        ok = False
    return ok

async def close_all() -> None:
    """Gracefully close Redis clients on shutdown."""
    global _client, _queue
    try:
        if _client is not None:
            await _client.close()
    finally:
        _client = None
    try:
        if _queue is not None:
            await _queue.close()
    finally:
        _queue = None
