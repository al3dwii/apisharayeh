#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

# ----------------- config -----------------
API_BASE="${API_BASE:-http://localhost:8081}"
DEMO_DOC="${DEMO_DOC:-https://calibre-ebook.com/downloads/demos/demo.docx}"
TITLE="${TITLE:-Sample}"
MAX_SLIDES="${MAX_SLIDES:-8}"
OUTDIR="${OUTDIR:-./artifacts}"
RETRIES="${RETRIES:-60}"

# ----------------- helpers ----------------
# log to STDERR so command substitutions (like JOB_ID="$(submit_job)") stay clean
log() { printf "\n\033[1;36m%s\033[0m\n" "$*" >&2; }
die() { printf "\n\033[1;31mERROR:\033[0m %s\n" "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || die "Missing dependency: $1"; }

wait_for_health() {
  local svc="$1" cid
  cid="$(docker compose ps -q "$svc")"
  [ -n "$cid" ] || die "Service '$svc' container not found"
  log "Waiting for '$svc' to be healthy..."
  local i=0
  while :; do
    local status
    status="$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo starting)"
    [[ "$status" == "healthy" ]] && break
    (( i++ > 120 )) && die "'$svc' did not become healthy in time"
    sleep 1
  done
}

wait_for_api() {
  log "Waiting for API to be ready at $API_BASE ..."
  for _ in $(seq 1 60); do
    # openapi.json is lightweight and public
    if curl -fsS "$API_BASE/openapi.json" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  die "API did not become ready"
}

submit_job() {
  log "Submitting job to convert DOCX -> PPTX ..."
  local payload id
  payload=$(jq -nc --arg url "$DEMO_DOC" --arg title "$TITLE" --argjson max "$MAX_SLIDES" \
    '{service_id:"office.word_to_pptx", inputs:{file_url:$url,title:$title,max_slides:$max}}')
  for _ in $(seq 1 10); do
    id="$(curl -fsS -X POST "$API_BASE/v1/jobs" \
          -H 'Content-Type: application/json' \
          --data-binary "$payload" \
          | jq -r .id || true)"
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && { echo "$id"; return 0; }
    sleep 1
  done
  die "Failed to submit job"
}

wait_for_artifact_url() {
  local job_id="$1" url=""
  log "Waiting for PPTX URL (job: $job_id) ..."
  for _ in $(seq 1 "$RETRIES"); do
    url="$(curl -fsS "$API_BASE/v1/jobs/$job_id" \
      | jq -r '.output.pptx_url // .output.result.pptx_url // empty')"
    [ -n "$url" ] && { echo "$url"; return 0; }
    sleep 1
  done
  return 1
}

verify_pptx() {
  local file="$1"
  need unzip
  local slides
  slides="$(unzip -l "$file" | awk '/ppt\/slides\/slide[0-9]+\.xml/ {c++} END{print c+0}')"
  log "Slide count detected: $slides"
  [ "${slides:-0}" -ge 2 ] || die "PPTX seems incomplete (slides=$slides)"
  unzip -p "$file" 'ppt/slides/slide*.xml' | grep -q -F "$TITLE" || \
    log "Note: title not found in XML (may still be fine)."
}

# ----------------- preflight --------------
need docker; need jq; need curl
docker compose version >/dev/null || die "'docker compose' not available"
cd "$(dirname "$0")/.." || die "Run from within repository"

# ----------------- 1) infra ---------------
log "Starting Postgres + Redis ..."
docker compose up -d db redis
wait_for_health db

# ----------------- 2) migrations ----------
log "Running DB migrations ..."
docker compose run --rm migrate

# ----------------- 3) API + worker -------
log "Starting API + Worker ..."
docker compose up -d --build api worker
wait_for_api

# ----------------- 4) submit job ----------
JOB_ID="$(submit_job | tail -n1)"
log "JOB_ID: $JOB_ID"
log "You can watch live events in another terminal:"
printf '  curl -N "%s/v1/jobs/%s/events"\n' "$API_BASE" "$JOB_ID" >&2

# ----------------- 5) wait & download -----
URL="$(wait_for_artifact_url "$JOB_ID")" || die "Timed out waiting for artifact URL"
log "Artifact URL: $URL"
mkdir -p "$OUTDIR"
OUT_FILE="$OUTDIR/smoke-${JOB_ID}.pptx"
curl -fsSL "$URL" -o "$OUT_FILE" || die "Download failed"
log "Saved: $OUT_FILE"

# ----------------- 6) verify --------------
verify_pptx "$OUT_FILE"

# ----------------- 7) open (macOS) --------
command -v open >/dev/null && open "$OUT_FILE" || true

log "OK: Smoke test passed."
