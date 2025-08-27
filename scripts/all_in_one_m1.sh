#!/usr/bin/env bash
# Milestone-1: reset -> deps -> env -> infra -> migrate -> PATCH slides.generate -> run -> smoke
set -Eeuo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CY=$'\033[36m'; OK=$'\033[32m'; ER=$'\033[31m'; DIM=$'\033[2m'; NC=$'\033[0m'
log(){ printf "${CY}%s${NC}\n" "$*" >&2; }
ok(){  printf "${OK}%s${NC}\n" "$*" >&2; }
die(){ printf "${ER}ERROR:${NC} %s\n" "$*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null || die "Missing dependency: $1"; }

# ---------------- cfg ----------------
PORT="${PORT:-8081}"
HOST="${HOST:-localhost}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://${HOST}:${PORT}}"

DB_URL_ASYNC="${DATABASE_URL:-postgresql+asyncpg://agentic:agentic@localhost:5432/agentic}"
DB_URL_SYNC="${DATABASE_URL_SYNC:-postgresql+psycopg2://agentic:agentic@localhost:5432/agentic}"
REDIS_URL="${REDIS_URL:-redis://localhost:6379/1}"
REDIS_URL_QUEUE="${REDIS_URL_QUEUE:-redis://localhost:6379/2}"

PLUGINS_DIR_DEFAULT="${PLUGINS_DIR:-./plugins}"
ARTIFACTS_DIR_DEFAULT="${ARTIFACTS_DIR:-./artifacts}"
SOFFICE_BIN_DEFAULT="${SOFFICE_BIN:-/Applications/LibreOffice.app/Contents/MacOS/soffice}"

PROMPT="${PROMPT:-Quick intro to AI in education}"
SLIDES_COUNT="${SLIDES_COUNT:-6}"
LANG="${LANGUAGE:-en}"

DEV_API_SCRIPT=""
[[ -x scripts/dev-api.sh     ]] && DEV_API_SCRIPT="scripts/dev-api.sh"
[[ -z "$DEV_API_SCRIPT" && -x scripts/dev_api.sh ]] && DEV_API_SCRIPT="scripts/dev_api.sh"

DEV_WORKER_SCRIPT=""
[[ -x scripts/dev-worker.sh     ]] && DEV_WORKER_SCRIPT="scripts/dev-worker.sh"
[[ -z "$DEV_WORKER_SCRIPT" && -x scripts/dev_worker.sh ]] && DEV_WORKER_SCRIPT="scripts/dev_worker.sh"

# ---------------- deps ----------------
need curl; need jq; need python3; need lsof
command -v docker >/dev/null 2>&1 && need docker

# ---------------- reset ----------------
log "[reset] killing old local api/worker (if any) ..."
pkill -f "uvicorn .*app\.main:app" >/dev/null 2>&1 || true
pkill -f "python .*uvicorn .*app\.main:app" >/devnull 2>&1 || true
pkill -f "celery .*app\.workers\.celery_app" >/dev/null 2>&1 || true
pkill -f "celery -A app\.workers\.celery_app" >/dev/null 2>&1 || true

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  log "[reset] port ${PORT} is busy, killing listeners..."
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
  lsof -tiTCP:"$PORT" -sTCP:LISTEN | xargs -r kill -9 || true
fi

if command -v docker >/dev/null 2>&1; then
  log "[reset] docker compose down -v (db/redis) ..."
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
fi

rm -rf logs "$ARTIFACTS_DIR_DEFAULT"
mkdir -p logs "$ARTIFACTS_DIR_DEFAULT"

# ---------------- venv & deps ----------------
log "[venv] creating/upgrading virtualenv & installing deps ..."
if [[ ! -d ".venv" ]]; then python3 -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip -q install --upgrade pip wheel setuptools
if [[ -f src/requirements.txt ]]; then
  pip -q install -r src/requirements.txt
else
  pip -q install "fastapi>=0.115" "uvicorn>=0.30" "requests>=2.32.3"
fi
python - <<'PY' 2>/dev/null || pip -q install trafilatura readability-lxml
try:
  import trafilatura, readability  # noqa
except Exception:
  raise SystemExit(1)
PY
python -c "import psycopg2" 2>/dev/null || pip -q install psycopg2-binary
python -c "import asyncpg"  2>/dev/null || pip -q install asyncpg

# ---------------- .env ----------------
log "[env] writing a clean .env for local dev ..."
TMP_ENV="$(mktemp)"
{
  if [[ -f .env ]]; then
    grep -Ev '^(DATABASE_URL|DATABASE_URL_SYNC|REDIS_URL|REDIS_URL_QUEUE|PUBLIC_BASE_URL|PLUGINS_DIR|ARTIFACTS_DIR|SERVICE_FLAGS|SOFFICE_BIN)=' .env || true
  fi
  cat <<ENVBLK
DATABASE_URL=${DB_URL_ASYNC}
DATABASE_URL_SYNC=${DB_URL_SYNC}
REDIS_URL=${REDIS_URL}
REDIS_URL_QUEUE=${REDIS_URL_QUEUE}
PUBLIC_BASE_URL=${PUBLIC_BASE_URL}
PLUGINS_DIR=${PLUGINS_DIR_DEFAULT}
ARTIFACTS_DIR=${ARTIFACTS_DIR_DEFAULT}
SERVICE_FLAGS=*
SOFFICE_BIN=${SOFFICE_BIN_DEFAULT}
ENVBLK
} > "$TMP_ENV"
mv "$TMP_ENV" .env
log "[env] summary:"
grep -E '^(DATABASE_URL|DATABASE_URL_SYNC|REDIS_URL|REDIS_URL_QUEUE|PUBLIC_BASE_URL|PLUGINS_DIR|ARTIFACTS_DIR|SERVICE_FLAGS|SOFFICE_BIN)=' .env | sed "s/^/  /"

# ---------------- PATCH slides.generate (force overwrite) --------------------
SLIDES_DIR="${PLUGINS_DIR_DEFAULT}/slides.generate"
mkdir -p "$SLIDES_DIR"

log "[fix] writing safe ${SLIDES_DIR}/flow.yaml (no 'defined', offline-safe)"
cat > "${SLIDES_DIR}/flow.yaml" <<'YAML'
version: 1
vars:
  project_id: "{{ inputs.project_id | default(random_id('prj_')) }}"
  slides_count: "{{ inputs.slides_count | default(12) }}"
  topic_effective: "{{ inputs.topic or inputs.prompt or 'عرض تقديمي تجريبي' }}"
  queries: "{{ inputs.images_query_pack | default(['موضوع','أدوات','فوائد','تحديات']) }}"
steps:
  - id: source_branch
    run: switch
    when:
      "inputs.source == 'docx'":
        run: sequence
        do:
          - id: fetch_docx
            run: io.fetch
            in: { url: "{{ inputs.docx_url }}", to_dir: "input" }
            out: { path: "@docx_path" }
          - id: detect_type
            run: doc.detect_type
            in: { path: "@docx_path" }
            out: { kind: "@doc_kind" }
          - id: parse_docx
            run: doc.parse_docx
            in: { path: "@docx_path" }
            out: { text: "@doc_text" }
          - id: outline_from_doc
            run: slides.outline.from_doc
            in:
              text: "@doc_text"
              language: "{{ inputs.language | default('ar') }}"
              count: "{{ vars.slides_count }}"
            out: { outline: "@outline" }
      "inputs.source == 'prompt'":
        run: slides.outline.from_prompt_stub
        in:
          topic: "{{ vars.topic_effective }}"
          language: "{{ inputs.language | default('ar') }}"
          count: "{{ vars.slides_count }}"
        out: { outline: "@outline" }
      "true":
        run: slides.outline.from_prompt_stub
        in:
          topic: "{{ vars.topic_effective }}"
          language: "{{ inputs.language | default('ar') }}"
          count: "{{ vars.slides_count }}"
        out: { outline: "@outline" }

  - id: ensure_outline
    run: switch
    when:
      "{{ not @outline }}":
        run: slides.outline.from_prompt_stub
        in:
          topic: "{{ vars.topic_effective }}"
          language: "{{ inputs.language | default('ar') }}"
          count: "{{ vars.slides_count }}"
        out: { outline: "@outline" }
      "true":
        run: io.save_text
        in: { text: "ok", to_dir: "tmp", filename: "noop.txt" }
        out: { path: "@noop" }

  - id: get_images
    run: vision.images.from_fixtures
    needs: [ensure_outline]
    in:
      project_id: "{{ vars.project_id }}"
      queries: "{{ vars.queries }}"
    out: { images: "@images" }

  - id: render
    run: slides.html.render
    needs: [ensure_outline, get_images]
    in:
      project_id: "{{ vars.project_id }}"
      outline: "@outline"
      theme: "{{ inputs.theme | default('academic-ar') }}"
      images: "@images"
    out: { slides_html: "@slides_html" }

  - id: build_pptx
    run: slides.pptx.build
    needs: [render]
    in:
      project_id: "{{ vars.project_id }}"
      outline: "@outline"
      title: "{{ (@outline and @outline[0]['title']) or vars.topic_effective }}"
      language: "{{ inputs.language | default('ar') }}"
    out:
      pptx_url: "@pptx_url"
      pptx_path: "@pptx_path"

  - id: export_pdf
    run: slides.export.pdf_via_lo
    needs: [build_pptx]
    in:
      project_id: "{{ vars.project_id }}"
      pptx_path: "@pptx_path"
    out: { pdf_url: "@pdf_url" }

  - id: export_html
    run: slides.export.html_via_lo
    needs: [build_pptx]
    in:
      project_id: "{{ vars.project_id }}"
      pptx_path: "@pptx_path"
    out: { html_zip_url: "@html_zip_url" }

outputs:
  outline: "@outline"
  slides_html: "@slides_html"
  pdf_url: "@pdf_url"
  html_zip_url: "@html_zip_url"
  pptx_url: "@pptx_url"
YAML

log "[fix] bumping ${SLIDES_DIR}/manifest.yaml to 0.1.3"
cat > "${SLIDES_DIR}/manifest.yaml" <<'YAML'
id: slides.generate
name: "Slides: Generate"
version: "0.1.3"
summary: "Generate HTML slides, PPTX, and a PDF from a DOCX or a prompt."
runtime: dsl
entrypoint: flow.yaml
permissions: { fs_read: true, fs_write: true }
inputs:
  type: object
  properties:
    project_id: { type: string }
    source: { type: string, enum: ["docx","prompt"] }
    docx_url: { type: string }
    topic: { type: string }
    language: { type: string, default: "ar" }
    slides_count: { type: integer, minimum: 1, maximum: 50, default: 12 }
    theme: { type: string, default: "academic-ar" }
    images_query_pack:
      type: array
      items: { type: string }
  required: []
outputs:
  outline: { type: array, items: { type: string } }
  slides_html: { type: array, items: { type: string } }
  pdf_url: { type: string }
  html_zip_url: { type: string }
  pptx_url: { type: string }
events: { todos: [] }
YAML

if command -v shasum >/dev/null 2>&1; then
  log "[fix] flow.yaml checksum:"; shasum "${SLIDES_DIR}/flow.yaml" || true
fi

# ---------------- infra ----------------
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  log "[infra] starting db redis ..."
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
else
  log "[infra] docker compose not available; assuming local db/redis already running."
fi

# ---------------- migrate ----------------
if [[ -x ./scripts/dev-migrate.sh ]]; then
  log "[migrate] running alembic upgrade head ..."
  ./scripts/dev-migrate.sh
fi

# ---------------- run API & worker ----------------
log "[run] starting API & Worker in background ..."
API_LOG="logs/api.out"; WORKER_LOG="logs/worker.out"
: > "$API_LOG"; : > "$WORKER_LOG"

start_api_fallback(){
  export PYTHONPATH="${ROOT}/src"
  ( API_RELOAD=0 KILL_PORT_INUSE=1 python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --loop asyncio --http h11 --log-level info >"$API_LOG" 2>&1 ) &
  echo $!
}
start_worker_fallback(){
  export PYTHONPATH="${ROOT}/src"
  export CELERY_BROKER_URL="${REDIS_URL_QUEUE:-$REDIS_URL}"
  export CELERY_RESULT_BACKEND="${REDIS_URL_QUEUE:-$REDIS_URL}"
  ( python -m celery -A app.workers.celery_app:celery_app worker -l info --pool=solo >"$WORKER_LOG" 2>&1 ) &
  echo $!
}

if [[ -n "$DEV_API_SCRIPT" ]]; then
  ( API_RELOAD=0 KILL_PORT_INUSE=1 "$DEV_API_SCRIPT" "$PORT" >"$API_LOG" 2>&1 ) & API_PID=$!
else
  API_PID="$(start_api_fallback)"
fi

if [[ -n "$DEV_WORKER_SCRIPT" ]]; then
  ( "$DEV_WORKER_SCRIPT" >"$WORKER_LOG" 2>&1 ) & WORKER_PID=$!
else
  WORKER_PID="$(start_worker_fallback)"
fi

cleanup(){
  echo
  log "[stop] stopping background processes ..."
  kill "$API_PID" "$WORKER_PID" >/dev/null 2>&1 || true
  [[ -n "${SSE_PID:-}" ]] && kill "$SSE_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# wait for API
log "[wait] waiting for API on :$PORT (tailing $API_LOG) ..."
( tail -n +1 -F "$API_LOG" & echo $! > logs/.tail_api_pid ) 2>/dev/null || true
API_OK=0
for _ in $(seq 1 60); do
  curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1 && { API_OK=1; break; }
  curl -fsS "http://localhost:${PORT}/openapi.json" >/dev/null 2>&1 && { API_OK=1; break; }
  sleep 1
done
if [[ -f logs/.tail_api_pid ]]; then
  kill "$(cat logs/.tail_api_pid)" >/dev/null 2>&1 || true
  rm -f logs/.tail_api_pid
fi
[[ $API_OK -eq 1 ]] || { echo; log "[fail] API did not become healthy within 60s. Last 200 lines:"; tail -n 200 "$API_LOG" || true; die "API failed to start"; }
ok "[run] API is up."

# ---------------- enable slides via helper (if exists) ----------------
if [[ -x ./scripts/dev-enable-demo-tenant.sh ]]; then
  log "[plugins] syncing manifests for demo-tenant (slides only) ..."
  ./scripts/dev-enable-demo-tenant.sh
  if curl -fsS "http://localhost:${PORT}/v1/services" >/dev/null 2>&1; then
    SERVICES="$(curl -fsS "http://localhost:${PORT}/v1/services" | jq -r '.services[].id' || true)"
    echo "$SERVICES" | grep -q '^slides\.generate$' || { printf "Services:\n%s\n" "$SERVICES"; die "slides.generate not available"; }
    ok "[plugins] slides.generate is enabled."
  fi
else
  log "[plugins] no dev-enable-demo-tenant.sh; relying on dynamic plugin loader."
fi

# ---------------- submit slides.generate job ----------------
log "[test] submitting slides.generate job (slides_count=${SLIDES_COUNT}) ..."
payload="$(jq -nc --arg p "$PROMPT" --argjson n "$SLIDES_COUNT" --arg lang "$LANG" \
  '{service_id:"slides.generate", inputs:{source:"prompt", topic:$p, slides_count:$n, language:$lang, export:["pptx","pdf","html"]}}')"

CREATE="$(curl -fsS -X POST "http://localhost:${PORT}/v1/jobs" -H 'Content-Type: application/json' --data-binary "$payload" || true)"
echo "$CREATE" | jq . >&2
JOB_ID="$(echo "$CREATE" | jq -r .id)"
[[ "$JOB_ID" =~ ^[0-9a-f-]{36}$ ]] || die "invalid job id: $JOB_ID"
log "JOB_ID: $JOB_ID"

# try SSE if endpoint exists, else poll
EV_URL="http://localhost:${PORT}/v1/jobs/${JOB_ID}/events"
SSE_LOG="logs/sse-${JOB_ID}.log"
SSE_ERR="logs/sse-${JOB_ID}.err"
: > "$SSE_LOG"; : > "$SSE_ERR"

USE_SSE=0
if curl -fsSI "$EV_URL" >/dev/null 2>&1; then
  USE_SSE=1
  log "[test] using SSE for progress (→ $SSE_LOG)"
  ( curl -NsS --fail-with-body "$EV_URL" 2> "$SSE_ERR" | tee -a "$SSE_LOG" >/dev/null ) & SSE_PID=$!
fi

ARTIFACT=""
if [[ "$USE_SSE" -eq 1 ]]; then
  for _ in $(seq 1 120); do
    if [[ -s "$SSE_LOG" ]]; then
      set +e
      ARTIFACT="$(
        sed -n 's/^data: //p' "$SSE_LOG" \
        | jq -r '
            select(.event=="step") | .data
            | (.artifact // .pptx_url // .pdf_url // .html_zip_url // .output.pptx_url // .output.pdf_url // .output.html_zip_url // empty)
          ' \
        | head -n1
      )"
      set -e
      [[ -n "$ARTIFACT" ]] && break
    fi
    sleep 1
  done
  kill "${SSE_PID:-0}" >/dev/null 2>&1 || true
fi

# --- polling, now reading .output.* (not .outputs) + FS fallback ------------
if [[ -z "$ARTIFACT" ]]; then
  log "[test] polling job for outputs ..."
  for _ in $(seq 1 180); do
    JS="$(curl -fsS "http://localhost:${PORT}/v1/jobs/${JOB_ID}" || true)"
    ST="$(echo "$JS" | jq -r '.status // .state // empty')"
    printf "${DIM}  status: %s${NC}\n" "$ST" >&2

    # Primary: API fields (.output.*); fallbacks for older shapes.
    ARTIFACT="$(
      echo "$JS" | jq -r '
        .output.pptx_url // .output.pdf_url // .output.html_zip_url //
        .result.output.pptx_url // .result.output.pdf_url // .result.output.html_zip_url //
        .result.outputs.pptx_url // .result.outputs.pdf_url // .result.outputs.html_zip_url //
        .outputs.pptx_url // .outputs.pdf_url // .outputs.html_zip_url //
        empty
      '
    )"

    # If succeeded and still nothing, try file-system discovery (last 15 min)
    if [[ "$ST" =~ ^(succeeded|completed|done|success)$ && -z "$ARTIFACT" ]]; then
      CAND="$(find "$ARTIFACTS_DIR_DEFAULT" -type f \( -name '*.pptx' -o -name '*.pdf' -o -name '*.zip' \) -mmin -15 | head -n1 || true)"
      if [[ -n "$CAND" ]]; then
        # Convert FS path to API URL (strip leading ./)
        REL="${CAND#./}"
        [[ "$REL" != "$CAND" ]] && CAND="$REL"
        ARTIFACT="/${CAND}"
      fi
    fi

    # break only when we have a URL
    if [[ -n "$ARTIFACT" ]]; then
      break
    fi

    [[ "$ST" =~ ^(failed|error)$ ]] && { echo "$JS" | jq . >&2; die "job failed"; }
    sleep 2
  done
fi

if [[ -z "$ARTIFACT" ]]; then
  echo
  log "[fail] no artifact url found — deep diagnostics"
  echo "---- JOB JSON ----"
  curl -fsS "http://localhost:${PORT}/v1/jobs/${JOB_ID}" | jq . || true
  echo "---- LAST 120 WORKER LOG LINES ----"
  tail -n 120 "$WORKER_LOG" || true
  echo "---- LAST 120 API LOG LINES ----"
  tail -n 120 "$API_LOG" || true
  echo "---- FS PROBE (artifacts) ----"
  find "$ARTIFACTS_DIR_DEFAULT" -maxdepth 4 -type f -print 2>/dev/null || true
  die "artifact url not found"
fi

# normalize and download
if [[ "${ARTIFACT}" != http* ]]; then
  ARTIFACT="${PUBLIC_BASE_URL%/}/${ARTIFACT#'/'}"
fi
log "[test] artifact: ${ARTIFACT}"

OUT_DIR="$ARTIFACTS_DIR_DEFAULT"; mkdir -p "$OUT_DIR"
BASENAME="slides-${JOB_ID}"
EXT="${ARTIFACT##*.}"
OUT="${OUT_DIR}/${BASENAME}.${EXT}"

if ! curl -fsS -o "${OUT}" "${ARTIFACT}"; then
  die "download failed: ${ARTIFACT}"
fi

SZ=$(stat -f%z "${OUT}" 2>/dev/null || stat -c%s "${OUT}")
MIN_BYTES="${MIN_BYTES:-256}"
if [[ "${SZ}" -lt "${MIN_BYTES}" ]]; then
  echo "---- JOB JSON ----"; curl -fsS "http://localhost:${PORT}/v1/jobs/${JOB_ID}" | jq . || true
  die "artifact too small (${SZ} bytes < ${MIN_BYTES}) : ${OUT}"
fi
ok "[test] Artifact verified (${SZ} bytes) → ${OUT}"

ok "[DONE] end-to-end OK."
wait || true
