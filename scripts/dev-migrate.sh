#!/usr/bin/env bash
set -euo pipefail

# --- Resolve repo root -------------------------------------------------------
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

# --- Tools -------------------------------------------------------------------
command -v alembic >/dev/null || { echo "alembic not found (pip install alembic)"; exit 1; }

# --- Alembic config path (your repo layout) ---------------------------------
CFG="${ROOT_DIR}/src/db/alembic.ini"
if [[ ! -f "${CFG}" ]]; then
  echo "Expected Alembic config at ${CFG} but it does not exist."; exit 1
fi

# --- DB URL for migrations (sync driver) ------------------------------------
: "${DATABASE_URL_SYNC:=postgresql+psycopg://agentic:agentic@localhost:5432/agentic}"
export DATABASE_URL_SYNC

# Ensure app code is importable if migrations import it
export PYTHONPATH="${ROOT_DIR}/src:${PYTHONPATH:-}"

echo "[migrate] using config: ${CFG}"
echo "[migrate] database: ${DATABASE_URL_SYNC}"

echo "[migrate] alembic current (before):"
alembic -c "${CFG}" current || true
echo

echo "[migrate] alembic upgrade head"
alembic -c "${CFG}" upgrade head
echo

echo "[migrate] alembic current (after):"
alembic -c "${CFG}" current
