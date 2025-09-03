from __future__ import annotations

"""
Projection consumer: reads tenant.*.events from Kafka and upserts into Postgres.

Env:
- KAFKA_BOOTSTRAP      (default: localhost:9092)
- KAFKA_GROUP_ID       (default: o2-projection)
- TENANT_TOPICS        (optional, comma-separated tenant ids; if unset uses regex ^tenant\\..*\\.events$)
- DATABASE_URL         (e.g. postgresql://postgres:postgres@localhost:5432/postgres)
- EVENT_SCHEMA_VERSION (default: v1)

Run:
    python -m app.projections.consumer
"""

from typing import Any, Dict, Optional, Iterable
import asyncio
import json
import os
import signal
from datetime import datetime, timezone

import asyncpg

from app.core.logging import get_logger

log = get_logger(__name__)

# ---- env ----
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "o2-projection")
TENANT_TOPICS = os.getenv("TENANT_TOPICS", "")  # e.g. "tenantA,tenantB"
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or os.getenv("PG_DSN") or ""
EVENT_SCHEMA_VERSION = os.getenv("EVENT_SCHEMA_VERSION", "v1")

# ---- kafka (lazy) ----
_aiokafka_available = False
try:
    from aiokafka import AIOKafkaConsumer  # type: ignore

    _aiokafka_available = True
except Exception as e:  # pragma: no cover
    log.info("projection: aiokafka not available (%r)", e)
    _aiokafka_available = False


# ---- sql ----
DDL: Iterable[str] = (
    """
    create table if not exists runs(
        tenant_id   text not null,
        run_id      uuid not null,
        plan_id     uuid,
        goal_id     uuid,
        status      text not null default 'running',
        started_at  timestamptz default now(),
        updated_at  timestamptz default now(),
        cost_usd    numeric(12,4) default 0,
        primary key (tenant_id, run_id)
    );
    """,
    """
    create table if not exists nodes(
        tenant_id   text not null,
        run_id      uuid not null,
        plan_id     uuid,
        node_id     text not null,
        status      text not null,
        attempt     int default 1,
        outputs_ref jsonb,
        duration_ms int,
        updated_at  timestamptz default now(),
        primary key (tenant_id, run_id, node_id)
    );
    """,
)

UPSERT_RUN = """
insert into runs(tenant_id, run_id, plan_id, goal_id, status, started_at, updated_at)
values($1, $2, $3, $4, $5, $6, $6)
on conflict (tenant_id, run_id) do update
set plan_id=excluded.plan_id,
    goal_id=excluded.goal_id,
    status=excluded.status,
    updated_at=excluded.updated_at
"""

UPDATE_RUN_STATUS = """
update runs set status=$1, updated_at=$2 where tenant_id=$3 and run_id=$4
"""

UPSERT_NODE = """
insert into nodes(tenant_id, run_id, plan_id, node_id, status, attempt, outputs_ref, duration_ms, updated_at)
values($1,$2,$3,$4,$5,$6,$7,$8,$9)
on conflict (tenant_id, run_id, node_id) do update
set status=excluded.status,
    attempt=excluded.attempt,
    outputs_ref=coalesce(excluded.outputs_ref, nodes.outputs_ref),
    duration_ms=coalesce(excluded.duration_ms, nodes.duration_ms),
    updated_at=excluded.updated_at
"""

# ---- helpers ----
def _ts(ev: Dict[str, Any]) -> datetime:
    t = ev.get("ts_ms")
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(int(t) / 1000, tz=timezone.utc)
    return datetime.now(tz=timezone.utc)


async def _apply_event(conn: asyncpg.Connection, ev: Dict[str, Any]) -> None:
    typ = ev.get("type") or ""
    tenant_id = ev.get("tenant_id") or ""
    run_id = ev.get("run_id") or ""
    plan_id = ev.get("plan_id")
    goal_id = ev.get("goal_id")
    node_id = ev.get("node_id")
    ts = _ts(ev)

    data = ev.get("data") or {}
    outputs_ref = data.get("result_ref")
    duration_ms = data.get("duration_ms")
    attempt = data.get("attempt") or 1

    # Normalize uuids for asyncpg (pass as str; it will cast)
    run_id_s = str(run_id) if run_id else None
    plan_id_s = str(plan_id) if plan_id else None
    goal_id_s = str(goal_id) if goal_id else None

    if typ == "PlanCreated":
        await conn.execute(
            UPSERT_RUN,
            tenant_id,
            run_id_s,
            plan_id_s,
            goal_id_s,
            "running",
            ts,
        )
        return

    if typ == "PlanCompleted":
        await conn.execute(UPDATE_RUN_STATUS, "completed", ts, tenant_id, run_id_s)
        return

    if typ == "PlanFailed":
        await conn.execute(UPDATE_RUN_STATUS, "failed", ts, tenant_id, run_id_s)
        # we also ensure a node record exists for the failing node (optional)
        if node_id:
            await conn.execute(
                UPSERT_NODE,
                tenant_id,
                run_id_s,
                plan_id_s,
                node_id,
                "failed",
                attempt,
                None,
                None,
                ts,
            )
        return

    if typ == "NodeStarted":
        if not node_id:
            return
        await conn.execute(
            UPSERT_NODE,
            tenant_id,
            run_id_s,
            plan_id_s,
            node_id,
            "started",
            attempt,
            None,
            None,
            ts,
        )
        return

    if typ in ("NodeSucceeded", "NodeSkippedCached", "NodeFailed"):
        status = {
            "NodeSucceeded": "succeeded",
            "NodeSkippedCached": "cached",
            "NodeFailed": "failed",
        }[typ]
        if not node_id:
            return
        await conn.execute(
            UPSERT_NODE,
            tenant_id,
            run_id_s,
            plan_id_s,
            node_id,
            status,
            attempt,
            json.dumps(outputs_ref) if outputs_ref else None,
            int(duration_ms) if duration_ms is not None else None,
            ts,
        )
        return

    # Unknown or ignored types are fine
    return


async def _ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        for stmt in DDL:
            await conn.execute(stmt)


# ---- main loop ----
async def main() -> None:
    if not _aiokafka_available:
        raise RuntimeError("aiokafka not installed; projection cannot start")

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required for projection")

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    await _ensure_schema(pool)

    if TENANT_TOPICS.strip():
        topics = [f"tenant.{t.strip()}.events" for t in TENANT_TOPICS.split(",") if t.strip()]
        consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=KAFKA_BOOTSTRAP,
            group_id=KAFKA_GROUP_ID,
            enable_auto_commit=True,
            auto_offset_reset="latest",
        )
        pattern = None
        log.info("projection: subscribing explicit topics=%s", topics)
    else:
        # subscribe to all tenant.<id>.events via regex
        consumer = AIOKafkaConsumer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            group_id=KAFKA_GROUP_ID,
            enable_auto_commit=True,
            auto_offset_reset="latest",
        )
        pattern = r"^tenant\..*\.events$"
        log.info("projection: subscribing pattern=%s", pattern)

    await consumer.start()
    if pattern:
        consumer.subscribe(pattern=pattern)
    try:
        stop = asyncio.Event()

        def _sig(*_: object) -> None:
            stop.set()

        for s in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio.get_running_loop().add_signal_handler(s, _sig)
            except NotImplementedError:
                # Windows
                pass

        log.info("projection: running (bootstrap=%s, group=%s)", KAFKA_BOOTSTRAP, KAFKA_GROUP_ID)

        while not stop.is_set():
            batch = await consumer.getmany(timeout_ms=1000, max_records=500)
            if not batch:
                continue
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for tp, records in batch.items():
                        for rec in records:
                            try:
                                ev = json.loads(rec.value.decode("utf-8"))
                                await _apply_event(conn, ev)
                            except Exception as e:
                                log.info("projection: error applying event: %r value=%s", e, rec.value[:512])
            # auto-commit is enabled
    finally:
        try:
            await consumer.stop()
        except Exception:
            pass
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
