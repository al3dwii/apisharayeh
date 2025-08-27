#!/usr/bin/env bash
# Restart stack for local milestones WITHOUT changing any code/files.
# - kills old api/worker
# - (optional) docker compose up db/redis
# - (optional) run migrations
# - starts API + worker using existing project
# - verifies health, optionally ensures services exist (no file writes)

set -Eeuo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CY=$'\033[36m'; OK=$'\033[32m'; ER=$'\033[31m'; NC=$'\033[0m'
log(){ printf "${CY}%s${NC}\n" "$*" >&2; }
ok(){  printf "${OK}%s${NC}\n" "$*" >&2; }
die(){ printf "${ER}ERROR:${NC} %s\n" "$*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null || die "Missing dependency: $1"; }

# ─────────────────────────── config knobs ───────────────────────────
# you can override via env, e.g. PORT=8081 NO_DOCKER=1 bash scripts/restart_only.sh
PORT="${PORT:-8081}"
HOST="${HOST:-localhost}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://${HOST}:${PORT}}"

NO_DOCKER="${NO_DOCKER:-0}"     # 1 = skip docker compose for db/redis
NO_MIGRATE="${NO_MIGRATE:-0}"   # 1 = skip alembic migrations
NO_SYNC="${NO_SYNC:-0}"         # 1 = skip plugin sync/verification step
KILL_PORT_INUSE="${KILL_PORT_INUSE:-1}"  # 1 = free the API port if busy

# Prefer project helper scripts if present (no file changes!)
DEV_API_SCRIPT=""
[[ -x scripts/dev-api.sh     ]] && DEV_API_SCRIPT="scripts/dev-api.sh"
[[ -z "$DEV_API_SCRIPT" && -x scripts/dev_api.sh ]] && DEV_API_SCRIPT="scripts/dev_api.sh"

DEV_WORKER_SCRIPT=""
[[ -x scripts/dev-worker.sh     ]] && DEV_WORKER_SCRIPT="scripts/dev-worker.sh"
[[ -z "$DEV_WORKER_SCRIPT" && -x scripts/dev_worker.sh ]] && DEV_WORKER_SCRIPT="scripts/dev_worker.sh"

# Dependencies (no writing)
need curl; need jq; need python3

# ─────────────────────────── load .env (read-only) ───────────────────────────
if [[ -f .env ]]; then
  set -a; # export everything sourced
  # shellcheck disable=SC1091
  source .env || true
  set +a
fi

# ─────────────────────────── stop old processes ───────────────────────────────
log "[reset] stopping existing api/worker (if any) ..."
pkill -f "uvicorn .*app\.main:app" >/dev/null 2>&1 || true
pkill -f "python .*uvicorn .*app\.main:app" >/dev/null 2>&1 || true
pkill -f "celery .*app\.workers\.celery_app" >/dev/null 2>&1 || true
pkill -f "celery -A app\.workers\.celery_app" >/dev/null 2>&1 || true

if [[ "$KILL_PORT_INUSE" == "1" ]]; then
  if command -v lsof >/dev/null 2>&1; then
    if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      log "[reset] port ${PORT} busy; killing listeners..."
      lsof -tiTCP:"$PORT" -sTCP:LISTEN | xargs -r kill -9 || true
    fi
  fi
fi

# ─────────────────────────── infra: db/redis (optional) ───────────────────────
if [[ "$NO_DOCKER" != "1" ]] && command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  log "[infra] docker compose up -d db redis (no volume wipe)…"
  docker compose up -d db redis
  # wait for pg to be healthy if healthcheck is defined
  DB_ID="$(docker compose ps -q db || true)"
  if [[ -n "$DB_ID" ]]; then
    log "[infra] waiting for Postgres healthcheck ..."
    TIMEOUT=120; START="$(date +%s)"
    while :; do
      STATUS="$(docker inspect -f '{{.State.Health.Status}}' "$DB_ID" 2>/dev/null || echo starting)"
      [[ "$STATUS" == "healthy" ]] && { ok "[infra] db is healthy."; break; }
      (( $(date +%s) - START > TIMEOUT )) && { docker compose logs --no-log-prefix db | tail -n 200 || true; die "db not healthy in ${TIMEOUT}s"; }
      sleep 2
    done
  fi
else
  log "[infra] skipping docker (NO_DOCKER=1 or docker not available)."
fi

# ─────────────────────────── migrate (optional, no code changes) ──────────────
if [[ "$NO_MIGRATE" != "1" && -x ./scripts/dev-migrate.sh ]]; then
  log "[migrate] alembic upgrade head …"
  ./scripts/dev-migrate.sh
else
  log "[migrate] skipped (NO_MIGRATE=1 or script missing)."
fi

# ─────────────────────────── start API + worker ───────────────────────────────
mkdir -p logs
API_LOG="logs/api.out"
WORKER_LOG="logs/worker.out"
: > "$API_LOG"; : > "$WORKER_LOG"

start_api_fallback(){
  export PYTHONPATH="${ROOT}/src"
  # no reload to avoid double-process
  ( python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --loop asyncio --http h11 --log-level info >"$API_LOG" 2>&1 ) &
  echo $!
}
start_worker_fallback(){
  export PYTHONPATH="${ROOT}/src"
  # Celery will use REDIS_URL / REDIS_URL_QUEUE from .env if present
  ( python -m celery -A app.workers.celery_app:celery_app worker -l info --pool=solo >"$WORKER_LOG" 2>&1 ) &
  echo $!
}

log "[run] starting API …"
if [[ -n "$DEV_API_SCRIPT" ]]; then
  ( API_RELOAD=0 "$DEV_API_SCRIPT" "$PORT" >"$API_LOG" 2>&1 ) & API_PID=$!
else
  API_PID="$(start_api_fallback)"
fi
log "[run] starting Worker …"
if [[ -n "$DEV_WORKER_SCRIPT" ]]; then
  ( "$DEV_WORKER_SCRIPT" >"$WORKER_LOG" 2>&1 ) & WORKER_PID=$!
else
  WORKER_PID="$(start_worker_fallback)"
fi

# ─────────────────────────── wait for health ──────────────────────────────────
log "[wait] waiting for API on :$PORT …"
API_OK=0
for _ in $(seq 1 60); do
  curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1 && { API_OK=1; break; }
  sleep 1
done
[[ $API_OK -eq 1 ]] || { echo; tail -n 150 "$API_LOG" || true; die "API did not become healthy"; }
ok "[ok] API is up → http://localhost:${PORT}"

# ─────────────────────────── verify services (no file edits) ──────────────────
if [[ "$NO_SYNC" != "1" ]]; then
  if curl -fsS "http://localhost:${PORT}/v1/services" >/dev/null 2>&1; then
    SERVICES="$(curl -fsS "http://localhost:${PORT}/v1/services" | jq -r '.services[].id' || true)"
    # if slides.generate missing but helper exists, try syncing DB (reads manifests as-is)
    if ! echo "$SERVICES" | grep -q '^slides\.generate$'; then
      if [[ -x ./scripts/dev-enable-demo-tenant.sh ]]; then
        log "[plugins] slides.generate not found; calling dev-enable-demo-tenant.sh (DB-only)…"
        ./scripts/dev-enable-demo-tenant.sh >/dev/null 2>&1 || true
        SERVICES="$(curl -fsS "http://localhost:${PORT}/v1/services" | jq -r '.services[].id' || true)"
      else
        log "[plugins] slides.generate not found and no sync helper present (skipping)."
      fi
    fi
    printf "services:\n%s\n" "$SERVICES" | sed 's/^/  - /'
  else
    log "[plugins] services endpoint not available (skipping)."
  fi
else
  log "[plugins] verification skipped (NO_SYNC=1)."
fi

# ─────────────────────────── summary ──────────────────────────────────────────
echo
ok "Stack is running."
echo "API:     ${PUBLIC_BASE_URL}/health"
echo "OpenAPI: ${PUBLIC_BASE_URL}/openapi.json"
echo "Logs:    $API_LOG | $WORKER_LOG"
echo
echo "Tip: run your milestone tests now, e.g.:"
echo "  bash scripts/test_m3_m4.sh"
echo "  bash scripts/test_m5.sh"
echo "  bash scripts/test_m6.sh"

# NOTE: We **do not** trap/kill; processes stay up after this script exits.
exit 0
