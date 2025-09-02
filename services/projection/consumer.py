import asyncio, json, os, psycopg, aiokafka, datetime as dt
PG=os.getenv("PG","postgresql://postgres:pass@localhost:5432/postgres")
BOOT=os.getenv("KAFKA_BOOTSTRAP","redpanda:9092")
TOPIC=os.getenv("TOPIC","tenant.tenantA.events")

async def main():
    async with aiokafka.AIOKafkaConsumer(
        TOPIC, bootstrap_servers=BOOT, group_id="proj",
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
    ) as c, psycopg.AsyncConnection.connect(PG) as conn:
        await c.start()
        async for msg in c:
            v=msg.value; now=dt.datetime.utcnow()
            if v["type"]=="PlanCreated":
                await conn.execute("insert into runs(tenant_id,run_id,plan_id,goal_id,status,started_at,updated_at) values(%s,%s,%s,%s,%s,%s,%s) on conflict (tenant_id,run_id) do update set status=excluded.status, updated_at=excluded.updated_at",
                                   (v["tenant_id"], v["run_id"], v["plan_id"], v["goal_id"], "created", now, now))
            elif v["type"] in ("NodeStarted","NodeSucceeded","NodeFailed"):
                st = {"NodeStarted":"running","NodeSucceeded":"succeeded","NodeFailed":"failed"}[v["type"]]
                await conn.execute("insert into nodes(tenant_id,run_id,node_id,status,updated_at) values(%s,%s,%s,%s,%s) on conflict (tenant_id,run_id,node_id) do update set status=excluded.status, updated_at=excluded.updated_at",
                                   (v["tenant_id"], v["run_id"], v["node_id"], st, now))
            elif v["type"]=="PlanCompleted":
                await conn.execute("update runs set status=%s, updated_at=%s where tenant_id=%s and run_id=%s",
                                   ("completed", now, v["tenant_id"], v["run_id"]))
            elif v["type"]=="PlanFailed":
                await conn.execute("update runs set status=%s, updated_at=%s where tenant_id=%s and run_id=%s",
                                   ("failed", now, v["tenant_id"], v["run_id"]))
            await conn.commit()

if __name__=="__main__": asyncio.run(main())
