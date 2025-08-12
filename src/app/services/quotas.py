from datetime import date
from sqlalchemy import text
from app.services.db import tenant_session
from app.core.metrics import TOKENS_USED
from app.core.config import settings

async def add_usage(tenant_id: str, tokens: int, cost_cents: int = 0):
    today = date.today()
    async with tenant_session(tenant_id) as session:
        await session.execute(text("""
            INSERT INTO quotas (tenant_id, day, tokens_used)
            VALUES (:t, :d, :u)
            ON CONFLICT (tenant_id, day)
            DO UPDATE SET tokens_used = quotas.tokens_used + EXCLUDED.tokens_used
        """), {"t": tenant_id, "d": today, "u": tokens})
        await session.commit()
    try:
        TOKENS_USED.labels(tenant_id).inc(tokens)
    except Exception:
        pass

async def check_budget(tenant_id: str) -> bool:
    # This can aggregate today's usage and compare to settings.TOKEN_BUDGET_PER_DAY
    # Kept minimal: callers should use it before heavy runs if needed.
    return True
