#!/usr/bin/env bash
set -euo pipefail

API="${API_URL:-http://localhost:8081}"
TENANT="${TENANT:-demo-tenant}"

die(){ echo "ERR:" "$@" >&2; exit 1; }

post_job() { # $1=json
  curl -sS -H 'Content-Type: application/json' -H "x-tenant: $TENANT" -d "$1" "$API/v1/jobs"
}

wait_job(){ # $1=job_id
  local id="$1" s=""
  while true; do
    s=$(curl -sS -H "x-tenant: $TENANT" "$API/v1/jobs/$id" | jq -r .status)
    [[ "$s" == "succeeded" || "$s" == "failed" ]] && break
    echo "  status=$s"; sleep 1
  done
  echo "$s"
}

echo "[1] seed a deck via slides.generate"
PID="prj_$(date +%s)"
REQ_GEN=$(jq -nc --arg pid "$PID" '{
  service_id:"slides.generate",
  inputs:{
    project_id:$pid,
    source:"prompt",
    prompt:"M14 live-edit smoke",
    language:"en",
    slides_count:6,
    theme:"academic-ar",
    export:[]
  }
}')
JGEN=$(post_job "$REQ_GEN")
JID_GEN=$(echo "$JGEN" | jq -r .id)
[[ "$JID_GEN" != "null" ]] || { echo "$JGEN"; die "job create failed"; }
echo "  job=$JID_GEN"
[[ "$(wait_job "$JID_GEN")" == "succeeded" ]] || die "seed failed"

echo "[2] apply an edit to slide #3 via slides.edit"

# Try a few known input shapes; stop at first success
try_payloads=()

# Shape A: flat fields (most trees)
try_payloads+=("$(jq -nc --arg pid "$PID" '{
  service_id:"slides.edit",
  inputs:{ project_id:$pid, no:3, title:"Edited: Live update",
           bullets:["Point A","Point B","Point C"], note:"Edited in M14 smoke" }
}')")

# Shape B: update object wrapper
try_payloads+=("$(jq -nc --arg pid "$PID" '{
  service_id:"slides.edit",
  inputs:{ project_id:$pid, no:3,
           update:{ title:"Edited: Live update",
                    bullets:["Point A","Point B","Point C"],
                    note:"Edited in M14 smoke" } }
}')")

# Shape C: slide_no instead of no
try_payloads+=("$(jq -nc --arg pid "$PID" '{
  service_id:"slides.edit",
  inputs:{ project_id:$pid, slide_no:3, title:"Edited: Live update",
           bullets:["Point A","Point B","Point C"], note:"Edited in M14 smoke" }
}')")

# Shape D: bullets as objects
try_payloads+=("$(jq -nc --arg pid "$PID" '{
  service_id:"slides.edit",
  inputs:{ project_id:$pid, no:3, title:"Edited: Live update",
           bullets:[{text:"Point A"},{text:"Point B"},{text:"Point C"}],
           note:"Edited in M14 smoke" }
}')")

success_job=""
for P in "${try_payloads[@]}"; do
  echo "  → trying payload variant…"
  # Show error body if it 4xx’s
  HTTP=$(curl -s -o /tmp/edit.res -w '%{http_code}' \
               -H 'Content-Type: application/json' -H "x-tenant: $TENANT" \
               -d "$P" "$API/v1/jobs")
  if [[ "$HTTP" != "200" ]]; then
    echo "    server HTTP=$HTTP"
    cat /tmp/edit.res; echo
    continue
  fi
  JID_EDIT=$(cat /tmp/edit.res | jq -r .id)
  if [[ "$JID_EDIT" == "null" || -z "$JID_EDIT" ]]; then
    echo "    no job id in response:"; cat /tmp/edit.res; echo
    continue
  fi
  st=$(wait_job "$JID_EDIT")
  echo "    result=$st"
  if [[ "$st" == "succeeded" ]]; then
    success_job="$JID_EDIT"
    break
  fi
done

[[ -n "$success_job" ]] || die "all payload variants failed; see error bodies above."

echo "[3] verify HTML re-rendered"
SLIDE_URL="$API/artifacts/$PID/slides/003.html"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -I "$SLIDE_URL")
echo "  $SLIDE_URL -> $CODE"
curl -fsS "$SLIDE_URL" | grep -q "Edited: Live update" && echo "[ok] title present" || die "title not found in slide HTML"

echo "[done] Open the viewer:"
echo "       $API/artifacts/$PID/slides/index.html"
