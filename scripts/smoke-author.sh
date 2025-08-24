#!/usr/bin/env bash
# scripts/smoke-author.sh
# End-to-end smoke for slides.author with robust auto-boot on macOS/Linux.
# - If API is down, it launches scripts/dev-up-and-smoke.sh in a detached session
#   using setsid (if available) or a tiny Python3 shim (os.setsid()).
# - Waits for API health, submits slides.author, tails SSE, verifies artifacts,
#   checks timeline, PATCHes slide #3 and confirms mtime + state.json.
set -euo pipefail

# -------------------- Config --------------------
API_BASE="${API_BASE:-http://localhost:8081}"
TMP_DIR="${TMP_DIR:-./logs}"
AUTO_BOOT="${AUTO_BOOT:-1}"                            # set to 0 to skip auto-boot
DEV_UP_SCRIPT="${DEV_UP_SCRIPT:-scripts/dev-up-and-smoke.sh}"

PROMPT="${SMOKE_PROMPT:-التعليم الإلكتروني}"          # default Arabic to exercise RTL
SLIDE_COUNT="${SMOKE_COUNT:-6}"
LANGUAGE="${SMOKE_LANG:-ar}"

TIMEOUT_API="${TIMEOUT_API:-45}"                       # seconds to wait for API
TIMEOUT_JOB="${TIMEOUT_JOB:-300}"                      # seconds to wait for job
PDF_MIN_BYTES="${PDF_MIN_BYTES:-20000}"                # sanity threshold for PDF size
JQ="${JQ_BIN:-jq}"

mkdir -p "$TMP_DIR"

# -------------------- Helpers --------------------
have() { command -v "$1" >/dev/null 2>&1; }
need() { have "$1" || { echo "[fail] '$1' is required"; exit 1; }; }

need curl
need "$JQ"

log_json() { "$JQ" . 2>/dev/null || cat; }

file_mtime() {
  local p="$1"
  if have python3; then
    python3 - <<'PY' "$p"
import os,sys
p=sys.argv[1]
try: print(int(os.path.getmtime(p)))
except: print(0)
PY
    return
  fi
  stat -f %m "$p" 2>/dev/null && return   # macOS/BSD
  stat -c %Y "$p" 2>/dev/null && return   # GNU
  echo 0
}

die_with_sse() {
  local msg="$1" sse_log="$2" job_json="${3:-}"
  echo "[fail] $msg"
  [[ -n "$job_json" ]] && echo "$job_json" | log_json
  if [[ -f "$sse_log" ]]; then
    echo "---- last 150 SSE lines ----"
    tail -n 150 "$sse_log" || true
  fi
  exit 1
}

health_ok() {
  # Accept either /health or /healthz
  curl -fsS "$API_BASE/health"  >/dev/null 2>&1 && return 0
  curl -fsS "$API_BASE/healthz" >/dev/null 2>&1 && return 0
  return 1
}

wait_for_api() {
  local timeout="${1:-$TIMEOUT_API}"
  local i=0
  echo "[author] waiting for API at $API_BASE/{health,healthz} ..."
  while (( i < timeout )); do
    if health_ok; then
      echo "[author] API is reachable."
      return 0
    fi
    sleep 1; ((i++))
  done
  return 1
}

launch_dev_up_detached() {
  # Start dev-up in its own session, pipe output to a logfile, and (optionally) tail it.
  local log="${TMP_DIR}/dev-up.$$.out"
  echo "[author] Booting API/Worker via $DEV_UP_SCRIPT (detached) …"
  echo "[author] Dev-up log: $log"

  if have setsid; then
    # New session on Linux; some macOS installations don’t have setsid.
    ( setsid bash "$DEV_UP_SCRIPT" >"$log" 2>&1 & echo $! >"${log}.pid" ) || true
  elif have python3; then
    # Portable: create new session via Python
    python3 - "$DEV_UP_SCRIPT" "$log" <<'PY'
import os, sys, subprocess
script, log = sys.argv[1], sys.argv[2]
# Detach: new session and stdio to file/devnull
with open(log, "ab", buffering=0) as f, open(os.devnull, "rb") as dn:
    # Start new session so "kill 0" traps in the child can't kill this parent.
    proc = subprocess.Popen(
        ["bash", script],
        stdin=dn, stdout=f, stderr=subprocess.STDOUT,
        preexec_fn=os.setsid
    )
    print(proc.pid)
PY
  else
    # Last resort: nohup (does NOT create a new session; may still work if dev-up is tame)
    nohup bash "$DEV_UP_SCRIPT" >"$log" 2>&1 &
    echo $! >"${log}.pid"
  fi

  # Tail while waiting (non-fatal if tail not available)
  if have tail; then
    ( tail -n +1 -f "$log" & echo $! >"${log}.tailpid" ) || true
  fi

  # Re-check health
  if ! wait_for_api "$TIMEOUT_API"; then
    [[ -f "$log" ]] && { echo "---- dev-up log (last 120 lines) ----"; tail -n 120 "$log" || true; }
    echo "[fail] API still not reachable after launching $DEV_UP_SCRIPT"
    exit 1
  fi
}

fetch_ok() {
  local url="$1" out="${2:-}"
  if [[ -n "$out" ]]; then
    curl -fsS "$API_BASE$url" -o "$out" >/dev/null 2>&1
  else
    curl -fsS "$API_BASE$url" >/dev/null 2>&1
  fi
}

# -------------------- 0) Ensure API up --------------------
if ! wait_for_api "$TIMEOUT_API"; then
  echo "[warn] API not reachable."
  if [[ "${AUTO_BOOT}" == "1" ]]; then
    [[ -x "$DEV_UP_SCRIPT" || -f "$DEV_UP_SCRIPT" ]] || { echo "[fail] DEV_UP_SCRIPT '$DEV_UP_SCRIPT' not found."; exit 1; }
    launch_dev_up_detached
  else
    echo "[fail] AUTO_BOOT disabled and API not running."
    exit 1
  fi
fi

# -------------------- 1) Start job --------------------
SSE_PID=""
cleanup() {
  if [[ -n "${SSE_PID:-}" ]]; then
    kill "$SSE_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# FYI: verify slides.author exists (do not fail on schema changes)
if curl -fsS "$API_BASE/v1/services" | grep -q '"id":"slides.author"'; then
  echo "[author] slides.author service is registered."
else
  echo "[warn] slides.author not visible in /v1/services; proceeding anyway."
fi

echo "[author] starting slides.author job …"

REQ_JSON=$("$JQ" -n --arg p "$PROMPT" --argjson n "$SLIDE_COUNT" --arg l "$LANGUAGE" \
  '{inputs:{prompt:$p,slides_count:$n,language:$l}}')

HDRS="$TMP_DIR/author-headers.$$.txt"
BODY="$TMP_DIR/author-body.$$.json"

HTTP_CODE="$(curl -sS -X POST "$API_BASE/v1/services/slides.author/jobs" \
  -H 'Content-Type: application/json' -d "$REQ_JSON" \
  -D "$HDRS" -o "$BODY" -w '%{http_code}')"

if [[ "$HTTP_CODE" -lt 200 || "$HTTP_CODE" -ge 300 ]]; then
  echo "[fail] job start HTTP $HTTP_CODE"
  echo "---- response headers ----"; cat "$HDRS"
  echo "---- response body ----"; cat "$BODY" | log_json
  exit 1
fi

RESP="$(cat "$BODY")"
JOB_ID="$(echo "$RESP" | "$JQ" -r '.job_id // .jobId // empty')"
WATCH="$(echo "$RESP" | "$JQ" -r '.watch // empty')"
PROJECT_ID="$(echo "$RESP" | "$JQ" -r '.project_id // empty')"

if [[ -z "$JOB_ID" || -z "$WATCH" || -z "$PROJECT_ID" ]]; then
  echo "[fail] could not parse job start response"
  echo "$RESP" | log_json
  exit 1
fi

SSE_LOG="$TMP_DIR/sse-$JOB_ID.log"
echo "[author] JOB_ID: $JOB_ID  PROJECT_ID: $PROJECT_ID"
echo "[author] streaming events to $SSE_LOG"
( curl -sS -N "$API_BASE$WATCH" | tee "$SSE_LOG" >/dev/null ) &
SSE_PID=$!

# -------------------- 2) Wait for job --------------------
echo "[author] waiting for job to finish …"
deadline=$(( $(date +%s) + TIMEOUT_JOB ))
STATUS="running"

while :; do
  J="$(curl -fsS "$API_BASE/v1/jobs/$JOB_ID" || true)"
  STATUS="$(echo "$J" | "$JQ" -r '.status // empty')"
  if [[ "$STATUS" == "failed" ]]; then
    die_with_sse "job failed" "$SSE_LOG" "$J"
  elif [[ "$STATUS" == "succeeded" ]]; then
    break
  fi
  [[ $(date +%s) -ge $deadline ]] && die_with_sse "timed out waiting for job" "$SSE_LOG" "$J"
  sleep 2
done

# -------------------- 3) Artifacts & assertions --------------------
echo "[author] job succeeded. fetching artifacts summary …"
ART="$(curl -fsS "$API_BASE/v1/projects/$PROJECT_ID/artifacts")"
[[ -n "$ART" ]] || die_with_sse "artifacts API returned empty response" "$SSE_LOG"

STATE_URL="$(echo "$ART" | "$JQ" -r '.state_url // empty')"
INDEX_URL="$(echo "$ART" | "$JQ" -r '.index_url // empty')"
PDF_URL="$(echo "$ART" | "$JQ" -r '.exports.pdf_url // empty')"
PPTX_URL="$(echo "$ART" | "$JQ" -r '.exports.pptx_url // empty')"
HTML_URL="$(echo "$ART" | "$JQ" -r '.exports.html_zip_url // empty')"
SLIDES_COUNT="$(echo "$ART" | "$JQ" -r '.slides | length')"

echo "[author] artifacts summary:"
echo "$ART" | "$JQ" '{state_url, index_url, slides: .slides|length, exports}'

[[ -n "$STATE_URL" ]] || die_with_sse "missing state_url in artifacts" "$SSE_LOG"
[[ -n "$INDEX_URL" ]] || die_with_sse "missing index_url in artifacts" "$SSE_LOG"
[[ -n "$PDF_URL"   ]] || die_with_sse "missing pdf_url" "$SSE_LOG"
[[ -n "$PPTX_URL"  ]] || die_with_sse "missing pptx_url" "$SSE_LOG"
[[ -n "$HTML_URL"  ]] || die_with_sse "missing html_zip_url" "$SSE_LOG"
[[ "$SLIDES_COUNT" -ge 1 ]] || die_with_sse "no slides listed" "$SSE_LOG"

# Validate link + state.json
echo "[author] validating index + state.json …"
fetch_ok "$INDEX_URL" || die_with_sse "index_url not reachable: $INDEX_URL" "$SSE_LOG"
STATE_TMP="$TMP_DIR/state-$PROJECT_ID.json"
fetch_ok "$STATE_URL" "$STATE_TMP" || die_with_sse "state_url not reachable: $STATE_URL" "$SSE_LOG"
[[ "$("$JQ" -r '.project_id' "$STATE_TMP")" == "$PROJECT_ID" ]] || die_with_sse "state.json project_id mismatch" "$SSE_LOG"
[[ "$("$JQ" -r '.slides | length' "$STATE_TMP")" -ge 1 ]] || die_with_sse "state.json has zero slides" "$SSE_LOG"

# Download PDF and sanity-check size
PDF_TMP="$TMP_DIR/${PROJECT_ID}.pdf"
fetch_ok "$PDF_URL" "$PDF_TMP" || die_with_sse "pdf_url not reachable: $PDF_URL" "$SSE_LOG"
PDF_SIZE=$(wc -c < "$PDF_TMP" | tr -d ' ')
if [[ "$PDF_SIZE" -lt "$PDF_MIN_BYTES" ]]; then
  die_with_sse "PDF too small ($PDF_SIZE bytes < $PDF_MIN_BYTES)" "$SSE_LOG"
fi

# -------------------- 4) SSE timeline checks --------------------
echo "[author] checking SSE timeline for narration & research events …"
REQ_CNT=$(grep -Ec '"type"\s*:\s*"using_tool\.start"' "$SSE_LOG" || true)
TODO_INIT_CNT=$(grep -Ec '"type"\s*:\s*"todos\.init"' "$SSE_LOG" || true)
IMG_CNT=$(grep -Ec '"type"\s*:\s*"images\.results"' "$SSE_LOG" || true)
SLIDE_GEN_CNT=$(grep -Ec '"type"\s*:\s*"slide_generated"' "$SSE_LOG" || true)
LINK_READY_CNT=$(grep -Ec '"kind"\s*:\s*"link"' "$SSE_LOG" || true)

[[ "$REQ_CNT" -ge 1 ]] || die_with_sse "missing using_tool.start events" "$SSE_LOG"
[[ "$TODO_INIT_CNT" -ge 1 ]] || die_with_sse "missing todos.init event" "$SSE_LOG"
[[ "$IMG_CNT" -ge 1 ]] || die_with_sse "missing images.results events" "$SSE_LOG"
[[ "$SLIDE_GEN_CNT" -ge 1 ]] || die_with_sse "missing slide_generated events" "$SSE_LOG"
[[ "$LINK_READY_CNT" -ge 1 ]] || die_with_sse "missing artifact.ready {kind: link}" "$SSE_LOG"

echo "[author] SSE timeline looks good."

# -------------------- 5) PATCH slide #3 + verify re-render --------------------
if [[ "$SLIDES_COUNT" -ge 3 ]]; then
  echo "[author] testing PATCH for slide 3 …"
  SLIDE3_PATH="$(echo "$ART" | "$JQ" -r '.slides[] | select(.no==3) | .path // empty')"
  if [[ -z "$SLIDE3_PATH" ]]; then
    echo "[warn] slide 3 path not listed; skipping mtime check."
  else
    BEFORE_MTIME="$(file_mtime "$SLIDE3_PATH")"
    PATCH_PAY=$("$JQ" -n --arg t "فوائد التقنيات التعليمية (مُحدّث)" --arg tpl "text_image_right" \
      '{title:$t, template:$tpl}')
    PATCH_RESP="$(curl -fsS -X PATCH "$API_BASE/v1/projects/$PROJECT_ID/slides/3" \
      -H 'Content-Type: application/json' -d "$PATCH_PAY" || true)"
    echo "[author] PATCH response:"; echo "${PATCH_RESP:-<empty>}" | log_json
    sleep 1
    AFTER_MTIME="$(file_mtime "$SLIDE3_PATH")"
    if [[ "$AFTER_MTIME" -le "$BEFORE_MTIME" ]]; then
      die_with_sse "slide 3 mtime did not change after PATCH" "$SSE_LOG"
    fi
    fetch_ok "$STATE_URL" "$STATE_TMP" || die_with_sse "failed to refetch state.json" "$SSE_LOG"
    TITLE3="$("$JQ" -r '.slides[] | select(.no==3) | .title // empty' "$STATE_TMP")"
    if [[ "$TITLE3" != "فوائد التقنيات التعليمية (مُحدّث)" ]]; then
      die_with_sse "slide 3 title not updated in state.json" "$SSE_LOG"
    fi
    echo "[author] slide 3 re-rendered and state.json updated."
  fi
else
  echo "[warn] deck has fewer than 3 slides; skipping PATCH test."
fi

# -------------------- 6) Extra artifact pings --------------------
echo "[author] verifying pptx/html endpoints respond ..."
fetch_ok "$PPTX_URL" || die_with_sse "pptx_url not reachable: $PPTX_URL" "$SSE_LOG"
fetch_ok "$HTML_URL"  || die_with_sse "html_zip_url not reachable: $HTML_URL" "$SSE_LOG"

echo "[OK] slides.author smoke test passed."
