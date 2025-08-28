#!/usr/bin/env bash
set -euo pipefail

API="${API_URL:-http://localhost:8081}"
TENANT_HEADER=(-H "x-tenant: demo-tenant")

echo "[check] health"
curl -fsS "${API}/health" >/dev/null || { echo "API not reachable at $API"; exit 1; }

# Optional: plugin sync (admin-protected). Skip if no ADMIN_SECRET.
if [[ -n "${ADMIN_SECRET:-}" ]]; then
  echo "[plugins] syncing for demo-tenant…"
  curl -fsS -X POST -H "x-admin-secret: ${ADMIN_SECRET}" \
       -H "x-tenant: demo-tenant" \
       "${API}/v1/plugins/sync" || echo "[warn] /v1/plugins/sync failed (check ADMIN_SECRET)"
else
  echo "[plugins] skipping /v1/plugins/sync (no ADMIN_SECRET set)"
fi

echo "[check] services contain slides.author"
SVCS=$(curl -fsS "${API}/v1/services" "${TENANT_HEADER[@]}")
echo "$SVCS" | jq -r '.services[].id' | tee /tmp/svcs.txt >/dev/null
grep -q '^slides.author$' /tmp/svcs.txt || { echo "[fail] slides.author not enabled for tenant"; exit 1; }

PROJECT_ID="prj_$(date +%s)"
echo "[submit] ${PROJECT_ID}"

REQ=$(jq -nc --arg pid "$PROJECT_ID" '{
  service_id: "slides.author",
  inputs: {
    project_id: $pid,
    topic: "Responsible AI",
    use_research: true,
    urls: [
      "https://developers.google.com/machine-learning/responsible-ai",
      "https://www.ibm.com/think/topics/ai-ethics"
    ],
    language: "en",
    max_docs: 4,
    export: ["html"]
  }
}')

SUBMIT=$(mktemp)
if ! curl -sS -D /tmp/submit.h \
         -H 'Content-Type: application/json' \
         "${TENANT_HEADER[@]}" \
         -d "$REQ" \
         "${API}/v1/jobs" -o "$SUBMIT"; then
  echo "[error] submit failed"
  echo "== request =="; echo "$REQ"
  echo "== response headers =="; cat /tmp/submit.h
  echo "== response body =="; cat "$SUBMIT"
  exit 1
fi

JOB_ID=$(jq -r '.id // .job_id' "$SUBMIT")
echo "[job] ${JOB_ID:-<none>}"
if [[ -z "${JOB_ID}" || "$JOB_ID" == "null" ]]; then
  echo "[error] no job id. Response:"
  cat "$SUBMIT"
  exit 1
fi

echo "[wait] job…"
for _ in $(seq 1 180); do
  ST=$(curl -fsS "${API}/v1/jobs/${JOB_ID}" "${TENANT_HEADER[@]}" | jq -r '.status // .state')
  echo "  status=$ST"
  if [[ "$ST" =~ ^(succeeded|completed)$ ]]; then break; fi
  if [[ "$ST" =~ ^(failed|error)$ ]]; then
    echo "[fail] job failed:"
    curl -s "${API}/v1/jobs/${JOB_ID}" "${TENANT_HEADER[@]}" | jq .
    exit 1
  fi
  sleep 1
done

OUT=$(curl -fsS "${API}/v1/jobs/${JOB_ID}" "${TENANT_HEADER[@]}")
PROJECT_ID=$(jq -r '(.output.state_url // .outputs.state_url // .result.state_url) | capture("/artifacts/(?<pid>[^/]+)/").pid' <<<"$OUT")
echo "[proj] $PROJECT_ID"

INDEX_URL=$(jq -r '.output.index_url // .result.index_url // empty' <<<"$OUT")
[[ -z "$INDEX_URL" ]] && INDEX_URL="$API/artifacts/$PROJECT_ID/index.html"

echo "[verify] index pages:"
for U in \
  "$INDEX_URL" \
  "$API/artifacts/$PROJECT_ID/slides/index.html"; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -I "$U")
  echo "  $U -> $CODE"
done
