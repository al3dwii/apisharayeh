from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Optional

from app.core.logging import get_logger

log = get_logger(__name__)

# ---- env / config ----
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
EVENT_SCHEMA_VERSION = os.getenv("EVENT_SCHEMA_VERSION", "v1")

# Lazy aiokafka import (so unit tests don’t require it)
try:
    from aiokafka import AIOKafkaProducer  # type: ignore
except Exception:  # pragma: no cover
    AIOKafkaProducer = None  # type: ignore

# ---- datamodel ----
@dataclass
class EventEnvelope:
    id: str
    tenant_id: str
    run_id: str
    type: str
    ts_ms: int
    # Optional/correlation
    plan_id: Optional[str] = None
    goal_id: Optional[str] = None
    node_id: Optional[str] = None
    trace_id: Optional[str] = None
    # Payload
    data: Optional[dict[str, Any]] = None
    # Evolvable contracts
    schema_version: str = EVENT_SCHEMA_VERSION


def now_ms() -> int:
    return int(time.time() * 1000)


# ---- producer singleton ----
_producer: Optional["AIOKafkaProducer"] = None
_start_lock = asyncio.Lock()


async def _get_producer() -> Optional["AIOKafkaProducer"]:
    """Create and cache a Kafka producer if aiokafka is available; else None."""
    global _producer
    if AIOKafkaProducer is None:
        log.warning("aiokafka not installed; EventBus will no-op (logging only)")
        return None
    if _producer is not None:
        return _producer
    async with _start_lock:
        if _producer is not None:
            return _producer
        prod = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
        await prod.start()
        _producer = prod
        log.info("Kafka producer started bootstrap=%s", KAFKA_BOOTSTRAP)
        return _producer


def _topic_for(tenant_id: str) -> str:
    return f"tenant.{tenant_id}.events"


def _serialize(ev: EventEnvelope) -> bytes:
    # Ensure schema_version exists in both top-level and inside data for belt+braces
    obj = asdict(ev)
    data = dict(obj.get("data") or {})
    data.setdefault("schema_version", ev.schema_version)
    obj["data"] = data
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


async def emit(ev: EventEnvelope) -> None:
    """
    Emit to Kafka if available; otherwise log JSON (safe fallback for local dev).
    Key = run_id for partition locality per run.
    """
    payload = _serialize(ev)
    topic = _topic_for(ev.tenant_id)

    prod = await _get_producer()
    if prod is None:
        log.info("[eventbus:noop] topic=%s key=%s value=%s", topic, ev.run_id, payload.decode("utf-8"))
        return

    try:
        await prod.send_and_wait(topic, value=payload, key=ev.run_id.encode("utf-8"))
    except Exception as e:
        log.warning("Kafka emit failed topic=%s error=%r; falling back to log", topic, e)
        log.info("[eventbus:fallback] topic=%s key=%s value=%s", topic, ev.run_id, payload.decode("utf-8"))
