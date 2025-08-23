#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="src:${PYTHONPATH:-}"

# load .env for DATABASE_URL_SYNC
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

cd src/db
echo "[migrate] using config: $(pwd)/alembic.ini"
echo "[migrate] database: ${DATABASE_URL_SYNC:-<unset>}"
alembic current || true
alembic upgrade head
alembic current || true
