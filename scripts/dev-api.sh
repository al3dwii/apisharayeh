#!/usr/bin/env bash
set -euo pipefail

# repo root
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# venv (optional)
if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

# load env file
if [[ -f ".env" ]]; then
  export $(grep -v '^#' .env | xargs)
fi

# make sure Python can import src/app/*
export PYTHONPATH="${PYTHONPATH:-}:src"

# default port
export PORT="${PORT:-8081}"

mkdir -p artifacts logs

echo "[dev-api] starting uvicorn on :${PORT}"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" --reload
