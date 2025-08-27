#!/usr/bin/env bash
set -euo pipefail

# Starts FastAPI dev server, auto-detecting where main.py is.
# Usage: scripts/dev_api.sh [PORT]
PORT="${1:-8000}"

die(){ echo "ERROR: $*" >&2; exit 1; }

# Find main.py in common layouts
if [[ -f "src/app/main.py" ]]; then
  export PYTHONPATH="$(pwd)/src"
  APP_PATH="app.main:app"
  APP_DIR="src"
elif [[ -f "app/main.py" ]]; then
  APP_PATH="app.main:app"
  APP_DIR="."
elif [[ -f "backend/app/main.py" ]]; then
  export PYTHONPATH="$(pwd)/backend"
  APP_PATH="app.main:app"
  APP_DIR="backend"
else
  die "Could not find main.py. Checked: src/app/main.py, app/main.py, backend/app/main.py"
fi

echo "→ Using APP_DIR=${APP_DIR}  APP_PATH=${APP_PATH}  PORT=${PORT}"
exec uvicorn --app-dir "${APP_DIR}" "${APP_PATH}" --host 0.0.0.0 --port "${PORT}"
