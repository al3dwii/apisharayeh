cat <<'SH' | bash
set -euo pipefail

# 0) Project root + optional venv
cd /Users/omair/O2
[ -f .venv/bin/activate ] && . .venv/bin/activate || true

# 1) Env + clean old processes
export ROOT="$PWD"
export PYTHONPATH="$ROOT/src"
export BROKER_URL="redis://127.0.0.1:6379/2"
export REDIS_URL_QUEUE="$BROKER_URL"
export CELERY_BROKER_URL="$BROKER_URL"
pkill -f "uvicorn app.main:app" || true
pkill -f 'celery' || true

# 2) Ensure Redis/DB are running
docker compose up -d redis db >/dev/null

# 3) Start API and wait until healthy
uvicorn app.main:app --host 127.0.0.1 --port 8081 >/dev/null 2>&1 &
for i in {1..30}; do
  curl -sf http://127.0.0.1:8081/v1/services >/dev/null && break || sleep 0.5
done
echo "Services:" && curl -s http://127.0.0.1:8081/v1/services | jq '.services[] | select(.id=="media.dub")'

# 4) Start Celery worker bound to Redis queues
BROKER_URL="$BROKER_URL" REDIS_URL_QUEUE="$BROKER_URL" scripts/dev-worker.sh >/dev/null 2>&1 &

# 5) Upload test asset and capture paths
FILE="/Users/omair/O2/tests/assets/sample_speech.mp4"
UPLOAD_JSON="$(curl -s -F "file=@${FILE}" http://127.0.0.1:8081/v1/uploads/media)"
echo "$UPLOAD_JSON" | jq
export PROJECT="$(echo "$UPLOAD_JSON" | jq -r .project_id)"
REL_PATH="$(echo "$UPLOAD_JSON" | jq -r .path)"

# 6) Use absolute path for the engine (ffmpeg reads local files)
VIDEO_ABS="$ROOT$REL_PATH"
if [ ! -f "$VIDEO_ABS" ]; then
  mkdir -p "$(dirname "$VIDEO_ABS")"
  cp "$FILE" "$VIDEO_ABS"
fi
echo "Using video: $VIDEO_ABS"
ls -lh "$VIDEO_ABS"

# 7) Submit job and stream events
JOB="$(
  jq -n \
    --arg prj "$PROJECT" \
    --arg vid "$VIDEO_ABS" \
    --arg lang "ar" \
    --arg voice "ar-SA-HamedNeural" \
    '{service_id:"media.dub", project_id:$prj, inputs:{video:$vid, target_lang:$lang, voice:$voice}}' \
  | curl -s -X POST http://127.0.0.1:8081/v1/jobs -H 'Content-Type: application/json' --data-binary @- \
  | jq -r .id
)"
echo "JOB=$JOB"
curl -N "http://127.0.0.1:8081/v1/jobs/$JOB/events" || true

# 8) Show final status and outputs
echo "Final:"
curl -s "http://127.0.0.1:8081/v1/jobs/$JOB" | jq '{status, output, error}'
SH


cat <<'SH' | bash
set -euo pipefail

# 0) Project root + optional venv
cd /Users/omair/O2
[ -f .venv/bin/activate ] && . .venv/bin/activate || true

# 1) Env + clean old processes
export ROOT="$PWD"
export PYTHONPATH="$ROOT/src"
export BROKER_URL="redis://127.0.0.1:6379/2"
export REDIS_URL_QUEUE="$BROKER_URL"
export CELERY_BROKER_URL="$BROKER_URL"
pkill -f "uvicorn app.main:app" || true
pkill -f 'celery' || true

# 2) Ensure Redis/DB are running
docker compose up -d redis db >/dev/null

# 3) Start API and wait until healthy
uvicorn app.main:app --host 127.0.0.1 --port 8081 >/dev/null 2>&1 &
for i in {1..30}; do
  curl -sf http://127.0.0.1:8081/v1/services >/dev/null && break || sleep 0.5
done
echo "Services:" && curl -s http://127.0.0.1:8081/v1/services | jq '.services[] | select(.id=="media.dub")'

# 4) Start Celery worker bound to Redis queues
BROKER_URL="$BROKER_URL" REDIS_URL_QUEUE="$BROKER_URL" scripts/dev-worker.sh >/dev/null 2>&1 &

# 5) Upload test asset and capture paths
FILE="/Users/omair/O2/tests/assets/sample2.mp4"
UPLOAD_JSON="$(curl -s -F "file=@${FILE}" http://127.0.0.1:8081/v1/uploads/media)"
echo "$UPLOAD_JSON" | jq
export PROJECT="$(echo "$UPLOAD_JSON" | jq -r .project_id)"
REL_PATH="$(echo "$UPLOAD_JSON" | jq -r .path)"

# 6) Use absolute path for the engine (ffmpeg reads local files)
VIDEO_ABS="$ROOT$REL_PATH"
if [ ! -f "$VIDEO_ABS" ]; then
  mkdir -p "$(dirname "$VIDEO_ABS")"
  cp "$FILE" "$VIDEO_ABS"
fi
echo "Using video: $VIDEO_ABS"
ls -lh "$VIDEO_ABS"

# 7) Submit job (French target, no voice) and stream events
JOB="$(
  jq -n \
    --arg prj "$PROJECT" \
    --arg vid "$VIDEO_ABS" \
    '{"service_id":"media.dub","project_id":$prj,"inputs":{"video":$vid,"target_lang":"fr"}}' \
  | curl -s -X POST http://127.0.0.1:8081/v1/jobs -H 'Content-Type: application/json' --data-binary @- \
  | jq -r .id
)"
echo "JOB=$JOB"
curl -N "http://127.0.0.1:8081/v1/jobs/$JOB/events" || true

# 8) Show final status and outputs
echo "Final:"
curl -s "http://127.0.0.1:8081/v1/jobs/$JOB" | jq '{status, output, error}'
SH
