#!/usr/bin/env bash
# Dev API launcher with strict diagnostics, .env export, and PYTHONPATH=src
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8081}"
HOST="${HOST:-0.0.0.0}"
API_RELOAD="${API_RELOAD:-0}"        # 0 for smoke, 1 for local hot-reload
KILL_PORT_INUSE="${KILL_PORT_INUSE:-1}"

timestamp(){ date +"%Y-%m-%d %H:%M:%S"; }

# 1) Ensure venv
if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

# 2) Export .env (make variables exported, not just available to python-dotenv)
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# 3) Ensure PYTHONPATH so "import app" works
export PYTHONPATH="src:${PYTHONPATH:-}"

echo "[api] $(timestamp) PWD=$(pwd)"
echo "[api] $(timestamp) PYTHONPATH=${PYTHONPATH}"
echo "[api] $(timestamp) DATABASE_URL=${DATABASE_URL:-}"
echo "[api] $(timestamp) REDIS_URL=${REDIS_URL:-}"
echo "[api] $(timestamp) PLUGINS_DIR=${PLUGINS_DIR:-}"
echo "[api] $(timestamp) ARTIFACTS_DIR=${ARTIFACTS_DIR:-}"
echo "[api] $(timestamp) SERVICE_FLAGS=${SERVICE_FLAGS:-}"
echo "[api] $(timestamp) PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-}"
echo "[api] $(timestamp) SOFFICE_BIN=${SOFFICE_BIN:-}"
echo "[api] $(timestamp) preflight: importing app.main ..."

# Optional filesystem hints (don’t fail if missing)
if [[ -d src ]]; then
  echo "[api] $(timestamp) src/ exists; listing packages:"
  ls -la src | sed 's/^/[api]  /'
  [[ -d src/app ]] && ls -la src/app | sed 's/^/[api]  /' || echo "[api]   (no src/app dir?)"
fi

# 4) Quick Python import probe to catch issues early, with sys.path dump
python - <<'PY' || true
import sys, os, json
print("[api] python:", sys.version)
print("[api] sys.path:", json.dumps(sys.path, indent=2))
try:
    import app
    from app import main  # noqa: F401
    print("[api] preflight import: OK")
except Exception as e:
    print("[api] preflight import: FAILED:", repr(e))
    import traceback; traceback.print_exc()
PY

# 5) Port diagnostics & cleanup (avoid reloader/race double-binds)
if command -v lsof >/dev/null 2>&1; then
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[api] $(timestamp) Port ${PORT} is already in use by:"
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
    if [[ "$KILL_PORT_INUSE" == "1" ]]; then
      echo "[api] $(timestamp) Killing listeners on ${PORT}..."
      lsof -tiTCP:"$PORT" -sTCP:LISTEN | xargs -r kill -9 || true
      sleep 0.5
    else
      echo "[api] $(timestamp) Refusing to start because port is busy."
      exit 2
    fi
  fi
else
  echo "[api] $(timestamp) WARNING: lsof not found; cannot pre-check port ${PORT}"
fi

# 6) Run uvicorn (no reload for smoke by default)
if [[ "$API_RELOAD" == "1" ]]; then
  exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
else
  exec uvicorn app.main:app --host "$HOST" --port "$PORT"
fi
