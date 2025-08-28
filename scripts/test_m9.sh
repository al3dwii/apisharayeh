# scripts/test_m9.sh  (fix absolute URL handling)
#!/usr/bin/env bash
set -Eeuo pipefail

API="${API_URL:-http://localhost:8081}"

echo "[check] services contain slides.generate + slides.author"
SVC=$(curl -fsS "$API/v1/services" | jq -r '.services[].id' | sort)
echo "$SVC"
grep -q '^slides.generate$' <<<"$SVC" || { echo "[fail] slides.generate not loaded"; exit 1; }
grep -q '^slides.author$'   <<<"$SVC" || { echo "[fail] slides.author not loaded"; exit 1; }

PROJECT_ID="prj_$(date +%s)"
REQ=$(jq -nc --arg pid "$PROJECT_ID" '
  { service_id:"slides.generate"
  , inputs:{ project_id:$pid, source:"prompt", topic:"M9 index test", language:"en" }
  }')

echo "[submit] $PROJECT_ID"
JOB=$(curl -fsS -H 'Content-Type: application/json' -d "$REQ" "$API/v1/jobs")
JOB_ID=$(jq -r '.id // .job_id' <<<"$JOB")
echo "[job] $JOB_ID"

echo "[wait] job…"
for _ in $(seq 1 120); do
  JS=$(curl -fsS "$API/v1/jobs/$JOB_ID")
  ST=$(jq -r '.status // .state' <<<"$JS")
  echo "  status=$ST"
  [[ "$ST" =~ ^(succeeded|completed)$ ]] && break
  [[ "$ST" =~ ^(failed|error)$ ]] && { echo "$JS" | jq .; exit 1; }
  sleep 1
done

JS=$(curl -fsS "$API/v1/jobs/$JOB_ID")
PROJECT_ID=$(jq -r '(.output.state_url // .outputs.state_url // .result.state_url)
  | capture("/artifacts/(?<pid>[^/]+)/").pid' <<<"$JS")

INDEX_URL=$(jq -r '.output.index_url // .outputs.index_url // .result.index_url // empty' <<<"$JS")
[[ -z "$INDEX_URL" ]] && INDEX_URL="/artifacts/$PROJECT_ID/index.html"
SLIDES_INDEX="/artifacts/$PROJECT_ID/slides/index.html"

abs() { case "$1" in http://*|https://*) echo "$1";; *) echo "$API$1";; esac; }
head_ok() { curl -s -o /dev/null -w '%{http_code}' -I "$1"; }

IU=$(abs "$INDEX_URL")
SU=$(abs "$SLIDES_INDEX")

echo "[verify] index pages:"
echo "  $IU -> $(head_ok "$IU")"
echo "  $SU -> $(head_ok "$SU")"

[[ "$(head_ok "$IU")" == "200" ]] || { 
  echo "[fail] root index missing"; 
  echo "[diag] project files:"; curl -fsS "$API/v1/projects/$PROJECT_ID/artifacts" | jq .
  exit 1; 
}
[[ "$(head_ok "$SU")" == "200" ]] || { echo "[fail] slides/index.html missing"; exit 1; }

echo "[pass] M9 OK  (plugins loaded, index reachable)"
