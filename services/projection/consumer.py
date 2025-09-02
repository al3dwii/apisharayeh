import asyncio, json, os, datetime as dt
from aiokafka import AIOKafkaConsumer
import psycopg

PG = os.getenv("PG", "postgresql://postgres:pass@postgres:5432/postgres")
BOOT = os.getenv("KAFKA_BOOTSTRAP", "redpanda:9092")
TOPIC = os.getenv("TOPIC", "tenant.tenantA.events")

async def main():
    async with AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=BOOT,
        group_id="proj",
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        auto_offset_reset="earliest",
    ) as c, await psycopg.AsyncConnection.connect(PG) as conn:
        async for msg in c:
            v = msg.value
            now = dt.datetime.now(dt.UTC)
            if v["type"] == "PlanCreated":
                await conn.execute(
                    """
                    insert into runs(tenant_id,run_id,plan_id,goal_id,status,started_at,updated_at)
                    values (%s,%s,%s,%s,%s,%s,%s)
                    on conflict (tenant_id,run_id) do update
                    set status=excluded.status, updated_at=excluded.updated_at
                    """,
                    (
                        v["tenant_id"],
                        v["run_id"],
                        v["plan_id"],
                        v.get("goal_id"),
                        "created",
                        dt.datetime.fromtimestamp(v["ts_ms"] / 1000, dt.UTC),
                        now,
                    ),
                )
            elif v["type"] in ("NodeStarted", "NodeSucceeded", "NodeFailed"):
                st = {"NodeStarted": "running", "NodeSucceeded": "succeeded", "NodeFailed": "failed"}[v["type"]]
                await conn.execute(
                    """
                    insert into nodes(tenant_id,run_id,node_id,status,updated_at)
                    values (%s,%s,%s,%s,%s)
                    on conflict (tenant_id,run_id,node_id) do update
                    set status=excluded.status, updated_at=excluded.updated_at
                    """,
                    (v["tenant_id"], v["run_id"], v["node_id"], st, now),
                )
            elif v["type"] == "PlanCompleted":
                await conn.execute(
                    "update runs set status=%s, updated_at=%s where tenant_id=%s and run_id=%s",
                    ("completed", now, v["tenant_id"], v["run_id"]),
                )
            elif v["type"] == "PlanFailed":
                await conn.execute(
                    "update runs set status=%s, updated_at=%s where tenant_id=%s and run_id=%s",
                    ("failed", now, v["tenant_id"], v["run_id"]),
                )
            await conn.commit()

if __name__ == "__main__":
    asyncio.run(main())
