#!/usr/bin/env bash
set -euo pipefail

# --- Config / Args -----------------------------------------------------------
DOCX_PATH_ARG="${1:-}"           # pass absolute path to your .docx as the first arg
OPEN_BROWSER="${OPEN_BROWSER:-0}" # set to 1 to auto-open the PDF/first slide
PORT="${PORT:-8000}"
HOST="http://localhost:${PORT}"

# --- Resolve project root & venv --------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

# --- Preflight checks --------------------------------------------------------
command -v python >/dev/null || { echo "python not found"; exit 1; }
command -v curl >/dev/null || { echo "curl not found"; exit 1; }
command -v jq   >/dev/null || { echo "jq not found (brew install jq)"; exit 1; }

if [[ -z "${DOCX_PATH_ARG}" ]]; then
  echo "Usage: $0 /ABS/PATH/TO/file.docx"
  echo "Tip: export OPEN_BROWSER=1 to auto-open the results."
  exit 2
fi
if [[ ! -f "${DOCX_PATH_ARG}" ]]; then
  echo "DOCX not found at: ${DOCX_PATH_ARG}"
  exit 2
fi

# --- Environment for server --------------------------------------------------
export DEV_OFFLINE=true
export PLUGINS_DIR="${ROOT_DIR}/plugins"
export ARTIFACTS_DIR="${ROOT_DIR}/artifacts"
mkdir -p "${ARTIFACTS_DIR}" "${ROOT_DIR}/logs"

echo "ENV:"
echo "  PLUGINS_DIR=${PLUGINS_DIR}"
echo "  ARTIFACTS_DIR=${ARTIFACTS_DIR}"
echo "  DEV_OFFLINE=${DEV_OFFLINE}"
echo

# --- Stop any old server on the port ----------------------------------------
echo "Stopping anything on :${PORT}…"
if lsof -ti tcp:"${PORT}" >/dev/null; then
  lsof -ti tcp:"${PORT}" | xargs kill -9 || true
fi
# also kill old run_server processes (best-effort)
pkill -f "python -m src.run_server" 2>/dev/null || true
sleep 1

# --- Start server ------------------------------------------------------------
echo "Starting server…"
nohup python -m src.run_server > "logs/server.out" 2>&1 & echo $! > "logs/server.pid"
SRV_PID="$(cat logs/server.pid)"
echo "Server PID: ${SRV_PID}"

# --- Wait for /healthz -------------------------------------------------------
echo -n "Waiting for /healthz "
ATTEMPTS=60
until curl -fsS "${HOST}/healthz" >/dev/null 2>&1; do
  ((ATTEMPTS--)) || { echo; echo "Server didn't become healthy. Tail logs/server.out"; tail -n 200 logs/server.out; exit 1; }
  echo -n "."
  sleep 1
done
echo " OK"

# --- Verify slides.generate is registered -----------------------------------
echo "Checking service registry…"
if ! curl -fsS "${HOST}/v1/services/slides.generate" >/dev/null 2>&1; then
  echo "slides.generate not found. Listing services:"
  curl -fsS "${HOST}/v1/services" | jq .
  echo "Fix your plugin manifest/flow, then re-run."
  exit 1
fi
echo "slides.generate is available."

# --- Upload DOCX -------------------------------------------------------------
echo "Uploading DOCX: ${DOCX_PATH_ARG}"
UPLOAD_JSON="$(curl -fsS -F "file=@${DOCX_PATH_ARG}" "${HOST}/v1/uploads")"
echo "${UPLOAD_JSON}" | jq .
PRJ="$(echo "${UPLOAD_JSON}"     | jq -r .project_id)"
DOCX_ABS_PATH="$(echo "${UPLOAD_JSON}" | jq -r .path)"

if [[ -z "${PRJ}" || -z "${DOCX_ABS_PATH}" || "${PRJ}" == "null" || "${DOCX_ABS_PATH}" == "null" ]]; then
  echo "Upload failed or missing fields. Response:"
  echo "${UPLOAD_JSON}"
  exit 1
fi

# --- Kick off job ------------------------------------------------------------
echo "Starting slides.generate job for project ${PRJ}…"
JOB_JSON="$(curl -fsS -X POST "${HOST}/v1/services/slides.generate/jobs?validate=1" \
  -H "Content-Type: application/json" \
  -d "{
        \"inputs\":{
          \"source\":\"docx\",
          \"docx_url\":\"${DOCX_ABS_PATH}\",
          \"language\":\"ar\",
          \"slides_count\":12,
          \"project_id\":\"${PRJ}\"
        }
      }")"
echo "${JOB_JSON}" | jq .
JOB_ID="$(echo "${JOB_JSON}" | jq -r .job_id)"
if [[ -z "${JOB_ID}" || "${JOB_ID}" == "null" ]]; then
  echo "Failed to create job. Response above."
  exit 1
fi

# --- Wait for job to complete ------------------------------------------------
echo -n "Waiting for job ${JOB_ID} to finish "
ATTEMPTS=180
STATUS="running"
while [[ "${STATUS}" == "running" || "${STATUS}" == "queued" ]]; do
  ((ATTEMPTS--)) || { echo; echo "Timed out waiting for job. Current:"; curl -fsS "${HOST}/v1/jobs/${JOB_ID}" | jq .; echo; echo "Server logs:"; tail -n 200 logs/server.out; exit 1; }
  STATUS_JSON="$(curl -fsS "${HOST}/v1/jobs/${JOB_ID}")"
  STATUS="$(echo "${STATUS_JSON}" | jq -r .status)"
  echo -n "."
  sleep 1
done
echo " ${STATUS}"

if [[ "${STATUS}" != "succeeded" ]]; then
  echo "Job failed. Full record:"
  echo "${STATUS_JSON}" | jq .
  echo
  echo "Tail of server logs:"
  tail -n 200 logs/server.out
  exit 1
fi

# --- Fetch artifacts ---------------------------------------------------------
ART_JSON="$(curl -fsS "${HOST}/v1/jobs/${JOB_ID}/artifacts")"
echo "Artifacts:"
echo "${ART_JSON}" | jq .

PDF_URL="$(echo "${ART_JSON}" | jq -r .exports.pdf_url)"
FIRST_SLIDE_URL="$(echo "${ART_JSON}" | jq -r '.slides[0].url // empty')"

if [[ -n "${PDF_URL}" && "${PDF_URL}" != "null" ]]; then
  echo "PDF: ${HOST}${PDF_URL}"
fi
if [[ -n "${FIRST_SLIDE_URL}" && "${FIRST_SLIDE_URL}" != "null" ]]; then
  echo "First slide: ${HOST}${FIRST_SLIDE_URL}"
fi

# --- Optionally open in browser ---------------------------------------------
if [[ "${OPEN_BROWSER}" == "1" ]]; then
  if [[ -n "${PDF_URL}" && "${PDF_URL}" != "null" ]]; then
    open "${HOST}${PDF_URL}" || true
  fi
  if [[ -n "${FIRST_SLIDE_URL}" && "${FIRST_SLIDE_URL}" != "null" ]]; then
    open "${HOST}${FIRST_SLIDE_URL}" || true
  fi
fi

echo
echo "Done. 🎉"

