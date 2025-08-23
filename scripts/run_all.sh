#!/usr/bin/env bash
# scripts/run_all.sh
set -Eeuo pipefail
trap 'code=$?; echo "[FAIL] line $LINENO: $BASH_COMMAND (exit=$code)"; cleanup || true; exit $code' ERR

REQ_FILE="src/requirements.txt"
API_HOST="0.0.0.0"
API_PORT="8081"
API_URL="http://localhost:${API_PORT}"
WORKER_NAME="worker-$(date +%s)"
LOGS_DIR="logs"
UPLOADS_DIR="artifacts/uploads"
ALEMBIC_INI="src/db/alembic.ini"

mkdir -p "$LOGS_DIR" "$UPLOADS_DIR"

cleanup() {
  if [[ -n "${WORKER_PID:-}" ]] && ps -p "$WORKER_PID" >/dev/null 2>&1; then
    echo "[cleanup] Stopping worker (PID $WORKER_PID)..."
    kill "$WORKER_PID" || true
  fi
  if [[ -n "${API_PID:-}" ]] && ps -p "$API_PID" >/dev/null 2>&1; then
    echo "[cleanup] Stopping API (PID $API_PID)..."
    kill "$API_PID" || true
  fi
}

log() { echo "[$(date +%H:%M:%S)] $*"; }

log "Ensuring virtualenv + deps (${REQ_FILE})…"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip -q install --upgrade pip
pip -q install -r "$REQ_FILE"

log "Starting docker compose services (db, redis)…"
docker compose up -d db redis >/dev/null

log "Waiting for ports 5432 (postgres) and 6379 (redis)…"
# Quick wait loop (simple TCP check)
for host_port in "localhost:5432" "localhost:6379"; do
  h="${host_port%:*}"; p="${host_port#*:}"
  for i in {1..60}; do
    (exec 3<>/dev/tcp/$h/$p) >/dev/null 2>&1 && break || sleep 0.5
  done
done

log "Running alembic upgrade…"
PYTHONPATH=src alembic -c "$ALEMBIC_INI" upgrade head

log "Starting API on :${API_PORT} …"
# Kill anything already on the port
lsof -tiTCP:${API_PORT} -sTCP:LISTEN | xargs -r kill -9 || true
PYTHONPATH=src uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" > "$LOGS_DIR/api.out" 2>&1 &
API_PID=$!
echo "[info] API PID = $API_PID (logs: $LOGS_DIR/api.out)"

log "Waiting for API /health…"
until curl -sSf "${API_URL}/health" >/dev/null; do sleep 0.5; done
log "API is up."

log "Starting Celery worker as ${WORKER_NAME}…"
PYTHONPATH=src celery -A app.workers.celery_app:celery worker \
  -l info -I app.workers.job_reaper \
  -Q celery,jobs -n "${WORKER_NAME}" \
  > "$LOGS_DIR/worker.out" 2>&1 &
WORKER_PID=$!
echo "[info] Worker PID = $WORKER_PID (logs: $LOGS_DIR/worker.out)"

# --- Prepare a DOCX (file:// URL so it works with DEV_OFFLINE=true) ---
DOCX_PATH="${UPLOADS_DIR}/ai.docx"
if [[ ! -f "$DOCX_PATH" ]]; then
  log "Creating sample DOCX at ${DOCX_PATH}…"
  if command -v textutil >/dev/null 2>&1; then
    cat > "${UPLOADS_DIR}/ai.txt" <<'TXT'
مقدمة في الذكاء الاصطناعي

- تعريف الذكاء الاصطناعي
- لمحة تاريخية
- أهم الطرق: التعلم الآلي، الشبكات العصبية، التعلم العميق
- تطبيقات عملية
- التحديات والأخلاقيات
TXT
    textutil -convert docx -output "${DOCX_PATH}" "${UPLOADS_DIR}/ai.txt"
    rm -f "${UPLOADS_DIR}/ai.txt"
  else
    # Fallback via python-docx (must be in requirements)
    python - <<'PY'
from docx import Document
p = "artifacts/uploads/ai.docx"
doc = Document()
doc.add_heading('مقدمة في الذكاء الاصطناعي', 0)
doc.add_paragraph('تعريف الذكاء الاصطناعي')
doc.add_paragraph('لمحة تاريخية')
doc.add_paragraph('أهم الطرق: التعلم الآلي، الشبكات العصبية، التعلم العميق')
doc.add_paragraph('تطبيقات عملية')
doc.add_paragraph('التحديات والأخلاقيات')
doc.save(p)
print("Wrote", p)
PY
  fi
fi

DOCX_ABS="$(cd "$(dirname "$DOCX_PATH")" && pwd)/$(basename "$DOCX_PATH")"
DOCX_FILE_URL="file://${DOCX_ABS}"

log "Submitting slides.generate job (file:// docx)…"
JOB_ID=$(curl -sS -H 'Content-Type: application/json' -X POST "${API_URL}/v1/jobs" \
  -d "{\"service_id\":\"slides.generate\",\"inputs\":{\"source\":\"docx\",\"docx_url\":\"${DOCX_FILE_URL}\",\"language\":\"ar\",\"slides_count\":8,\"theme\":\"academic-ar\"}}" \
  | python -c 'import sys,json; print(json.load(sys.stdin)["id"])')
echo "[info] JOB_ID = ${JOB_ID}"

log "Enqueuing Celery execution for job…"
PYTHONPATH=src python - <<PY
from app.workers.celery_app import celery
print(celery.send_task('execute_service_job', args=['${JOB_ID}']).id)
PY



log "Watching job status until it finishes…"
while :; do
  JSON="$(curl -sS "${API_URL}/v1/jobs/${JOB_ID}" || true)"
  if [[ -z "$JSON" ]]; then
    echo "[watch] empty response"; sleep 2; continue
  fi

  STATUS=$(printf '%s' "$JSON" \
    | python -c 'import sys,json; d=json.load(sys.stdin); print(d.get("status","unknown"))' \
    2>/dev/null || echo "unknown")

  PDF=$(printf '%s' "$JSON" \
    | python -c 'import sys,json; d=json.load(sys.stdin); print((d.get("output") or {}).get("pdf_url") or "")' \
    2>/dev/null || echo "")

  echo "[watch] status=${STATUS} pdf=${PDF:-None}"

  if [[ "$STATUS" == "succeeded" || "$STATUS" == "failed" ]]; then
    echo "---- FINAL JOB JSON ----"
    printf '%s\n' "$JSON" | python -m json.tool
    break
  fi
  sleep 2
done

# Open the PDF (http:// or file://) on macOS if present
if [[ -n "$PDF" ]]; then
  command -v open >/dev/null 2>&1 && open "$PDF" || true
fi

log "Done."
