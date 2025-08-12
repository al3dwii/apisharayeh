#!/usr/bin/env bash
set -euo pipefail

alembic upgrade head || true
exec "$@"
