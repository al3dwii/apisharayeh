#!/usr/bin/env bash
set -euo pipefail

# scripts/dev-worker.sh
# Start a Celery worker for the dev stack, auto-detecting the Celery app instance.

# --- repo root / venv --------------------------------------------------------
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
[[ -d .venv ]] && source .venv/bin/activate
[[ -f .env ]] && set -a && source .env && set +a

# --- env ---------------------------------------------------------------------
export PYTHONPATH="${PYTHONPATH:-}:${ROOT_DIR}/src"

# Local defaults if not provided in .env
export PLUGINS_DIR="${PLUGINS_DIR:-${ROOT_DIR}/plugins}"
export ARTIFACTS_DIR="${ARTIFACTS_DIR:-${ROOT_DIR}/artifacts}"
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://agentic:agentic@localhost:5432/agentic}"
export REDIS_URL_QUEUE="${REDIS_URL_QUEUE:-redis://localhost:6379/1}"
export CELERY_BROKER_URL="${CELERY_BROKER_URL:-${REDIS_URL_QUEUE}}"
export CELERY_RESULT_BACKEND="${CELERY_RESULT_BACKEND:-${REDIS_URL_QUEUE}}"

mkdir -p "${ARTIFACTS_DIR}"

echo "[dev-worker] environment"
echo "  PYTHONPATH=${PYTHONPATH}"
echo "  DATABASE_URL=${DATABASE_URL}"
echo "  REDIS_URL_QUEUE=${REDIS_URL_QUEUE}"
echo "  PLUGINS_DIR=${PLUGINS_DIR}"
echo "  ARTIFACTS_DIR=${ARTIFACTS_DIR}"
echo

# --- auto-discover a Celery app instance ------------------------------------
FOUND_APP="$(
PYTHONPATH="${PYTHONPATH}" python - <<'PY' 2>/dev/null || true
import importlib
from typing import Optional

candidates = [
    # most likely first
    "app.workers.celery_app",
    # other common fallbacks
    "app.workers.celery",
    "app.worker.celery_app",
    "app.celery_app",
    "celery_app",
    "worker.celery_app",
]

def find_app_ref(modname: str) -> Optional[str]:
    try:
        m = importlib.import_module(modname)
    except Exception:
        return None
    # try common attribute names first
    for name in ("celery_app", "app", "celery"):
        if hasattr(m, name):
            obj = getattr(m, name)
            # avoid importing celery just to isinstance-check; duck-type via attribute
            if hasattr(obj, "send_task") and hasattr(obj, "worker_main"):
                return f"{modname}:{name}"
    # fallback: scan for any Celery-like object
    for name, obj in m.__dict__.items():
        if hasattr(obj, "send_task") and hasattr(obj, "worker_main"):
            return f"{modname}:{name}"
    return None

for mod in candidates:
    ref = find_app_ref(mod)
    if ref:
        print(ref)
        break
PY
)"

if [[ -z "${FOUND_APP}" ]]; then
  echo "[dev-worker] ERROR: could not locate a Celery app instance."
  echo "Looked in these modules for variables named celery_app/app/celery:"
  cat <<LIST
  - app.workers.celery_app
  - app.workers.celery
  - app.worker.celery_app
  - app.celery_app
  - celery_app
  - worker.celery_app
LIST
  echo
  echo "Project tree (celery*):"
  (cd src && find app -maxdepth 3 -type f -iname "celery*.py" -print 2>/dev/null || true)
  exit 1
fi

echo "[dev-worker] using Celery app: ${FOUND_APP}"
echo

# --- start worker ------------------------------------------------------------
# Use solo pool on macOS to avoid fork issues
exec celery -A "${FOUND_APP}" worker \
  --loglevel=INFO \
  --pool=solo \
  -Q cpu,io,llm,default \
  --hostname=worker@%h
