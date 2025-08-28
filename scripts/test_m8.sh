# scripts/test_m8.sh
#!/usr/bin/env bash
set -Eeuo pipefail

API="${API_URL:-http://localhost:8081}"
SOFFICE="${SOFFICE_BIN:-}"

echo "[env] API=$API"
if [[ -z "${SOFFICE:-}" ]]; then
  echo "[warn] SOFFICE_BIN not set; relying on PATH."
fi

PROJECT_ID="prj_$(date +%s)"
REQ=$(jq -nc --arg pid "$PROJECT_ID" '
  { service_id:"slides.generate"
  , inputs:
    { project_id:$pid
    , source:"prompt"
    , topic:"M8 export smoke"
    , language:"en"
    , export:["pdf","html"]
    }
  }')

echo "[submit] slides.generate -> $PROJECT_ID"
SUBMIT=$(curl -fsS -H 'Content-Type: application/json' -d "$REQ" "$API/v1/jobs")
JOB_ID=$(jq -r '.id // .job_id' <<<"$SUBMIT")
echo "[job] $JOB_ID"

echo "[wait] polling job status…"
for _ in $(seq 1 180); do
  JS=$(curl -fsS "$API/v1/jobs/$JOB_ID")
  ST=$(jq -r '.status // .state' <<<"$JS")
  echo "  status=$ST"
  [[ "$ST" =~ ^(succeeded|completed)$ ]] && break
  [[ "$ST" =~ ^(failed|error)$ ]] && { echo "$JS" | jq .; exit 1; }
  sleep 1
done

JOB_JSON=$(curl -fsS "$API/v1/jobs/$JOB_ID")

# 1) derive PROJECT_ID from state_url (needed for fallbacks)
PROJECT_ID=$(jq -r '
  (.outputs.state_url // .output.state_url // .result.state_url) 
  | capture("/artifacts/(?<pid>[^/]+)/").pid
' <<<"$JOB_JSON")

# 2) collect URLs from outputs (any shape), else fall back to known paths
PPTX_URL=$(jq -r '.outputs.pptx_url // .output.pptx_url // .result.pptx_url // empty' <<<"$JOB_JSON")
PDF_URL=$(jq  -r '.outputs.pdf_url  // .output.pdf_url  // .result.pdf_url  // empty' <<<"$JOB_JSON")
HTML_ZIP_URL=$(jq -r '.outputs.html_zip_url // .output.html_zip_url // .result.html_zip_url // empty' <<<"$JOB_JSON")

# fallbacks (now PROJECT_ID is set, so set -u is safe)
[[ -z "$PPTX_URL" ]] && PPTX_URL="/artifacts/$PROJECT_ID/export/presentation.pptx"
[[ -z "$PDF_URL"  ]] && PDF_URL="/artifacts/$PROJECT_ID/presentation.pdf"
[[ -z "$HTML_ZIP_URL" ]] && HTML_ZIP_URL="/artifacts/$PROJECT_ID/html.zip"

# helper: ensure absolute
abs() { case "$1" in http://*|https://*) echo "$1";; *) echo "$API$1";; esac; }

echo "[verify] HEAD artifacts:"
for U in "$PPTX_URL" "$PDF_URL" "$HTML_ZIP_URL"; do
  A=$(abs "$U")
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -I "$A")
  echo "  $A -> $CODE"
  [[ "$CODE" == "200" ]] || { echo "[fail] not accessible: $A"; exit 1; }
done

# optional: print the root index alias
INDEX_URL=$(jq -r '.outputs.index_url // .output.index_url // .result.index_url // empty' <<<"$JOB_JSON")
[[ -n "$INDEX_URL" ]] && echo "[index] $(abs "$INDEX_URL")"

echo "[pass] M8 OK"
