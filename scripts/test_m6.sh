#!/usr/bin/env bash
# M6: exercise edit-one-slide end-to-end without modifying backend code
set -Eeuo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CY=$'\033[36m'; OK=$'\033[32m'; ER=$'\033[31m'; NC=$'\033[0m'; DIM=$'\033[2m'
log(){ printf "${CY}%s${NC}\n" "$*" >&2; }
ok(){  printf "${OK}%s${NC}\n" "$*" >&2; }
die(){ printf "${ER}ERROR:${NC} %s\n" "$*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null || die "Missing dependency: $1"; }

need curl; need jq; need awk; need hexdump; need sed

API="${API:-http://localhost:8081}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-artifacts}"

PROMPT="${PROMPT:-Future of Green Hydrogen}"
SLIDES_COUNT="${SLIDES_COUNT:-8}"
LANG="${LANG:-en}"
THEME="${THEME:-academic-ar}"

EDIT_SLIDE_NO="${EDIT_SLIDE_NO:-2}"
NEW_TITLE="${NEW_TITLE:-Green Hydrogen: Costs 2025}"
NEW_TEMPLATE="${NEW_TEMPLATE:-text_image_right}"
# If NEW_BULLETS_JSON is valid JSON, we use it. Otherwise BULLETS (newline list) → JSON.
BULLETS="${BULLETS:-$'CapEx falling ~10-15%/yr\nElectrolyzer efficiency improving\nPolicy incentives expanding'}"

# ───────────────── helpers ─────────────────

poll_job() {
  local job_id="$1" ; local timeout="${2:-180}"
  local start="$(date +%s)"
  while :; do
    local TMPB TMPH CT ST
    TMPB="$(mktemp)"; TMPH="$(mktemp)"
    curl -sS -H 'Accept: application/json' -D "$TMPH" -o "$TMPB" "$API/v1/jobs/$job_id" || true
    CT="$(awk -F': *' 'tolower($1)=="content-type"{print tolower($2)}' "$TMPH" | tr -d '\r')"
    if [[ "$CT" == application/json* ]] && jq -e . >/dev/null 2>&1 <"$TMPB"; then
      ST="$(jq -r '.status // .state // empty' "$TMPB")"
      printf "  status: %s\n" "${ST:-unknown}" >&2
      if [[ "$ST" == "succeeded" ]]; then
        cat "$TMPB"; rm -f "$TMPB" "$TMPH"; return 0
      elif [[ "$ST" == "failed" || "$ST" == "error" ]]; then
        cat "$TMPB"; rm -f "$TMPB" "$TMPH"; return 2
      fi
    else
      echo "  status: (non-JSON response)" >&2
      sed 's/\r$//' "$TMPH" | sed 's/^/    /' >&2 || true
      head -c 256 "$TMPB" | hexdump -C >&2 || true
    fi
    rm -f "$TMPB" "$TMPH"
    if (( $(date +%s) - start >= timeout )); then
      echo "{}"; return 3
    fi
    sleep 1
  done
}

build_bullets_json() {
  if [[ -n "${NEW_BULLETS_JSON:-}" ]] && echo "${NEW_BULLETS_JSON}" | jq -e . >/dev/null 2>&1; then
    echo "${NEW_BULLETS_JSON}"
  else
    printf '%s\n' "$BULLETS" | jq -Rsc 'split("\n") | map(select(length>0))'
  fi
}

# Try to infer project_id → state_url when backend doesn’t return it
infer_state_url() {
  local job_json="$1"
  local base="${API%/}"

  # Prefer explicit state_url
  local s_url
  s_url="$(echo "$job_json" | jq -r '.output.state_url // .result.output.state_url // empty')"
  if [[ -n "$s_url" && "$s_url" != "null" ]]; then
    echo "$s_url"; return 0
  fi

  # Try to derive from slides_html[0]
  local first_slide
  first_slide="$(echo "$job_json" | jq -r '.output.slides_html[0] // empty')"
  if [[ -n "$first_slide" ]]; then
    if [[ "$first_slide" =~ /artifacts/([^/]+)/ ]]; then
      local pid="${BASH_REMATCH[1]}"
      echo "$base/artifacts/$pid/state.json"; return 0
    fi
  fi

  # Try to derive from pptx/pdf/html zip urls
  local any_url
  any_url="$(echo "$job_json" | jq -r '.output.pptx_url // .output.pdf_url // .output.html_zip_url // empty')"
  if [[ -n "$any_url" && "$any_url" =~ /artifacts/([^/]+)/ ]]; then
    local pid="${BASH_REMATCH[1]}"
    echo "$base/artifacts/$pid/state.json"; return 0
  fi

  # Last resort: look for the newest state.json in artifacts (best-effort)
  local newest
  newest="$(find "$ARTIFACTS_DIR" -type f -name state.json -mmin -15 2>/dev/null | head -n1 || true)"
  if [[ -n "$newest" ]]; then
    if [[ "$newest" =~ /artifacts/([^/]+)/state\.json$ ]]; then
      local pid="${BASH_REMATCH[1]}"
      echo "$base/artifacts/$pid/state.json"; return 0
    fi
    # If it’s a local path, try to extract PID from path segments
    if [[ "$newest" =~ /artifacts/([^/]+)/ ]]; then
      local pid="${BASH_REMATCH[1]}"
      echo "$base/artifacts/$pid/state.json"; return 0
    fi
  fi

  echo ""
}

create_deck() {
  log "[submit] slides.generate (create base deck)"
  local req job_id js
  req="$(jq -nc --arg p "$PROMPT" --argjson n "$SLIDES_COUNT" --arg l "$LANG" --arg t "$THEME" \
         '{service_id:"slides.generate", inputs:{source:"prompt", topic:$p, language:$l, theme:$t, slides_count:$n}}')"
  job_id="$(curl -fsS -H 'Content-Type: application/json' -X POST -d "$req" "$API/v1/jobs" | jq -r .id)"
  [[ "$job_id" =~ ^[0-9a-f-]{36}$ ]] || die "bad job id for generate: $job_id"

  log "[wait] generate job $job_id"
  js="$(poll_job "$job_id" 240)" || true

  # Extract a state_url (explicit or inferred)
  local state_url pid
  state_url="$(infer_state_url "$js")"
  if [[ -z "$state_url" ]]; then
    die "generate produced no state_url"
  fi

  # Extract PID from state_url
  if [[ "$state_url" =~ /artifacts/([^/]+)/state\.json$ ]]; then
    pid="${BASH_REMATCH[1]}"
  else
    pid="$(sed -n 's#.*\/artifacts/\([^/]*\)\/.*#\1#p' <<<"$state_url" | head -n1 || true)"
  fi
  [[ -n "$pid" ]] || die "could not parse project_id from state_url=$state_url"

  # IMPORTANT: print with a TAB so the caller can split reliably even with IFS=$'\n\t'
  printf "%s\t%s\n" "$pid" "$state_url"
}

check_service() {
  log "[setup] checking slides.edit service"
  local ids
  ids="$(curl -fsS "$API/v1/services" | jq -r '.services[].id')"
  echo "$ids" | grep -q '^slides\.edit$' || {
    printf "Services:\n%s\n" "$ids"
    die "slides.edit not available (enable the plugin manifest first)"
  }
  ok "[plugins] slides.edit is enabled"
}

# ───────────────── main ─────────────────

log "[probe] $API/health"
curl -fsS "$API/health" >/dev/null || die "API not reachable at $API"

check_service

# Read PID and STATE_URL using a localized tab-only IFS
PID=""; STATE_URL=""
{ IFS=$'\t'; read -r PID STATE_URL; } < <(create_deck)
[[ -n "$PID" && -n "$STATE_URL" ]] || die "internal: failed to retrieve PID/STATE_URL"
ok "[deck] project_id=$PID $STATE_URL"

log "[inspect] reading state.json"
curl -fsS "$STATE_URL" | jq -r '.slides | length as $n | "slides=\($n)"' || true

BULLETS_JSON="$(build_bullets_json)"

log "[submit] slides.edit (slide #$EDIT_SLIDE_NO)"
REQ_EDIT="$(jq -nc \
  --arg pid "$PID" \
  --argjson no "$EDIT_SLIDE_NO" \
  --arg t "$NEW_TITLE" \
  --arg tpl "$NEW_TEMPLATE" \
  --argjson bullets "$BULLETS_JSON" \
  '{service_id:"slides.edit", inputs:{project_id:$pid, slide_no:$no, patch:{title:$t, bullets:$bullets, template:$tpl}}}')"

EDIT_JOB_ID="$(curl -fsS -H 'Content-Type: application/json' -X POST -d "$REQ_EDIT" "$API/v1/jobs" | jq -r '.id')"
[[ "$EDIT_JOB_ID" =~ ^[0-9a-f-]{36}$ ]] || die "bad edit job id: $EDIT_JOB_ID"

log "[wait] edit job $EDIT_JOB_ID"
JS_EDIT="$(poll_job "$EDIT_JOB_ID" 120)" || true
STATUS="$(echo "$JS_EDIT" | jq -r '.status // .state // empty')"
[[ "$STATUS" == "succeeded" ]] || { echo "$JS_EDIT" | jq . >&2; die "edit job did not succeed"; }

STATE_URL_OUT="$(echo "$JS_EDIT" | jq -r '.output.state_url // empty')"
URL_OUT="$(echo "$JS_EDIT" | jq -r '.output.url // empty')"

echo "URL:   ${URL_OUT:-<none>}"
echo "STATE: ${STATE_URL_OUT:-$STATE_URL}"

if [[ -n "${STATE_URL_OUT:-$STATE_URL}" ]]; then
  log "[verify] fetch state.json and show edited slide"
  curl -fsS "${STATE_URL_OUT:-$STATE_URL}" \
    | jq -r --argjson n "$EDIT_SLIDE_NO" \
      '.slides[] | select(.no==($n|tonumber)) | "no=\(.no) | title=\(.title) | bullets=\(.bullets|length)"'
fi

ok "[M6] edit flow OK."
