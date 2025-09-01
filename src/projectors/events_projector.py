# src/projectors/events_projector.py
from __future__ import annotations
import asyncio, json, os
from typing import Any, Dict
from aiokafka import AIOKafkaConsumer
from sqlalchemy import text
from app.services.db import session

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
GROUP = os.getenv("PROJECTOR_GROUP", "o2-projectors")
TENANT = os.getenv("TENANT_FILTER")  # if set, only consume this tenant
TOPIC = f"tenant.{TENANT}.events" if TENANT else r"^tenant\..*\.events$"

async def project_event(ev: Dict[str, Any]):
    # Example: stitch into read-models; real logic would upsert goals/plans/nodes/runs
    typ = ev.get("type")
    data = ev.get("data", {})
    # No-op skeleton to show DB access; keep RLS via GUC if your session() sets it.
    async with session() as s:
        await s.execute(text("SELECT 1"))

async def main():
    consumer = AIOKafkaConsumer(
        TOPIC, bootstrap_servers=BOOTSTRAP, group_id=GROUP,
        enable_auto_commit=True, auto_offset_reset="earliest", allow_auto_create_topics=True
    )
    await consumer.start()
    print(f"[projector] consuming {TOPIC} on {BOOTSTRAP}")
    try:
        async for msg in consumer:
            ev = json.loads(msg.value)
            await project_event(ev)
    finally:
        await consumer.stop()

if __name__ == "__main__":
    asyncio.run(main())
