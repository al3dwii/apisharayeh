#!/usr/bin/env bash
set -euo pipefail

# scripts/dev-enable-demo-tenant.sh
# Sync plugin manifests from ./plugins into DB and enable them for the demo tenant.
# Fixes prior error by ensuring tenant_plugins.updated_at (and other cols/indexes) exist.

# --- repo root / venv --------------------------------------------------------
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
[[ -d .venv ]] && source .venv/bin/activate
[[ -f .env ]] && set -a && source .env && set +a

# --- env ---------------------------------------------------------------------
export PYTHONPATH="${PYTHONPATH:-}:${ROOT_DIR}/src"
export PLUGINS_DIR="${PLUGINS_DIR:-${ROOT_DIR}/plugins}"
export TENANT_ID="${TENANT_ID:-demo-tenant}"

# Prefer local dev DB if not set
DB_SYNC_URL_DEFAULT="postgresql+psycopg://agentic:agentic@localhost:5432/agentic"
export DB_SYNC_URL="${DATABASE_URL_SYNC:-$DB_SYNC_URL_DEFAULT}"

echo "[1/2] Ensuring schema, syncing manifests from ${PLUGINS_DIR} for tenant=${TENANT_ID} …"

python - <<'PY'
import os, re, json, sys
import psycopg

# ----- connect (normalize URL for psycopg) -----
url = os.environ.get("DB_SYNC_URL","")
url = re.sub(r'^postgresql\+psycopg2?://', 'postgresql://', url)
url = re.sub(r'^postgresql\+psycopg://',   'postgresql://', url)

tenant_id   = os.environ.get("TENANT_ID", "demo-tenant")
plugins_dir = os.environ.get("PLUGINS_DIR", "")

# Import plugin loader from src/
os.environ.setdefault("PLUGINS_DIR", plugins_dir)
from app.kernel.plugins.loader import registry as reg

DDL = """
-- base tables (if you already have them, these are no-ops)
CREATE TABLE IF NOT EXISTS plugin_registry (
  service_id  text    NOT NULL,
  version     text    NOT NULL,
  enabled     boolean NOT NULL DEFAULT TRUE,
  spec        jsonb   NOT NULL
);

CREATE TABLE IF NOT EXISTS tenant_plugins (
  tenant_id   text    NOT NULL,
  service_id  text    NOT NULL,
  version     text    NOT NULL,
  enabled     boolean NOT NULL DEFAULT TRUE
);

-- make sure timestamps exist even if tables predated this script
ALTER TABLE plugin_registry
  ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE tenant_plugins
  ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

-- unique indexes to support ON CONFLICT
CREATE UNIQUE INDEX IF NOT EXISTS plugin_registry_sid_ver_uidx
  ON plugin_registry(service_id, version);

CREATE UNIQUE INDEX IF NOT EXISTS tenant_plugins_tid_sid_uidx
  ON tenant_plugins(tenant_id, service_id);
"""

SQL_TOUCH_UPDATED_AT = """
-- if the columns were just added, backfill updated_at so future updates won't fail
UPDATE plugin_registry SET updated_at = now() WHERE updated_at IS NULL;
UPDATE tenant_plugins  SET updated_at = now() WHERE updated_at IS NULL;
"""

SQL_UPSERT_REG = """
INSERT INTO plugin_registry (service_id, version, enabled, spec)
VALUES (%s, %s, TRUE, %s::jsonb)
ON CONFLICT (service_id, version)
DO UPDATE SET enabled = EXCLUDED.enabled, spec = EXCLUDED.spec, updated_at = now();
"""

SQL_UPSERT_TENANT = """
INSERT INTO tenant_plugins (tenant_id, service_id, version, enabled)
VALUES (%s, %s, %s, TRUE)
ON CONFLICT (tenant_id, service_id)
DO UPDATE SET version = EXCLUDED.version, enabled = TRUE, updated_at = now();
"""

with psycopg.connect(url) as conn:
    conn.execute("SET search_path TO public")
    with conn.cursor() as cur:
        # 1) ensure schema & indexes
        cur.execute(DDL)
        cur.execute(SQL_TOUCH_UPDATED_AT)
        conn.commit()

        # 2) scan manifests from filesystem
        reg.refresh()
        services = reg.list()
        print(f" - Loaded {len(services)} manifest(s) from {plugins_dir or '[default plugins/]'}")

        # 3) upsert registry + enable for tenant
        reg_count = 0
        t_count = 0
        for mf in services:
            spec_json = json.dumps(mf.model_dump())
            cur.execute(SQL_UPSERT_REG, (mf.id, str(mf.version), spec_json))
            reg_count += 1

            cur.execute(SQL_UPSERT_TENANT, (tenant_id, mf.id, str(mf.version)))
            t_count += 1

        conn.commit()
        print(f" - Upserted {reg_count} into plugin_registry")
        print(f" - Enabled/updated {t_count} for tenant '{tenant_id}'")

        # 4) verify
        cur.execute("SELECT COUNT(*) FROM tenant_plugins WHERE tenant_id=%s AND enabled=TRUE", (tenant_id,))
        enabled_count = cur.fetchone()[0]
        print(f" - Tenant '{tenant_id}' now has {enabled_count} enabled service(s)")
PY

echo "[2/2] Verify via API (optional) …"
if [[ -n "${PUBLIC_BASE_URL:-}" ]]; then
  set +e
  curl -fsS "${PUBLIC_BASE_URL}/v1/services" | jq . || echo "API not reachable; start API then re-run verification."
  set -e
fi

echo "Done."
