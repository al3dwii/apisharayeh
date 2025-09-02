# src/app/workflows/goal_workflow.py
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Dict, List, Set

from temporalio import workflow
from temporalio.common import RetryPolicy

from app.planner.schemas import PlanSpec  # Pure data model; safe to import in workflow sandbox


@workflow.defn
class GoalWorkflow:
    def __init__(self) -> None:
        # Node IDs that have been HITL-approved
        self._approved: Set[str] = set()

    @workflow.signal
    def approve(self, node_id: str) -> None:
        """Human-in-the-loop approval for nodes that require it."""
        self._approved.add(node_id)

    @workflow.run
    async def run(
        self,
        tenant_id: str,
        goal_id: str,
        run_id: str,
        plan_spec: Dict[str, Any],
    ) -> str:
        # Parse/validate incoming spec into canonical model
        plan = PlanSpec.model_validate(plan_spec)

        # Announce plan creation (safe side-effect in an activity)
        # NOTE: call activities by *name strings* to avoid importing non-deterministic code in the workflow sandbox
        await workflow.execute_activity(
            "emit_plan_created",
            args=[tenant_id, run_id, plan.id, plan.goal_id],
            start_to_close_timeout=timedelta(seconds=10),
        )

        # Hard validate plan against the tool registry (fail fast)
        try:
            await workflow.execute_activity(
                "validate_plan_activity",
                args=[tenant_id, plan_spec],
                start_to_close_timeout=timedelta(seconds=20),
            )
        except Exception:
            await workflow.execute_activity(
                "emit_plan_failed",
                args=[tenant_id, run_id, plan.id, "__validation__"],
                start_to_close_timeout=timedelta(seconds=10),
            )
            raise

        # Build dependency & lookup helpers
        deps: Dict[str, Set[str]] = {n.id: set(n.depends_on) for n in plan.nodes}
        node_by_id: Dict[str, Any] = {n.id: n.model_dump() for n in plan.nodes}
        done: Set[str] = set()
        running: Set[str] = set()

        async def runnable() -> List[str]:
            """Nodes ready to run: deps satisfied, not running/done, and (if required) approved."""
            ready: List[str] = []
            for nid, d in deps.items():
                if nid in done or nid in running:
                    continue
                if not d.issubset(done):
                    continue
                node = node_by_id[nid]
                needs_approval = bool(node.get("inputs", {}).get("requires_approval"))
                if needs_approval and nid not in self._approved:
                    continue
                ready.append(nid)
            return ready

        # Scheduling loop with per-plan concurrency cap (>=1)
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
                tasks.append(
                    workflow.execute_activity(
                        "run_node_activity",
                        args=[tenant_id, run_id, plan.id, node],
                        start_to_close_timeout=timedelta(minutes=30),
                        heartbeat_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=1),
                            backoff_coefficient=2.0,
                            maximum_attempts=3,
                        ),
                    )
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for nid, res in zip(ready, results):
                running.discard(nid)
                if isinstance(res, Exception):
                    # Mark plan failed at first failing node and surface error
                    await workflow.execute_activity(
                        "emit_plan_failed",
                        args=[tenant_id, run_id, plan.id, nid],
                        start_to_close_timeout=timedelta(seconds=10),
                    )
                    raise res
                done.add(nid)

        await workflow.execute_activity(
            "emit_plan_completed",
            args=[tenant_id, run_id, plan.id],
            start_to_close_timeout=timedelta(seconds=10),
        )
        return "completed"
