#!/usr/bin/env bash
# One-button: reset -> deps -> infra -> migrate -> run -> enable slides -> smoke
set -Eeuo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CY=$'\033[36m'; OK=$'\033[32m'; ERR=$'\033[31m'; DIM=$'\033[2m'; NC=$'\033[0m'
log(){ printf "${CY}%s${NC}\n" "$*" >&2; }
ok(){  printf "${OK}%s${NC}\n" "$*" >&2; }
die(){ printf "${ERR}ERROR:${NC} %s\n" "$*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null || die "Missing dependency: $1"; }

need docker; need curl; need jq; need unzip; need python3; need lsof

log "[reset] killing old local api/worker (if any) ..."
pkill -f "uvicorn .*app\.main:app" >/dev/null 2>&1 || true
pkill -f "python .*uvicorn .*app\.main:app" >/dev/null 2>&1 || true
pkill -f "celery .*app\.workers\.celery_app" >/dev/null 2>&1 || true
pkill -f "celery -A app\.workers\.celery_app" >/dev/null 2>&1 || true

# Also kill anything holding the API port
PORT="${PORT:-8081}"
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  log "[reset] port ${PORT} is busy, killing listeners..."
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
  lsof -tiTCP:"$PORT" -sTCP:LISTEN | xargs -r kill -9 || true
fi

log "[reset] docker compose down -v (db/redis) ..."
docker compose down -v --remove-orphans >/dev/null || true

rm -rf logs artifacts
mkdir -p logs artifacts

log "[venv] creating/upgrading virtualenv & installing deps ..."
if [[ ! -d ".venv" ]]; then python3 -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip -q install --upgrade pip
pip -q install -r src/requirements.txt
python -c "import psycopg2" 2>/dev/null || pip -q install psycopg2-binary
python -c "import asyncpg"  2>/dev/null || pip -q install asyncpg

# --- write a clean .env block to avoid bad DSNs --------------------------------
log "[env] ensuring .env has correct connection strings ..."
TMP_ENV="$(mktemp)"
{
  if [[ -f .env ]]; then
    grep -Ev '^(DATABASE_URL|DATABASE_URL_SYNC|REDIS_URL|REDIS_URL_QUEUE|PUBLIC_BASE_URL|PLUGINS_DIR|ARTIFACTS_DIR|SERVICE_FLAGS|SOFFICE_BIN)=' .env || true
  fi
  cat <<'ENVBLK'
DATABASE_URL=postgresql+asyncpg://agentic:agentic@localhost:5432/agentic
DATABASE_URL_SYNC=postgresql+psycopg2://agentic:agentic@localhost:5432/agentic
REDIS_URL=redis://localhost:6379/1
REDIS_URL_QUEUE=redis://localhost:6379/2
PUBLIC_BASE_URL=http://localhost:8081
PLUGINS_DIR=./plugins
ARTIFACTS_DIR=./artifacts
SERVICE_FLAGS=*
SOFFICE_BIN=/Applications/LibreOffice.app/Contents/MacOS/soffice
ENVBLK
} > "$TMP_ENV"
mv "$TMP_ENV" .env

log "[env] .env summary:"
grep -E '^(DATABASE_URL|DATABASE_URL_SYNC|REDIS_URL|REDIS_URL_QUEUE|PUBLIC_BASE_URL|PLUGINS_DIR|ARTIFACTS_DIR|SERVICE_FLAGS|SOFFICE_BIN)=' .env | sed "s/^/  /"

# --- infra -------------------------------------------------------------------
log "[infra] starting db + redis ..."
docker compose up -d db redis

log "[infra] waiting for Postgres healthcheck ..."
DB_ID="$(docker compose ps -q db)"
TIMEOUT=120; START="$(date +%s)"
while true; do
  STATUS="$(docker inspect -f '{{.State.Health.Status}}' "$DB_ID" 2>/dev/null || echo starting)"
  [[ "$STATUS" == "healthy" ]] && { ok "[infra] db is healthy."; break; }
  (( $(date +%s) - START > TIMEOUT )) && { docker compose logs --no-log-prefix db | tail -n 200; die "db not healthy in ${TIMEOUT}s"; }
  sleep 2
done

# --- migrate -----------------------------------------------------------------
log "[migrate] running alembic upgrade head ..."
./scripts/dev-migrate.sh

# --- start API & worker (local) ----------------------------------------------
log "[run] starting API & Worker in background ..."
# For smoke: run API WITHOUT reload to avoid reloader race on the same port
( API_RELOAD=0 KILL_PORT_INUSE=1 ./scripts/dev-api.sh    >logs/api.out    2>&1 ) & API_PID=$!
( ./scripts/dev-worker.sh                                   >logs/worker.out 2>&1 ) & WORKER_PID=$!

cleanup(){
  echo
  log "[stop] stopping background processes ..."
  kill "$API_PID" "$WORKER_PID" >/dev/null 2>&1 || true
  [[ -n "${SSE_PID:-}" ]] && kill "$SSE_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# tail API logs while waiting
log "[wait] waiting for API on :$PORT (tailing logs/api.out) ..."
( tail -n +1 -F logs/api.out & echo $! > logs/.tail_api_pid ) 2>/dev/null || true

API_OK=0
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1; then API_OK=1; break; fi
  sleep 1
done
if [[ -f logs/.tail_api_pid ]]; then
  kill "$(cat logs/.tail_api_pid)" >/dev/null 2>&1 || true
  rm -f logs/.tail_api_pid
fi

if [[ $API_OK -ne 1 ]]; then
  echo
  log "[fail] API did not become healthy within 60s. Last 200 log lines:"
  tail -n 200 logs/api.out || true
  # Port probe to explain "Address already in use"
  log "[diag] Port ${PORT} listeners:"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
  die "API failed to start"
fi
ok "[run] API is up."

# --- enable slides plugin ----------------------------------------------------
log "[plugins] syncing manifests for demo-tenant (slides only) ..."
./scripts/dev-enable-demo-tenant.sh

SERVICES="$(curl -fsS "http://localhost:${PORT}/v1/services" | jq -r '.services[].id' || true)"
echo "$SERVICES" | grep -q '^slides\.generate$' || { printf "Services:\n%s\n" "$SERVICES"; die "slides.generate not available"; }
ok "[plugins] slides.generate is enabled."

# --- smoke: slides.generate --------------------------------------------------
PROMPT="${PROMPT:-Quick intro to AI in education}"
SLIDES_COUNT="${SLIDES_COUNT:-6}"
log "[test] submitting slides.generate job (slides_count=${SLIDES_COUNT}) ..."

payload="$(jq -nc --arg p "$PROMPT" --argjson n "$SLIDES_COUNT" \
  '{service_id:"slides.generate", inputs:{prompt:$p, slides_count:$n}}')"

JOB_ID="$(curl -fsS -X POST "http://localhost:${PORT}/v1/jobs" -H 'Content-Type: application/json' --data-binary "$payload" | jq -r .id)"
[[ "$JOB_ID" =~ ^[0-9a-f-]{36}$ ]] || die "invalid job id: $JOB_ID"
log "JOB_ID: $JOB_ID"
printf "${DIM}  stream: curl -N \"http://localhost:%s/v1/jobs/%s/events\"${NC}\n" "$PORT" "$JOB_ID" >&2

EV_URL="http://localhost:${PORT}/v1/jobs/${JOB_ID}/events"
SSE_LOG="logs/sse-${JOB_ID}.log"
SSE_ERR="logs/sse-${JOB_ID}.err"
: > "$SSE_LOG"; : > "$SSE_ERR"

log "[test] waiting for artifact URL (max 120s) ... (SSE → $SSE_LOG)"
( curl -NsS --fail-with-body "$EV_URL" 2> "$SSE_ERR" | tee -a "$SSE_LOG" >/dev/null ) & SSE_PID=$!

ARTIFACT=""
for _ in $(seq 1 120); do
  if [[ -s "$SSE_LOG" ]]; then
    set +e
    ARTIFACT="$(
      sed -n 's/^data: //p' "$SSE_LOG" \
      | jq -r 'select(.event=="step") | .data | select(.name=="artifact.ready") | (.artifact // .pdf_url // empty)' \
      | head -n1
    )"
    set -e
    [[ -n "$ARTIFACT" ]] && break
  fi
  sleep 1
done

kill "${SSE_PID:-0}" >/dev/null 2>&1 || true

# Fallback to job polling if SSE didn’t yield
if [[ -z "$ARTIFACT" ]]; then
  log "[test] SSE didn’t yield — polling job for url ..."
  for _ in $(seq 1 30); do
    ARTIFACT="$(
      curl -fsS "http://localhost:${PORT}/v1/jobs/${JOB_ID}" \
      | jq -r '.output.pdf_url // .output.result.pdf_url // empty'
    )" || true
    [[ -n "$ARTIFACT" ]] && break
    sleep 1
  done
fi

if [[ -z "$ARTIFACT" ]]; then
  echo
  log "[fail] no artifact url found — deep diagnostics"
  echo "---- JOB JSON ----"
  curl -fsS "http://localhost:${PORT}/v1/jobs/${JOB_ID}" | jq . || true
  echo "---- LAST 120 WORKER LOG LINES ----"
  tail -n 120 logs/worker.out || true
  echo "---- LAST 120 API LOG LINES ----"
  tail -n 120 logs/api.out || true
  echo "---- SSE STDOUT (tail) ----"
  tail -n 80 "$SSE_LOG" || true
  echo "---- SSE STDERR (tail) ----"
  tail -n 80 "$SSE_ERR" || true
  echo "---- Port ${PORT} listeners ----"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
  echo "---- FS PROBE (artifacts) ----"
  find artifacts -maxdepth 4 -type f -print 2>/dev/null || true
  die "artifact url not found"
fi

# Normalize to absolute URL if needed
if [[ "${ARTIFACT}" != http* ]]; then
  ARTIFACT="http://localhost:${PORT}${ARTIFACT}"
fi
log "[test] artifact: ${ARTIFACT}"

# Download + quick size sanity check (stub PDFs are small; allow override)
MIN_PDF_BYTES="${MIN_PDF_BYTES:-256}"
OUT="artifacts/slides-${JOB_ID}.pdf"
mkdir -p artifacts
if ! curl -fsS -o "${OUT}" "${ARTIFACT}"; then
  echo "---- SSE STDOUT (tail) ----"; tail -n 80 "$SSE_LOG" || true
  echo "---- SSE STDERR (tail) ----"; tail -n 80 "$SSE_ERR" || true
  die "download failed"
fi
SZ=$(stat -f%z "${OUT}" 2>/dev/null || stat -c%s "${OUT}")
if [[ "${SZ}" -lt "${MIN_PDF_BYTES}" ]]; then
  echo "---- JOB JSON ----"; curl -fsS "http://localhost:${PORT}/v1/jobs/${JOB_ID}" | jq . || true
  die "artifact too small (${SZ} bytes < ${MIN_PDF_BYTES}) : ${OUT}"
fi
ok "[test] PDF verified (${SZ} bytes) → ${OUT}"

ok "[DONE] end-to-end OK."
wait
