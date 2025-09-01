# src/app/services/event_bus.py
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
import json, time

from app.services.kafka import kafka_send

@dataclass
class EventEnvelope:
    id: str
    tenant_id: str
    run_id: str
    type: str
    data: Dict[str, Any]
    ts_ms: int
    trace_id: Optional[str] = None
    node_id: Optional[str] = None
    plan_id: Optional[str] = None
    goal_id: Optional[str] = None

async def emit(ev: EventEnvelope) -> None:
    topic = f"tenant.{ev.tenant_id}.events"
    await kafka_send(topic, key=ev.run_id.encode(), value=json.dumps(asdict(ev)).encode())

def now_ms() -> int:
    return int(time.time() * 1000)
