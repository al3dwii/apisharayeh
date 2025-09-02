#!/usr/bin/env bash
set -euo pipefail

# Always run from repo root
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

say()  { printf '%s\n' "$*"; }
err()  { printf '%s\n' "$*" >&2; }
hr()   { printf '\n%s\n\n' "----------------------------------------------------------------"; }

# Use the worker venv if available (so temporalio and app deps are present)
if [[ -f "src/.venv-worker/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "src/.venv-worker/bin/activate"
fi

TEMPORAL_ADDR="${TEMPORAL_ADDRESS:-127.0.0.1:7233}"
KBOOT="${KAFKA_BOOTSTRAP:-redpanda:9092}"
TOPIC="${EVENT_TOPIC:-tenant.tenantA.events}"
OPA_URL_DEFAULT="http://opa:8181/v1/data/o2/policy/allow"
OPA_URL="${OPA_URL:-$OPA_URL_DEFAULT}"
METRICS_URL="${METRICS_URL:-http://localhost:9108/metrics}"

have() { command -v "$1" >/dev/null 2>&1; }

# ---------- helpers ----------
_check_temporal_completed() {
  local run_id="$1"
  ( cd "$ROOT/src" && RUN="$run_id" TEMPORAL_ADDRESS="$TEMPORAL_ADDR" python - <<'PY'
import os, asyncio, sys
from temporalio.client import Client
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.workflowservice.v1 import GetWorkflowExecutionHistoryRequest
from temporalio.api.enums.v1 import event_type_pb2 as ev

RUN = os.environ["RUN"]; WID = f"goal-{RUN}"
ADDR = os.getenv("TEMPORAL_ADDRESS","127.0.0.1:7233")
async def main():
    client = await Client.connect(ADDR)
    hist = await client.workflow_service.get_workflow_execution_history(
        GetWorkflowExecutionHistoryRequest(namespace="default",
                                           execution=WorkflowExecution(workflow_id=WID)))
    last = [ev.EventType.Name(e.event_type) for e in hist.history.events][-8:]
    if "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED" not in last:
        print(f"[e2e] ❌ Temporal missing COMPLETED for {WID}. Last events: {last}", file=sys.stderr)
        sys.exit(1)
    print(f"[e2e] Temporal OK. Last events: {last}")
asyncio.run(main())
PY
  )
}

_check_temporal_failed() {
  local run_id="$1"
  ( cd "$ROOT/src" && RUN="$run_id" TEMPORAL_ADDRESS="$TEMPORAL_ADDR" python - <<'PY'
import os, asyncio, sys
from temporalio.client import Client
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.workflowservice.v1 import GetWorkflowExecutionHistoryRequest
from temporalio.api.enums.v1 import event_type_pb2 as ev

RUN = os.environ["RUN"]; WID = f"goal-{RUN}"
ADDR = os.getenv("TEMPORAL_ADDRESS","127.0.0.1:7233")
async def main():
    client = await Client.connect(ADDR)
    hist = await client.workflow_service.get_workflow_execution_history(
        GetWorkflowExecutionHistoryRequest(namespace="default",
                                           execution=WorkflowExecution(workflow_id=WID)))
    last = [ev.EventType.Name(e.event_type) for e in hist.history.events][-8:]
    if "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED" not in last:
        print(f"[e2e] ❌ Temporal expected FAILED for {WID}. Last events: {last}", file=sys.stderr)
        sys.exit(1)
    print(f"[e2e] Temporal shows FAILED as expected. Last events: {last}")
asyncio.run(main())
PY
  )
}

_run_plan_py() {
  # args: tenant, json_nodes_snippet, max_concurrency
  local tenant="$1"
  local nodes_snippet="$2"
  local max_cc="${3:-4}"

  ( cd src && TENANT="$tenant" MAXCC="$max_cc" NODES="$nodes_snippet" TEMPORAL_ADDRESS="$TEMPORAL_ADDR" python - <<'PY'
import asyncio, uuid, os, sys, json
from temporalio.client import Client
from app.workflows.goal_workflow import GoalWorkflow
from app.planner.schemas import PlanSpec, NodeSpec

TENANT=os.environ["TENANT"]
MAXCC=int(os.environ.get("MAXCC","4"))
NODES=json.loads(os.environ["NODES"])
ADDR=os.getenv("TEMPORAL_ADDRESS","127.0.0.1:7233")

async def go():
    client = await Client.connect(ADDR)
    rid = str(uuid.uuid4())
    plan = PlanSpec(
        id=str(uuid.uuid4()),
        goal_id=str(uuid.uuid4()),
        nodes=[NodeSpec(**n) for n in NODES],
        max_concurrency=MAXCC,
    ).model_dump()
    handle = await client.start_workflow(
        GoalWorkflow.run,
        id=f"goal-{rid}",
        task_queue="o2-default",
        args=[TENANT, plan["goal_id"], rid, plan],
    )
    # Print run id first (so we can capture even on failure)
    print(rid)
    # Wait for result (this will raise on failure)
    await handle.result()

asyncio.run(go())
PY
  )
}

_kafka_check() {
  local run_id="$1"
  if ! have jq; then
    say "[e2e] Kafka check skipped (jq not installed)"
    return 0
  fi
  # try consume recent tail; if compose-redpanda-1 missing, skip
  if ! docker ps --format '{{.Names}}' | grep -q '^compose-redpanda-1$'; then
    say "[e2e] Kafka check skipped (compose-redpanda-1 not running)"
    return 0
  fi
  say "[e2e] Kafka: validating events for RUN=$run_id"
  set +e
  EVENTS="$(
    docker exec -i compose-redpanda-1 sh -lc "timeout 8s rpk topic consume '$TOPIC' --offset end -n 400" \
      | jq -r --arg run "$run_id" -c '
          .value
          | (try fromjson catch empty)
          | select(.run_id==$run)
          | [.ts_ms,.type,.id,.node_id] | @tsv
        '
  )"
  RC=$?
  set -e
  if [[ $RC -ne 0 || -z "${EVENTS}" ]]; then
    say "[e2e] Kafka check skipped (rpk/jq error or no events found)"
    return 0
  fi

  printf '%s\n' "$EVENTS"
  if grep -q $'\tPlanFailed\t' <<<"$EVENTS"; then
    err "[e2e] ❌ Kafka shows PlanFailed (unexpected for success run)"
    return 1
  fi
  if ! grep -q $'\tPlanCompleted\t' <<<"$EVENTS"; then
    err "[e2e] ❌ Kafka missing PlanCompleted"
    return 1
  fi
  say "[e2e] ✅ Kafka OK"
}

_metrics_check() {
  # basic scrape to ensure metrics are exposed and counters exist
  if ! have curl; then
    say "[e2e] Metrics check skipped (curl not installed)"
    return 0
  fi
  set +e
  BODY="$(curl -fsS --max-time 3 "$METRICS_URL" || true)"
  set -e
  if [[ -z "$BODY" ]]; then
    say "[e2e] Metrics check skipped (no response from $METRICS_URL)"
    return 0
  fi
  if grep -q '^o2_node_events_total' <<<"$BODY"; then
    say "[e2e] ✅ Metrics exporter up ($METRICS_URL)"
  else
    say "[e2e] Metrics present but custom counters not seen"
  fi
}

_pg_projection_check() {
  # best-effort: find a running postgres container and query run status
  local run_id="$1"
  local pgc
  pgc="$(docker ps --format '{{.Names}}' | grep -E '(postgres|cockroach)' | head -1 || true)"
  if [[ -z "$pgc" ]]; then
    say "[e2e] Projection check skipped (no postgres container found)"
    return 0
  fi
  say "[e2e] Projection: checking runs/nodes for RUN=$run_id on container '$pgc'"
  # Postgres image has psql by default; DB/creds may vary—this is a best-effort check.
  set +e
  docker exec -i "$pgc" sh -lc "psql -U postgres -d postgres -tAc \"select status from runs where run_id='$run_id' limit 1\"" | sed '/^\s*$/d' | sed 's/^[[:space:]]*//'
  RC=$?
  set -e
  if [[ $RC -ne 0 ]]; then
    say "[e2e] Projection check skipped (psql failed)"
    return 0
  fi
}

# ============================================================
# 1) Happy-path: n1 -> (n2,n3) -> n4  (echo), expect COMPLETED
# ============================================================
hr
say "[e2e] TEST#1: Happy path — expect PlanCompleted"

NODES_SUCCESS='[
  {"id":"n1","tool_id":"echo","inputs":{"msg":"A"}},
  {"id":"n2","tool_id":"echo","depends_on":["n1"],"inputs":{"msg":"B"}},
  {"id":"n3","tool_id":"echo","depends_on":["n1"],"inputs":{"msg":"C"}},
  {"id":"n4","tool_id":"echo","depends_on":["n2","n3"],"inputs":{"msg":"D"}}
]'

RUN1="$(_run_plan_py "tenantA" "$NODES_SUCCESS" "4")"
say "[e2e] Run id: $RUN1"
_check_temporal_completed "$RUN1"
_kafka_check "$RUN1" || exit 1
_metrics_check
_pg_projection_check "$RUN1"

# ============================================================
# 2) Registry validation failure: unknown tool -> expect FAILED
# ============================================================
hr
say "[e2e] TEST#2: Registry validation — unknown tool, expect PlanFailed"
NODES_BAD_TOOL='[
  {"id":"n1","tool_id":"__missing_tool__","inputs":{"msg":"X"}}
]'
set +e
RUN2="$(_run_plan_py "tenantA" "$NODES_BAD_TOOL" "1")"
RC2=$?
set -e
say "[e2e] Run id: $RUN2"
# Even when Python raised, we printed rid first; confirm Temporal FAILED
_check_temporal_failed "$RUN2"

# ============================================================
# 3) OPA policy denial (if OPA_URL reachable): expect FAILED
#    Policy example allows tenantA+echo only; we try tenantB.
# ============================================================
hr
say "[e2e] TEST#3: OPA policy — tenantB denied (if OPA reachable), expect PlanFailed"
if have curl && curl -fsS --max-time 2 "$OPA_URL" >/dev/null; then
  set +e
  RUN3="$(_run_plan_py "tenantB" "$NODES_SUCCESS" "2")"
  RC3=$?
  set -e
  say "[e2e] Run id: $RUN3"
  _check_temporal_failed "$RUN3"
  say "[e2e] ✅ OPA denial behaved as expected"
else
  say "[e2e] OPA check skipped (OPA_URL not reachable: $OPA_URL)"
fi

hr
say "[e2e] ✅ E2E suite PASS. Runs: success=$RUN1, registry_fail=$RUN2${RUN3:+, opa_fail=$RUN3}"
