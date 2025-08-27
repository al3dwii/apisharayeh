#!/usr/bin/env bash
set -euo pipefail
API="${API:-http://localhost:8081}"

echo "[probe] $API/health"
curl -fsS "$API/health" >/dev/null

PROJECT="prj_$(date +%s)"
REQ="$(jq -nc --arg pid "$PROJECT" '
{
  service_id: "slides.generate",
  inputs: {
    project_id: $pid,
    source: "research",
    topic: "Future of Green Hydrogen",
    language: "en",
    slides_count: 8,
    max_docs: 6
  }
}')"

echo "[submit] slides.generate (research)"
JOB_ID="$(curl -fsS -H 'Content-Type: application/json' -d "$REQ" "$API/v1/jobs" | jq -r .id)"
echo "JOB_ID=$JOB_ID"

echo "[wait]"
for _ in $(seq 1 180); do
  ST="$(curl -fsS "$API/v1/jobs/$JOB_ID" | jq -r '.status // .state')"
  echo "  status: $ST"
  [[ "$ST" == "failed" ]] && curl -fsS "$API/v1/jobs/$JOB_ID" | jq . && exit 1
  [[ "$ST" == "succeeded" || "$ST" == "completed" ]] && break
  sleep 2
done

RES="$(curl -fsS "$API/v1/jobs/$JOB_ID")"
PDF="$(echo "$RES" | jq -r '.output.pdf_url // .result.outputs.pdf_url // empty')"
PPTX="$(echo "$RES" | jq -r '.output.pptx_url // .result.outputs.pptx_url // empty')"
INDEX="$(echo "$RES" | jq -r '.output.index_url // .result.outputs.index_url // empty')"
STATE="$(echo "$RES" | jq -r '.output.state_url // .result.outputs.state_url // empty')"

echo "PDF:  $PDF"
echo "PPTX: $PPTX"
echo "INDEX: $INDEX"
echo "STATE: $STATE"

[[ -n "$INDEX" ]] && curl -fsS "$INDEX" >/dev/null && echo "[ok] index reachable"
