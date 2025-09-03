from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from app.core.logging import get_logger

log = get_logger(__name__)

# ------------------ config ------------------
CACHE_BACKEND = os.getenv("O2_CACHE_BACKEND", "file").lower()  # file | redis | postgres
CACHE_DIR = Path(os.getenv("O2_CACHE_DIR", str(Path.cwd() / "var" / "cache" / "node")))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

REDIS_URL = os.getenv("REDIS_URL") or os.getenv("O2_REDIS_URL")
PG_DSN = os.getenv("PG") or os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DSN")

# Optional deps (safe to be missing)
try:  # Redis (async)
    import redis.asyncio as aioredis  # type: ignore
except Exception:  # pragma: no cover
    aioredis = None  # type: ignore

try:  # psycopg3 (sync)
    import psycopg  # type: ignore
except Exception:  # pragma: no cover
    psycopg = None  # type: ignore


# ------------------ utils ------------------
_HEX_RE = re.compile(r"^[a-fA-F0-9]{16,}$")


def _safe_key(key: str) -> str:
    """Return a filesystem-safe key (use sha256 if the key isn't already hex-ish)."""
    if _HEX_RE.match(key):
        return key.lower()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ------------------ FILE backend ------------------
def _file_path(key: str) -> Path:
    return CACHE_DIR / _safe_key(key)

async def _file_get(key: str) -> Optional[Any]:
    path = _file_path(key)
    if not path.exists():
        return None
    try:
        # file IO off the loop
        data = await asyncio.to_thread(path.read_text, "utf-8")
        return json.loads(data)
    except Exception as e:
        log.warning("cache(file) read failed key=%s err=%r", key, e)
        return None

async def _file_put(key: str, value: Any) -> None:
    path = _file_path(key)
    tmp = path.with_suffix(".tmp")
    try:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        await asyncio.to_thread(tmp.write_text, payload, "utf-8")
        await asyncio.to_thread(os.replace, tmp, path)  # atomic rename
    except Exception as e:
        log.warning("cache(file) write failed key=%s err=%r", key, e)


# ------------------ REDIS backend ------------------
_redis_client: Optional["aioredis.Redis"] = None

async def _redis() -> Optional["aioredis.Redis"]:
    global _redis_client
    if aioredis is None or not REDIS_URL:
        return None
    if _redis_client is None:
        _redis_client = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
        try:
            await _redis_client.ping()
            log.info("cache(redis) connected url=%s", REDIS_URL)
        except Exception as e:
            log.warning("cache(redis) connect failed: %r", e)
            _redis_client = None
    return _redis_client

async def _redis_get(key: str) -> Optional[Any]:
    r = await _redis()
    if not r:
        return None
    try:
        s = await r.get(key)
        return None if s is None else json.loads(s)
    except Exception as e:
        log.warning("cache(redis) get failed key=%s err=%r", key, e)
        return None

async def _redis_put(key: str, value: Any) -> None:
    r = await _redis()
    if not r:
        return
    try:
        await r.set(key, json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except Exception as e:
        log.warning("cache(redis) set failed key=%s err=%r", key, e)


# ------------------ POSTGRES backend ------------------
def _pg_available() -> bool:
    return bool(PG_DSN and psycopg is not None)

def _pg_exec(fn, *args, **kwargs):
    """Run blocking psycopg code in a thread."""
    return asyncio.to_thread(fn, *args, **kwargs)

def _pg_init_sync() -> None:
    assert psycopg is not None and PG_DSN
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists node_cache(
                  idem_key text primary key,
                  tool_id text,
                  result_ref jsonb,
                  created_at timestamptz default now()
                )
                """
            )

def _pg_get_sync(key: str) -> Optional[Any]:
    assert psycopg is not None and PG_DSN
    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("select result_ref from node_cache where idem_key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else None

def _pg_put_sync(key: str, value: Any, tool_id: Optional[str]) -> None:
    assert psycopg is not None and PG_DSN
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into node_cache (idem_key, tool_id, result_ref)
                values (%s, %s, %s)
                on conflict (idem_key) do update set result_ref = excluded.result_ref
                """,
                (key, tool_id, json.dumps(value)),
            )

_pg_inited = False

async def _pg_ensure_init() -> None:
    global _pg_inited
    if not _pg_available() or _pg_inited:
        return
    try:
        await _pg_exec(_pg_init_sync)
        _pg_inited = True
        log.info("cache(postgres) initialized dsn=%s", PG_DSN)
    except Exception as e:
        log.warning("cache(postgres) init failed: %r", e)


# ------------------ public API ------------------
async def cache_get(idem_key: str) -> Optional[Any]:
    """
    Fetch a cached value by idempotency key.
    Order: redis -> postgres -> file.
    """
    key = _safe_key(idem_key)

    # Redis
    if CACHE_BACKEND in ("redis", "auto") and aioredis and REDIS_URL:
        v = await _redis_get(key)
        if v is not None:
            return v

    # Postgres
    if CACHE_BACKEND in ("postgres", "auto") and _pg_available():
        await _pg_ensure_init()
        try:
            v = await _pg_exec(_pg_get_sync, key)
            if v is not None:
                return v
        except Exception as e:
            log.warning("cache(postgres) get failed key=%s err=%r", key, e)

    # File
    return await _file_get(key)


async def cache_put(idem_key: str, value: Any, *, tool_id: Optional[str] = None) -> None:
    """
    Store a cached value by idempotency key.
    Order: redis -> postgres -> file (best-effort).
    """
    key = _safe_key(idem_key)

    # Redis
    if CACHE_BACKEND in ("redis", "auto") and aioredis and REDIS_URL:
        await _redis_put(key, value)
        return

    # Postgres
    if CACHE_BACKEND in ("postgres", "auto") and _pg_available():
        await _pg_ensure_init()
        try:
            await _pg_exec(_pg_put_sync, key, value, tool_id)
            return
        except Exception as e:
            log.warning("cache(postgres) put failed key=%s err=%r", key, e)

    # File
    await _file_put(key, value)
