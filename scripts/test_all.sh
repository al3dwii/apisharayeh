#!/usr/bin/env bash
# scripts/test_all.sh
# End-to-end local test runner:
# - ensures Redis (on a custom local port)
# - runs Alembic migrations
# - starts API (uvicorn) and Celery worker
# - uploads sample media
# - creates a media.dub job and waits for completion
# - prints outputs / errors
# Safe defaults + dev bypass (no auth / tenant gating)

set -euo pipefail

# ---------- Config (override via env) ----------
ROOT="${ROOT:-$HOME/O2}"
SRC_DIR="${SRC_DIR:-$ROOT/src}"

PLUGINS_DIR="${PLUGINS_DIR:-$ROOT/plugins}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$ROOT/artifacts}"
MODELS_CONFIG="${MODELS_CONFIG:-$ROOT/config/models.yaml}"

DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://o2:o2@127.0.0.1:55432/o2}"
DATABASE_URL_SYNC="${DATABASE_URL_SYNC:-postgresql+psycopg://o2:o2@127.0.0.1:55432/o2}"

REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT_LOCAL="${REDIS_PORT_LOCAL:-6380}"
REDIS_URL="${REDIS_URL:-redis://$REDIS_HOST:$REDIS_PORT_LOCAL/1}"
REDIS_URL_QUEUE="${REDIS_URL_QUEUE:-redis://$REDIS_HOST:$REDIS_PORT_LOCAL/2}"

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8081}"
API_BASE="${API_BASE:-http://$API_HOST:$API_PORT}"

TENANT="${TENANT:-demo}"
ENV_NAME="${ENV:-dev}"
DISABLE_TENANT_GATING="${DISABLE_TENANT_GATING:-1}"
DISABLE_RATE_LIMIT="${DISABLE_RATE_LIMIT:-1}"

SAMPLE_MP4="${SAMPLE_MP4:-$ROOT/tests/assets/sample_speech.mp4}"
VOICE="${VOICE:-en_US-amy-medium}"
TARGET_LANG="${TARGET_LANG:-en}"

# ---------- Helpers ----------
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: missing required command '$1'"; exit 1;
  }
}

wait_http_up() {
  local url="$1" timeout="${2:-60}" i=0
  until curl -fsS "$url" >/dev/null 2>&1; do
    i=$((i+1))
    if [ "$i" -ge "$timeout" ]; then
      echo "ERROR: timed out waiting for $url"; return 1
    fi
    sleep 1
  done
}

# ---------- Pre-flight ----------
need curl
need jq
need python
need uvicorn
need celery
need docker || true  # optional; only used if Redis not listening and docker present

mkdir -p "$PLUGINS_DIR" "$ARTIFACTS_DIR" "$ROOT/.logs"

# ---------- Redis ----------
if ! (echo > /dev/tcp/$REDIS_HOST/$REDIS_PORT_LOCAL) >/dev/null 2>&1; then
  log "Redis not listening on :$REDIS_PORT_LOCAL, attempting to run docker redis:7-alpine…"
  docker run -d --rm --name o2-redis-"$REDIS_PORT_LOCAL" -p "$REDIS_PORT_LOCAL:6379" redis:7-alpine >/dev/null 2>&1 || true
  # give it a moment
  sleep 1
  # wait up to 10s
  for _ in $(seq 1 10); do
    if (echo > /dev/tcp/$REDIS_HOST/$REDIS_PORT_LOCAL) >/dev/null 2>&1; then break; fi
    sleep 1
  done
fi

if (echo > /dev/tcp/$REDIS_HOST/$REDIS_PORT_LOCAL) >/dev/null 2>&1; then
  log "Redis listening on :$REDIS_PORT_LOCAL"
else
  echo "WARNING: Redis still not reachable at $REDIS_HOST:$REDIS_PORT_LOCAL (continuing; your env might manage it)."
fi

# ---------- Migrations ----------
log "Running Alembic migrations…"
ENV="$ENV_NAME" PYTHONPATH="$SRC_DIR" DATABASE_URL_SYNC="$DATABASE_URL_SYNC" \
  alembic -c "$SRC_DIR/db/alembic.ini" upgrade head

# ---------- Start API ----------
log "Starting API (uvicorn)…"
API_LOG="$ROOT/.logs/api.log"
ENV="$ENV_NAME" \
PLUGINS_DIR="$PLUGINS_DIR" \
ARTIFACTS_DIR="$ARTIFACTS_DIR" \
MODELS_CONFIG="$MODELS_CONFIG" \
DATABASE_URL="$DATABASE_URL" \
REDIS_URL="$REDIS_URL" \
REDIS_URL_QUEUE="$REDIS_URL_QUEUE" \
DISABLE_RATE_LIMIT="$DISABLE_RATE_LIMIT" \
DISABLE_TENANT_GATING="$DISABLE_TENANT_GATING" \
PYTHONPATH="$SRC_DIR" \
  uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" >"$API_LOG" 2>&1 &
API_PID=$!
log "API PID = $API_PID (logs: $API_LOG)"

# wait API readiness
wait_http_up "$API_BASE/v1/services" 60
log "API is ready at $API_BASE"

# ---------- Start Celery worker ----------
log "Starting Celery worker (queue=media)…"
WORKER_LOG="$ROOT/.logs/worker.log"
ENV="$ENV_NAME" \
PLUGINS_DIR="$PLUGINS_DIR" \
ARTIFACTS_DIR="$ARTIFACTS_DIR" \
MODELS_CONFIG="$MODELS_CONFIG" \
DATABASE_URL="$DATABASE_URL" \
REDIS_URL_QUEUE="$REDIS_URL_QUEUE" \
DISABLE_TENANT_GATING="$DISABLE_TENANT_GATING" \
PYTHONPATH="$SRC_DIR" \
  celery -A app.workers.celery_app -b "$REDIS_URL_QUEUE" worker -Q media -l INFO >"$WORKER_LOG" 2>&1 &
WORKER_PID=$!
log "Worker PID = $WORKER_PID (logs: $WORKER_LOG)"

cleanup() {
  log "Shutting down worker/API…"
  kill "$WORKER_PID" >/dev/null 2>&1 || true
  kill "$API_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ---------- List services ----------
log "Listing services…"
curl -fsS "$API_BASE/v1/services" | jq .

# ---------- Upload sample media ----------
if [ ! -f "$SAMPLE_MP4" ]; then
  echo "ERROR: sample media not found at $SAMPLE_MP4"
  exit 1
fi

log "Uploading sample media: $SAMPLE_MP4"
UPLOAD_JSON=$(curl -fsS -F "file=@$SAMPLE_MP4;type=video/mp4" -F "project_id=$TENANT" "$API_BASE/v1/uploads/media")
echo "$UPLOAD_JSON" | jq .
VIDEO_PATH="$(echo "$UPLOAD_JSON" | jq -r .path)"

# ---------- Create job ----------
log "Creating media.dub job…"
REQ=$(jq -n --arg v "$VIDEO_PATH" --arg proj "$TENANT" --arg tl "$TARGET_LANG" --arg voice "$VOICE" '
{
  service_id: "media.dub",
  project_id: $proj,
  inputs: {
    video: $v,
    target_lang: $tl,
    voice: $voice
  }
}')
JOB_JSON=$(curl -fsS -X POST "$API_BASE/v1/jobs" -H "Content-Type: application/json" -d "$REQ")
echo "$JOB_JSON" | jq .
JOB_ID="$(echo "$JOB_JSON" | jq -r .id)"
log "JOB_ID=$JOB_ID"

# ---------- Wait for completion ----------
log "Waiting for job to finish…"
STATUS_JSON="{}"
for _ in $(seq 1 180); do
  STATUS_JSON="$(curl -fsS "$API_BASE/v1/jobs/$JOB_ID")"
  STATUS="$(echo "$STATUS_JSON" | jq -r .status)"
  printf '.'
  if [ "$STATUS" = "succeeded" ] || [ "$STATUS" = "failed" ]; then
    echo
    break
  fi
  sleep 2
done
echo

log "Final job status:"
echo "$STATUS_JSON" | jq .

# ---------- Print artifact URLs (if any) ----------
OUT_DUBBED="$(echo "$STATUS_JSON" | jq -r '.output.dubbed // empty')"
OUT_SRT="$(echo "$STATUS_JSON" | jq -r '.output.srt // empty')"
if [ -n "$OUT_DUBBED" ]; then
  echo "Dubbed video:  $API_BASE$OUT_DUBBED"
fi
if [ -n "$OUT_SRT" ]; then
  echo "Subtitles SRT: $API_BASE$OUT_SRT"
fi

log "Done."
