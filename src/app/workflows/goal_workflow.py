from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Dict, List, Set

from temporalio import workflow
from temporalio.common import RetryPolicy  # use RetryPolicy from common

from app.planner.schemas import PlanSpec
from app.workflows.activities import (
    run_node_activity,
    emit_plan_created,
    emit_plan_completed,
    emit_plan_failed,
)


@workflow.defn
class GoalWorkflow:
    def __init__(self) -> None:
        # Simple in-memory state so callers can query progress
        self._running: Set[str] = set()
        self._done: Set[str] = set()
        self._failed: Set[str] = set()

    @workflow.query
    def get_state(self) -> Dict[str, List[str]]:
        """Return current node state for observability."""
        return {
            "running": sorted(self._running),
            "done": sorted(self._done),
            "failed": sorted(self._failed),
        }

    @workflow.run
    async def run(
        self,
        tenant_id: str,
        goal_id: str,
        run_id: str,
        plan_spec: Dict[str, Any],
    ) -> str:
        plan = PlanSpec.model_validate(plan_spec)

        # Emit PlanCreated
        await workflow.execute_activity(
            emit_plan_created,
            args=[tenant_id, run_id, plan.id, plan.goal_id],
            start_to_close_timeout=timedelta(seconds=10),
        )

        # Build dependency map and node lookup
        deps: Dict[str, Set[str]] = {n.id: set(n.depends_on) for n in plan.nodes}
        node_by_id: Dict[str, Any] = {n.id: n.model_dump() for n in plan.nodes}

        # Use the same sets for local logic and query visibility
        done: Set[str] = self._done
        running: Set[str] = self._running

        async def runnable() -> List[str]:
            return [
                nid
                for nid, d in deps.items()
                if nid not in done and nid not in running and d.issubset(done)
            ]

        # Ensure we always have at least 1 slot; if unspecified, allow all nodes.
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

            # Start all currently-runnable nodes
            tasks = []
            for nid in ready:
                running.add(nid)
                node = node_by_id[nid]
                tasks.append(
                    workflow.execute_activity(
                        run_node_activity,
                        args=[tenant_id, run_id, plan.id, node],
                        # activity runtime controls
                        start_to_close_timeout=timedelta(minutes=30),
                        heartbeat_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=1),
                            backoff_coefficient=2.0,
                            maximum_attempts=3,
                        ),
                    )
                )

            # Wait for this batch; mark state and propagate any failure
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for nid, res in zip(ready, results):
                running.discard(nid)
                if isinstance(res, Exception):
                    self._failed.add(nid)
                    await workflow.execute_activity(
                        emit_plan_failed,
                        args=[tenant_id, run_id, plan.id, nid],
                        start_to_close_timeout=timedelta(seconds=10),
                    )
                    # Fail the workflow so callers see the error
                    raise res
                done.add(nid)

        # All nodes done
        await workflow.execute_activity(
            emit_plan_completed,
            args=[tenant_id, run_id, plan.id],
            start_to_close_timeout=timedelta(seconds=10),
        )
        return "completed"
