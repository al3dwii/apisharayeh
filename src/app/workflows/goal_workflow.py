from __future__ import annotations
import asyncio
from datetime import timedelta
from typing import Any, Dict, List, Set

from temporalio import workflow
from temporalio.common import RetryPolicy  # <-- fix: use RetryPolicy from common
from app.planner.schemas import PlanSpec
from app.workflows.activities import (
    run_node_activity,
    emit_plan_created,
    emit_plan_completed,
    emit_plan_failed,
)

@workflow.defn
class GoalWorkflow:
    @workflow.run
    async def run(self, tenant_id: str, goal_id: str, run_id: str, plan_spec: Dict[str, Any]) -> str:
        plan = PlanSpec.model_validate(plan_spec)

        await workflow.execute_activity(
            emit_plan_created,
            args=[tenant_id, run_id, plan.id, plan.goal_id],
            start_to_close_timeout=timedelta(seconds=10),
        )

        deps: Dict[str, Set[str]] = {n.id: set(n.depends_on) for n in plan.nodes}
        node_by_id: Dict[str, Any] = {n.id: n.model_dump() for n in plan.nodes}
        done: Set[str] = set()
        running: Set[str] = set()

        async def runnable() -> List[str]:
            return [nid for nid, d in deps.items() if nid not in done and nid not in running and d.issubset(done)]

        # Ensure we always have at least 1 slot to run; if unspecified, allow all nodes.
        max_cc = max(1, getattr(plan, "max_concurrency", 0) or len(plan.nodes) or 1)
        while len(done) < len(plan.nodes):
            ready = await runnable()
            if not ready:
                await workflow.sleep(0.1)
                continue

            allowed = max(1, max_cc - len(running))
            ready = ready[:allowed]
            if not ready:
                await workflow.sleep(0.05)
                continue

            tasks = []
            for nid in ready:
                running.add(nid)
                node = node_by_id[nid]
                tasks.append(workflow.execute_activity(
                    run_node_activity,
                    args=[tenant_id, run_id, plan.id, node],
                    start_to_close_timeout=timedelta(minutes=30),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(   # <-- fix: use RetryPolicy here
                        initial_interval=timedelta(seconds=1),
                        backoff_coefficient=2.0,
                        maximum_attempts=3,
                    ),
                ))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for nid, res in zip(ready, results):
                running.discard(nid)
                if isinstance(res, Exception):
                    await workflow.execute_activity(
                        emit_plan_failed,
                        args=[tenant_id, run_id, plan.id, nid],
                        start_to_close_timeout=timedelta(seconds=10),
                    )
                    raise res
                done.add(nid)

        await workflow.execute_activity(
            emit_plan_completed,
            args=[tenant_id, run_id, plan.id],
            start_to_close_timeout=timedelta(seconds=10),
        )
        return "completed"
