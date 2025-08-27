#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

API="${API_URL:-http://localhost:8081}"

echo "[probe] $API/health"
curl -fsS "$API/health" >/dev/null

# Submit slides.generate
echo "[submit] slides.generate"
PROJECT_ID="prj_$(date +%s)"
REQ="$(jq -nc --arg pid "$PROJECT_ID" '
  { service_id:"slides.generate",
    inputs:{
      project_id:$pid,
      source:"prompt",
      topic:"Future of Green Hydrogen",
      language:"en",
      slides_count:8,
      theme:"academic-ar"
    }}')"

JOB_ID="$(curl -fsS -H 'Content-Type: application/json' -d "$REQ" "$API/v1/jobs" | jq -r .id)"
echo "JOB_ID=$JOB_ID"

echo "[wait]"
for _ in $(seq 1 120); do
  JS="$(curl -fsS "$API/v1/jobs/$JOB_ID")" || true
  ST="$(jq -r '.status // .state // empty' <<<"$JS")"
  echo "  status: $ST"
  case "$ST" in
    succeeded|completed) break ;;
    failed|error) echo "$JS" | jq .; exit 1 ;;
  esac
  sleep 2
done

OUT="$(curl -fsS "$API/v1/jobs/$JOB_ID")"
PDF_URL="$(jq -r '.output.pdf_url // .result.outputs.pdf_url // empty' <<<"$OUT")"
PPTX_URL="$(jq -r '.output.pptx_url // .result.outputs.pptx_url // empty' <<<"$OUT")"
INDEX_URL="$(jq -r '.output.index_url // .result.outputs.index_url // empty' <<<"$OUT")"
FIRST_SLIDE="$(jq -r '(.output.slides_html // .result.outputs.slides_html // []) | .[0] // empty' <<<"$OUT")"

# Print artifacts
echo "PDF:  ${PDF_URL:-<none>}"
echo "PPTX: ${PPTX_URL:-<none>}"

# If index_url exists, use it; else derive from first slide without breaking if empty
if [[ -z "${INDEX_URL}" ]]; then
  if [[ -n "${FIRST_SLIDE}" ]]; then
    BASE="${FIRST_SLIDE%/*}"
    INDEX_URL="${BASE}/index.html"
  fi
fi

if [[ -n "${INDEX_URL}" ]]; then
  echo "INDEX: ${INDEX_URL}"
  # Don’t error out if the index isn’t generated (older flow); just report status
  if curl -fsS --head "${INDEX_URL}" >/dev/null 2>&1; then
    echo "[ok] index reachable"
  else
    echo "[warn] index not reachable (flow may be older), continuing.."
  fi
else
  echo "INDEX: <none>"
fi
