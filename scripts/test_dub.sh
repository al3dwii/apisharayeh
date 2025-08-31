#!/usr/bin/env bash
set -euo pipefail

ROOT="$(pwd)"
FILE=${1:-"$ROOT/sample_speech.mp4"}   # pass a file or default to sample_speech.mp4
PORT=8081

echo "[1/5] Starting services (db, redis)…"
docker compose up -d db redis

echo "[2/5] Launching API + Worker…"
# Kill old ones if running
pkill -f "uvicorn app.main:app" || true
pkill -f "celery worker" || true

# Start API
mkdir -p "$ROOT/.logs"
(uvicorn app.main:app --host 127.0.0.1 --port $PORT >"$ROOT/.logs/api.log" 2>&1 &)
sleep 2
# Start worker
(BROKER_URL=redis://127.0.0.1:6379/0 scripts/dev-worker.sh >"$ROOT/.logs/worker.log" 2>&1 &)
sleep 3

echo "[3/5] Uploading file: $FILE"
UPLOAD=$(curl -s -F "file=@${FILE}" http://localhost:$PORT/v1/uploads/media)
echo "$UPLOAD" | jq
PROJECT=$(echo "$UPLOAD" | jq -r .project_id)
VIDEO_PATH=$(echo "$UPLOAD" | jq -r .path)

echo "[4/5] Submitting media.dub job…"
JOB=$(curl -s -X POST http://localhost:$PORT/v1/jobs \
  -H 'Content-Type: application/json' \
  --data-binary @- <<JSON | jq -r .id
{
  "service_id": "media.dub",
  "project_id": "$PROJECT",
  "inputs": {
    "video": "$VIDEO_PATH",
    "target_lang": "ar",
    "voice": "ar-SA-HamedNeural"
  }
}
JSON
)
echo "JOB=$JOB"

echo "[5/5] Streaming events…"
curl -N "http://localhost:$PORT/v1/jobs/$JOB/events"
