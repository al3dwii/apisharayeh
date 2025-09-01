from __future__ import annotations
import asyncio, os, sys
from temporalio.client import Client
from temporalio.worker import Worker

from app.workflows.goal_workflow import GoalWorkflow
from app.workflows import activities as activities_mod

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "o2-default")

async def connect_temporal(addr: str, attempts: int = 60, delay: float = 1.0) -> Client:
    last = None
    for _ in range(attempts):
        try:
            return await Client.connect(addr)
        except Exception as e:
            last = e
            await asyncio.sleep(delay)
    raise RuntimeError(f"Temporal connect failed after {attempts} attempts to {addr}") from last

async def main():
    print(f"[worker] connecting to Temporal at {TEMPORAL_ADDRESS} ...")
    client = await connect_temporal(TEMPORAL_ADDRESS)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[GoalWorkflow],
        activities=[
            activities_mod.emit_plan_created,
            activities_mod.emit_plan_completed,
            activities_mod.emit_plan_failed,
            activities_mod.run_node_activity,
        ],
    )
    print(f"[worker] connected. task_queue={TASK_QUEUE}")
    await worker.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
