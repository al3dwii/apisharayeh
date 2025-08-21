#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- venv & deps -------------------------------------------------------------
if [[ ! -d ".venv" ]]; then
  python -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -r src/requirements.txt >/dev/null

# --- infra -------------------------------------------------------------------
docker compose up -d db redis

echo "[wait] checking db health via docker healthcheck..."
DB_ID="$(docker compose ps -q db)"
TIMEOUT=120
START="$(date +%s)"
while true; do
  STATUS="$(docker inspect -f '{{.State.Health.Status}}' "$DB_ID" 2>/dev/null || echo starting)"
  if [[ "$STATUS" == "healthy" ]]; then
    echo "[ok] db is healthy."
    break
  fi
  NOW="$(date +%s)"
  if (( NOW - START > TIMEOUT )); then
    echo "[fail] db did not become healthy within ${TIMEOUT}s. Last 200 log lines:"
    docker compose logs --no-log-prefix db | tail -n 200
    exit 1
  fi
  sleep 2
done

# --- migrate -----------------------------------------------------------------
./scripts/dev-migrate.sh

# --- start api & worker ------------------------------------------------------
mkdir -p logs artifacts
trap 'echo; echo "[stop] stopping bg processes"; kill 0 2>/dev/null || true' EXIT

PORT="${PORT:-8081}"
( ./scripts/dev-api.sh    >logs/api.out    2>&1 ) &
( ./scripts/dev-worker.sh >logs/worker.out 2>&1 ) &

# --- wait for API ------------------------------------------------------------
echo "[wait] API on :$PORT ..."
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1; then
    echo "[ok] API is up."
    break
  fi
  sleep 1
done

# --- enable plugins ----------------------------------------------------------
./scripts/dev-enable-demo-tenant.sh

# --- smoke test --------------------------------------------------------------
echo "[test] running smoke.sh..."
API_BASE="http://localhost:${PORT}" bash src/scripts/smoke.sh
echo "[ok] smoke test passed."
wait
