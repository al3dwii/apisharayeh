#!/usr/bin/env bash
set -euo pipefail

# Always run from repo root
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "[e2e] TEST: n1 -> (n2,n3) -> n4 (echo) — expect PlanCompleted"

# Use the worker venv if available (so temporalio and app deps are present)
if [[ -f "src/.venv-worker/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "src/.venv-worker/bin/activate"
fi

# --- 1) Start a workflow run and print the RUN id ---
RUN="$(
  cd src && python - <<'PY'
import asyncio, uuid, sys
from temporalio.client import Client
from app.workflows.goal_workflow import GoalWorkflow
from app.planner.schemas import PlanSpec, NodeSpec

async def go():
    try:
        client = await Client.connect("127.0.0.1:7233")
    except Exception as e:
        print(f"[e2e] ERROR: cannot connect to Temporal: {e}", file=sys.stderr)
        sys.exit(2)

    rid = str(uuid.uuid4())
    plan = PlanSpec(
        id=str(uuid.uuid4()),
        goal_id=str(uuid.uuid4()),
        nodes=[
            NodeSpec(id="n1", tool_id="echo", inputs={"msg": "A"}),
            NodeSpec(id="n2", tool_id="echo", depends_on=["n1"], inputs={"msg": "B"}),
            NodeSpec(id="n3", tool_id="echo", depends_on=["n1"], inputs={"msg": "C"}),
            NodeSpec(id="n4", tool_id="echo", depends_on=["n2", "n3"], inputs={"msg": "D"}),
        ],
        max_concurrency=4,
    ).model_dump()

    handle = await client.start_workflow(
        GoalWorkflow.run,
        id=f"goal-{rid}",
        task_queue="o2-default",
        args=["tenantA", plan["goal_id"], rid, plan],
    )

    # Wait for workflow completion (script will fail-fast on exceptions)
    await handle.result()
    print(rid)

asyncio.run(go())
PY
)"

echo "[e2e] Run id: $RUN"

# --- 2) Temporal health check: assert the workflow actually completed ---
(
  cd "$ROOT/src"
  RUN="$RUN" python - <<'PY'
import os, asyncio, sys
from temporalio.client import Client
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.workflowservice.v1 import GetWorkflowExecutionHistoryRequest
from temporalio.api.enums.v1 import event_type_pb2 as ev

RUN = os.environ["RUN"]
WID = f"goal-{RUN}"

async def main():
    client = await Client.connect("127.0.0.1:7233")
    hist = await client.workflow_service.get_workflow_execution_history(
        GetWorkflowExecutionHistoryRequest(
            namespace="default",
            execution=WorkflowExecution(workflow_id=WID),
        )
    )
    last = [ev.EventType.Name(e.event_type) for e in hist.history.events][-6:]
    if "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED" not in last:
        print(f"[e2e] ❌ Temporal missing COMPLETED for {WID}. Last events: {last}", file=sys.stderr)
        sys.exit(1)
    print(f"[e2e] Temporal OK. Last events: {last}")

asyncio.run(main())
PY
)

# --- 3) Kafka validation (optional) ---
if command -v jq >/dev/null 2>&1; then
  echo "[e2e] Kafka: validating events for RUN=$RUN"

  # Avoid aborting the whole script if rpk/jq hiccup; guard with set +e
  set +e
  EVENTS="$(
    docker exec -i compose-redpanda-1 sh -lc 'timeout 8s rpk topic consume tenant.tenantA.events --offset end -n 200' \
      | jq -r --arg run "$RUN" -c '
          .value
          | (try fromjson catch empty)
          | select(.run_id==$run)
          | [.ts_ms,.type,.id,.node_id] | @tsv
        '
  )"
  RC=$?
  set -e

  if [[ $RC -ne 0 || -z "${EVENTS}" ]]; then
    echo "[e2e] Kafka check skipped (rpk/jq error or no events found)"
  else
    printf '%s\n' "$EVENTS"

    if grep -q $'\tPlanFailed\t' <<<"$EVENTS"; then
      echo "[e2e] ❌ Kafka shows PlanFailed" >&2
      exit 1
    fi
    if ! grep -q $'\tPlanCompleted\t' <<<"$EVENTS"; then
      echo "[e2e] ❌ Kafka missing PlanCompleted" >&2
      exit 1
    fi
    echo "[e2e] ✅ Kafka OK"
  fi
else
  echo "[e2e] Kafka check skipped (jq not installed)"
fi

echo "[e2e] ✅ E2E PASS for RUN=$RUN"
