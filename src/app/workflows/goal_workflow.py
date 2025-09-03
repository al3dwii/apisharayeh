from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Dict, List, Set

from temporalio import workflow
from temporalio.common import RetryPolicy

# Pure data models only – safe to import inside workflow sandbox
from app.planner.schemas import PlanSpec


@workflow.defn
class GoalWorkflow:
    """
    Durable, deterministic orchestration for a single run:
      - Validates plan
      - Schedules by dependency + max_concurrency
      - Supports HITL approval per node
      - On failure, optionally runs a deterministic fallback (replan-lite)
    """

    def __init__(self) -> None:
        # Node IDs that have been HITL-approved
        self._approved: Set[str] = set()

    # ---------- HITL signal ----------
    @workflow.signal
    def approve(self, node_id: str) -> None:
        """Human-in-the-loop: call this to approve a specific node."""
        self._approved.add(node_id)

    # ---------- Main workflow ----------
    @workflow.run
    async def run(
        self,
        tenant_id: str,
        goal_id: str,
        run_id: str,
        plan_spec: Dict[str, Any],
    ) -> str:
        # Parse/validate to canonical model (no side-effects)
        plan = PlanSpec.model_validate(plan_spec)

        # Announce plan creation
        await workflow.execute_activity(
            "emit_plan_created",
            args=[tenant_id, run_id, plan.id, plan.goal_id],
            start_to_close_timeout=timedelta(seconds=15),
        )

        # Registry/shape validation (fail-fast)
        try:
            await workflow.execute_activity(
                "validate_plan_activity",
                args=[tenant_id, plan_spec],
                start_to_close_timeout=timedelta(seconds=30),
            )
        except Exception:
            await workflow.execute_activity(
                "emit_plan_failed",
                args=[tenant_id, run_id, plan.id, "__validation__"],
                start_to_close_timeout=timedelta(seconds=10),
            )
            raise

        # Build helpers
        deps: Dict[str, Set[str]] = {n.id: set(n.depends_on or []) for n in plan.nodes}
        node_by_id: Dict[str, Any] = {n.id: n.model_dump() for n in plan.nodes}
        done: Set[str] = set()
        running: Set[str] = set()

        async def runnable() -> List[str]:
            """Nodes whose deps satisfied, not done/running, and approved if required."""
            ready: List[str] = []
            for nid, parents in deps.items():
                if nid in done or nid in running:
                    continue
                if not parents.issubset(done):
                    continue
                node = node_by_id[nid]
                needs_approval = bool(node.get("requires_approval") or node.get("inputs", {}).get("requires_approval"))
                if needs_approval and nid not in self._approved:
                    continue
                ready.append(nid)
            return ready

        # Concurrency cap
        max_cc = max(1, getattr(plan, "max_concurrency", 0) or len(plan.nodes) or 1)
        workflow.logger.info(
            "GoalWorkflow started goal_id=%s nodes=%d max_concurrency=%d",
            goal_id, len(plan.nodes), max_cc
        )

        # Scheduling loop
        while len(done) < len(plan.nodes):
            ready = await runnable()
            if not ready:
                # No ready nodes: either waiting for approval or blocked by running deps
                await workflow.sleep(0.1)
                continue

            # Respect plan concurrency
            allowed = max(1, max_cc - len(running))
            ready = ready[:allowed]
            if not ready:
                await workflow.sleep(0.05)
                continue

            # Launch activities for ready nodes
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

            # Wait for completion/failure; handle per-node outcomes
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for nid, res in zip(ready, results):
                running.discard(nid)
                if isinstance(res, Exception):
                    # Attempt deterministic fallback if configured
                    node = node_by_id[nid]
                    on_error = node.get("on_error") or {}
                    fallback = on_error.get("fallback")

                    if fallback and isinstance(fallback, dict):
                        fb_node = {
                            "id": f"{nid}__fallback",
                            "tool_id": fallback.get("tool_id"),
                            "inputs": {
                                **(node.get("inputs") or {}),
                                **(fallback.get("inputs") or {}),
                            },
                            "requires_approval": bool(fallback.get("requires_approval", False)),
                            "depends_on": [],  # standalone execution
                        }

                        delta = {
                            "action": "fallback",
                            "failed_node_id": nid,
                            "fallback_tool_id": fb_node["tool_id"],
                        }
                        await workflow.execute_activity(
                            "emit_plan_replanned",
                            args=[tenant_id, run_id, plan.id, nid, delta],
                            start_to_close_timeout=timedelta(seconds=10),
                        )

                        if fb_node.get("requires_approval"):
                            # Wait for HITL approval of fallback
                            while fb_node["id"] not in self._approved:
                                await workflow.sleep(0.2)

                        try:
                            await workflow.execute_activity(
                                "run_node_activity",
                                args=[tenant_id, run_id, plan.id, fb_node],
                                start_to_close_timeout=timedelta(minutes=30),
                                heartbeat_timeout=timedelta(seconds=30),
                                retry_policy=RetryPolicy(
                                    initial_interval=timedelta(seconds=1),
                                    backoff_coefficient=2.0,
                                    maximum_attempts=2,
                                ),
                            )
                            done.add(nid)
                            continue
                        except Exception as e2:
                            await workflow.execute_activity(
                                "emit_plan_failed",
                                args=[tenant_id, run_id, plan.id, nid],
                                start_to_close_timeout=timedelta(seconds=10),
                            )
                            raise e2
                    else:
                        # No fallback – fail the plan
                        await workflow.execute_activity(
                            "emit_plan_failed",
                            args=[tenant_id, run_id, plan.id, nid],
                            start_to_close_timeout=timedelta(seconds=10),
                        )
                        raise res
                else:
                    done.add(nid)

        # All nodes done
        await workflow.execute_activity(
            "emit_plan_completed",
            args=[tenant_id, run_id, plan.id],
            start_to_close_timeout=timedelta(seconds=10),
        )
        return "completed"
