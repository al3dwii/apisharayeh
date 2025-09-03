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
    emit_plan_replanned,
)
from app.obs.metrics import boot_metrics
from app.core.logging import configure_root, get_logger

log = get_logger(__name__)


async def main() -> None:
    # Ensure root logging/format is installed before any log line
    configure_root()

    addr = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    task_queue = os.getenv("TASK_QUEUE", "o2-default")

    log.info("connecting to Temporal at %s ...", addr)
    client = await Client.connect(addr)
    log.info("connected. task_queue=%s", task_queue)

    # Prometheus exporter (idempotent)
    try:
        boot_metrics()
    except Exception as e:
        log.warning("metrics exporter not started: %r", e)

    # Worker with workflow + activities
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
            emit_plan_replanned,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
