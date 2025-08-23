#!/usr/bin/env bash
# Developer checklist for the local backend with terminal logging.
# Checks:
#  - Env snapshot
#  - API health
#  - JSON-Schema validation (422/400 on bad inputs)
#  - Services exposure (slides.generate)
#  - Idempotency: same key+body → same job; (optional) conflict → 409
#  - Queue/worker flow (create job, SSE, artifact)
#  - Artifact integrity (header + size)
#  - Observability: SSE markers (service.start/end, artifact.ready)

set -Eeuo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p logs artifacts

# colors
if command -v tput >/dev/null 2>&1 && [ -t 2 ]; then
  CY="$(tput setaf 6)"; OK="$(tput setaf 2)"; WARN="$(tput setaf 3)"; ERR="$(tput setaf 1)"
  DIM="$(tput dim)"; NC="$(tput sgr0)"
else
  CY=''; OK=''; WARN=''; ERR=''; DIM=''; NC=''
fi

log(){   printf "${CY}%s${NC}\n" "$*" >&2; }
pass(){  printf "${OK}PASS${NC}  %s\n" "$*" >&2; }
warn(){  printf "${WARN}WARN${NC}  %s\n" "$*" >&2; }
fail(){  printf "${ERR}FAIL${NC}  %s\n" "$*" >&2; }
section(){ echo; log "── $* ─────────────────────────────────────────────"; }
saycurl(){ printf "${DIM}→ curl %s${NC}\n" "$*" >&2; }
mark(){ printf "${DIM}… %s${NC}\n" "$*" >&2; }

need(){ command -v "$1" >/dev/null 2>&1 || { fail "Missing dependency: $1"; exit 1; }; }
need curl; need jq

PORT="${PORT:-8081}"
API="http://localhost:${PORT}"
TENANT="${TENANT:-demo-tenant}"
TENANT_HEADER="${TENANT_HEADER:-X-Tenant-ID}"
MIN_PDF_BYTES="${MIN_PDF_BYTES:-256}"

# Flags (1 = enabled)
TRACE="${TRACE:-1}"
PRINT_SSE="${PRINT_SSE:-1}"
CHECK_IDEMPOTENCY_CONFLICT="${CHECK_IDEMPOTENCY_CONFLICT:-1}"
CHECK_OBSERVABILITY="${CHECK_OBSERVABILITY:-1}"

# ERR trap for diagnostics
trap 'rc=$?; echo; echo "${ERR}ABORT${NC} rc=$rc at line $LINENO: ${DIM}${BASH_COMMAND}${NC}" >&2; exit $rc' ERR

PASSES=0; WARNS=0; FAILS=0
# IMPORTANT: avoid ((x++)) with `set -e` (first eval returns 1). Use assignment.
inc_pass(){ PASSES=$((PASSES+1)); }
inc_warn(){ WARNS=$((WARNS+1)); }
inc_fail(){ FAILS=$((FAILS+1)); }

runrc(){ set +e; "$@"; local rc=$?; set -e; return $rc; }

# pretty curl that logs to terminal and to file
# usage: scurl OUTFILE -- <curl args...>
scurl(){
  local outfile="$1"; shift
  [[ "${1:-}" == "--" ]] && shift || true
  [[ "$TRACE" == "1" ]] && saycurl "$*"
  set +e
  local resp; resp="$(curl "$@")"; local rc=$?
  set -e
  if [[ -n "$outfile" ]]; then
    if jq -e . >/dev/null 2>&1 <<<"$resp"; then
      tee "$outfile" <<<"$resp" >/dev/null
      jq . <<<"$resp" >&2
    else
      tee "$outfile" <<<"$resp" >&2
    fi
  else
    if jq -e . >/dev/null 2>&1 <<<"$resp"; then jq . <<<"$resp" >&2; else echo "$resp" >&2; fi
  fi
  return $rc
}

wait_api() {
  for _ in $(seq 1 60); do
    curl -fsS "${API}/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

summary() {
  echo
  log "Summary: ${OK}${PASSES} pass${NC}, ${WARN}${WARNS} warn${NC}, ${ERR}${FAILS} fail${NC}"
  [[ $FAILS -eq 0 ]] || exit 1
}

# ── 0) env snapshot ───────────────────────────────────────────────────────────
section "Local environment snapshot"
{
  echo "PWD=$(pwd)"
  echo "API=${API}"
  echo "TENANT=${TENANT}"
  echo "TENANT_HEADER=${TENANT_HEADER}"
  echo "FLAGS: TRACE=${TRACE} PRINT_SSE=${PRINT_SSE} CHECK_IDEMPOTENCY_CONFLICT=${CHECK_IDEMPOTENCY_CONFLICT} CHECK_OBSERVABILITY=${CHECK_OBSERVABILITY}"
} | tee "logs/checklist.env"

# ── 1) API health ─────────────────────────────────────────────────────────────
section "Health check"
if wait_api; then
  pass "API healthy at ${API}/health"; inc_pass
else
  fail "API did not respond healthy within 60s"; inc_fail
  summary; exit 1
fi
mark "health section complete"

# ── 2) JSON-Schema validation (expect 422/400) ────────────────────────────────
section "JSON-Schema validation (expect 422/400 on invalid inputs)"
BAD_PAYLOAD_FILE="logs/checklist-bad-payload.json"
echo '{"inputs":{"foo":"bar"}}' > "$BAD_PAYLOAD_FILE"
[[ "$TRACE" == "1" ]] && saycurl "-sS -o /dev/null -w '%{http_code}' -X POST ${API}/v1/jobs -H 'Content-Type: application/json' --data-binary @$BAD_PAYLOAD_FILE"
CODE="$(runrc curl -sS -o /dev/null -w '%{http_code}' -X POST "${API}/v1/jobs" \
  -H 'Content-Type: application/json' --data-binary @"$BAD_PAYLOAD_FILE")"
echo "${DIM}HTTP ${CODE}${NC}"
if [[ "${CODE}" == "422" || "${CODE}" == "400" ]]; then
  pass "Invalid payload correctly rejected (HTTP ${CODE})"; inc_pass
else
  fail "Expected 422/400, got ${CODE}"; inc_fail
fi
mark "schema section complete"

# ── 3) Services exposure ──────────────────────────────────────────────────────
section "Services exposure (slides.generate present)"
SERVICES_LOG="logs/checklist-services.json"
scurl "$SERVICES_LOG" -- -fsS "${API}/v1/services" || true
if jq -r '.services[].id' < "$SERVICES_LOG" | grep -q '^slides\.generate$'; then
  pass "slides.generate service is listed"; inc_pass
else
  fail "slides.generate not present in /v1/services (see $SERVICES_LOG)"; inc_fail
fi
mark "services section complete"

# ── 4) Idempotency (same key → same job id) ───────────────────────────────────
section "Idempotency-Key (same key + same body)"
IDK="chk-$(date +%s)-$RANDOM"
payload_same='{"service_id":"slides.generate","inputs":{"prompt":"hello","slides_count":3}}'
R1_LOG="logs/checklist-idem-1.json"
R2_LOG="logs/checklist-idem-2.json"

scurl "$R1_LOG" -- -fsS -X POST "${API}/v1/jobs" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: ${IDK}" --data-binary "$payload_same" || true
jid1="$(jq -r '.id // empty' < "$R1_LOG" 2>/dev/null || true)"

scurl "$R2_LOG" -- -fsS -X POST "${API}/v1/jobs" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: ${IDK}" --data-binary "$payload_same" || true
jid2="$(jq -r '.id // empty' < "$R2_LOG" 2>/dev/null || true)"

echo "${DIM}jid1=${jid1} jid2=${jid2}${NC}"
if [[ -n "$jid1" && "$jid1" == "$jid2" ]]; then
  pass "Same Idempotency-Key re-used prior job (${jid1})"; inc_pass
else
  warn "Idempotency unexpected (see $R1_LOG / $R2_LOG)"; inc_warn
fi
mark "idempotency section complete"

# Optional conflict path (409)
if [[ "${CHECK_IDEMPOTENCY_CONFLICT}" -eq 1 ]]; then
  section "Idempotency conflict (same key + different body → expect 409)"
  KEY2="chk-conf-$(date +%s)-$RANDOM"
  P1='{"service_id":"slides.generate","inputs":{"prompt":"A","slides_count":3}}'
  P2='{"service_id":"slides.generate","inputs":{"prompt":"B","slides_count":3}}'
  scurl "logs/checklist-idem-conf-first.json" -- -fsS -X POST "${API}/v1/jobs" \
    -H 'Content-Type: application/json' -H "Idempotency-Key: $KEY2" --data-binary "$P1" || true
  [[ "$TRACE" == "1" ]] && saycurl "-sS -o /dev/null -w '%{http_code}' -X POST ${API}/v1/jobs -H 'Content-Type: application/json' -H 'Idempotency-Key: $KEY2' --data-binary '$P2'"
  CODE2="$(runrc curl -sS -o /dev/null -w '%{http_code}' -X POST "${API}/v1/jobs" \
    -H 'Content-Type: application/json' -H "Idempotency-Key: $KEY2" --data-binary "$P2")"
  echo "${DIM}HTTP ${CODE2}${NC}"
  if [[ "$CODE2" == "409" ]]; then
    pass "Got 409 conflict as expected"; inc_pass
  else
    warn "Expected 409, got $CODE2"; inc_warn
  fi
  mark "idempotency-conflict section complete"
fi

# ── 5) Job execution (SSE + artifact) ─────────────────────────────────────────
section "Job execution via Celery + SSE + artifact"
payload="$(jq -nc --arg p "Quick intro to AI in education" --argjson n 3 \
  '{service_id:"slides.generate", inputs:{prompt:$p, slides_count:$n}}')"
CREATE_LOG="logs/checklist-create.json"
scurl "$CREATE_LOG" -- -fsS -X POST "${API}/v1/jobs" -H 'Content-Type: application/json' --data-binary "$payload" || true
JOB_ID="$(jq -r .id < "$CREATE_LOG" 2>/dev/null || true)"
[[ "$JOB_ID" =~ ^[0-9a-f-]{36}$ ]] || { fail "Bad JOB_ID '$JOB_ID'"; inc_fail; summary; exit 1; }
log "JOB_ID: ${JOB_ID}"

EV_URL="${API}/v1/jobs/${JOB_ID}/events"
SSE_LOG="logs/checklist-sse-${JOB_ID}.log"
SSE_ERR="logs/checklist-sse-${JOB_ID}.err"
: > "$SSE_LOG"; : > "$SSE_ERR"

log "Streaming SSE from ${EV_URL} (also saving to $SSE_LOG)"
[[ "$PRINT_SSE" == "1" ]] && saycurl "-NsS --fail-with-body $EV_URL"

if [[ "$PRINT_SSE" == "1" ]]; then
  ( curl -NsS --fail-with-body "$EV_URL" \
      2> >(tee -a "$SSE_ERR" >&2) \
      | tee -a "$SSE_LOG" ) & SSE_PID=$!
else
  ( curl -NsS --fail-with-body "$EV_URL" 2> "$SSE_ERR" | tee -a "$SSE_LOG" >/dev/null ) & SSE_PID=$!
fi

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

# Poll if SSE didn’t yield
if [[ -z "$ARTIFACT" ]]; then
  section "SSE didn’t yield — polling job"
  JOB_JSON="logs/checklist-job-${JOB_ID}.json"
  for _ in $(seq 1 30); do
    scurl "$JOB_JSON" -- -fsS "${API}/v1/jobs/${JOB_ID}" || true
    ARTIFACT="$(jq -r '.output.pdf_url // .output.result.pdf_url // empty' < "$JOB_JSON" 2>/dev/null || true)"
    [[ -n "$ARTIFACT" ]] && break
    sleep 1
  done
fi

if [[ -z "$ARTIFACT" ]]; then
  fail "No artifact URL found for job ${JOB_ID}"; inc_fail
  echo "---- JOB JSON ----"; [[ -f "$JOB_JSON" ]] && cat "$JOB_JSON" || curl -fsS "${API}/v1/jobs/${JOB_ID}" | jq .
  echo "---- SSE (tail) ----"; tail -n 120 "$SSE_LOG" 2>/dev/null || true
  echo "---- SSE ERR (tail) ----"; tail -n 120 "$SSE_ERR" 2>/dev/null || true
  summary; exit 1
fi

# Normalize to absolute URL if needed
if [[ "${ARTIFACT}" != http* ]]; then
  ARTIFACT="${API}${ARTIFACT}"
fi
log "artifact: ${ARTIFACT}"
mark "job + sse section complete"

# ── 6) Artifact integrity ─────────────────────────────────────────────────────
section "Artifact integrity"
OUT="artifacts/checklist-${JOB_ID}.pdf"
[[ "$TRACE" == "1" ]] && saycurl "-fsS -o $OUT $ARTIFACT"
if runrc curl -fsS -o "$OUT" "$ARTIFACT"; then
  SZ=$(stat -f%z "$OUT" 2>/dev/null || stat -c%s "$OUT")
  echo "${DIM}saved: $OUT ($SZ bytes)${NC}"
  if [[ "${SZ:-0}" -ge "${MIN_PDF_BYTES}" ]] && head -c 4 "$OUT" | grep -q "%PDF"; then
    pass "Artifact OK (${SZ} bytes) → $OUT"; inc_pass
  else
    fail "Artifact invalid/too small (${SZ} bytes) → $OUT"; inc_fail
  fi
else
  fail "Download failed: ${ARTIFACT}"; inc_fail
fi
mark "artifact section complete"

# ── 7) Observability (SSE markers) ────────────────────────────────────────────
if [[ "${CHECK_OBSERVABILITY}" -eq 1 ]]; then
  section "Observability (SSE contains service.start/end + artifact.ready)"
  has_start="$(grep -c '"name":"service.start"' "$SSE_LOG" || true)"
  has_end="$(grep -c '"name":"service.end"' "$SSE_LOG" || true)"
  has_art="$(grep -c '"name":"artifact.ready"' "$SSE_LOG" || true)"
  echo "${DIM}SSE_LOG=$SSE_LOG${NC}"
  echo "${DIM}Markers: start=${has_start} end=${has_end} artifact=${has_art}${NC}"
  if [[ "$has_start" -ge 1 && "$has_end" -ge 1 && "$has_art" -ge 1 ]]; then
    pass "SSE stream had all expected markers"; inc_pass
  else
    warn "Missing SSE markers (start:${has_start}, end:${has_end}, artifact:${has_art})"; inc_warn
  fi
fi
mark "observability section complete"

summary
