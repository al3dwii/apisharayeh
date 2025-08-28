#!/usr/bin/env bash
set -Eeuo pipefail
API="${API_URL:-http://localhost:8081}"

# 1) Ensure you have a deck. Quick path: reuse your M1 script to create one.
if [[ -z "${PROJECT_ID:-}" ]]; then
  echo "[info] Launching slides.generate to create a project…"
  PROJECT_ID="prj_$(date +%s)"
  REQ="$(jq -nc --arg pid "$PROJECT_ID" '{service_id:"slides.generate",inputs:{project_id:$pid,topic:"Test deck M7",language:"en"}}')"
  JOB_ID="$(curl -fsS -H 'Content-Type: application/json' -d "$REQ" "$API/v1/jobs" | jq -r '.job_id')"
  for _ in $(seq 1 90); do
    JS="$(curl -fsS "$API/v1/jobs/$JOB_ID")"
    ST="$(jq -r '.status // .state' <<<"$JS")"
    [[ "$ST" == "succeeded" || "$ST" == "completed" ]] && break
    [[ "$ST" == "failed" || "$ST" == "error" ]] && { echo "$JS" | jq .; exit 1; }
    sleep 1
  done
fi
echo "[ok] PROJECT_ID=$PROJECT_ID"

# 2) List slides (baseline)
echo "[probe] list slides"
curl -fsS "$API/v1/projects/$PROJECT_ID/slides" | jq -r '.slides[] | "\(.no): \(.title)"'

# 3) BULK PATCH: insert + move + replace + delete
BODY="$(jq -nc '
  {ops:[
    {op:"insert", at:2, slide:{title:"Agenda", bullets:["A","B","C"]}},
    {op:"move",   frm:1, to:3},
    {op:"replace",no:3,  patch:{title:"Updated Slide 3", bullets:["new point 1","new point 2"]}},
    {op:"delete", no:5}
  ]}'
)"
echo "[edit] PATCH /v1/projects/$PROJECT_ID/slides"
RESP_HEADERS="$(mktemp)"
curl -fsS -D "$RESP_HEADERS" -H 'Content-Type: application/json' \
  -X PATCH -d "$BODY" "$API/v1/projects/$PROJECT_ID/slides" | jq .

ETAG_AFTER="$(awk -F': ' 'tolower($1)=="etag"{print $2}' "$RESP_HEADERS" | tr -d '\r\n')"
ETAG_BEFORE="$(awk -F': ' 'tolower($1)=="etag-before"{print $2}' "$RESP_HEADERS" | tr -d '\r\n')"
echo "[etag] before=$ETAG_BEFORE after=$ETAG_AFTER"

# 4) Verify state.json exists and slides renumbered
echo "[probe] state.json"
curl -fsS "$API/artifacts/$PROJECT_ID/state.json" | jq '.slides | map({no,title})'

# 5) PUT with ETag (optimistic update)
PATCH_ONE='{"title":"PUT-updated title","bullets":["x","y","z"]}'
echo "[edit] PUT /v1/projects/$PROJECT_ID/slides/2 with If-Match"
curl -fsS -D /dev/stderr -H 'Content-Type: application/json' \
  -H "If-Match: $ETAG_AFTER" \
  -X PUT -d "$PATCH_ONE" "$API/v1/projects/$PROJECT_ID/slides/2" | jq .

# 6) Negative: wrong ETag → expect 412
echo "[edit] PUT (wrong ETag) expect 412"
set +e
curl -s -o /dev/null -w "%{http_code}\n" \
  -H 'Content-Type: application/json' -H "If-Match: deadbeef" \
  -X PUT -d "$PATCH_ONE" "$API/v1/projects/$PROJECT_ID/slides/2" | grep -q '^412$' && echo "[ok] 412" || { echo "[fail] expected 412"; exit 1; }
set -e

# 7) Final list to eyeball
echo "[probe] final slides"
curl -fsS "$API/v1/projects/$PROJECT_ID/slides" | jq -r '.slides[] | "\(.no): \(.title)"'

echo "[pass] M7 edit API looks good for $PROJECT_ID"
