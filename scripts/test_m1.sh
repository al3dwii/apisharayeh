#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Milestone 1 Test Runner (Auto Research for Slides) — v3
# - Logs go to stderr so command-substitution doesn't break URLs
# - Works even if /v1/plugins is missing (warns & continues)
# - Prefers /v1/jobs for launching runs; falls back to plugin-run routes
# Requirements: bash, curl, jq
# Env (optional):
#   API_URL, AUTH_BEARER, PROJECT_ID, TOPIC, TEST_URLS, TIMEOUT_SECS
# ─────────────────────────────────────────────────────────────────────────────

# ---- logging (stderr) ----
RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; BLU=$'\033[34m'; RST=$'\033[0m'
log()  { >&2 echo "${BLU}[$(date +%H:%M:%S)]${RST} $*"; }
pass() { >&2 echo "${GRN}PASS${RST} $*"; }
fail() { >&2 echo "${RED}FAIL${RST} $*"; exit 1; }
warn() { >&2 echo "${YLW}WARN${RST} $*"; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || { fail "missing command '$1'"; }; }
require_cmd curl; require_cmd jq

AUTH_BEARER="${AUTH_BEARER:-}"
PROJECT_ID="${PROJECT_ID:-prj_m1_$(date +%s)}"
TOPIC="${TOPIC:-Future of Green Hydrogen}"
TIMEOUT_SECS="${TIMEOUT_SECS:-300}"
TEST_URLS="${TEST_URLS:-}"

# Candidate API roots
if [[ -n "${API_URL:-}" ]]; then
  CANDIDATES=("$API_URL")
else
  CANDIDATES=("http://localhost:8000" "http://127.0.0.1:8000" "http://localhost:8080" "http://127.0.0.1:8080" "http://localhost:8081" "http://127.0.0.1:8081")
fi

CURL_COMMON=(-sS -H 'Content-Type: application/json')
[[ -n "$AUTH_BEARER" ]] && CURL_COMMON+=(-H "Authorization: ${AUTH_BEARER}")

api_get()  { curl "${CURL_COMMON[@]}" "$1"; }
api_post() { curl "${CURL_COMMON[@]}" -X POST -d "$2" "$1"; }

probe_plugins_once() {
  local base="$1"
  api_get "${base}/v1/plugins" || api_get "${base}/plugins" || true
}
probe_openapi_once() {
  local base="$1"
  api_get "${base}/openapi.json" || true
}

pick_api_base() {
  for base in "${CANDIDATES[@]}"; do
    log "Probing ${base} …"
    # try plugin list
    local js; js="$(probe_plugins_once "$base")"
    if [[ -n "$js" && "$js" != "null" ]]; then echo "$base"; return 0; fi
    # try openapi
    local oj; oj="$(probe_openapi_once "$base")"
    if [[ -n "$oj" && "$oj" != "null" ]]; then echo "$base"; return 0; fi
    # last resort: a ping
    if curl -fsS "${base}/" >/dev/null 2>&1; then echo "$base"; return 0; fi
  done
  return 1
}

# Try several job-start endpoints; prefer /v1/jobs
job_start() {
  local base="$1" plugin_id="$2" inputs_json="$3"
  local js body

  # A) /v1/jobs
  body="$(jq -n --arg pid "$plugin_id" --argjson inputs "$inputs_json" '$inputs | fromjson as $in | {plugin_id:$pid, inputs:$in}')" || true
  js="$(api_post "${base}/v1/jobs" "$body" || true)"
  if [[ "$(echo "$js" | jq -r '.id? // empty')" != "" ]]; then echo "$js" | jq -r '.id'; return 0; fi

  # B) /v1/plugins/{id}/run
  js="$(api_post "${base}/v1/plugins/${plugin_id}/run" "$inputs_json" || true)"
  if [[ "$(echo "$js" | jq -r '.id? // empty')" != "" ]]; then echo "$js" | jq -r '.id'; return 0; fi

  # C) /v1/plugins/run
  body="$(jq -n --arg id "$plugin_id" --argjson inputs "$inputs_json" '$inputs | fromjson as $in | {id:$id, inputs:$in}')" || true
  js="$(api_post "${base}/v1/plugins/run" "$body" || true)"
  if [[ "$(echo "$js" | jq -r '.id? // empty')" != "" ]]; then echo "$js" | jq -r '.id'; return 0; fi

  >&2 echo "$js" | sed 's/^/  /'
  fail "Unable to start job for ${plugin_id} at ${base}"
}

poll_job() {
  local base="$1" job_id="$2" started_ts; started_ts=$(date +%s)
  while true; do
    (( $(date +%s) - started_ts > TIMEOUT_SECS )) && fail "Job ${job_id} timed out after ${TIMEOUT_SECS}s"
    local js st
    js="$(api_get "${base}/v1/jobs/${job_id}" || true)"
    st="$(echo "$js" | jq -r '.status // .state // empty')"
    [[ -z "$st" ]] && st="unknown"
    log "Job ${job_id} status: ${st}"
    case "$st" in
      succeeded|completed|done|success) echo "$js"; return 0 ;;
      failed|error) >&2 echo "$js" | jq .; fail "Job ${job_id} failed." ;;
      *) sleep 2 ;;
    esac
  done
}

assert_url_ok() {
  local base="$1" url="$2"
  [[ -z "$url" || "$url" == "null" ]] && fail "Expected non-empty URL"
  [[ "$url" != http* ]] && url="${base%/}/${url#'/'}"
  log "GET $url"
  curl -sSf -o /dev/null "$url" && pass "200 OK $url" || fail "GET failed $url"
}

main() {
  log "Trying to locate the API server…"
  local BASE
  if ! BASE="$(pick_api_base)"; then
    echo
    echo "No API detected on: ${CANDIDATES[*]}"
    echo "Start the server, e.g.:"
    echo "  PYTHONPATH=src python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --loop asyncio --http h11"
    exit 1
  fi

  log "API base: $BASE"
  log "PROJECT_ID = $PROJECT_ID"
  log "TOPIC      = $TOPIC"
  [[ -z "$AUTH_BEARER" ]] && warn "AUTH_BEARER not set (assuming open dev API)"

  # 0) Try to list plugins (optional)
  local plugins_json
  plugins_json="$(probe_plugins_once "$BASE")"
  if [[ -z "$plugins_json" || "$plugins_json" == "null" ]]; then
    warn "No /v1/plugins or /plugins endpoint (continuing anyway)"
  else
    if echo "$plugins_json" | jq -e 'tostring | test("slides.generate")' >/dev/null; then
      pass "slides.generate appears in plugin registry"
    else
      warn "slides.generate not visible in plugin registry (may still run via /v1/jobs)"
    fi
  fi

  # 1) Research preview (optional route)
  local prev_req prev_resp
  prev_req="$(jq -n --arg pid "$PROJECT_ID" --arg topic "$TOPIC" '{project_id:$pid, topic:$topic, language:"en", max_docs:4}')"
  prev_resp="$(api_post "${BASE}/v1/research/preview" "$prev_req" || true)"
  if [[ -n "$prev_resp" && "$prev_resp" != "null" && "$prev_resp" != *"Not Found"* ]]; then
    local olc; olc="$(echo "$prev_resp" | jq -r '.outline | length // 0')"
    [[ "$olc" -ge 1 ]] && pass "preview: outline ($olc slides)"
  else
    warn "preview route not available; skipping"
  fi

  # 2) Start slides.generate via /v1/jobs
  log "Starting slides.generate (topic auto-research)…"
  local run_inputs_json job_id final_json outputs_json pptx_url pdf_url html_zip_url
  run_inputs_json="$(jq -n --arg pid "$PROJECT_ID" --arg topic "$TOPIC" '{project_id:$pid, topic:$topic, language:"en", export:["pptx","pdf","html"]}')" 
  job_id="$(job_start "$BASE" "slides.generate" "$run_inputs_json")"
  pass "job started: $job_id"

  final_json="$(poll_job "$BASE" "$job_id")"
  pass "job succeeded"

  outputs_json="$(echo "$final_json" | jq -r '.result.outputs // .outputs // {}')"
  echo "$outputs_json" | jq .

  pptx_url="$(echo "$outputs_json" | jq -r '.pptx_url // empty')"
  pdf_url="$(echo "$outputs_json" | jq -r '.pdf_url // empty')"
  html_zip_url="$(echo "$outputs_json" | jq -r '.html_zip_url // empty')"

  [[ -n "$pptx_url"     ]] && assert_url_ok "$BASE" "$pptx_url"     || warn "pptx_url missing"
  [[ -n "$pdf_url"      ]] && assert_url_ok "$BASE" "$pdf_url"      || warn "pdf_url missing"
  [[ -n "$html_zip_url" ]] && assert_url_ok "$BASE" "$html_zip_url" || warn "html_zip_url missing"

  # 3) Research artifacts
  assert_url_ok "$BASE" "/artifacts/${PROJECT_ID}/research/citations.json"
  assert_url_ok "$BASE" "/artifacts/${PROJECT_ID}/research/outline.json"
  assert_url_ok "$BASE" "/artifacts/${PROJECT_ID}/research/docs.json"

  # 4) Optional URL-mode test
  if [[ -n "$TEST_URLS" ]]; then
    IFS=',' read -r -a urls_arr <<< "$TEST_URLS"
    local urls_json; urls_json="$(printf '%s\n' "${urls_arr[@]}" | jq -R . | jq -s .)"
    log "Starting slides.generate (urls)…"
    local run_inputs_urls job2
    run_inputs_urls="$(jq -n --arg pid "$PROJECT_ID" --argjson urls "$urls_json" '{project_id:$pid, urls:$urls, language:"en"}')"
    job2="$(job_start "$BASE" "slides.generate" "$run_inputs_urls")"
    poll_job "$BASE" "$job2" >/dev/null
    pass "urls-mode job completed"
  fi

  pass "Milestone 1 smoke tests complete on ${BASE} (project ${PROJECT_ID})"
}

main "$@"
