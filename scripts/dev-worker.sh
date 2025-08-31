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

export PYTHONPATH="${PYTHONPATH:-src}:$ROOT/src"

python - <<'PY' || true
import sys
print("[worker] python OK, path entries:", len(sys.path))
try:
    import app
    print("[worker] import app: OK")
except Exception as e:
    print("[worker] import app: FAILED:", e)
PY

# ✅ Listen on 'media' as well (this was missing)
exec celery -A app.workers.celery_app worker -Q media,cpu,io,llm,default --concurrency=1 --loglevel=INFO
