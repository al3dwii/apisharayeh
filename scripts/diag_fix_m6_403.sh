#!/usr/bin/env bash
# Diagnose + fix "403" on slides.edit by confirming service + creating a deck,
# then robustly extracting project_id from job outputs or filesystem.
set -Eeuo pipefail
IFS=$'\n\t'

API="${API:-http://localhost:8081}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CY=$'\033[36m'; OK=$'\033[32m'; ER=$'\033[31m'; NC=$'\033[0m'
log(){ printf "${CY}%s${NC}\n" "$*" >&2; }
ok(){  printf "${OK}%s${NC}\n" "$*" >&2; }
die(){ printf "${ER}ERROR:${NC} %s\n" "$*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null || die "Missing dependency: $1"; }

need curl; need jq; need sed; need awk

# --- 1) Probe API -------------------------------------------------------------
log "[1] Probe API"
curl -fsS "$API/health" >/dev/null || die "API not healthy at $API"
ok "API healthy"

# --- 2) Show services ---------------------------------------------------------
log "[2] Show services (looking for slides.edit)"
SRV_JSON="$(curl -fsS "$API/v1/services")"
echo "$SRV_JSON" | jq -r '.services[].id' | sed 's/^/  - /'
echo "$SRV_JSON" | jq -r '.services[].id' | grep -q '^slides\.edit$' || die "slides.edit not registered"

# --- 3) Force plugin refresh (optional, safe if absent) ----------------------
log "[3] Force plugin registry refresh for demo-tenant"
if [[ -x ./scripts/dev-enable-demo-tenant.sh ]]; then
  ./scripts/dev-enable-demo-tenant.sh
  ok "Done."
else
  log "No dev-enable-demo-tenant.sh; skipping explicit refresh."
fi

# Confirm again
curl -fsS "$API/v1/services" | jq -r '.services[].id' | grep -q '^slides\.edit$' \
  || die "slides.edit missing after refresh"
ok "slides.edit appears in /v1/services"

# --- helpers -----------------------------------------------------------------
extract_pid_from_url(){
  # Input: any URL that contains /artifacts/<project_id>/...
  sed -n 's#.*\/artifacts/\([^/]*\)\/.*#\1#p'
}

robust_project_id_from_job(){
  # Args: job_json
  local js="$1"

  # try state_url in multiple shapes
  local state_url
  state_url="$(echo "$js" | jq -r '
    .output.state_url //
    .result.output.state_url //
    .outputs.state_url //
    .result.outputs.state_url // empty
  ')"

  if [[ -n "$state_url" && "$state_url" != "null" ]]; then
    echo "$state_url" | extract_pid_from_url && return 0
  fi

  # try first slide html url
  local first_slide
  first_slide="$(echo "$js" | jq -r '
    (.output.slides_html // .result.output.slides_html // .outputs.slides_html // .result.outputs.slides_html // []) | .[0] // empty
  ')"
  if [[ -n "$first_slide" && "$first_slide" != "null" ]]; then
    echo "$first_slide" | extract_pid_from_url && return 0
  fi

  # try exports
  local any_art
  any_art="$(echo "$js" | jq -r '
    .output.pptx_url // .output.pdf_url // .output.html_zip_url //
    .result.output.pptx_url // .result.output.pdf_url // .result.output.html_zip_url //
    .outputs.pptx_url // .outputs.pdf_url // .outputs.html_zip_url // empty
  ')"
  if [[ -n "$any_art" && "$any_art" != "null" ]]; then
    echo "$any_art" | extract_pid_from_url && return 0
  fi

  # last resort: newest artifacts subdir
  if [[ -d artifacts ]]; then
    local latest
    latest="$(ls -dt artifacts/*/ 2>/dev/null | head -n1 | xargs -n1 basename 2>/dev/null || true)"
    if [[ -n "$latest" ]]; then
      echo "$latest"; return 0
    fi
  fi

  return 1
}

poll_job(){
  local job_id="$1"; local timeout="${2:-180}"
  local start=$(date +%s)
  while :; do
    local js
    js="$(curl -fsS "$API/v1/jobs/$job_id" || echo '{}')"
    local st
    st="$(echo "$js" | jq -r '.status // .state // empty')"
    printf "  status: %s\n" "${st:-unknown}" >&2
    [[ "$st" == "succeeded" ]] && { echo "$js"; return 0; }
    [[ "$st" == "failed" || "$st" == "error" ]] && { echo "$js"; return 2; }
    (( $(date +%s) - start > timeout )) && { echo "$js"; return 3; }
    sleep 1
  done
}

# --- 4) Create a quick deck (optional) ---------------------------------------
log "[4] Optional: create a quick deck to get a project_id"

REQ="$(jq -nc '{service_id:"slides.generate", inputs:{source:"prompt", topic:"Diag deck", language:"en", slides_count:6}}')"
JOB_CREATE="$(curl -fsS -H 'Content-Type: application/json' -X POST -d "$REQ" "$API/v1/jobs" | jq -r '.id')"
[[ "$JOB_CREATE" =~ ^[0-9a-f-]{36}$ ]] || die "could not create generate job"

JS="$(poll_job "$JOB_CREATE" 240)" || true
ST="$(echo "$JS" | jq -r '.status // .state // empty')"
[[ "$ST" == "succeeded" ]] || { echo "$JS" | jq . >&2; die "generate job did not succeed"; }

PID="$(robust_project_id_from_job "$JS" || true)"
if [[ -z "${PID:-}" ]]; then
  die "could not parse project_id from job outputs or filesystem"
fi

ok "project_id=$PID"
echo
echo "Try edit now with:"
cat <<CMD
curl -sS -H 'Content-Type: application/json' -X POST "$API/v1/jobs" -d @- <<'JSON'
{
  "service_id": "slides.edit",
  "inputs": {
    "project_id": "$PID",
    "slide_no": 2,
    "patch": {
      "title": "Green Hydrogen: Costs 2025",
      "bullets": ["CapEx falling ~10-15%/yr","Electrolyzer efficiency improving","Policy incentives expanding"],
      "template": "text_image_right"
    }
  }
}
JSON
CMD
