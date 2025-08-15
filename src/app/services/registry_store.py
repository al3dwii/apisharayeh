import json
from sqlalchemy import text
from app.services.db import tenant_session
from app.kernel.plugins.loader import registry

async def upsert_manifest(mf) -> None:
    spec_json = json.dumps(mf.model_dump())
    sql = text("""
        INSERT INTO plugin_registry (service_id, version, spec, enabled, staged)
        VALUES (:sid, :ver, CAST(:spec AS jsonb), TRUE, FALSE)
        ON CONFLICT (service_id) DO UPDATE
          SET version = EXCLUDED.version,
              spec    = EXCLUDED.spec,
              updated_at = now();
    """)
    async with tenant_session("system") as session:
        await session.execute(sql, {"sid": mf.id, "ver": mf.version, "spec": spec_json})
        await session.commit()

async def sync_all_from_loader() -> int:
    registry.refresh()
    count = 0
    for mf in registry.list():
        await upsert_manifest(mf)
        count += 1
    return count
