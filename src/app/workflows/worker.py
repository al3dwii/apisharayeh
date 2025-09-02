from __future__ import annotations

import asyncio
import os
from temporalio.client import Client
from temporalio.worker import Worker

from app.workflows.goal_workflow import GoalWorkflow
from app.workflows.activities import (
    run_node_activity,
    emit_plan_created,
    emit_plan_completed,
    emit_plan_failed,
    validate_plan_activity,
)
from app.obs.metrics import boot_metrics


async def main() -> None:
    addr = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    task_queue = os.getenv("TASK_QUEUE", "o2-default")

    print("[worker] connecting to Temporal at", addr, "...")
    client = await Client.connect(addr)
    print("[worker] connected. task_queue=", task_queue)

    # Start Prometheus exporter
    boot_metrics()

    # Start worker
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[GoalWorkflow],
        activities=[
            run_node_activity,
            emit_plan_created,
            emit_plan_completed,
            emit_plan_failed,
            validate_plan_activity,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
