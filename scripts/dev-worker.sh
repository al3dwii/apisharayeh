#!/usr/bin/env bash
# Dev Celery worker with .env export and PYTHONPATH=src
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
export PYTHONPATH="src:${PYTHONPATH:-}"

echo "[dev-worker] environment"
echo "  PYTHONPATH=${PYTHONPATH}"
echo "  DATABASE_URL=${DATABASE_URL:-}"
echo "  REDIS_URL_QUEUE=${REDIS_URL_QUEUE:-}"
echo "  PLUGINS_DIR=${PLUGINS_DIR:-}"
echo "  ARTIFACTS_DIR=${ARTIFACTS_DIR:-}"
echo "  SOFFICE_BIN=${SOFFICE_BIN:-}"
echo

# Probe import so we fail fast if path/env is wrong
python - <<'PY' || true
import sys
print("[worker] python OK, path entries:", len(sys.path))
try:
    import app
    print("[worker] import app: OK")
except Exception as e:
    print("[worker] import app: FAILED:", e)
PY

# Concurrency=1 keeps logs ordered during smoke tests
exec celery -A app.workers.celery_app worker -Q cpu,io,llm,default --concurrency=1 --loglevel=INFO
