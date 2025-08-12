#!/usr/bin/env bash
set -euo pipefail

### CONFIG (override with env if you want)
API_PORT="${API_PORT:-8080}"
PG_PORT="${PG_PORT:-5432}"
REDIS_PORT="${REDIS_PORT:-6379}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-agentic}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://localhost:${API_PORT}}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-/tmp/agentic-artifacts}"
DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${PG_PORT}/${POSTGRES_DB}}"
REDIS_URL="${REDIS_URL:-redis://localhost:${REDIS_PORT}/0}"
REDIS_URL_QUEUE="${REDIS_URL_QUEUE:-redis://localhost:${REDIS_PORT}/1}"
SAMPLE_PDF_URL="${SAMPLE_PDF_URL:-https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf}"
SYNC_AGENT_TIMEOUT_S="${SYNC_AGENT_TIMEOUT_S:-60}"

### COLORS
BOLD="$(tput bold || true)"; RESET="$(tput sgr0 || true)"; GREEN="$(tput setaf 2 || true)"; YELLOW="$(tput setaf 3 || true)"; RED="$(tput setaf 1 || true)"

log(){ echo "${BOLD}${GREEN}▶${RESET} $*"; }
warn(){ echo "${BOLD}${YELLOW}⚠${RESET} $*"; }
err(){ echo "${BOLD}${RED}✖${RESET} $*" >&2; }

### PRECHECKS
command -v docker >/dev/null || { err "Docker is required"; exit 1; }
command -v python3 >/dev/null || { err "python3 is required"; exit 1; }
command -v pip3 >/dev/null || { err "pip3 is required"; exit 1; }
command -v curl >/dev/null || { err "curl is required"; exit 1; }
command -v uvicorn >/dev/null || { warn "uvicorn not found; installing…"; pip3 install "uvicorn[standard]" >/dev/null; }
command -v celery >/dev/null || { warn "celery not found; installing…"; pip3 install "celery" >/dev/null; }
test -f requirements.txt && { log "Installing Python deps…"; pip3 install -r requirements.txt >/dev/null; } || warn "No requirements.txt found; assuming deps installed."

command -v soffice >/dev/null || warn "LibreOffice (soffice) not found — pptx↔pdf/html conversions may fail (office pack other routes). pdf→pptx is fine."

mkdir -p "$ARTIFACTS_DIR" logs

### START REDIS & POSTGRES (ephemeral containers)
if ! docker ps --format '{{.Names}}' | grep -q '^agentic-redis$'; then
  log "Starting Redis @ ${REDIS_PORT}…"
  docker run -d --rm --name agentic-redis -p "${REDIS_PORT}:6379" redis:7-alpine >/dev/null
fi
if ! docker ps --format '{{.Names}}' | grep -q '^agentic-pg$'; then
  log "Starting Postgres @ ${PG_PORT}…"
  docker run -d --rm --name agentic-pg \
    -e POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" -e POSTGRES_USER="${POSTGRES_USER}" -e POSTGRES_DB="${POSTGRES_DB}" \
    -p "${PG_PORT}:5432" postgres:16-alpine >/dev/null
fi

### WAIT FOR SERVICES
log "Waiting for Postgres to be ready…"
for i in {1..60}; do
  if docker exec agentic-pg pg_isready -U "${POSTGRES_USER}" >/dev/null 2>&1; then break; fi
  sleep 1
  if [[ $i -eq 60 ]]; then err "Postgres did not become ready"; exit 1; fi
done

log "Waiting for Redis to be ready…"
for i in {1..30}; do
  if docker exec agentic-redis redis-cli ping | grep -q PONG; then break; fi
  sleep 1
  if [[ $i -eq 30 ]]; then err "Redis did not respond"; exit 1; fi
done

### RUN ALEMBIC (if available)
if test -f db/alembic.ini; then
  log "Running Alembic migrations…"
  ( export DATABASE_URL="${DATABASE_URL}"; python3 -m alembic -c db/alembic.ini upgrade head ) || warn "Alembic failed; ensure migrations exist. Continuing…"
else
  warn "db/alembic.ini not found. Skipping migrations (RLS may be missing)."
fi

### START API + WORKER (background)
export DATABASE_URL REDIS_URL REDIS_URL_QUEUE ARTIFACTS_DIR PUBLIC_BASE_URL SYNC_AGENT_TIMEOUT_S
log "Starting API on :${API_PORT}…"
( uvicorn app.main:app --host 0.0.0.0 --port "${API_PORT}" > logs/api.log 2>&1 ) &
API_PID=$!

log "Starting Celery worker…"
( celery -A app.workers.celery_app worker --loglevel=INFO > logs/worker.log 2>&1 ) &
WORKER_PID=$!

cleanup() {
  warn "Cleaning up…"
  kill ${API_PID} ${WORKER_PID} >/dev/null 2>&1 || true
  docker stop agentic-redis >/dev/null 2>&1 || true
  docker stop agentic-pg >/dev/null 2>&1 || true
}
trap cleanup EXIT

### WAIT FOR API
log "Waiting for API /health…"
for i in {1..60}; do
  if curl -sf "http://localhost:${API_PORT}/health" >/dev/null; then break; fi
  sleep 1
  if [[ $i -eq 60 ]]; then err "API did not come up. Check logs/api.log"; exit 1; fi
done

### KICK A JOB (office.pdf_to_pptx)
log "Submitting test job (office.pdf_to_pptx)…"
JOB_JSON=$(curl -sf -X POST "http://localhost:${API_PORT}/v1/jobs" \
  -H "Content-Type: application/json" \
  -d "{\"pack\":\"office\",\"agent\":\"pdf_to_pptx\",\"inputs\":{\"file_url\":\"${SAMPLE_PDF_URL}\",\"max_slides\":5,\"title\":\"Smoke Test\"}}")

# parse job id using python (avoid jq dependency)
JOB_ID=$(python3 - <<PY
import json,sys
j=json.loads("""$JOB_JSON""")
print(j.get("id") or j.get("job_id") or "")
PY
)

if [[ -z "$JOB_ID" ]]; then
  err "Could not parse job id from response: $JOB_JSON"
  exit 1
fi
log "Job id: ${JOB_ID}"
echo "  GET http://localhost:${API_PORT}/v1/jobs/${JOB_ID}"

### POLL UNTIL DONE
ATTEMPTS=120 # ~2 min
SLEEP=1
STATUS=""
OUT_JSON=""
for i in $(seq 1 $ATTEMPTS); do
  RESP=$(curl -sf "http://localhost:${API_PORT}/v1/jobs/${JOB_ID}") || true
  STATUS=$(python3 - <<PY
import json,sys
try:
    j=json.loads("""$RESP""")
    print(j.get("status",""))
except Exception:
    print("")
PY
)
  if [[ "$STATUS" == "succeeded" || "$STATUS" == "failed" ]]; then
    OUT_JSON="$RESP"
    break
  fi
  sleep $SLEEP
done

if [[ "$STATUS" != "succeeded" ]]; then
  err "Job status: ${STATUS}. Response: ${OUT_JSON:-$RESP}"
  echo "---- API LOGS ----"; tail -n 200 logs/api.log || true
  echo "---- WORKER LOGS ----"; tail -n 200 logs/worker.log || true
  exit 1
fi

### PRINT RESULT URL
PPTX_URL=$(python3 - <<PY
import json,sys
j=json.loads("""$OUT_JSON""")
out=j.get("output") or {}
res=(out.get("result") if isinstance(out,dict) else {}) or {}
print(res.get("pptx_url",""))
PY
)

log "Success!"
echo "  PPTX URL: ${PPTX_URL:-<missing>}"
echo
echo "Open: ${PUBLIC_BASE_URL}/v1/jobs/${JOB_ID}"
echo "Artifacts dir: ${ARTIFACTS_DIR}"
